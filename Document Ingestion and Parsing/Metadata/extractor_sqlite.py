import json
import logging
import re
import sqlite3
from contextlib import closing
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

try:  # works both as a package (Metadata.schema_metadata) and as a plain script
    from .schema_metadata import (
        CHUNK_COLUMNS, CHUNK_METADATA_DDL, DOCUMENT_METADATA_DDL, INDEX_DDL,
        ChunkMetadataSchema, ContentFlagsSchema, DocumentMetadataSchema,
        MetadataOutputSchema, SecurityEntitiesSchema,
    )
except ImportError:
    from schema_metadata import (
        CHUNK_COLUMNS, CHUNK_METADATA_DDL, DOCUMENT_METADATA_DDL, INDEX_DDL,
        ChunkMetadataSchema, ContentFlagsSchema, DocumentMetadataSchema,
        MetadataOutputSchema, SecurityEntitiesSchema,
    )


class SecurityMetadataExtractor:

    # 1. MAPPING DICTIONARY FOR THE TEAM'S 5 KEY VULNERABILITIES (OWASP Top 10:2025)
    VULN_KNOWLEDGE_BASE = {
        "SQL Injection": {
            "owasp_category": "A05:2025",
            "keywords": [r"\bsql\s*injection\b", r"\bsqli\b", r"\bblind\s*sqli\b"],
            "cwe_ids": ["CWE-89", "CWE-564"],
            "wstg_ids": ["WSTG-INPV-05"],
        },
        "Cross-Site Scripting": {
            "owasp_category": "A05:2025",
            "keywords": [
                r"\bcross[\s-]*site\s*scripting\b",
                r"\bxss\b",
                r"\bdom\s*xss\b",
                r"\breflected\s*xss\b",
                r"\bstored\s*xss\b",
            ],
            "cwe_ids": ["CWE-79", "CWE-80"],
            "wstg_ids": ["WSTG-INPV-01", "WSTG-INPV-02"],
        },
        "Broken Access Control": {
            "owasp_category": "A01:2025",
            "keywords": [
                r"\bbroken\s*access\s*control\b",
                r"\bidor\b",
                r"\binsecure\s*direct\s*object\b",
                r"\bprivilege\s*escalation\b",
                r"\bpath\s*traversal\b",
            ],
            "cwe_ids": ["CWE-22", "CWE-284", "CWE-285", "CWE-639", "CWE-862", "CWE-863"],
            "wstg_ids": ["WSTG-ATHZ-01", "WSTG-ATHZ-02", "WSTG-ATHZ-03", "WSTG-ATHZ-04"],
        },
        "Broken Authentication / Session Management": {
            "owasp_category": "A07:2025",
            "keywords": [
                r"\bbroken\s*authentication\b",
                r"\bauthentication\s*failures?\b",
                r"\bsession\s*management\b",
                r"\bsession\s*fixation\b",
                r"\bsession\s*hijacking\b",
                r"\bcredential\s*stuffing\b",
            ],
            "cwe_ids": ["CWE-287", "CWE-384", "CWE-798", "CWE-307", "CWE-521"],
            "wstg_ids": ["WSTG-ATHN-01", "WSTG-SESS-01", "WSTG-SESS-02", "WSTG-SESS-03"],
        },
        "Security Misconfiguration": {
            "owasp_category": "A02:2025",
            "keywords": [
                r"\bsecurity\s*misconfiguration\b",
                r"\bdefault\s*credentials\b",
                r"\bdirectory\s*listing\b",
                r"\bxxe\b",
                r"\bxml\s*external\s*entity\b",
                r"\bcors\s*misconfiguration\b",
            ],
            "cwe_ids": ["CWE-16", "CWE-611", "CWE-209", "CWE-942", "CWE-1004"],
            "wstg_ids": ["WSTG-CONF-01", "WSTG-CONF-04", "WSTG-CONF-06"],
        },
    }

    # 2. REGULAR EXPRESSION (REGEX) SET FOR SECURITY ENTITY EXTRACTION
    REGEX_PATTERNS = {
        "owasp": re.compile(r"\b(A(?:0[1-9]|10):202[15])\b", re.IGNORECASE),
        "cwe": re.compile(r"\b(CWE-\d+)\b", re.IGNORECASE),
        "wstg": re.compile(r"\b(WSTG-[A-Z]{4}-\d{2})\b", re.IGNORECASE),
        "asvs": re.compile(r"\b(?:ASVS\s*)?(?:Level\s*([123])|L([123]))\b", re.IGNORECASE),
        "severity": re.compile(
            r"(?:severity|risk|priority|mức\s*độ)[\s:=-]*\b(CRITICAL|HIGH|MEDIUM|LOW|INFO)\b"
            r"|\b(CRITICAL|HIGH|MEDIUM|LOW)\s+(?:severity|risk|vulnerability)\b",
            re.IGNORECASE,
        ),
        "cvss_score": re.compile(
            r"(?:CVSS(?:\s*v?[34]\.\d)?(?:\s*Base)?\s*Score|Base\s*Score|CVSS(?:\s*v?[34]\.\d)?\s*[:=])"
            r"[\s:=\-\[]*\b(10\.0|[0-9]\.[0-9])\b(?!\s*/)",
            re.IGNORECASE,
        ),
        "poc_keywords": re.compile(
            r"\b(proof\s*of\s*concept|poc|exploit\s*payload|attack\s*payload|reproduction\s*steps|steps\s*to\s*reproduce)\b"
            r"|(\'\s*OR\s*[\'1]\s*=\s*[\'1]|UNION\s+SELECT|<script[^>]*>|onerror\s*=|javascript:|(?:\.\./){2,}etc/passwd)",
            re.IGNORECASE,
        ),
    }

    VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}


    def __init__(self, db_path: str = "canonical_metadata.db"):
        self.db_path = db_path
        self._init_sqlite_db()

    # ------------------------------------------------------------------
    # SQLite
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        """FIX: PRAGMA foreign_keys only applies per-connection,
        so it must be enabled on EVERY connection (previously only enabled during init)."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_sqlite_db(self) -> None:
        """Create the 2 SQLite tables (DDL is defined once, in schema_metadata.py)."""
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute(DOCUMENT_METADATA_DDL)
                conn.execute(CHUNK_METADATA_DDL)
                for ddl in INDEX_DDL:
                    conn.execute(ddl)
        except sqlite3.Error as e:
            logger.error(f"Error initializing SQLite DB: {e}")
            raise

    # ------------------------------------------------------------------
    # Security entity extraction
    # ------------------------------------------------------------------
    @staticmethod
    def _score_to_severity(score: Optional[float]) -> Optional[str]:
        """Convert CVSS score (0.0 - 10.0) to standard FIRST.org risk severity rating."""
        if score is None:
            return None
        if score == 0.0:
            return "INFO"
        elif 0.1 <= score <= 3.9:
            return "LOW"
        elif 4.0 <= score <= 6.9:
            return "MEDIUM"
        elif 7.0 <= score <= 8.9:
            return "HIGH"
        elif 9.0 <= score <= 10.0:
            return "CRITICAL"
        return None

    def _detect_vulnerability(
        self, heading_path: str, text: str, cwe_ids: List[str], wstg_ids: List[str]
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """
        Identify the vulnerability type (single value). Priority order:
          1) keywords in heading_path (most reliable contextual signal)
          2) explicitly matching CWE / WSTG IDs
          3) keywords in chunk text content
        FIX: Previously scanned merged heading + content and executed `break` on the first match in the dict,
        which caused chunks mentioning multiple flaws to always be assigned based on dict insertion order.
        """

        def keyword_hit(info: Dict[str, Any], haystack: str) -> bool:
            return any(re.search(kw, haystack, re.IGNORECASE) for kw in info["keywords"])

        for name, info in self.VULN_KNOWLEDGE_BASE.items():
            if keyword_hit(info, heading_path):
                return name, info
        for name, info in self.VULN_KNOWLEDGE_BASE.items():
            if any(c in info["cwe_ids"] for c in cwe_ids) or any(w in info["wstg_ids"] for w in wstg_ids):
                return name, info
        for name, info in self.VULN_KNOWLEDGE_BASE.items():
            if keyword_hit(info, text):
                return name, info
        return None, None

    def extract_security_entities(self, text: str, heading_path: str = "") -> Dict[str, Any]:
        """Extract security_entities from heading_path + chunk text using Regex & Inference dictionary."""
        combined_text = f"{heading_path}\n{text}"

        owasp_matches = [m.upper() for m in self.REGEX_PATTERNS["owasp"].findall(combined_text)]
        cwe_matches = sorted({m.upper() for m in self.REGEX_PATTERNS["cwe"].findall(combined_text)})
        wstg_matches = sorted({m.upper() for m in self.REGEX_PATTERNS["wstg"].findall(combined_text)})

        # ASVS Levels (normalized to "Level 1", "Level 2", "Level 3")
        asvs_raw = self.REGEX_PATTERNS["asvs"].findall(combined_text)
        asvs_levels = sorted({f"Level {m[0] or m[1]}" for m in asvs_raw if (m[0] or m[1])})

        # CVSS Base Score
        cvss_match = self.REGEX_PATTERNS["cvss_score"].search(combined_text)
        cvss_base_score = float(cvss_match.group(1)) if cvss_match else None

        # Severity (prioritize explicit text, fallback to inference from CVSS score)
        sev_match = self.REGEX_PATTERNS["severity"].search(combined_text)
        severity = None
        if sev_match:
            severity = (sev_match.group(1) or sev_match.group(2)).upper()
        elif cvss_base_score is not None:
            severity = self._score_to_severity(cvss_base_score)

        # Cross-reference with the 5 core vulnerabilities dictionary
        vuln_type, vuln_info = self._detect_vulnerability(heading_path, text, cwe_matches, wstg_matches)
        detected_owasp = owasp_matches[0] if owasp_matches else None
        if not detected_owasp and vuln_info:
            detected_owasp = vuln_info["owasp_category"]

        return {
            "owasp_category": detected_owasp,
            "vulnerability_type": vuln_type,
            "cwe_ids": cwe_matches,
            "wstg_ids": wstg_matches,
            "asvs_levels": asvs_levels,
            "severity": severity,
            "cvss_base_score": cvss_base_score,
        }

    def detect_has_poc(self, text: str, heading_path: str, has_code: bool) -> bool:
        """Determine has_poc flag (contains actual exploit payload / POC)."""
        combined = f"{heading_path}\n{text}"
        if self.REGEX_PATTERNS["poc_keywords"].search(combined):
            return True
        if has_code and re.search(r"(?:GET|POST|PUT|DELETE)\s+/[^\s]*\s+HTTP/1\.[01]|curl\s+-X", text):
            return True
        return False

    # ------------------------------------------------------------------
    # Look up position / block type from chunk_sources -> NormalizedBlock
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_heading_path(heading_path: Any) -> str:
        """TraceableChunkSchema.heading_path is list[str] -> breadcrumb 'A > B > C'."""
        if isinstance(heading_path, (list, tuple)):
            return " > ".join(str(h) for h in heading_path if h)
        return heading_path or ""

    @staticmethod
    def index_blocks(normalized_blocks: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Index NormalizedBlock by normalized_id (fallback block_id)."""
        index: Dict[str, Dict[str, Any]] = {}
        for b in normalized_blocks:
            if b.get("normalized_id"):
                index[b["normalized_id"]] = b
        for b in normalized_blocks:
            if b.get("block_id"):
                index.setdefault(b["block_id"], b)
        return index

    @staticmethod
    def _get_locator(block: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve a block's location regardless of which Step 2 / Step 3 representation is used:
          1) `locator`        : nested dict from ContentBlockSchema.to_dict()
          2) `source_locator` : legacy name
          3) flat columns     : ContentBlockSchema.to_flat_dict() (page_number, start_line, end_line at top level)
        """
        loc = block.get("locator") or block.get("source_locator")
        if isinstance(loc, dict):
            return loc
        return {k: block.get(k) for k in ("page_number", "start_line", "end_line") if block.get(k) is not None}

    @staticmethod
    def _is_code_or_payload(block: Dict[str, Any]) -> bool:
        """Step 3 stores `is_code_or_payload` at the top level; fall back to legacy `normalization_flags`."""
        if block.get("is_code_or_payload") is not None:
            return bool(block["is_code_or_payload"])
        return bool((block.get("normalization_flags") or {}).get("is_code_or_payload"))

    @classmethod
    def infer_total_pages(cls, normalized_blocks: Iterable[Dict[str, Any]]) -> Optional[int]:
        """Estimate total_pages = highest page number containing a content block (primarily relevant for PDF).
        If the parser in Step 2 already counted the total pages, use that count instead."""
        pages = [cls._get_locator(b).get("page_number") for b in normalized_blocks]
        pages = [p for p in pages if p is not None]
        return max(pages) if pages else None

    def _collect_source_context(
        self,
        chunk_id: str,
        chunk_type: Optional[str],
        sources: List[Dict[str, Any]],
        blocks_by_id: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        pages: List[int] = []
        lines: List[int] = []
        has_code = chunk_type == "code"
        has_table = chunk_type == "table"

        for src in sorted(sources, key=lambda s: s.get("source_order", 0)):
            block = blocks_by_id.get(src.get("normalized_block_id"))
            if block is None:
                logger.warning(f"Chunk {chunk_id}: block {src.get('normalized_block_id')} not found")
                continue
            loc = self._get_locator(block)

            if loc.get("page_number") is not None:
                pages.append(int(loc["page_number"]))
            for key in ("start_line", "end_line"):
                if loc.get(key) is not None:
                    lines.append(int(loc[key]))

            has_code = has_code or block.get("block_type") == "code" or self._is_code_or_payload(block)
            has_table = has_table or block.get("block_type") == "table"

        if not sources:
            logger.warning(f"Chunk {chunk_id}: missing chunk_sources -> missing page/line")

        return {
            "page_range": [min(pages), max(pages)] if pages else None,
            "line_range": [min(lines), max(lines)] if lines else None,
            "has_code": has_code,
            "has_table": has_table,
        }

    # ------------------------------------------------------------------
    # Build + Save (typed with schema_metadata.py)
    # ------------------------------------------------------------------
    @staticmethod
    def _heading_list(heading_path: Any) -> List[str]:
        """Keep heading_path as list[str] (same type as Kiet's ChunkSchema)."""
        if isinstance(heading_path, (list, tuple)):
            return [str(h) for h in heading_path if h]
        return [heading_path] if heading_path else []

    def build_chunk_metadata(
        self,
        chunk: Dict[str, Any],
        sources: List[Dict[str, Any]],
        blocks_by_id: Dict[str, Dict[str, Any]],
    ) -> ChunkMetadataSchema:
        """Build 1 ChunkMetadataSchema (not yet persisted to DB)."""
        # text_content is the actual field name in ChunkSchema ("content" is the legacy name in notes)
        content = chunk.get("text_content") or chunk.get("content") or ""
        heading_text = self._normalize_heading_path(chunk.get("heading_path"))
        ctx = self._collect_source_context(chunk["chunk_id"], chunk.get("chunk_type"), sources, blocks_by_id)

        ent = self.extract_security_entities(content, heading_text)
        has_poc = self.detect_has_poc(content, heading_text, ctx["has_code"])

        try:
            entities = SecurityEntitiesSchema(**ent)  # validates severity / CVSS range
        except ValueError as e:
            raise ValueError(f"Chunk {chunk['chunk_id']}: {e}") from e

        page_range = ctx["page_range"]
        return ChunkMetadataSchema(
            chunk_id=chunk["chunk_id"],
            document_id=chunk["document_id"],
            version_id=chunk["version_id"],
            chunk_idx=chunk.get("chunk_idx"),
            chunk_type=chunk.get("chunk_type"),
            chunk_strategy=chunk.get("chunk_strategy"),
            chunking_version=chunk.get("chunking_version"),
            heading_path=self._heading_list(chunk.get("heading_path")),
            page_number=page_range[0] if page_range else None,
            page_range=page_range,
            line_range=ctx["line_range"],
            security_entities=entities,
            content_flags=ContentFlagsSchema(
                has_code=ctx["has_code"], has_table=ctx["has_table"], has_poc=has_poc
            ),
            token_count=chunk.get("token_count"),  # calculated by the Step 4 tokenizer
        )

    def save_document_metadata(self, doc: Union[DocumentMetadataSchema, Dict[str, Any]]) -> DocumentMetadataSchema:
        """Save Document Metadata. A dict is converted via DocumentMetadataSchema.from_dict()
        (accepts Step 1 names: name -> title, file_format -> document_format)."""
        if isinstance(doc, dict):
            doc = DocumentMetadataSchema.from_dict(doc)

        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO document_metadata
                    (document_id, version_id, title, document_format, domain_category, total_pages)
                VALUES (:document_id, :version_id, :title, :document_format, :domain_category, :total_pages)
                ON CONFLICT(document_id, version_id) DO UPDATE SET
                    title = excluded.title,
                    document_format = excluded.document_format,
                    domain_category = excluded.domain_category,
                    total_pages = excluded.total_pages
                """,
                doc.to_flat_dict(),
            )
        return doc

    def save_chunk_metadata(self, records: List[ChunkMetadataSchema]) -> None:
        """Persist multiple records in a single transaction (document_metadata must exist first: FOREIGN KEY)."""
        cols = ", ".join(CHUNK_COLUMNS)
        params = ", ".join(f":{c}" for c in CHUNK_COLUMNS)
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                f"INSERT OR REPLACE INTO chunk_metadata ({cols}) VALUES ({params})",
                [r.to_flat_dict() for r in records],
            )

    def process_chunking_output(
        self,
        chunking_output: Dict[str, Any],
        normalized_blocks: List[Dict[str, Any]],
        chunking_version: Optional[str] = None,
    ) -> List[ChunkMetadataSchema]:
        """
        Consume Kiet's ChunkingOutputSchema (object or to_dict()) + Step 3 NormalizedBlock list,
        extract metadata for all chunks and persist them. Requires document_metadata to be saved first;
        use process_document() to do both.
        `chunking_version` is optional because ChunkSchema / ChunkingOutputSchema do not carry it.
        """
        if not isinstance(chunking_output, dict) and hasattr(chunking_output, "to_dict"):
            chunking_output = chunking_output.to_dict()
        blocks_by_id = self.index_blocks(normalized_blocks)

        sources_by_chunk: Dict[str, List[Dict[str, Any]]] = {}
        for src in chunking_output.get("chunk_sources", []):
            sources_by_chunk.setdefault(src["chunk_id"], []).append(src)

        # Output-level values serve as defaults; individual chunk fields (if present) override them
        defaults = {
            "document_id": chunking_output["document_id"],
            "version_id": chunking_output["version_id"],
            "chunk_strategy": chunking_output.get("chunking_strategy"),
            "chunking_version": chunking_version or chunking_output.get("chunking_version"),
        }

        records = []
        for chunk in chunking_output.get("chunks", []):
            merged = {**defaults, **{k: v for k, v in chunk.items() if v is not None}}
            records.append(self.build_chunk_metadata(merged, sources_by_chunk.get(merged["chunk_id"], []), blocks_by_id))

        self.save_chunk_metadata(records)
        return records

    def process_document(
        self,
        document_info: Dict[str, Any],
        chunking_output: Dict[str, Any],
        normalized_blocks: List[Dict[str, Any]],
        chunking_version: Optional[str] = None,
    ) -> MetadataOutputSchema:
        """One call for the whole step: save document metadata, then chunk metadata.
        `document_info` may contain title|name, document_format|file_format, domain_category, total_pages.
        document_id / version_id default to the ones in chunking_output."""
        if not isinstance(chunking_output, dict) and hasattr(chunking_output, "to_dict"):
            chunking_output = chunking_output.to_dict()

        info = {
            "document_id": chunking_output["document_id"],
            "version_id": chunking_output["version_id"],
            **document_info,
        }
        if info.get("total_pages") is None:
            info["total_pages"] = self.infer_total_pages(normalized_blocks)

        document = self.save_document_metadata(info)
        chunks = self.process_chunking_output(chunking_output, normalized_blocks, chunking_version)
        return MetadataOutputSchema(document=document, chunks=chunks)


# =====================================================================
# TEST WITH MOCK DATA
# =====================================================================
if __name__ == "__main__":
    extractor = SecurityMetadataExtractor(db_path="test_security_metadata.db")

    # Mock NormalizedBlocks from Step 3 (Step 2 ContentBlockSchema.to_dict() keys + Step 3 fields:
    # `locator` is nested, `is_code_or_payload` is top-level)
    normalized_blocks = [
        {"normalized_id": "nrm_001", "block_id": "blk_001", "block_type": "paragraph",
         "locator": {"page_number": 12}, "is_code_or_payload": False},
        {"normalized_id": "nrm_002", "block_id": "blk_002", "block_type": "code",
         "locator": {"page_number": 13}, "is_code_or_payload": True},
        {"normalized_id": "nrm_003", "block_id": "blk_003", "block_type": "paragraph",
         "locator": {"page_number": 20, "start_line": 10, "end_line": 18},
         "is_code_or_payload": False},
        {"normalized_id": "nrm_004", "block_id": "blk_004", "block_type": "table",
         "locator": {"page_number": 20, "start_line": 19, "end_line": 25},
         "is_code_or_payload": False},
    ]

    # Mock Step 4 output (same keys as ChunkingOutputSchema.to_dict() / ChunkSchema)
    chunking_output = {
        "document_id": "doc_asvs_001",
        "version_id": "ver_001",
        "chunking_strategy": "structure_aware",
        "chunks": [
            {
                "chunk_id": "chk_ver8f4a_001", "document_id": "doc_asvs_001", "version_id": "ver_001",
                "chunk_idx": 0, "chunk_type": "paragraph", "token_count": 68,
                "chunk_strategy": "structure_aware",
                "heading_path": ["A05:2025 Injection", "SQL Injection", "Remediation"],
                "text_content": (
                    "Requirement V5.3.4 (ASVS Level 1, Level 2): Verify that data selection or database queries "
                    "use parameterized queries to prevent CWE-89. Testing can follow WSTG-INPV-05. "
                    "Example PoC payload: ' OR 1=1 -- . CVSS v3.1 Base Score: 9.8"
                ),
            },
            {
                "chunk_id": "chk_ver8f4a_002", "document_id": "doc_asvs_001", "version_id": "ver_001",
                "chunk_idx": 1, "chunk_type": "mixed", "token_count": 42,
                "chunk_strategy": "structure_aware",
                "heading_path": ["Pentest Report", "Finding 02", "IDOR"],
                "text_content": (
                    "In this Broken Access Control case (IDOR), an attacker can modify the user_id parameter "
                    "to view other profiles. Severity: HIGH."
                ),
            },
        ],
        "chunk_sources": [
            {"chunk_source_id": "cs_1", "chunk_id": "chk_ver8f4a_001", "normalized_block_id": "nrm_001",
             "source_order": 0, "start_offset": 0, "end_offset": 120},
            {"chunk_source_id": "cs_2", "chunk_id": "chk_ver8f4a_001", "normalized_block_id": "nrm_002",
             "source_order": 1, "start_offset": 0, "end_offset": 60},
            {"chunk_source_id": "cs_3", "chunk_id": "chk_ver8f4a_002", "normalized_block_id": "nrm_003",
             "source_order": 0, "start_offset": 0, "end_offset": 90},
            {"chunk_source_id": "cs_4", "chunk_id": "chk_ver8f4a_002", "normalized_block_id": "nrm_004",
             "source_order": 1, "start_offset": 0, "end_offset": 40},
        ],
    }

    # Whole Step 5 in one call: Document Metadata + Chunk Metadata -> SQLite
    output = extractor.process_document(
        document_info={
            "name": "OWASP ASVS v5.0 & Pentest Report Sample",   # Document.name
            "file_format": "PDF",                                # Document_version.file_format
            "domain_category": "standards_context",
        },
        chunking_output=chunking_output,
        normalized_blocks=normalized_blocks,
        chunking_version="1.0",   # not in ChunkSchema -> pass it explicitly
    )
    print(json.dumps(output.to_dict(), indent=2, ensure_ascii=False))
