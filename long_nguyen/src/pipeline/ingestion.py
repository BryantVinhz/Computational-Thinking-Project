"""
src/pipeline/ingestion.py
Stage 1: Data Ingestion & Validation Pipeline Module
Assignee: Long (CSC10014 - Computational Thinking, FIT-HCMUS 2026)
Reference: Note-Computational-Thinking.pdf (Pages 2-3)
"""

import os
import sqlite3
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".md": "md",
    ".txt": "txt",
    ".csv": "csv",
    ".json": "json"
}

@dataclass
class IngestionResult:
    document_id: str
    version_id: str
    file_name: str
    status: str
    sha256_hash: str
    error_message: Optional[str] = None
    is_duplicate: bool = False

class DataIngestionPipeline:
    """Deterministic Stage 1 Ingestion Pipeline."""

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
        """Streamed SHA-256 calculation."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def validate_file(file_path: Path) -> tuple[bool, Optional[str]]:
        """Validate existence, readability, non-emptiness, and size ceiling."""
        if not file_path.exists():
            return False, "File does not exist."
        if not file_path.is_file():
            return False, "Target is not a regular file."
        try:
            size = file_path.stat().st_size
        except Exception as e:
            return False, f"Failed to access file stats: {e}"

        if size == 0:
            return False, "File is empty (0 bytes)."
        if size > MAX_FILE_SIZE:
            return False, f"File exceeds maximum allowable size of 50MB ({size} bytes)."

        try:
            with open(file_path, "rb") as f:
                f.read(1024)
        except Exception as e:
            return False, f"File read permission error: {e}"

        return True, None

    def discover_files(self, raw_data_dir: str | Path) -> List[Path]:
        """Recursively scan directory for supported documents."""
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
        """Ingest a single file with validation, hashing, deduplication, and status tracking."""
        p = Path(file_path)
        file_name = p.name
        file_format = SUPPORTED_EXTENSIONS.get(p.suffix.lower(), "unknown")
        now = datetime.now(timezone.utc).isoformat()

        # 1. Input Validation Check
        is_valid, error = self.validate_file(p)
        if not is_valid:
            # Register failed record if possible
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
        """Ingest all supported files in a directory."""
        candidates = self.discover_files(raw_data_dir)
        results = []
        for file_path in candidates:
            res = self.ingest_file(file_path, domain_category=domain_category)
            results.append(res)
        return results
