import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

# Make path.py (parent folder) and schema_metadata.py importable when running this file directly
CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
for _dir in (CURRENT_DIR, PARENT_DIR):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

from path import FINAL_OUT_DIR, INGESTION_OUT_DIR
from schema_metadata import ChunkMetadataSchema, DocumentMetadataSchema, MetadataOutputSchema


class MetadataStore:

    def __init__(self, db_path: Path | str = FINAL_OUT_DIR / "metadata.db"):
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
                    chunk_id TEXT PRIMARY KEY
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
                "INSERT OR REPLACE INTO chunk_metadata (chunk_id) VALUES (:chunk_id)",
                [c.to_dict() for c in chunks],
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
            title=document["canonical_title"],
            file_format=version["file_format"],
        )

    @staticmethod
    def build_chunk_metadata(chunking_output) -> list[ChunkMetadataSchema]:
        if not isinstance(chunking_output, dict):
            chunking_output = chunking_output.to_dict()
        return [ChunkMetadataSchema(chunk_id=c["chunk_id"]) for c in chunking_output["chunks"]]

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
    versions = json.loads((INGESTION_OUT_DIR / "ducuments_version.json").read_text(encoding="utf-8"))

    document = documents[0]
    version = next(v for v in versions if v["document_id"] == document["document_id"])

    # Mock ChunkingOutputSchema.to_dict() (only the keys this step needs)
    chunking_output = {
        "document_id": document["document_id"],
        "version_id": version["version_id"],
        "chunking_strategy": "structure_aware",
        "chunks": [{"chunk_id": f"{version['version_id']}_c{i:05d}"} for i in range(3)],
        "chunk_sources": [],
    }

    extractor = MetadataExtractor(MetadataStore())
    result = extractor.run(document, version, chunking_output)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
