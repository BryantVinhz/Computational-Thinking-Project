# Intelligent Document Workspace (Topic C) — Data Pipeline Engineering Guide
> **Course**: CSC10014 — Computational Thinking (FIT - HCMUS, 2026)  
> **Topic Track**: Track C — Intelligent Document Workspace  
> **Application Domain**: Cybersecurity, Penetration Testing & Web Application Security (OWASP Top 10 2025, CWE, WSTG, ASVS, Pentest Reports)  
> **Milestone**: Phase 1 — Document Ingestion, Parsing, Normalization, Chunking & Canonical Storage  

---

## 1. Executive Summary & Design Philosophy

The objective of this project is **not** to build a superficial LLM wrapper, but to engineer a robust, testable, and traceable computational pipeline:
$$\text{Understand} \longrightarrow \text{Decompose} \longrightarrow \text{Represent} \longrightarrow \text{Decide} \longrightarrow \text{Execute} \longrightarrow \text{Verify} \longrightarrow \text{Improve}$$

### Core Computational Thinking Principles (Academic Syllabus Alignment)
1. **AI is a Component, Not the Architecture**: Deterministic logic handles file hashing, format detection, text parsing, payload preservation, and metadata regex. LLMs are reserved for semantic reasoning, question answering, and synthesis.
2. **End-to-End Provenance & Traceability**: Every retrieved answer must pinpoint its exact origin: `Document -> Version -> Page / Line / Bounding Box -> Chunk`.
3. **Rigorous Evaluation & Improvement (R8)**: The pipeline includes an explicit baseline vs. improved method (e.g., *Fixed-size Chunking* vs. *Structure-aware / Header-hierarchy Chunking*) to document measurable improvements in retrieval precision.

---

## 2. Pipeline Architecture Overview

The pipeline transforms raw heterogeneous documents into a unified, high-integrity **Canonical Document Store** backed by SQLite.

```
+----------------------------------------------------------------------------------------------------+
|                                      RAW DATASETS & DOCUMENTS                                      |
|    - OWASP Top 10 2025 & MITRE CWE (Taxonomy)            - OWASP Cheat Sheets (Remediation)        |
|    - OWASP WSTG v4.2 / ASVS 4.0.3 (Testing/Standards)    - Sample Pentest Reports (20-25 files)    |
|    Formats: PDF, Markdown (.md), TXT, CSV, JSON                                                    |
+----------------------------------------------------------------------------------------------------+
                                                  |
                                                  v
+----------------------------------------------------------------------------------------------------+
| 1. DATA INGESTION & VALIDATION [Owner: Long]                                                       |
|    - File discovery & format validation                                                            |
|    - SHA-256 deduplication & document ID generation                                               |
|    - Status tracking: pending -> processing -> success / failed                                    |
+----------------------------------------------------------------------------------------------------+
                                                  |
                                                  v
+----------------------------------------------------------------------------------------------------+
| 2. DOCUMENT PARSING & STRUCTURAL EXTRACTION [Owners: Nguyên, Bảo]                                   |
|    - PDF Parser: text extraction, page tracking, bounding boxes (bbox), tables                      |
|    - Markdown Parser: heading hierarchy, section tree, code blocks, lists                          |
|    - Structured Parsers: CSV tables, JSON records                                                  |
|    - Output: In-memory ContentBlock objects                                                        |
+----------------------------------------------------------------------------------------------------+
                                                  |
                                                  v
+----------------------------------------------------------------------------------------------------+
| 3. TEXT CLEANING & NORMALIZATION [Owner: Trí]                                                      |
|    - Whitespace & newline canonicalization, extraction artifact cleanup                            |
|    - CRITICAL RULE: Preserve security payloads (SQLi, XSS strings, scripts, regex, code intact)   |
|    - Delta tracking: char_count_delta, is_code_or_payload flag (no redundant duplicate storage!)   |
+----------------------------------------------------------------------------------------------------+
                                                  |
                                                  v
+----------------------------------------------------------------------------------------------------+
| 4. DOCUMENT CHUNKING ENGINE [Owner: Kiệt]                                                          |
|    - Baseline Strategy: Fixed-size token/character chunking with sliding overlap                   |
|    - Improved Strategy: Structure-aware chunking (respecting heading breadcrumbs, tables, code)    |
|    - Computes token counts & maps back to source locators                                          |
+----------------------------------------------------------------------------------------------------+
                                                  |
                                                  v
+----------------------------------------------------------------------------------------------------+
| 5. METADATA EXTRACTION & CANONICAL STORAGE [Owners: Diệp, Kiệt]                                    |
|    - Deterministic entity extraction: OWASP tags, CWE IDs, WSTG IDs, ASVS levels, CVSS scores       |
|    - Store directly in SQLite (Structured Relational Storage - No scattered JSONs)                |
|    - Full Provenance mapping: document_id, version_id, chunk_id, page_range, line_range, bbox      |
+----------------------------------------------------------------------------------------------------+
```

---

## 3. Detailed Stage-by-Stage Specifications

### Stage 1: Data Ingestion & Validation
* **Assignee**: **Long**
* **Deadline**: Tuesday 20/10

#### Objectives
1. **File Discovery**: Recursively discover files from a designated directory (`data/raw/`).
2. **Format Detection**: Detect true file types via MIME type + extension (`.pdf`, `.md`, `.txt`, `.csv`, `.json`).
3. **Input Validation**: Check that file exists, is non-empty (> 0 bytes), readable, and does not exceed maximum allowable size (e.g., 50MB).
4. **Unique Identification**: Generate a persistent `document_id` (`doc_<uuid4_prefix>`) and compute `sha256_hash`.
5. **Duplicate Detection**: If a file with identical `sha256_hash` already exists in `document_versions`, skip reprocessing or create a new version if updated.
6. **Status Lifecycle**: Record and update state: `pending` $\rightarrow$ `processing` $\rightarrow$ `success` | `failed`. Log specific error reasons upon failure.

---

### Stage 2: Document Parsing & Structural Extraction
* **Assignees**: **Nguyên**, **Bảo**

#### Objectives
Implement specialized parsers to extract structured content while preserving positional metadata (source locators):
1. **PDF Parser (`PdfParser`)**:
   - Recommended library: `PyMuPDF` (`fitz`) or `pdfplumber`.
   - Extract page-by-page text, paragraph blocks, and coordinate bounding boxes `[x0, y0, x1, y1]`.
   - Extract tables with structured column headers and row cells.
2. **Markdown Parser (`MarkdownParser`)**:
   - Recommended library: `mistune` or `markdown-it-py`.
   - Parse hierarchical heading paths (e.g., `A01:2025 > Overview > Remediation`).
   - Extract code blocks with associated language tags (`sql`, `python`, `http`, `bash`).
   - Capture line ranges (`start_line`, `end_line`).
3. **Structured Parser (`StructuredParser`)**:
   - Parse CSV/JSON files into structured schema blocks with table metadata and row index ranges.

---

### Stage 3: Text Cleaning & Normalization
* **Assignee**: **Trí**

#### Objectives & Solutions to Team Questions
> **Resolution to Trí's Note**:  
> *Should we duplicate full tables between Step 2 and Step 3 if text is unchanged?*  
> **Answer: NO.** Storing duplicate text copies wastes memory and database size. Instead:
> - Keep `raw_text` in the extraction stage.
> - Apply normalization in-memory.
> - Store `normalized_content` in the chunk, along with flags:
>   - `has_modified: bool` (true only if normalized text differs from raw text)
>   - `char_count_delta: int` (`len(raw) - len(normalized)`)
>   - `is_code_or_payload: bool` (flag for code/security payloads)

#### Security Domain Specific Rules
1. **Payload & Code Protection**: **DO NOT** sanitize quotes, semicolons, brackets, or angle brackets inside code blocks, POCs, or exploit strings (e.g. `' OR 1=1 --`, `<script>alert(1)</script>`, `union select null`). Normalization must never break the syntactic validity of security payloads.
2. **Whitespace Normalization**: Normalize consecutive non-newline spaces into a single space; remove trailing whitespaces; canonicalize multiple blank lines into at most two newlines (`\n\n`).
3. **Encoding Fixes**: Fix Unicode mojibake (e.g. `ftfy` or utf-8 decoding cleanup) while preserving diacritics and technical symbols.

---

### Stage 4: Document Chunking Engine
* **Assignee**: **Kiệt**

#### Objectives
Break long documents into discrete, retrievable semantic units while maintaining source provenance. To satisfy **Requirement R8 (Evaluation & Improvement)**, we implement two distinct strategies:

1. **Baseline Strategy: Fixed-Size Chunking (`fixed_size`)**
   - Chunk size: ~500 tokens / 1500 characters.
   - Overlap: ~50 tokens / 150 characters.
   - Fast, uniform, but may split sentences, code snippets, or table rows mid-way.

2. **Improved Strategy: Structure-Aware Chunking (`structure_aware`)**
   - Respects markdown headers (`#`, `##`, `###`), page boundaries, and paragraphs.
   - Keeps code blocks, exploit payloads, and table rows intact within a single chunk whenever possible.
   - Injects the **Heading Path Breadcrumb** (e.g. `[Context: OWASP Top 10 > A01:2025 Broken Access Control > Prevention]`) at the top of each chunk content to enrich embedding and LLM comprehension.
   - Retains exact `source_locator` mappings (`page_range`, `bbox_list`, `line_range`, `char_offsets`).

---

### Stage 5: Security Entity & Metadata Extraction
* **Assignee**: **Diệp**

#### Objectives
Extract domain-specific metadata via fast, deterministic regex rules and heuristic classifiers, saving directly to SQLite:

1. **Deterministic Security Entities**:
   - `owasp_category`: Match `A0[1-9]:2025`, `A10:2025`, `A0[1-9]:2021`
   - `cwe_ids`: Match `CWE-\d{1,5}` (e.g., `CWE-89`, `CWE-79`)
   - `wstg_ids`: Match `WSTG-[A-Z]+-\d{2}` (e.g., `WSTG-INPV-05`)
   - `asvs_levels`: Match `ASVS (Level [1-3]|L[1-3]|v[0-9.]+)`
   - `cvss_score`: Match `CVSS:3\.[01]/AV:.*` or float scores between `0.0` and `10.0`
   - `severity`: Standardize into `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, `INFO`

2. **Content Flags**:
   - `has_code`: True if chunk contains code blocks, scripts, or terminal commands.
   - `has_table`: True if chunk was derived from a structured table.
   - `has_poc`: True if chunk contains exploit payloads or proof-of-concept steps.

---

### Stage 6: Provenance & Traceability Engine
* **Assignee**: **Kiệt**

#### Objectives
Ensure that every chunk stored in the database has an unambiguous trace back to its origin:
- Document level: File name, file path, SHA-256 hash.
- Page level: `page_number` or `page_range: [start_page, end_page]`.
- Coordinate level: Bounding boxes `[x0, y0, x1, y1]` for PDF visual highlighting.
- Text level: `line_range: [start_line, end_line]` and `char_offsets: [start_char, end_char]`.

---

## 4. Canonical SQLite Database Schema

Per Diệp's update, all intermediate and final outputs are stored in a relational **SQLite** database (`workspace.db`) rather than scattered JSON files. This ensures ACID transactions, fast indexing, structured joins, and verifiable evaluation.

```sql
-- ============================================================================
-- 1. DOCUMENTS TABLE (Master Record)
-- ============================================================================
CREATE TABLE IF NOT EXISTS documents (
    document_id         TEXT PRIMARY KEY,           -- e.g. 'doc_owasp_a01'
    title               TEXT NOT NULL,
    domain_category     TEXT NOT NULL,              -- 'taxonomy', 'remediation', 'testing', 'pentest_report'
    original_file_name  TEXT NOT NULL,
    source_url          TEXT,                       -- Provenance URL if downloaded
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 2. DOCUMENT VERSIONS TABLE (Tracking Files & Processing State)
-- ============================================================================
CREATE TABLE IF NOT EXISTS document_versions (
    version_id          TEXT PRIMARY KEY,           -- e.g. 'ver_doc_owasp_a01_v1'
    document_id         TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    version_number      INTEGER NOT NULL DEFAULT 1,
    file_path           TEXT NOT NULL,
    file_format         TEXT NOT NULL,              -- 'pdf', 'md', 'txt', 'csv', 'json'
    file_size_bytes     INTEGER NOT NULL,
    sha256_hash         TEXT NOT NULL,
    total_pages         INTEGER DEFAULT NULL,
    status              TEXT NOT NULL CHECK(status IN ('pending', 'processing', 'success', 'failed')),
    error_message       TEXT DEFAULT NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    processed_at        DATETIME DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_versions_sha256 ON document_versions(sha256_hash);
CREATE INDEX IF NOT EXISTS idx_versions_status ON document_versions(status);

-- ============================================================================
-- 3. CHUNKS TABLE (Core Retrievable Units for Search & RAG)
-- ============================================================================
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id            TEXT PRIMARY KEY,           -- e.g. 'chk_a01_0001'
    version_id          TEXT NOT NULL REFERENCES document_versions(version_id) ON DELETE CASCADE,
    chunk_idx           INTEGER NOT NULL,           -- Sequential index inside the document
    chunk_strategy      TEXT NOT NULL CHECK(chunk_strategy IN ('fixed_size', 'structure_aware')),
    content             TEXT NOT NULL,              -- Normalized, searchable chunk content
    raw_content         TEXT,                       -- Preserved original text if modified
    token_count         INTEGER NOT NULL,
    char_count          INTEGER NOT NULL,
    
    -- Structural Context
    heading_path        TEXT,                       -- Breadcrumb: 'A01:2025 > Mitigation'
    heading_level       INTEGER,                    -- 1, 2, 3...
    
    -- Physical Provenance Locators
    page_start          INTEGER,                    -- Physical page start (1-indexed)
    page_end            INTEGER,                    -- Physical page end
    line_start          INTEGER,                    -- Line start (for MD/TXT)
    line_end            INTEGER,                    -- Line end
    bbox_json           TEXT,                       -- JSON string: '[[x0,y0,x1,y1], ...]'
    
    -- Flags
    has_code            BOOLEAN DEFAULT 0,
    has_table           BOOLEAN DEFAULT 0,
    has_poc             BOOLEAN DEFAULT 0,
    is_modified         BOOLEAN DEFAULT 0,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chunks_version_strategy ON chunks(version_id, chunk_strategy);
CREATE INDEX IF NOT EXISTS idx_chunks_strategy ON chunks(chunk_strategy);

-- ============================================================================
-- 4. SECURITY ENTITY MENTIONS TABLE (Extracted Security Metadata)
-- ============================================================================
CREATE TABLE IF NOT EXISTS security_entities (
    entity_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id            TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    entity_type         TEXT NOT NULL,              -- 'OWASP', 'CWE', 'WSTG', 'ASVS', 'CVSS', 'SEVERITY'
    entity_value        TEXT NOT NULL,              -- e.g. 'A01:2025', 'CWE-89', 'CRITICAL'
    confidence          REAL DEFAULT 1.0,           -- 1.0 for regex matches
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_entities_type_val ON security_entities(entity_type, entity_value);
CREATE INDEX IF NOT EXISTS idx_entities_chunk ON security_entities(chunk_id);

-- ============================================================================
-- 5. PIPELINE AUDIT LOGS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS pipeline_logs (
    log_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id          TEXT REFERENCES document_versions(version_id),
    stage               TEXT NOT NULL,              -- 'ingestion', 'parsing', 'normalization', 'chunking'
    level               TEXT NOT NULL,              -- 'INFO', 'WARNING', 'ERROR'
    message             TEXT NOT NULL,
    timestamp           DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## 5. Team Responsibilities & Work Breakdown

| Member | Primary Pipeline Role | Key Deliverables | Expected Test / Evidence |
| :--- | :--- | :--- | :--- |
| **Long** | Data Ingestion & Validation | - `src/pipeline/ingestion.py`<br>- File scanner, validator, SHA-256 deduplication<br>- Document/Version table initialization | `tests/test_ingestion.py`<br>- Handles corrupt files<br>- Detects duplicate files<br>- Validates MIME types |
| **Nguyên** | Parsing (PDF & Taxonomies) | - `src/pipeline/parsers/pdf_parser.py`<br>- OWASP & CWE parser<br>- Extracts headings, tables, bounding boxes | `tests/test_pdf_parser.py`<br>- Verifies bbox coordinates<br>- Verifies table extraction |
| **Bảo** | Parsing (Markdown & Testing Guides) | - `src/pipeline/parsers/markdown_parser.py`<br>- WSTG & Cheat Sheet parser<br>- Preserves code blocks & header trees | `tests/test_md_parser.py`<br>- Parses nested `#`, `##` headers<br>- Preserves code fences ```` ``` ```` |
| **Trí** | Text Cleaning & Normalization | - `src/pipeline/normalization.py`<br>- Safe whitespace/newline canonicalizer<br>- **Payload Preservation Filter** (ensures exploit strings remain untouched) | `tests/test_normalization.py`<br>- Tests SQLi & XSS strings are NOT altered<br>- Tests excessive spacing is cleaned |
| **Kiệt** | Chunking Engine & Provenance | - `src/pipeline/chunking.py`<br>- `fixed_size` chunker (baseline)<br>- `structure_aware` chunker (improved)<br>- Provenance locator resolver | `tests/test_chunking.py`<br>- Verifies chunk overlap<br>- Verifies heading breadcrumbs attached<br>- Compares chunk statistics |
| **Diệp** | Metadata Extraction & SQLite Storage | - `src/pipeline/metadata_extractor.py`<br>- `src/pipeline/database.py`<br>- Regex entity extractors (CWE, OWASP, WSTG)<br>- SQLite CRUD & foreign key management | `tests/test_metadata.py`<br>- Verifies regex extraction on real OWASP/CWE samples<br>- Verifies SQLite schema integrity |

---

## 6. Directory Structure Recommendation

```
Computational-Thinking-Project/
├── 2026_Project_Virtual_Assistant_Academic.pdf
├── Note-Computational-Thinking.pdf
├── guide.md                           # This pipeline engineering guide
├── README.md
├── requirements.txt                   # Project dependencies (pymupdf, mistune, pydantic, etc.)
│
├── data/
│   ├── raw/                           # Raw downloaded files
│   │   ├── taxonomy/                  # OWASP Top 10, MITRE CWE
│   │   ├── remediation/               # OWASP Cheat Sheets
│   │   ├── testing/                   # OWASP WSTG, ASVS
│   │   └── pentest_reports/           # 20-25 sample reports
│   └── database/
│       └── workspace.db               # SQLite Canonical Database
│
├── src/
│   ├── __init__.py
│   ├── config.py                      # Paths, thresholds, chunk parameters
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── database.py                # SQLite schema setup & queries
│   │   ├── ingestion.py               # Discovery, validation, SHA-256
│   │   ├── parsers/
│   │   │   ├── __init__.py
│   │   │   ├── base_parser.py         # Abstract Base Parser interface
│   │   │   ├── pdf_parser.py          # PyMuPDF implementation
│   │   │   ├── markdown_parser.py     # Mistune / MD parser
│   │   │   └── structured_parser.py   # CSV/JSON parser
│   │   ├── normalization.py           # Text cleaning with payload preservation
│   │   ├── chunking.py                # Fixed-size & Structure-aware chunking
│   │   └── metadata_extractor.py      # Regex entity extractors (CWE, OWASP, CVSS)
│   │
│   └── run_pipeline.py                # CLI runner / Orchestrator for pipeline
│
└── tests/
    ├── __init__.py
    ├── test_ingestion.py
    ├── test_parsers.py
    ├── test_normalization.py
    ├── test_chunking.py
    └── test_metadata.py
```

---

## 7. Immediate Next Steps for the Team

1. **Environment Setup**:
   Create a virtual environment and install core parsing/database libraries:
   ```bash
   pip install pymupdf markdown-it-py pydantic pytest
   ```
2. **Execute Database & Ingestion (Long & Diệp)**:
   - Implement `database.py` with the approved SQLite schema.
   - Implement `ingestion.py` to scan `data/raw/` and register document versions.
3. **Implement Parsers (Nguyên & Bảo)**:
   - Implement `pdf_parser.py` and `markdown_parser.py` returning standardized `ContentBlock` objects.
4. **Implement Cleaning & Chunking (Trí & Kiệt)**:
   - Ensure exploit payloads are preserved during cleaning.
   - Implement both `fixed_size` and `structure_aware` chunking methods.
5. **Extract Metadata & Insert into SQLite (Diệp & Kiệt)**:
   - Run entity extractors and commit all chunks to `workspace.db`.
6. **Benchmark & Verify (Evaluation Track)**:
   - Run unit tests and generate the initial pipeline statistics (total files processed, chunk count distribution, entity extraction accuracy).
