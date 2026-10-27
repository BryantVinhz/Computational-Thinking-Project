import sqlite3
from pathlib import Path


class SQLiteDatabase:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    canonical_title TEXT NOT NULL,
                    publisher TEXT
                );

                CREATE TABLE IF NOT EXISTS document_versions (
                    version_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    version_tag TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_format TEXT NOT NULL,
                    file_size_bytes INTEGER NOT NULL,
                    sha256_hash TEXT,
                    FOREIGN KEY (document_id) REFERENCES documents(document_id)
                );


                CREATE TABLE IF NOT EXISTS normalized_blocks (
                    normalized_id TEXT PRIMARY KEY,
                    block_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    block_idx INTEGER NOT NULL,
                    file_format TEXT NOT NULL,
                    block_type TEXT NOT NULL,
                    normalized_text TEXT,
                    locator_json TEXT,
                    heading_path_json TEXT,
                    heading_level INTEGER,
                    code_language TEXT,
                    FOREIGN KEY (document_id) REFERENCES documents(document_id),
                    FOREIGN KEY (version_id) REFERENCES document_versions(version_id)
                );

                CREATE TABLE IF NOT EXISTS chunk_sources (
                    chunk_source_id TEXT PRIMARY KEY,
                    chunk_id TEXT NOT NULL,
                    normalized_block_id TEXT NOT NULL,
                    source_order INTEGER NOT NULL,
                    start_offset INTEGER NOT NULL,
                    end_offset INTEGER NOT NULL,
                    FOREIGN KEY (normalized_block_id) REFERENCES normalized_blocks(normalized_id)
                );

                CREATE INDEX IF NOT EXISTS idx_chunk_sources_chunk_id ON chunk_sources(chunk_id);
                CREATE INDEX IF NOT EXISTS idx_blocks_version_id ON normalized_blocks(version_id);
                """
            )

            connection.commit()