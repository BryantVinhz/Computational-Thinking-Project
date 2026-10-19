# Pipeline Stage 1: Data Ingestion & Validation — Step-by-Step Engineering Guide

> **Course**: CSC10014 — Computational Thinking (FIT - HCMUS, 2026)  
> **Topic Track**: Track C — Intelligent Document Workspace  
> **Stage Focus**: Stage 1 — Data Ingestion, Format Detection, Validation, Deduplication & Version Tracking (`data_ingestion`)  
> **Stage Owner**: **Long**  
> **Deadline**: Tuesday 20/10  
> **Reference Specification**: [`Note-Computational-Thinking.pdf`](file:///d:/computational_thinking/Computational-Thinking-Project/Note-Computational-Thinking.pdf) (Pages 2–3) & [`long_nguyen/guide.md`](file:///d:/computational_thinking/Computational-Thinking-Project/long_nguyen/guide.md)

---

## 1. Executive Summary & Computational Thinking Framing

Data Ingestion is the gatekeeper of the entire Intelligent Document Workspace. If corrupt, unvalidated, or duplicate files pass into the downstream stages (Parsing, Normalization, Chunking, Embeddings), the pipeline produces invalid embeddings, breaks relational foreign keys, and wastes compute.

### Computational Thinking Decomposition

```
+----------------------------------------------------------------------------------------------------+
| 1. UNDERSTAND                                                                                      |
| Input: Heterogeneous raw files (.pdf, .txt, .md, .csv, .json) located in `data/raw/`.               |
| Output: Validated document & version records committed to SQLite with SHA-256 integrity proof.     |
+----------------------------------------------------------------------------------------------------+
                                                  |
                                                  v
+----------------------------------------------------------------------------------------------------+
| 2. DECOMPOSE (Sub-Tasks from Note-Computational-Thinking.pdf)                                      |
|  - Sub-task 1: File Discovery (recursive directory scan)                                           |
|  - Sub-task 2: Format Detection (MIME type + file extension)                                       |
|  - Sub-task 3: Input Validation (existence, readability, > 0 bytes, size ceiling)                  |
|  - Sub-task 4: Document Identification (persistent UUID / prefix generation)                       |
|  - Sub-task 5: Duplicate Detection (SHA-256 content hashing)                                       |
|  - Sub-task 6: Processing Lifecycle (pending -> processing -> success | failed)                    |
|  - Sub-task 7: Error Handling (detailed error messages persisted upon failure)                     |
+----------------------------------------------------------------------------------------------------+
                                                  |
                                                  v
+----------------------------------------------------------------------------------------------------+
| 3. REPRESENT                                                                                       |
| Relational Schema: 2 core tables (`documents` and `document_versions`) with 1-to-many FK cascade.   |
+----------------------------------------------------------------------------------------------------+
                                                  |
                                                  v
+----------------------------------------------------------------------------------------------------+
| 4. DECIDE (Deduplication & State Machine Logic)                                                    |
|  - If SHA-256 exists with status='success': SKIP (idempotent, avoid redundant processing).         |
|  - If same document_id but new hash: Increment version_number (v1 -> v2) and register new version.  |
|  - If file invalid or unreadable: Mark status='failed' with error_message in audit log.            |
+----------------------------------------------------------------------------------------------------+
                                                  |
                                                  v
+----------------------------------------------------------------------------------------------------+
| 5. EXECUTE & VERIFY                                                                                |
| Deterministic Python ingestion module + SQLite transaction + Pytest test suite with 100% coverage.  |
+----------------------------------------------------------------------------------------------------+
```

> [!IMPORTANT]
> **Core Principle**: Data Ingestion is **100% deterministic**. No LLMs are used here. We use cryptographic hashing, filesystem checks, and relational integrity.

---

## 2. Database Schema Design (From `Note-Computational-Thinking.pdf`)

As specified in the course note, Long must implement **2 core schemas** in SQLite (`workspace.db`):

### 1. `documents` (Master Document Table)
Stores the high-level identity of the document that remains constant across versions:
* `document_id` (`TEXT PRIMARY KEY`): Unique persistent identifier (e.g. `doc_a1b2c3d4` or `cs_sqli_prevention`).
* `name` (`TEXT NOT NULL`): Human-readable name or title of the document.
* `domain_category` (`TEXT DEFAULT 'general'`): Category (e.g. `remediation`, `testing`, `taxonomy`, `standards`, `pentest_report`).
* `created_at` (`DATETIME NOT NULL`): ISO-8601 timestamp when the document was first introduced.

### 2. `document_versions` (Version & Pipeline Tracking Table)
Tracks every ingested revision, cryptographic hash, and processing lifecycle:
* `version_id` (`TEXT PRIMARY KEY`): Unique version ID (e.g. `ver_doc_a1b2c3d4_v1`).
* `document_id` (`TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE`): Foreign key linking back to master document.
* `version_number` (`INTEGER NOT NULL DEFAULT 1`): Incremental revision counter (1, 2, 3...).
* `file_name` (`TEXT NOT NULL`): Exact file name on disk (e.g. `SQL_Injection_Prevention_Cheat_Sheet.md`).
* `file_path` (`TEXT NOT NULL`): Relative or canonical path on disk.
* `file_format` (`TEXT NOT NULL`): Format detected (`pdf`, `md`, `txt`, `csv`, `json`).
* `file_size_bytes` (`INTEGER NOT NULL`): Exact byte size on disk.
* `sha256_hash` (`TEXT NOT NULL`): Cryptographic SHA-256 hex digest of raw file content.
* `status` (`TEXT NOT NULL CHECK(status IN ('pending', 'processing', 'success', 'failed'))`): Pipeline state machine flag.
* `error_message` (`TEXT DEFAULT NULL`): Specific failure reason if `status = 'failed'`.
* `created_at` (`DATETIME NOT NULL`): Timestamp when this version was staged.
* `updated_at` (`DATETIME NOT NULL`): Timestamp of last state change.

---

## 3. Step-by-Step Implementation Guide for Long

```
   Step 1: Setup DDL Schema in SQLite
                 |
                 v
   Step 2: File Discovery & Format Filtering
                 |
                 v
   Step 3: Robust Input Validation Checks
                 |
                 v
   Step 4: Streamed SHA-256 Hashing & ID Generation
                 |
                 v
   Step 5: Deduplication & Versioning Decision Engine
                 |
                 v
   Step 6: Atomic SQLite State Transition & Commit
                 |
                 v
   Step 7: Verification Queries & Pytest Unit Tests
```

---

### Step 1: Initialize Database Tables (DDL)

Long sets up the database table migration in `src/pipeline/schema.py` or directly in the ingestion module:

```sql
-- Enable foreign keys
PRAGMA foreign_keys = ON;

-- 1. Master Documents Table
CREATE TABLE IF NOT EXISTS documents (
    document_id         TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    domain_category     TEXT DEFAULT 'general',
    created_at          DATETIME NOT NULL
);

-- 2. Document Versions Table
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

-- Index for instant duplicate lookup by SHA-256
CREATE INDEX IF NOT EXISTS idx_doc_versions_sha256 ON document_versions(sha256_hash);
CREATE INDEX IF NOT EXISTS idx_doc_versions_status ON document_versions(status);
```

---

### Step 2: Recursive File Discovery & Format Detection

* **Objective**: Scan the target input directory (`data/raw/`) recursively while filtering out unsupported or hidden OS files (`.DS_Store`, `Thumbs.db`, `.git`, `.tmp`).
* **Supported Formats**:
  * `.pdf` $\rightarrow$ `pdf` (`application/pdf`)
  * `.md` $\rightarrow$ `md` (`text/markdown`)
  * `.txt` $\rightarrow$ `txt` (`text/plain`)
  * `.csv` $\rightarrow$ `csv` (`text/csv`)
  * `.json` $\rightarrow$ `json` (`application/json`)

```python
from pathlib import Path
import mimetypes

SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".md": "md",
    ".txt": "txt",
    ".csv": "csv",
    ".json": "json"
}

def discover_files(directory_path: str | Path) -> list[Path]:
    """Recursively discover supported candidate files from directory."""
    root = Path(directory_path)
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory_path}")
        
    discovered = []
    for file_path in root.rglob("*"):
        if file_path.is_file():
            # Skip hidden files or files in hidden folders (e.g. .git)
            if any(part.startswith(".") for part in file_path.parts):
                continue
            if file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                discovered.append(file_path)
    return discovered
```

---

### Step 3: Input Validation & Size Checks

Before processing, Long must validate each candidate file against strict defensive rules:
1. **File Exists & Is Regular File**: Reject broken symlinks or missing files.
2. **File Readable**: Reject unreadable permissions.
3. **Non-Empty Check**: File size must be $> 0\text{ bytes}$ (empty files corrupt parsers).
4. **Max Size Ceiling**: Check that file size does not exceed maximum allowable threshold (e.g. $50\text{ MB} = 52,428,800\text{ bytes}$).

```python
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

def validate_candidate_file(file_path: Path) -> tuple[bool, str | None]:
    """Validate file readability, non-emptiness, and size ceiling.
    Returns: (is_valid, error_reason)
    """
    if not file_path.exists():
        return False, "File does not exist on disk."
    if not file_path.is_file():
        return False, "Path is not a regular file."
    
    try:
        size = file_path.stat().st_size
    except Exception as e:
        return False, f"Cannot read file stat: {e}"

    if size == 0:
        return False, "File is empty (0 bytes)."
    if size > MAX_FILE_SIZE_BYTES:
        return False, f"File exceeds maximum size limit of 50MB ({size} bytes)."

    # Test read access
    try:
        with open(file_path, "rb") as f:
            f.read(1024)  # Read small sample
    except Exception as e:
        return False, f"File permission or read error: {e}"

    return True, None
```

---

### Step 4: Streamed SHA-256 Hashing & Document ID Generation

* **Objective**: Compute cryptographic SHA-256 hash without loading entire 50MB files into memory at once (stream in 64KB blocks).
* **ID Generation**: Generate clean, collision-free identifiers:
  * Master: `doc_<slug_or_uuid>`
  * Version: `ver_<doc_id>_v<version_number>`

```python
import hashlib
import uuid

def compute_file_sha256(file_path: Path) -> str:
    """Compute SHA-256 hash using chunked streaming."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):  # 64 KB chunks
            hasher.update(chunk)
    return hasher.hexdigest()

def generate_document_id(prefix: str = "doc") -> str:
    """Generate a persistent 8-character unique document ID."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"
```

---

### Step 5: Duplicate Detection & Versioning Logic

Long implements the decision engine:
1. **Case A: Exact Hash Already Ingested (`Duplicate File`)**:
   * Query `document_versions` WHERE `sha256_hash = ?` AND `status = 'success'`.
   * Action: Skip re-ingestion, return existing `document_id` and `version_id`. Log as `[SKIPPED - DUPLICATE]`.
2. **Case B: Updated Content for Existing Document (`New Version`)**:
   * If the file has the same document name/identity but a different SHA-256 hash:
   * Action: Increment version number ($v_2 = v_1 + 1$), register new row in `document_versions` linked to the same `document_id`.
3. **Case C: Brand New Document**:
   * Action: Insert into `documents` and insert `version_number = 1` into `document_versions`.

```python
def check_existing_hash(cursor, sha256_hash: str):
    cursor.execute("""
        SELECT version_id, document_id, status 
        FROM document_versions 
        WHERE sha256_hash = ?
    """, (sha256_hash,))
    return cursor.fetchone()

def get_latest_version_number(cursor, document_id: str) -> int:
    cursor.execute("""
        SELECT MAX(version_number) FROM document_versions WHERE document_id = ?
    """, (document_id,))
    res = cursor.fetchone()[0]
    return res if res is not None else 0
```

---

### Step 6: Status Lifecycle State Machine & Error Handling

Each document version transitions through distinct states:

$$\text{pending} \longrightarrow \text{processing} \longrightarrow \begin{cases} \text{success} & \text{(parsing & chunking succeeded)} \\ \text{failed} & \text{(validation error, corrupt file, or parser crash)} \end{cases}$$

* When starting: Record `status = 'pending'`.
* During parser processing: Update `status = 'processing'`.
* On completion: Update `status = 'success'`.
* On failure: Update `status = 'failed'`, recording the exact traceback/message in `error_message`.

---

## 4. Complete Production Implementation: `src/pipeline/ingestion.py`

Long can copy and save this complete, production-grade module to [`src/pipeline/ingestion.py`](file:///d:/computational_thinking/Computational-Thinking-Project/src/pipeline/ingestion.py):

```python
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
```

---

## 5. Automated Unit Test Suite (`tests/test_ingestion.py`)

To satisfy **Rubric R2 (Deterministic Capability Evidence)**, Long must implement unit tests covering:
1. Valid file ingestion & metadata extraction.
2. Duplicate file detection (same SHA-256 skips duplicate insert).
3. Version increment (new file with same `document_id` creates version 2).
4. Validation rejection of empty (0-byte) files.
5. Status state transitions (`pending` $\rightarrow$ `processing` $\rightarrow$ `success`).

```python
"""
tests/test_ingestion.py
Unit tests for Stage 1 Data Ingestion Pipeline.
"""

import sys
import os
from pathlib import Path

# Add project root to sys.path so tests can resolve `src`
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
import sqlite3
from src.pipeline.ingestion import DataIngestionPipeline

@pytest.fixture
def test_pipeline(tmp_path):
    db_file = tmp_path / "test_workspace.db"
    pipeline = DataIngestionPipeline(str(db_file))
    return pipeline

@pytest.fixture
def sample_files(tmp_path):
    data_dir = tmp_path / "raw"
    data_dir.mkdir()
    
    # 1. Normal valid markdown file
    valid_md = data_dir / "valid_cheat_sheet.md"
    valid_md.write_text("# SQL Injection\nPrevent SQLi with prepared statements.", encoding="utf-8")
    
    # 2. Normal valid json file
    valid_json = data_dir / "taxonomy.json"
    valid_json.write_text('{"category": "A01", "name": "Access Control"}', encoding="utf-8")
    
    # 3. Empty file (0 bytes)
    empty_file = data_dir / "empty.txt"
    empty_file.write_text("", encoding="utf-8")
    
    return data_dir

def test_file_discovery(test_pipeline, sample_files):
    files = test_pipeline.discover_files(sample_files)
    file_names = [f.name for f in files]
    assert "valid_cheat_sheet.md" in file_names
    assert "taxonomy.json" in file_names
    assert "empty.txt" in file_names

def test_valid_ingestion(test_pipeline, sample_files):
    md_file = sample_files / "valid_cheat_sheet.md"
    result = test_pipeline.ingest_file(md_file, domain_category="remediation")
    
    assert result.status == "pending"
    assert result.is_duplicate is False
    assert len(result.sha256_hash) == 64

    # Verify SQLite row
    with sqlite3.connect(test_pipeline.db_path) as conn:
        conn.row_factory = sqlite3.Row
        doc = conn.execute("SELECT * FROM documents WHERE document_id = ?", (result.document_id,)).fetchone()
        assert doc is not None
        assert doc["domain_category"] == "remediation"

def test_duplicate_detection(test_pipeline, sample_files):
    md_file = sample_files / "valid_cheat_sheet.md"
    res1 = test_pipeline.ingest_file(md_file)
    test_pipeline.update_status(res1.version_id, "success")
    
    # Ingest the exact same file again
    res2 = test_pipeline.ingest_file(md_file)
    assert res2.is_duplicate is True
    assert res2.version_id == res1.version_id

def test_empty_file_rejection(test_pipeline, sample_files):
    empty_file = sample_files / "empty.txt"
    result = test_pipeline.ingest_file(empty_file)
    
    assert result.status == "failed"
    assert "0 bytes" in result.error_message

def test_status_lifecycle_updates(test_pipeline, sample_files):
    md_file = sample_files / "valid_cheat_sheet.md"
    result = test_pipeline.ingest_file(md_file)
    
    test_pipeline.update_status(result.version_id, "processing")
    with sqlite3.connect(test_pipeline.db_path) as conn:
        status = conn.execute("SELECT status FROM document_versions WHERE version_id = ?", (result.version_id,)).fetchone()[0]
        assert status == "processing"

    test_pipeline.update_status(result.version_id, "success")
    with sqlite3.connect(test_pipeline.db_path) as conn:
        status = conn.execute("SELECT status FROM document_versions WHERE version_id = ?", (result.version_id,)).fetchone()[0]
        assert status == "success"
```

---

## 6. Execution & Verification Checklist for Long

- [ ] **Task 1: Verify Schema Setup**:
  Run schema migration and verify that `documents` and `document_versions` exist:
  ```powershell
  python -c "import sqlite3; conn = sqlite3.connect('long_nguyen/data/database/workspace.db'); print(conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall())"
  ```
- [ ] **Task 2: Implement `src/pipeline/ingestion.py`**:
  Save the production pipeline class into `src/pipeline/ingestion.py`.
- [ ] **Task 3: Run Ingestion against Staged Markdown Files**:
  Ingest all 8 staged cheat sheets in `long_nguyen/data/raw/remediation/staged/`:
  ```python
  from src.pipeline.ingestion import DataIngestionPipeline
  pipeline = DataIngestionPipeline("long_nguyen/data/database/workspace.db")
  results = pipeline.ingest_directory("long_nguyen/data/raw/remediation/staged", domain_category="remediation")
  print(f"Ingested {len(results)} files.")
  ```
- [ ] **Task 4: Execute SQL Audit Queries**:
  ```sql
  -- Check document count & versions
  SELECT d.document_id, d.name, v.version_number, v.file_name, v.sha256_hash, v.status
  FROM documents d
  JOIN document_versions v ON d.document_id = v.document_id;
  ```
- [ ] **Task 5: Run Automated Pytest Suite**:
  ```powershell
  # If pytest is directly on PATH:
  pytest tests/test_ingestion.py -v

  # Or using the Windows Python launcher:
  py -m pytest tests/test_ingestion.py -v
  ```
