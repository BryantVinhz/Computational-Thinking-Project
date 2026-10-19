"""
src/pipeline/ingestion.py
Stage 1: Data Ingestion & Validation Pipeline Module
Assignee: Long (CSC10014 - Computational Thinking, FIT-HCMUS 2026)
Reference: Note-Computational-Thinking.pdf (Pages 2-3)

Canonical Output Specification (Document Ingestion and Parsing/output/Ingestion/):
  - documents.json
  - ducuments_version.json
  - ingestion_rejected.json
"""

import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Fix Windows console UTF-8 encoding
if sys.platform.startswith("win") and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Constants & Enums
# ---------------------------------------------------------------------------

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


class FileFormat(str, Enum):
    PDF = "PDF"
    MARKDOWN = "MARKDOWN"
    CSV = "CSV"
    JSON = "JSON"
    TXT = "TXT"
    UNKNOWN = "UNKNOWN"


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"


EXTENSION_MAP: Dict[str, FileFormat] = {
    ".pdf": FileFormat.PDF,
    ".md": FileFormat.MARKDOWN,
    ".markdown": FileFormat.MARKDOWN,
    ".csv": FileFormat.CSV,
    ".json": FileFormat.JSON,
    ".txt": FileFormat.TXT,
}

SUPPORTED_EXTENSIONS: Dict[str, str] = {
    ".pdf": "PDF",
    ".md": "MARKDOWN",
    ".markdown": "MARKDOWN",
    ".csv": "CSV",
    ".json": "JSON",
    ".txt": "TXT",
}


# ---------------------------------------------------------------------------
# Standard Canonical Schemas (Matches output/Ingestion/ 3 JSON files)
# ---------------------------------------------------------------------------

@dataclass
class DocumentSchema:
    """Master document schema stored in documents.json."""
    document_id: str
    canonical_title: str
    publisher: str
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DocumentVersionSchema:
    """Document version schema stored in ducuments_version.json and ingestion_rejected.json."""
    version_id: str
    document_id: str
    version_tag: str
    file_name: str
    file_path: str
    file_format: str
    file_size_bytes: int
    sha256_hash: Optional[str]
    status: str
    current_stage: str = "INGESTION"
    is_duplicate: bool = False
    error_message: Optional[str] = None
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class IngestionResult:
    """Result object returned by ingestion operations for status inspection."""
    document_id: str
    version_id: str
    file_name: str
    status: str
    sha256_hash: str
    error_message: Optional[str] = None
    is_duplicate: bool = False


# ---------------------------------------------------------------------------
# Core Utilities: Hasher & Validator
# ---------------------------------------------------------------------------

class Hasher:
    """Tính mã băm SHA-256 để nhận diện file trùng lặp và xác minh tính toàn vẹn."""

    @staticmethod
    def compute_sha256(file_path: Path, chunk_size: int = 65536) -> str:
        """Streamed SHA-256 calculation in 64KB blocks to prevent memory exhaustion."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(chunk_size):
                sha256.update(chunk)
        return sha256.hexdigest()


class FileValidator:
    """Nhận diện định dạng và kiểm tra tính hợp lệ của tệp đầu vào theo 5 lớp phòng thủ."""

    @classmethod
    def detect_format(cls, file_path: Path) -> FileFormat:
        ext = file_path.suffix.lower()
        return EXTENSION_MAP.get(ext, FileFormat.UNKNOWN)

    @classmethod
    def validate(cls, file_path: Path) -> Tuple[bool, FileFormat, Optional[str]]:
        """
        Kiểm tra tệp đầu vào.
        Trả về: (is_valid, file_format, error_message)
        """
        # 1. Kiểm tra tồn tại và loại file
        if not file_path.exists():
            return False, FileFormat.UNKNOWN, "File does not exist."
        if not file_path.is_file():
            return False, FileFormat.UNKNOWN, "Path is not a regular file."

        # 2. Kiểm tra quyền đọc
        if not os.access(file_path, os.R_OK):
            return False, FileFormat.UNKNOWN, "Permission denied: File is not readable."

        # 3. Kiểm tra định dạng hỗ trợ
        file_format = cls.detect_format(file_path)
        if file_format == FileFormat.UNKNOWN:
            return False, FileFormat.UNKNOWN, f"Unsupported file format '{file_path.suffix}'."

        # 4. Kiểm tra file rỗng và trần kích thước
        try:
            size = file_path.stat().st_size
        except Exception as e:
            return False, file_format, f"Failed to access file stats: {e}"

        if size == 0:
            return False, file_format, "File is empty (0 bytes)."
        if size > MAX_FILE_SIZE:
            return False, file_format, f"File exceeds maximum allowable size of 50MB ({size} bytes)."

        # 5. Kiểm tra khả năng đọc và chữ ký tệp (magic byte / utf-8)
        try:
            if file_format == FileFormat.PDF:
                with open(file_path, "rb") as f:
                    if f.read(5) != b"%PDF-":
                        return False, file_format, "Invalid or corrupted PDF header."
            else:
                with open(file_path, "r", encoding="utf-8") as f:
                    if not f.read(1024).strip():
                        return False, file_format, "File contains only whitespace."
        except UnicodeDecodeError:
            return False, file_format, "Encoding error: File is not valid UTF-8."
        except Exception as e:
            return False, file_format, f"Read error: {str(e)}"

        return True, file_format, None


# ---------------------------------------------------------------------------
# Ingestion Service (Canonical 3-JSON Manifest Output)
# ---------------------------------------------------------------------------

class IngestionService:
    """
    Dịch vụ điều phối Bước 1: Data Ingestion theo chuẩn cấu trúc 3 file JSON:
      1. documents.json
      2. ducuments_version.json
      3. ingestion_rejected.json
    """

    def __init__(self, raw_dir: str | Path, manifest_dir: str | Path, project_root: Optional[Path] = None):
        self.raw_dir = Path(raw_dir).resolve()
        self.manifest_dir = Path(manifest_dir).resolve()
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        self.project_root = Path(project_root).resolve() if project_root else self._find_project_root()

        self.docs_path = self.manifest_dir / "documents.json"
        self.versions_path = self.manifest_dir / "ducuments_version.json"
        self.rejected_path = self.manifest_dir / "ingestion_rejected.json"

        self.documents: Dict[str, DocumentSchema] = self._load_json_map(
            self.docs_path, key_field="document_id", dto_cls=DocumentSchema
        )
        self.versions: Dict[str, DocumentVersionSchema] = self._load_json_map(
            self.versions_path, key_field="version_id", dto_cls=DocumentVersionSchema
        )
        self.rejected: Dict[str, DocumentVersionSchema] = self._load_json_map(
            self.rejected_path, key_field="file_path", dto_cls=DocumentVersionSchema
        )

        self.hash_index: Dict[str, DocumentVersionSchema] = {
            v.sha256_hash: v for v in self.versions.values() if v.sha256_hash
        }

    def _find_project_root(self) -> Path:
        curr = Path(__file__).resolve().parent
        for parent in [curr] + list(curr.parents):
            if (parent / "Data").exists() or (parent / "Note-Computational-Thinking.pdf").exists():
                return parent
        return curr.parent.parent

    def to_relative_posix(self, path: Path | str) -> str:
        abs_path = Path(path).resolve()
        try:
            return abs_path.relative_to(self.project_root).as_posix()
        except ValueError:
            return abs_path.as_posix()

    @staticmethod
    def _load_json_map(path: Path, key_field: str, dto_cls) -> dict:
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {item[key_field]: dto_cls(**item) for item in data}
        except Exception:
            return {}

    def discover_files(self) -> List[Path]:
        """Quét danh sách toàn bộ tài liệu từ thư mục nguồn."""
        if not self.raw_dir.exists():
            return []
        return sorted([
            p for p in self.raw_dir.rglob("*")
            if p.is_file() and not any(part.startswith(".") for part in p.parts)
        ])

    @staticmethod
    def _parse_filename(file_path: Path) -> dict:
        """
        Tách tên file theo chuẩn: {Tổ chức}_{Tên file}_{Phiên bản / Năm xuất bản}.{ext}
        Ví dụ: OWASP_Top10-A01-Broken-Access-Control_2025.md
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

    def ingest_file(self, file_path: Path) -> Tuple[str, DocumentVersionSchema]:
        abs_file_path = file_path.resolve()
        norm_rel_path = self.to_relative_posix(abs_file_path)

        parsed_info = self._parse_filename(abs_file_path)
        doc_id = parsed_info["document_id"]
        version_tag = parsed_info["version_tag"]
        file_size = abs_file_path.stat().st_size if abs_file_path.exists() else 0

        # 1. Input Validation & Format Detection
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
                current_stage="INGESTION",
                is_duplicate=False,
                error_message=error_msg,
            )
            self.rejected[norm_rel_path] = failed_record
            return "FAILED", failed_record

        # 2. Tính SHA-256
        sha256_hash = Hasher.compute_sha256(abs_file_path)
        short_hash = sha256_hash[:12]
        version_id = f"ver_{short_hash}"

        # 3. Duplicate Detection Check
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
                current_stage="INGESTION",
                is_duplicate=True,
                error_message=(
                    f"Duplicate content with '{existing_record.file_name}' "
                    f"(version_id: '{existing_record.version_id}')."
                ),
            )
            self.rejected[norm_rel_path] = dup_record
            return "DUPLICATE", dup_record

        # 4. File hợp lệ mới
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

    def run(self) -> Dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Database & Pipeline Engine (SQLite Backend & Backwards Compatibility)
# ---------------------------------------------------------------------------

class DataIngestionPipeline:
    """Deterministic Stage 1 Ingestion Pipeline supporting SQLite backend and unit tests."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_database(self):
        """Initialize documents and document_versions schemas."""
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    document_id         TEXT PRIMARY KEY,
                    name                TEXT NOT NULL,
                    domain_category     TEXT DEFAULT 'general',
                    created_at          DATETIME NOT NULL
                );

                CREATE TABLE IF NOT EXISTS document_versions (
                    version_id          TEXT PRIMARY KEY,
                    document_id         TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                    version_number      INTEGER NOT NULL DEFAULT 1,
                    file_name           TEXT NOT NULL,
                    file_path           TEXT NOT NULL,
                    file_format         TEXT NOT NULL,
                    file_size_bytes     INTEGER NOT NULL,
                    sha256_hash         TEXT NOT NULL,
                    status              TEXT NOT NULL CHECK(status IN ('pending', 'processing', 'success', 'failed')),
                    error_message       TEXT DEFAULT NULL,
                    created_at          DATETIME NOT NULL,
                    updated_at          DATETIME NOT NULL,
                    UNIQUE(document_id, version_number)
                );

                CREATE INDEX IF NOT EXISTS idx_doc_versions_sha256 ON document_versions(sha256_hash);
                CREATE INDEX IF NOT EXISTS idx_doc_versions_status ON document_versions(status);
            """)

    @staticmethod
    def compute_sha256(file_path: Path) -> str:
        return Hasher.compute_sha256(file_path)

    @staticmethod
    def validate_file(file_path: Path) -> Tuple[bool, Optional[str]]:
        is_valid, _, error_msg = FileValidator.validate(file_path)
        return is_valid, error_msg

    def discover_files(self, raw_data_dir: str | Path) -> List[Path]:
        dir_path = Path(raw_data_dir)
        if not dir_path.exists():
            raise FileNotFoundError(f"Input directory does not exist: {raw_data_dir}")

        files = []
        for p in dir_path.rglob("*"):
            if p.is_file():
                if any(part.startswith(".") for part in p.parts):
                    continue
                if p.suffix.lower() in SUPPORTED_EXTENSIONS:
                    files.append(p)
        return sorted(files)

    def ingest_file(
        self,
        file_path: str | Path,
        domain_category: str = "general",
        existing_doc_id: Optional[str] = None
    ) -> IngestionResult:
        p = Path(file_path)
        file_name = p.name
        file_format = SUPPORTED_EXTENSIONS.get(p.suffix.lower(), "unknown")
        now = datetime.now(timezone.utc).isoformat()

        # 1. Input Validation Check
        is_valid, error = self.validate_file(p)
        if not is_valid:
            doc_id = existing_doc_id or f"doc_{uuid.uuid4().hex[:8]}"
            ver_id = f"ver_{doc_id}_v1"
            self._record_failure(doc_id, ver_id, file_name, str(p), file_format, error or "Validation failed")
            return IngestionResult(
                document_id=doc_id,
                version_id=ver_id,
                file_name=file_name,
                status="failed",
                sha256_hash="",
                error_message=error
            )

        # 2. Compute SHA-256 and File Size
        sha256 = self.compute_sha256(p)
        file_size = p.stat().st_size

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 3. Duplicate Detection Check
            cursor.execute("""
                SELECT version_id, document_id, status 
                FROM document_versions 
                WHERE sha256_hash = ? AND status = 'success'
            """, (sha256,))
            dup = cursor.fetchone()
            if dup:
                return IngestionResult(
                    document_id=dup["document_id"],
                    version_id=dup["version_id"],
                    file_name=file_name,
                    status="success",
                    sha256_hash=sha256,
                    is_duplicate=True
                )

            # 4. Determine Document Identity & Versioning
            if existing_doc_id:
                doc_id = existing_doc_id
                cursor.execute("SELECT MAX(version_number) FROM document_versions WHERE document_id = ?", (doc_id,))
                max_ver = cursor.fetchone()[0] or 0
                ver_num = max_ver + 1
            else:
                doc_id = f"doc_{uuid.uuid4().hex[:8]}"
                ver_num = 1
                cursor.execute("""
                    INSERT INTO documents (document_id, name, domain_category, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(document_id) DO UPDATE SET name=excluded.name
                """, (doc_id, p.stem.replace("_", " "), domain_category, now))

            ver_id = f"ver_{doc_id}_v{ver_num}"

            # 5. Insert Version with Status 'pending'
            cursor.execute("""
                INSERT INTO document_versions (
                    version_id, document_id, version_number, file_name, file_path,
                    file_format, file_size_bytes, sha256_hash, status, error_message,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?)
            """, (ver_id, doc_id, ver_num, file_name, str(p.resolve()), file_format, file_size, sha256, now, now))
            conn.commit()

        return IngestionResult(
            document_id=doc_id,
            version_id=ver_id,
            file_name=file_name,
            status="pending",
            sha256_hash=sha256
        )

    def update_status(self, version_id: str, status: str, error_message: Optional[str] = None):
        """Update lifecycle state: pending -> processing -> success / failed."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE document_versions 
                SET status = ?, error_message = ?, updated_at = ?
                WHERE version_id = ?
            """, (status, error_message, now, version_id))
            conn.commit()

    def _record_failure(self, doc_id: str, ver_id: str, file_name: str, file_path: str, file_format: str, error: str):
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT INTO documents (document_id, name, created_at)
                    VALUES (?, ?, ?) ON CONFLICT(document_id) DO NOTHING
                """, (doc_id, file_name, now))
                conn.execute("""
                    INSERT INTO document_versions (
                        version_id, document_id, version_number, file_name, file_path,
                        file_format, file_size_bytes, sha256_hash, status, error_message,
                        created_at, updated_at
                    ) VALUES (?, ?, 1, ?, ?, ?, 0, '', 'failed', ?, ?, ?)
                    ON CONFLICT(version_id) DO UPDATE SET status='failed', error_message=excluded.error_message
                """, (ver_id, doc_id, file_name, file_path, file_format, error, now, now))
                conn.commit()
        except Exception:
            pass

    def ingest_directory(self, raw_data_dir: str | Path, domain_category: str = "general") -> List[IngestionResult]:
        candidates = self.discover_files(raw_data_dir)
        results = []
        for file_path in candidates:
            res = self.ingest_file(file_path, domain_category=domain_category)
            results.append(res)
        return results

    def export_canonical_json(self, output_dir: str | Path, project_root: Optional[Path] = None):
        """
        Exports the current database state into the 3 canonical JSON manifests:
          - documents.json
          - ducuments_version.json
          - ingestion_rejected.json
        """
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        root = Path(project_root) if project_root else Path.cwd()

        docs_list = []
        versions_list = []
        rejected_list = []

        with self._get_connection() as conn:
            for row in conn.execute("SELECT * FROM documents"):
                docs_list.append({
                    "document_id": row["document_id"],
                    "canonical_title": row["name"],
                    "publisher": row["domain_category"],
                    "created_at": row["created_at"],
                })

            for row in conn.execute("SELECT * FROM document_versions"):
                try:
                    rel_path = Path(row["file_path"]).relative_to(root).as_posix()
                except ValueError:
                    rel_path = Path(row["file_path"]).as_posix()

                rec = {
                    "version_id": row["version_id"],
                    "document_id": row["document_id"],
                    "version_tag": f"v{row['version_number']}.0",
                    "file_name": row["file_name"],
                    "file_path": rel_path,
                    "file_format": row["file_format"].upper(),
                    "file_size_bytes": row["file_size_bytes"],
                    "sha256_hash": row["sha256_hash"] if row["sha256_hash"] else None,
                    "status": row["status"],
                    "current_stage": "INGESTED" if row["status"] != "failed" else "INGESTION",
                    "is_duplicate": False,
                    "error_message": row["error_message"],
                    "updated_at": row["updated_at"],
                }
                if row["status"] == "failed":
                    rejected_list.append(rec)
                else:
                    versions_list.append(rec)

        with open(out_path / "documents.json", "w", encoding="utf-8") as f:
            json.dump(docs_list, f, indent=2, ensure_ascii=False)

        with open(out_path / "ducuments_version.json", "w", encoding="utf-8") as f:
            json.dump(versions_list, f, indent=2, ensure_ascii=False)

        with open(out_path / "ingestion_rejected.json", "w", encoding="utf-8") as f:
            json.dump(rejected_list, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    # Test execution on standard project paths
    root_dir = Path(__file__).resolve().parent.parent.parent.parent
    data_dir = root_dir / "Data"
    out_dir = root_dir / "Document Ingestion and Parsing" / "output" / "Ingestion"

    print("=== RUNNING DATA INGESTION PIPELINE (STAGE 1) ===")
    print(f"Project root : {root_dir}")
    print(f"Data dir     : {data_dir}")
    print(f"Output dir   : {out_dir}")

    service = IngestionService(raw_dir=data_dir, manifest_dir=out_dir, project_root=root_dir)
    res = service.run()

    print("\n[RESULT SUMMARY]")
    print(f"- Total scanned       : {res['total_scanned']}")
    print(f"- New ingested        : {res['new_ingested']}")
    print(f"- Skipped unchanged   : {res['skipped_unchanged']}")
    print(f"- Duplicates detected : {res['duplicates']}")
    print(f"- Rejected (failed)   : {res['failed']}")
    print(f"=> Successfully generated canonical 3 JSON manifests at: {out_dir}")
