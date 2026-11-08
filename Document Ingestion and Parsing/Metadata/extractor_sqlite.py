import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Make path.py (parent folder) and schema_metadata.py importable when running this file directly
CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
for _dir in (CURRENT_DIR, PARENT_DIR):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

from path import VECTOR_DB_DIR, INGESTION_OUT_DIR
from schema_metadata import (
    ChunkMetadataSchema,
    DocumentMetadataSchema,
    MetadataOutputSchema,
    VectorDBRecordSchema,
)


class MetadataStore:

    def __init__(self, db_path: Path | str = VECTOR_DB_DIR / "metadata.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.create_tables()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def create_tables(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS document_metadata (
                    document_id TEXT NOT NULL,
                    version_id  TEXT NOT NULL,
                    title       TEXT,
                    file_format TEXT,
                    PRIMARY KEY (document_id, version_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chunk_metadata (
                    chunk_id       TEXT PRIMARY KEY,
                    document_id    TEXT NOT NULL,
                    version_id     TEXT NOT NULL,
                    chunk_idx      INTEGER,
                    chunk_type     TEXT,
                    token_count    INTEGER,
                    chunk_strategy TEXT,
                    heading_path   TEXT,
                    FOREIGN KEY (document_id, version_id)
                        REFERENCES document_metadata(document_id, version_id)
                )
            """)

    def save_document(self, document: DocumentMetadataSchema) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO document_metadata (document_id, version_id, title, file_format)
                VALUES (:document_id, :version_id, :title, :file_format)
                """,
                document.to_dict(),
            )

    def save_chunks(self, chunks: list[ChunkMetadataSchema]) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO chunk_metadata (
                    chunk_id, document_id, version_id, chunk_idx,
                    chunk_type, token_count, chunk_strategy, heading_path
                ) VALUES (
                    :chunk_id, :document_id, :version_id, :chunk_idx,
                    :chunk_type, :token_count, :chunk_strategy, :heading_path
                )
                """,
                [c.to_sqlite_dict() for c in chunks],
            )


class MetadataExtractor:

    def __init__(self, store: MetadataStore):
        self.store = store

    @staticmethod
    def build_document_metadata(document: dict, version: dict) -> DocumentMetadataSchema:
        if document["document_id"] != version["document_id"]:
            raise ValueError("document and version belong to different document_id")
        return DocumentMetadataSchema(
            document_id=document["document_id"],
            version_id=version["version_id"],
            title=document.get("canonical_title") or document.get("title") or "",
            file_format=version["file_format"],
        )

    @staticmethod
    def build_chunk_metadata(chunking_output) -> list[ChunkMetadataSchema]:
        if not isinstance(chunking_output, dict):
            chunking_output = chunking_output.to_dict()

        doc_id = chunking_output.get("document_id", "")
        ver_id = chunking_output.get("version_id", "")
        default_strategy = chunking_output.get("chunking_strategy", "structure_aware")

        result = []
        for idx, c in enumerate(chunking_output["chunks"]):
            result.append(
                ChunkMetadataSchema(
                    chunk_id=c["chunk_id"],
                    document_id=c.get("document_id") or doc_id,
                    version_id=c.get("version_id") or ver_id,
                    chunk_idx=c.get("chunk_idx", idx),
                    chunk_type=c.get("chunk_type") or "text",
                    token_count=c.get("token_count", 0),
                    chunk_strategy=c.get("chunk_strategy") or default_strategy,
                    heading_path=c.get("heading_path") or [],
                )
            )
        return result

    @staticmethod
    def build_vector_records(
        doc_meta: DocumentMetadataSchema | dict,
        chunking_output,
        chunk_meta_list: list[ChunkMetadataSchema | dict] | None = None,
    ) -> list[VectorDBRecordSchema]:
        """
        Merge Document Metadata with all attributes of ChunkSchema (excluding ChunkSource),
        placing `chunk_id` (id) and `text_content` (text) outside `metadata`.
        """
        if isinstance(doc_meta, DocumentMetadataSchema):
            doc_dict = doc_meta.to_dict()
        else:
            doc_dict = doc_meta

        if not isinstance(chunking_output, dict):
            chunking_output = chunking_output.to_dict()

        chunks_raw = chunking_output.get("chunks", [])
        default_strategy = chunking_output.get("chunking_strategy", "structure_aware")

        meta_by_id = {}
        if chunk_meta_list:
            for cm in chunk_meta_list:
                cm_dict = cm.to_dict() if isinstance(cm, ChunkMetadataSchema) else cm
                meta_by_id[cm_dict["chunk_id"]] = cm_dict

        records: list[VectorDBRecordSchema] = []
        for idx, chunk in enumerate(chunks_raw):
            c_id = chunk["chunk_id"]
            text_content = chunk.get("text_content") or chunk.get("text") or ""
            c_meta = meta_by_id.get(c_id, chunk)

            heading_path = c_meta.get("heading_path")
            if heading_path is None:
                heading_path = chunk.get("heading_path") or []

            combined_meta = {
                # Attributes from Document Metadata
                "document_id": doc_dict["document_id"],
                "version_id": doc_dict["version_id"],
                "title": doc_dict.get("title") or doc_dict.get("canonical_title") or "",
                "file_format": doc_dict.get("file_format") or "PDF",

                # Attributes from ChunkSchema (excluding chunk_id, text_content, and ChunkSource)
                "chunk_idx": int(c_meta.get("chunk_idx", idx)),
                "chunk_type": c_meta.get("chunk_type") or "text",
                "token_count": int(c_meta.get("token_count", 0)),
                "chunk_strategy": c_meta.get("chunk_strategy") or default_strategy,
                "heading_path": heading_path,
            }

            records.append(
                VectorDBRecordSchema(
                    id=c_id,
                    text=text_content,
                    metadata=combined_meta,
                )
            )
        return records

    def run(self, document: dict, version: dict, chunking_output) -> MetadataOutputSchema:
        """Build + save both metadata tables for one document version."""
        output = MetadataOutputSchema(
            document=self.build_document_metadata(document, version),
            chunks=self.build_chunk_metadata(chunking_output),
        )
        self.store.save_document(output.document)
        self.store.save_chunks(output.chunks)
        return output


if __name__ == "__main__":
    documents = json.loads((INGESTION_OUT_DIR / "documents.json").read_text(encoding="utf-8"))
    versions = json.loads((INGESTION_OUT_DIR / "documents_version.json").read_text(encoding="utf-8"))

    document = documents[0]
    version = next(v for v in versions if v["document_id"] == document["document_id"])

    # Mock ChunkingOutputSchema.to_dict()
    chunking_output = {
        "document_id": document["document_id"],
        "version_id": version["version_id"],
        "chunking_strategy": "structure_aware",
        "chunks": [
            {
                "chunk_id": f"{version['version_id']}_c{i:05d}",
                "document_id": document["document_id"],
                "version_id": version["version_id"],
                "chunk_idx": i,
                "chunk_type": "text",
                "text_content": f"Sample security chunk content {i} for {document['canonical_title']}.",
                "token_count": 10,
                "chunk_strategy": "structure_aware",
                "heading_path": ["A05: Injection", "SQL Injection"],
            }
            for i in range(3)
        ],
        "chunk_sources": [],
    }

    # Remove existing database if schema changed and run test
    db_file = VECTOR_DB_DIR / "metadata.db"
    if db_file.exists():
        db_file.unlink()

    extractor = MetadataExtractor(MetadataStore(db_file))
    result = extractor.run(document, version, chunking_output)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
