import hashlib
import json
import re
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

from path import to_relative_posix, to_actual_path
from schema_ingestion import DocumentSchema, DocumentVersionSchema, ProcessingStatus
from validation import FileValidator


class Hasher:
    """Compute SHA-256 hash to detect duplicate files and content changes."""

    @staticmethod
    def compute_sha256(file_path: Path, chunk_size: int = 65536) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(chunk_size):
                sha256.update(chunk)
        return sha256.hexdigest()


class IngestionService:
    """Orchestration service for Step 1: Data Ingestion."""

    def __init__(self, raw_dir: str | Path, manifest_dir: str | Path):
        self.raw_dir = Path(raw_dir)
        self.manifest_dir = Path(manifest_dir)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)

        self.docs_path = self.manifest_dir / "documents.json"
        self.versions_path = self.manifest_dir / "documents_version.json"
        self.rejected_path = self.manifest_dir / "ingestion_rejected.json"

        self.documents: dict[str, DocumentSchema] = self._load_json_map(
            self.docs_path, key_field="document_id", dto_cls=DocumentSchema
        )
        self.versions: dict[str, DocumentVersionSchema] = self._load_json_map(
            self.versions_path, key_field="version_id", dto_cls=DocumentVersionSchema
        )
        self.rejected: dict[str, DocumentVersionSchema] = self._load_json_map(
            self.rejected_path, key_field="file_path", dto_cls=DocumentVersionSchema
        )

        self.hash_index: dict[str, DocumentVersionSchema] = {
            v.sha256_hash: v for v in self.versions.values() if v.sha256_hash
        }

    @staticmethod
    def _load_json_map(path: Path, key_field: str, dto_cls) -> dict:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {item[key_field]: dto_cls(**item) for item in data}

    def discover_files(self) -> list[Path]:
        """Scan all documents from the source directory."""
        if not self.raw_dir.exists():
            return []
        return sorted([
            p for p in self.raw_dir.rglob("*")
            if p.is_file() and not p.name.startswith(".")
        ])

    @staticmethod
    def _parse_filename(file_path: Path) -> dict:
        """
        Parse filename according to convention: {Organization}_{File Name}_{Version / Publication Year}.{ext}
        """
        stem = file_path.stem
        parts = [p for p in stem.split("_") if p]

        if len(parts) >= 3:
            publisher = parts[0]
            version_tag = parts[-1]
            topic_words = parts[1:-1]
        elif len(parts) == 2:
            publisher = parts[0]
            topic_words = [parts[1]]
            version_tag = "NaN"
        else:
            publisher = "Unknown"
            topic_words = parts if parts else [stem]
            version_tag = "NaN"

        topic_str = " ".join(topic_words)
        slug_raw = f"{publisher}_{'_'.join(topic_words)}".lower()
        clean_slug = re.sub(r"[^a-z0-9]+", "_", slug_raw).strip("_")

        return {
            "document_id": f"doc_{clean_slug}",
            "canonical_title": f"{publisher} - {topic_str}",
            "publisher": publisher,
            "version_tag": version_tag,
        }

    def ingest_file(self, file_path: Path) -> tuple[str, DocumentVersionSchema]:
        abs_file_path = to_actual_path(file_path)
        norm_rel_path = to_relative_posix(abs_file_path)

        parsed_info = self._parse_filename(abs_file_path)
        doc_id = parsed_info["document_id"]
        version_tag = parsed_info["version_tag"]
        file_size = abs_file_path.stat().st_size if abs_file_path.exists() else 0

        is_valid, file_format, error_msg = FileValidator.validate(abs_file_path)
        if not is_valid:
            failed_id = f"ver_failed_{hashlib.md5(norm_rel_path.encode()).hexdigest()[:8]}"
            failed_record = DocumentVersionSchema(
                version_id=failed_id,
                document_id=doc_id,
                version_tag=version_tag,
                file_name=abs_file_path.name,
                file_path=norm_rel_path,
                file_format=file_format.value,
                file_size_bytes=file_size,
                sha256_hash=None,
                status=ProcessingStatus.FAILED.value,
                is_duplicate=False,
                error_message=error_msg,
            )
            self.rejected[norm_rel_path] = failed_record
            return "FAILED", failed_record

        sha256_hash = Hasher.compute_sha256(abs_file_path)
        short_hash = sha256_hash[:12]
        version_id = f"ver_{short_hash}"

        if sha256_hash in self.hash_index:
            existing_record = self.hash_index[sha256_hash]

            if existing_record.file_path == norm_rel_path:
                self.rejected.pop(norm_rel_path, None)
                return "SKIPPED_UNCHANGED", existing_record

            dup_id = f"ver_dup_{short_hash}_{hashlib.md5(norm_rel_path.encode()).hexdigest()[:6]}"
            dup_record = DocumentVersionSchema(
                version_id=dup_id,
                document_id=doc_id,
                version_tag=version_tag,
                file_name=abs_file_path.name,
                file_path=norm_rel_path,
                file_format=file_format.value,
                file_size_bytes=file_size,
                sha256_hash=sha256_hash,
                status=ProcessingStatus.FAILED.value,
                is_duplicate=True,
                error_message=(
                    f"Duplicate content with '{existing_record.file_name}' "
                    f"(version_id: '{existing_record.version_id}')."
                ),
            )
            self.rejected[norm_rel_path] = dup_record
            return "DUPLICATE", dup_record

        self.rejected.pop(norm_rel_path, None)
        old_versions_at_same_path = [
            v_id for v_id, v in self.versions.items() if v.file_path == norm_rel_path
        ]
        for old_vid in old_versions_at_same_path:
            old_v = self.versions.pop(old_vid)
            if old_v.sha256_hash:
                self.hash_index.pop(old_v.sha256_hash, None)

        if doc_id not in self.documents:
            self.documents[doc_id] = DocumentSchema(
                document_id=doc_id,
                canonical_title=parsed_info["canonical_title"],
                publisher=parsed_info["publisher"],
            )

        version_record = DocumentVersionSchema(
            version_id=version_id,
            document_id=doc_id,
            version_tag=version_tag,
            file_name=abs_file_path.name,
            file_path=norm_rel_path,
            file_format=file_format.value,
            file_size_bytes=file_size,
            sha256_hash=sha256_hash,
            status=ProcessingStatus.PENDING.value,
            current_stage="INGESTED",
            is_duplicate=False,
            error_message=None,
        )

        self.versions[version_id] = version_record
        self.hash_index[sha256_hash] = version_record
        return "NEW_SUCCESS", version_record

    def run(self) -> dict:
        files = self.discover_files()
        stats = {
            "total_scanned": len(files),
            "new_ingested": 0,
            "skipped_unchanged": 0,
            "duplicates": 0,
            "failed": 0,
        }

        for file_path in files:
            outcome, _ = self.ingest_file(file_path)
            if outcome == "NEW_SUCCESS":
                stats["new_ingested"] += 1
            elif outcome == "SKIPPED_UNCHANGED":
                stats["skipped_unchanged"] += 1
            elif outcome == "DUPLICATE":
                stats["duplicates"] += 1
            elif outcome == "FAILED":
                stats["failed"] += 1

        self.save_outputs()
        return stats

    def save_outputs(self) -> None:
        with open(self.docs_path, "w", encoding="utf-8") as f:
            json.dump([d.to_dict() for d in self.documents.values()], f, indent=2, ensure_ascii=False)

        with open(self.versions_path, "w", encoding="utf-8") as f:
            json.dump([v.to_dict() for v in self.versions.values()], f, indent=2, ensure_ascii=False)

        with open(self.rejected_path, "w", encoding="utf-8") as f:
            json.dump([r.to_dict() for r in self.rejected.values()], f, indent=2, ensure_ascii=False)
