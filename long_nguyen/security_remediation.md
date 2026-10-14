# Pipeline Module Deep Dive: Security Remediation & Mitigation Knowledge Base
> **Course**: CSC10014 — Computational Thinking (FIT - HCMUS, 2026)  
> **Topic Track**: Track C — Intelligent Document Workspace  
> **Module Focus**: Security Remediation & Vulnerability Mitigation (`security_remediation`)  
> **Module Assignees**: **Long**, **Nguyên**  
> **Primary Data Source**: [OWASP Cheat Sheet Series](https://cheatsheetseries.owasp.org/)  
> **Key Objective**: Empower the assistant to accurately, deterministically, and safely answer:  
> *"How do I remediate, mitigate, or patch vulnerability X in technology stack Y?"*

---

## 1. Module Framing & Computational Thinking Perspective

In security operations and software development, discovering a vulnerability without providing an actionable, verified patch creates frustration. However, naive automated remediation is hazardous:
- LLMs frequently hallucinate outdated APIs, non-existent configuration flags, or insecure code (e.g. recommending `MD5` or flawed regex for sanitization).
- General text retrieval often splits code examples in half or confuses **vulnerable code examples** with **secure remediation patterns**.

### Computational Thinking Breakdown

```
+---------------------------------------------------------------------------------------------------------+
| UNDERSTAND                                                                                              |
| User asks: "How do I fix Broken Access Control (A01) in Node.js Express?"                               |
+---------------------------------------------------------------------------------------------------------+
                                                     |
                                                     v
+---------------------------------------------------------------------------------------------------------+
| DECOMPOSE                                                                                               |
| 1. Identify Vulnerability: A01:2025 / CWE-285 (Access Control)                                          |
| 2. Identify Target Tech Stack: Node.js / Express                                                        |
| 3. Retrieve Remediation Strategies: Principle of least privilege, RBAC middleware, direct object checks |
| 4. Extract Concrete Code Pattern: Secure middleware implementation                                      |
+---------------------------------------------------------------------------------------------------------+
                                                     |
                                                     v
+---------------------------------------------------------------------------------------------------------+
| REPRESENT                                                                                               |
| Relational schema linking: Vulnerability Category <-> Remediation Guide <-> Defense Tier <-> Code Block  |
+---------------------------------------------------------------------------------------------------------+
                                                     |
                                                     v
+---------------------------------------------------------------------------------------------------------+
| DECIDE (ROUTING)                                                                                        |
| If tech stack is specific -> Filter chunks by [programming_language = 'javascript']                     |
| If request is conceptual  -> Retrieve top-level defense principles                                      |
+---------------------------------------------------------------------------------------------------------+
                                                     |
                                                     v
+---------------------------------------------------------------------------------------------------------+
| EXECUTE & VERIFY                                                                                        |
| Synthesize verified OWASP snippet + strict code syntax check + cite exact Cheat Sheet file & section    |
+---------------------------------------------------------------------------------------------------------+
```

---

## 2. Target Dataset: OWASP Cheat Sheet Series

The **OWASP Cheat Sheet Series (OCSS)** is the industry-standard repository of concise, high-value defensive security guidance. For Track C, the ingestion pipeline will focus on the core vulnerability categories mapped to OWASP Top 10 2025:

| Core Target Vulnerability | OWASP 2025 Category | Target OWASP Cheat Sheet Files | Key Tech Stacks |
| :--- | :--- | :--- | :--- |
| **SQL Injection (SQLi)** | `A05:2025 - Injection` | `SQL_Injection_Prevention_Cheat_Sheet.md`<br>`Query_Parameterization_Cheat_Sheet.md` | Java, Python, PHP, .NET, Node.js |
| **Cross-Site Scripting (XSS)** | `A05:2025 - Injection` | `Cross_Site_Scripting_Prevention_Cheat_Sheet.md`<br>`DOM_based_XSS_Prevention_Cheat_Sheet.md` | JavaScript, HTML, React, Angular |
| **Broken Access Control** | `A01:2025 - Broken Access Control` | `Access_Control_Cheat_Sheet.md`<br>`Authorization_Cheat_Sheet.md`<br>`Insecure_Direct_Object_Reference_Prevention_Cheat_Sheet.md` | Node.js, Spring Boot, Django |
| **Broken Authentication** | `A07:2025 - Authentication Failures` | `Authentication_Cheat_Sheet.md`<br>`Password_Storage_Cheat_Sheet.md`<br>`Session_Management_Cheat_Sheet.md`<br>`JSON_Web_Token_for_Java_Cheat_Sheet.md` | General, Python (bcrypt/argon2), JWT |
| **Security Misconfiguration** | `A02:2025 - Security Misconfiguration` | `Security_Headers_Cheat_Sheet.md`<br>`Docker_Security_Cheat_Sheet.md`<br>`REST_Security_Cheat_Sheet.md` | Nginx, Apache, Express, Docker |

---

## 3. Structural Anatomy of Remediation Documents & Challenges

Unlike generic prose, OWASP Cheat Sheets possess a distinct structural anatomy that standard text processors break:

```
# [Document Title: e.g. SQL Injection Prevention Cheat Sheet]
  ## Introduction & Threat Model
  ## Primary Defenses
    ### Defense Option 1: Prepared Statements (Parameterized Queries)
      - Explanation
      - Java Implementation (Code Snippet: Secure)
      - Python Implementation (Code Snippet: Secure)
    ### Defense Option 2: Stored Procedures
    ### Defense Option 3: Allow-list Input Validation
  ## Additional Defenses
  ## What NOT to do (Vulnerable Examples / Pitfalls)
    - Vulnerable String Concatenation (Code Snippet: Insecure!)
```

### Critical Ingestion Pitfalls & Solutions

| Challenge | Naive Pipeline Behavior | Our Engineered Solution |
| :--- | :--- | :--- |
| **1. Insecure Code Confusion** | The chunker grabs a vulnerable code example and the assistant serves it as the fix. | **Snippet Classification**: Every code block is tagged with `code_role`: `'secure_solution'`, `'vulnerable_antipattern'`, or `'neutral_example'` based on section headers. |
| **2. Broken Code Snippets** | Fixed character chunking splits a 40-line code example across 2 chunks, corrupting syntax. | **Atomic Code Block Chunking**: Fenced code blocks ```` ```...``` ```` are never split. If a code block exceeds chunk size, it forms its own dedicated chunk. |
| **3. Lost Language Context** | Retrieval returns a snippet of parameterized query, but doesn't state if it's Java or PHP. | **Context Breadcrumb Injection**: Every chunk prepends a header banner: `[CheatSheet: SQL Injection Prevention > Primary Defenses > Java Prepared Statements]`. |
| **4. Defense Tier Loss** | The assistant recommends defense option 3 (sanitization) instead of option 1 (parameterization). | **Defense Tier Hierarchy**: Tag chunks with metadata `defense_tier`: `'primary'`, `'secondary'`, `'mitigation'`, `'anti_pattern'`. |

---

## 4. Concrete Task Breakdown for Long & Nguyên

### Phase A: Data Acquisition & Ingestion (Owner: Long)

1. **Acquire Clean Markdown Sources**:
   - Clone or download the target markdown files from `https://github.com/OWASP/CheatSheetSeries/tree/master/cheatsheets`.
   - Store them in `data/raw/remediation/`.
2. **Metadata Manifest Creation**:
   - Create a JSON manifest (`data/raw/remediation_manifest.json`) registering each cheat sheet:
     ```json
     {
       "doc_id": "cs_sql_injection",
       "file_name": "SQL_Injection_Prevention_Cheat_Sheet.md",
       "title": "SQL Injection Prevention Cheat Sheet",
       "owasp_2025_category": "A05:2025",
       "cwe_ids": ["CWE-89"],
       "primary_topics": ["SQL Injection", "Query Parameterization", "Stored Procedures"],
       "source_url": "https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html"
     }
     ```
3. **Integrate with Ingestion Pipeline**:
   - Register all files in the SQLite `documents` and `document_versions` tables using `src/pipeline/ingestion.py`.

---

### Phase B: Remediation-Aware Markdown Parser (Owner: Nguyên)

Nguyên will build a dedicated parser class in `src/pipeline/parsers/remediation_parser.py` inheriting from `BaseParser`.

#### Core Parsing Logic:
1. **Heading Tree Extraction**:
   - Track heading levels (`#`, `##`, `###`, `####`).
   - Maintain a running `heading_breadcrumbs` list (e.g., `["SQL Injection Prevention", "Primary Defenses", "Defense Option 1: Prepared Statements", "Java"]`).
2. **Code Block & Context Extraction**:
   - Detect fenced code blocks (```` ```<lang> ... ``` ````).
   - Infer the programming language from fence tags (e.g. `python`, `java`, `csharp`, `php`, `javascript`, `sql`, `nginx`).
   - Inspect surrounding text and parent headings for polarity indicators:
     - **Vulnerable / Bad Indicators**: `incorrect`, `vulnerable`, `bad`, `do not`, `avoid`, `insecure`, `flawed`.
     - **Secure / Good Indicators**: `correct`, `secure`, `solution`, `remediated`, `preferred`, `compliant`, `good`.
3. **Rule / Recommendation List Parser**:
   - Parse bullet lists into atomic actionable guidelines (e.g., *"Rule 1: Always use prepared statements"*, *"Rule 2: Implement strict allow-list input validation"*).

---

### Phase C: Remediation Schema Design (Collaborative: Long, Nguyên, Diệp)

To allow the Virtual Assistant to look up remedies efficiently by language, vulnerability, or defense tier, we extend the SQLite database with a dedicated `remediation_snippets` table:

```sql
-- ============================================================================
-- REMEDIATION CODE SNIPPETS & PATTERNS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS remediation_snippets (
    snippet_id          TEXT PRIMARY KEY,              -- e.g. 'snip_sqli_java_prepared_01'
    chunk_id            TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    document_id         TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    
    -- Taxonomy & Categorization
    owasp_category      TEXT NOT NULL,                 -- 'A05:2025'
    cwe_id              TEXT,                          -- 'CWE-89'
    vulnerability_name  TEXT NOT NULL,                 -- 'SQL Injection'
    
    -- Technical Context
    programming_language TEXT NOT NULL,                -- 'java', 'python', 'javascript', 'php', 'sql'
    framework           TEXT DEFAULT NULL,             -- 'express', 'spring', 'django', 'hibernate'
    defense_tier        TEXT NOT NULL CHECK(defense_tier IN ('primary', 'secondary', 'defense_in_depth', 'anti_pattern')),
    
    -- Code Content & Classification
    code_role           TEXT NOT NULL CHECK(code_role IN ('secure_solution', 'vulnerable_antipattern', 'neutral_example')),
    code_content        TEXT NOT NULL,                 -- The verbatim code snippet
    explanation         TEXT,                          -- Accompanying explanation from the cheat sheet
    
    -- Provenance
    source_heading      TEXT NOT NULL,                 -- 'Defense Option 1 > Java Implementation'
    line_start          INTEGER NOT NULL,
    line_end            INTEGER NOT NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_remediation_lang_owasp 
    ON remediation_snippets(programming_language, owasp_category);

CREATE INDEX IF NOT EXISTS idx_remediation_cwe 
    ON remediation_snippets(cwe_id);
```

---

## 5. Proposed Python Implementation Reference

Here is the reference architecture and helper logic for Long and Nguyên to implement in `src/pipeline/parsers/remediation_parser.py`:

```python
"""
src/pipeline/parsers/remediation_parser.py
Specialized parser for OWASP Cheat Sheets and remediation guides.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

@dataclass
class RemediationCodeBlock:
    snippet_id: str
    language: str
    code_content: str
    code_role: str  # 'secure_solution' | 'vulnerable_antipattern' | 'neutral_example'
    defense_tier: str  # 'primary' | 'secondary' | 'anti_pattern'
    heading_path: str
    line_start: int
    line_end: int
    explanation: str = ""

@dataclass
class ParsedRemediationDoc:
    doc_id: str
    title: str
    owasp_category: str
    cwe_ids: List[str]
    sections: List[Dict[str, Any]] = field(default_factory=list)
    code_snippets: List[RemediationCodeBlock] = field(default_factory=list)

class RemediationParser:
    """Parses OWASP Markdown Cheat Sheets into structured remediation entities."""

    VULNERABLE_KEYWORDS = re.compile(r'\b(vulnerable|incorrect|bad|insecure|avoid|anti-pattern|flawed|exploit)\b', re.IGNORECASE)
    SECURE_KEYWORDS = re.compile(r'\b(correct|secure|solution|remediated|safe|defense|option|recommended)\b', re.IGNORECASE)
    PRIMARY_DEFENSE_KEYWORDS = re.compile(r'\b(primary defense|defense option 1|first defense|fundamental)\b', re.IGNORECASE)

    def __init__(self, doc_id: str, owasp_category: str, cwe_ids: List[str]):
        self.doc_id = doc_id
        self.owasp_category = owasp_category
        self.cwe_ids = cwe_ids

    def parse_markdown(self, markdown_text: str) -> ParsedRemediationDoc:
        lines = markdown_text.splitlines()
        current_headings: Dict[int, str] = {}
        parsed_doc = ParsedRemediationDoc(
            doc_id=self.doc_id,
            title="",
            owasp_category=self.owasp_category,
            cwe_ids=self.cwe_ids
        )

        in_code_block = False
        current_code_lines = []
        current_code_lang = "text"
        code_start_line = 0
        current_explanation = []

        heading_regex = re.compile(r'^(#{1,6})\s+(.*)$')
        code_fence_regex = re.compile(r'^```(\w+)?')

        for idx, line in enumerate(lines, start=1):
            # Check for Code Fence Start/End
            fence_match = code_fence_regex.match(line.strip())
            if fence_match:
                if not in_code_block:
                    # Starting code block
                    in_code_block = True
                    current_code_lang = fence_match.group(1) or "text"
                    current_code_lines = []
                    code_start_line = idx
                else:
                    # Ending code block
                    in_code_block = False
                    code_end_line = idx
                    code_content = "\n".join(current_code_lines)

                    # Build breadcrumb
                    breadcrumb = " > ".join([v for k, v in sorted(current_headings.items())])

                    # Determine code role and defense tier
                    context_text = f"{breadcrumb} {' '.join(current_explanation[-3:])}"
                    role = self._classify_code_role(context_text)
                    tier = self._classify_defense_tier(context_text)

                    snippet = RemediationCodeBlock(
                        snippet_id=f"snip_{self.doc_id}_{code_start_line}",
                        language=current_code_lang.lower(),
                        code_content=code_content,
                        code_role=role,
                        defense_tier=tier,
                        heading_path=breadcrumb,
                        line_start=code_start_line,
                        line_end=code_end_line,
                        explanation="\n".join(current_explanation[-3:])
                    )
                    parsed_doc.code_snippets.append(snippet)
                    current_explanation = []
                continue

            if in_code_block:
                current_code_lines.append(line)
                continue

            # Check for Headings
            h_match = heading_regex.match(line)
            if h_match:
                level = len(h_match.group(1))
                text = h_match.group(2).strip()

                if level == 1 and not parsed_doc.title:
                    parsed_doc.title = text

                # Clean deeper levels
                current_headings = {k: v for k, v in current_headings.items() if k < level}
                current_headings[level] = text
                continue

            # Regular text line: collect for explanation context
            if line.strip():
                current_explanation.append(line.strip())

        return parsed_doc

    def _classify_code_role(self, context_text: str) -> str:
        if self.VULNERABLE_KEYWORDS.search(context_text):
            return "vulnerable_antipattern"
        if self.SECURE_KEYWORDS.search(context_text):
            return "secure_solution"
        return "neutral_example"

    def _classify_defense_tier(self, context_text: str) -> str:
        if self.PRIMARY_DEFENSE_KEYWORDS.search(context_text):
            return "primary"
        if "secondary" in context_text.lower() or "defense option 2" in context_text.lower():
            return "secondary"
        if self.VULNERABLE_KEYWORDS.search(context_text):
            return "anti_pattern"
        return "defense_in_depth"
```

---

## 6. How Downstream Capabilities Use This Module

Once Long and Nguyên parse and index the OWASP Cheat Sheets into SQLite, the Virtual Assistant leverages them across multiple pipeline components:

```
                          USER QUERY:
       "How do I prevent SQL Injection in Python with SQLAlchemy?"
                                 |
                                 v
                [ROUTER / INPUT UNDERSTANDING]
         - Intent: Remediation / Code Patch
         - Entities: OWASP='A05:2025', Lang='python', Tech='sqlalchemy'
                                 |
                                 v
                     [DETERMINISTIC RETRIEVAL]
  Query: SELECT code_content, explanation, heading_path 
         FROM remediation_snippets 
         WHERE owasp_category = 'A05:2025' 
           AND programming_language = 'python' 
           AND code_role = 'secure_solution'
         ORDER BY CASE defense_tier 
             WHEN 'primary' THEN 1 
             WHEN 'secondary' THEN 2 
             ELSE 3 END;
                                 |
                                 v
                      [LLM REASONING LAYER]
  Synthesizes an explanation grounded STRICTLY in the retrieved code_content
                                 |
                                 v
                    [VERIFICATION & GUARDRAIL]
  1. Syntax Check: Validates Python snippet parses without SyntaxError
  2. Provenance Check: Attaches exact Cheat Sheet citation + lines
                                 |
                                 v
                         FINAL RESPONSE
```

---

## 7. Evidence & Evaluation Strategy (Aligning with Course Rubric)

To guarantee high marks under the course evaluation rubric (**R2, R3, R7, R8**), Long and Nguyên must produce concrete, measurable evidence:

### 1. Deterministic Capability Evidence (R2)
- Unit tests proving that code classification (`secure_solution` vs. `vulnerable_antipattern`) achieves **100% precision** on sample cheat sheets without depending on an LLM.

### 2. Data & Provenance Evidence (R3)
- Traceability demo: Given any returned remediation response, the system displays:
  - Source File: `data/raw/remediation/SQL_Injection_Prevention_Cheat_Sheet.md`
  - Section Breadcrumb: `Primary Defenses > Defense Option 1 > Python`
  - Original Line Range: `L142-L168`

### 3. Safeguard & Anti-Pattern Defense (R7)
- **Red-team test case**: Inject a prompt attempting to solicit a vulnerable string concatenation example:  
  *System response*: Must refuse to recommend the vulnerable pattern, or explicitly flag it with a prominent warning: `[WARNING: INSECURE ANTI-PATTERN DETECTED - DO NOT USE IN PRODUCTION]`.

### 4. Measurable Evaluation & Improvement Cycle (R8)
- Compare two retrieval configurations on 20 synthetic remediation benchmark questions:
  - **Baseline**: Standard fixed-size chunking without code awareness.
  - **Improved (Our Method)**: Structure-aware remediation parser with atomic code blocks and breadcrumb headers.
  - **Expected Metric Improvement**:
    - Code Snippet Integrity: Baseline (60% valid code) vs. Improved (100% valid code).
    - Vulnerable Snippet Leakage: Baseline (15% accidental leaks) vs. Improved (0% leaks).

---

## 8. Summary Action Checklist for Long & Nguyên

- [ ] **Task 1 (Long)**: Clone and stage the 6 essential OWASP Cheat Sheet files in `data/raw/remediation/`.
- [ ] **Task 2 (Long)**: Write `data/raw/remediation_manifest.json` with document metadata and OWASP/CWE mappings.
- [ ] **Task 3 (Nguyên)**: Implement `src/pipeline/parsers/remediation_parser.py` using the regex and AST logic above.
- [ ] **Task 4 (Nguyên)**: Ensure atomic code fence preservation so code snippets are never severed mid-block.
- [ ] **Task 5 (Long & Nguyên)**: Populate the `remediation_snippets` SQLite table and verify foreign keys with `chunks`.
- [ ] **Task 6 (Long & Nguyên)**: Write `tests/test_remediation_parser.py` validating that Java, Python, and JS snippets are extracted and classified cleanly.
