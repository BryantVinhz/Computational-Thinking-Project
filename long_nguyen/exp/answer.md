# Question: Where is the Raw Dataset Presented (e.g., like a large dataset with 10,000 – 100,000 lines)?

---

## 1. Clarification: Why `remediation_manifest.json` is NOT 10,000+ Lines

In traditional machine learning or NLP tasks, datasets are often formatted as a single massive JSON/CSV file containing 10,000 to 100,000+ rows (e.g. prompt-completion pairs or CSV rows).

However, in this project (**Track C: Intelligent Document Workspace**), data is handled differently:
* [`remediation_manifest.json`](file:///d:/computational_thinking/Computational-Thinking-Project/long_nguyen/data/raw/remediation_manifest.json) is **NOT** a raw text dataset dump.
* It is strictly an **Ingestion Registry / Manifest (Task A.2)**. Its sole purpose is cataloging document-level metadata (OWASP 2025 category, CWE IDs, target languages, source URLs, and file names) across 8 target cheat sheets (~80 lines).
* Storing raw content directly inside a JSON manifest is intentionally avoided in computational thinking to prevent memory bloat, schema violation, and loss of source line provenance.

---

## 2. Where the Actual Large-Scale Raw Text Resides (Tens of Thousands of Lines)

The actual raw text content is stored in **unstructured Markdown documents** located under `data/raw/`:

### A. The Upstream Raw Dataset (26,700+ Lines)
* **Path**:
  ```text
  long_nguyen/data/raw/remediation/CheatSheetSeries/cheatsheets/
  ```
* **Scale**:
  - **137 markdown files**
  - **26,706 lines** of raw text, code blocks, tables, and defense guidelines.
* **Stage**: Available at the very beginning of **Phase A (Data Acquisition)** as the cloned upstream repository.

### B. The Staged Subset (Phase A, Task A.1)
* **Path**:
  ```text
  long_nguyen/data/raw/remediation/staged/
  ```
* **Scale**: 8 core cheat sheets directly covering OWASP Top 10 2025, normalized to Unix LF (`\n`) and UTF-8 without BOM (~1,200 lines).

---

## 3. At Which Stage Does the Raw Data Become a Massive Structured Dataset?

If you are looking for where the raw text is transformed into a **large-scale structured collection of records** (similar to a dataset with thousands or tens of thousands of items):

### Phase B / Stage 2: Document Parsing & Structural Extraction
* **Lead**: Nguyên
* **What happens**: The parser (`RemediationParser` in `src/pipeline/parsers/remediation_parser.py`) iterates line-by-line over the raw markdown files (`lines = markdown_text.splitlines()`).
* **Output**: Extracts every individual heading breadcrumb, atomic code block, and actionable directive into in-memory structured dataclass objects.

### Phase C / Stage 4 & 5: Chunking & Canonical Relational Storage
* **Leads**: Long, Nguyên, Diệp, Kiệt
* **Destination**: [`long_nguyen/data/database/workspace.db`](file:///d:/computational_thinking/Computational-Thinking-Project/long_nguyen/data/database/workspace.db)
* **How raw data is represented as structured records**:
  1. **`chunks` table**: Every section of the raw markdown is broken down into structured chunk records containing verbatim text (`content`), token counts, char counts, and exact line locators (`line_start`, `line_end`).
  2. **`remediation_snippets` table**: Every code block is extracted as an atomic record containing verbatim `code_content`, programming language, classification (`secure_solution` vs `vulnerable_antipattern`), defense tier, and line boundaries.
* Across the entire course dataset (OWASP Cheat Sheets, MITRE CWE taxonomy, WSTG v4.2, ASVS 4.0.3, and 20–25 pentest reports), this SQLite database serves as the unified canonical dataset holding tens of thousands of structured chunk and snippet rows.

---

## 4. Summary Comparison Matrix

| Stage / Component | File / Artifact Path | Purpose | Scale | Contains Verbatim Raw Text? |
| :--- | :--- | :--- | :--- | :--- |
| **Phase A (Acquisition)** | `long_nguyen/data/raw/remediation/CheatSheetSeries/cheatsheets/` | Raw upstream source repository | **137 files, 26,706 lines** | **Yes** (full raw markdown documents) |
| **Phase A (Task A.1)** | `long_nguyen/data/raw/remediation/staged/` | Normalized target core files | **8 files, ~1,200 lines** | **Yes** (clean UTF-8 markdown) |
| **Phase A (Task A.2)** | `long_nguyen/data/raw/remediation_manifest.json` | Document catalog & metadata mapping | **8 JSON objects (~80 lines)** | **No** (metadata & file pointers only) |
| **Phase A (Task A.3)** | SQLite: `documents`, `document_versions` | File registration & SHA-256 provenance tracking | 1 record per document/version | **No** (hash, size, status, file path) |
| **Phase B & C (Parsing & Storage)** | SQLite: `chunks` & `remediation_snippets` in `workspace.db` | Structured database of all text chunks & code snippets | **Hundreds to tens of thousands of records** | **Yes** (verbatim text chunks and code lines with line-range provenance) |
