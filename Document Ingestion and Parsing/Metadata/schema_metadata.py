import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

VALID_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------
# Chunk-level
# ----------------------------------------------------------------------
@dataclass
class SecurityEntitiesSchema:
    """Security entities found in one chunk (regex + 5-vulnerability dictionary)."""
    owasp_category: Optional[str] = None        # e.g. "A05:2025"
    vulnerability_type: Optional[str] = None    # one of the team's 5 key vulnerabilities, or None
    cwe_ids: List[str] = field(default_factory=list)      # e.g. ["CWE-89"]
    wstg_ids: List[str] = field(default_factory=list)     # e.g. ["WSTG-INPV-05"]
    asvs_levels: List[str] = field(default_factory=list)  # e.g. ["Level 1", "Level 2"]
    severity: Optional[str] = None              # CRITICAL | HIGH | MEDIUM | LOW | INFO | None
    cvss_base_score: Optional[float] = None     # 0.0 - 10.0

    def __post_init__(self) -> None:
        if self.severity is not None and self.severity not in VALID_SEVERITIES:
            raise ValueError(f"invalid severity '{self.severity}'")
        if self.cvss_base_score is not None and not 0.0 <= self.cvss_base_score <= 10.0:
            raise ValueError(f"CVSS score {self.cvss_base_score} is out of the 0-10 range")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ContentFlagsSchema:
    has_code: bool = False    # chunk contains code / HTTP / bash
    has_table: bool = False   # chunk contains tabular data
    has_poc: bool = False     # chunk contains a real exploit payload / PoC

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ChunkMetadataSchema:
    """One row of table `chunk_metadata`."""
    chunk_id: str
    document_id: str
    version_id: str
    chunk_idx: Optional[int] = None
    chunk_type: Optional[str] = None
    chunk_strategy: Optional[str] = None
    chunking_version: Optional[str] = None       # not in Kiet's ChunkSchema -> optional
    heading_path: List[str] = field(default_factory=list)   # same type as ChunkSchema.heading_path
    page_number: Optional[int] = None            # first page the chunk touches (PDF)
    page_range: Optional[List[int]] = None       # [start_page, end_page]
    line_range: Optional[List[int]] = None       # [start_line, end_line] (Markdown)
    security_entities: SecurityEntitiesSchema = field(default_factory=SecurityEntitiesSchema)
    content_flags: ContentFlagsSchema = field(default_factory=ContentFlagsSchema)
    token_count: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Nested form (security_entities / content_flags), as in the team's note."""
        return asdict(self)

    def to_flat_dict(self) -> Dict[str, Any]:
        """Flat form = one SQLite row. Lists are stored as JSON text, heading_path as 'A > B > C'."""
        e, f = self.security_entities, self.content_flags
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "version_id": self.version_id,
            "chunk_idx": self.chunk_idx,
            "chunk_type": self.chunk_type,
            "chunk_strategy": self.chunk_strategy,
            "chunking_version": self.chunking_version,
            "heading_path": " > ".join(self.heading_path),
            "page_number": self.page_number,
            "page_range": json.dumps(self.page_range) if self.page_range else None,
            "line_range": json.dumps(self.line_range) if self.line_range else None,
            "owasp_category": e.owasp_category,
            "vulnerability_type": e.vulnerability_type,
            "cwe_ids": json.dumps(e.cwe_ids),
            "wstg_ids": json.dumps(e.wstg_ids),
            "asvs_levels": json.dumps(e.asvs_levels),
            "severity": e.severity,
            "cvss_base_score": e.cvss_base_score,
            "has_code": int(f.has_code),
            "has_table": int(f.has_table),
            "has_poc": int(f.has_poc),
            "token_count": self.token_count,
        }


# ----------------------------------------------------------------------
# Document-level
# ----------------------------------------------------------------------
@dataclass
class DocumentMetadataSchema:
    """One row of table `document_metadata` (one per document VERSION)."""
    document_id: str
    version_id: str
    title: Optional[str] = None             # Document.name
    document_format: Optional[str] = None   # Document_version.file_format
    domain_category: Optional[str] = None   # set by the team per source folder; never guessed
    total_pages: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DocumentMetadataSchema":
        """Also accepts the Step 1 field names: name -> title, file_format -> document_format."""
        if not data.get("document_id") or not data.get("version_id"):
            raise ValueError("Missing document_id or version_id!")
        return cls(
            document_id=data["document_id"],
            version_id=data["version_id"],
            title=data.get("title") or data.get("name"),
            document_format=data.get("document_format") or data.get("file_format"),
            domain_category=data.get("domain_category"),
            total_pages=data.get("total_pages"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_flat_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MetadataOutputSchema:
    """Complete output of Step 5 for one document version (mirror of ChunkingOutputSchema)."""
    document: DocumentMetadataSchema
    chunks: List[ChunkMetadataSchema]
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document": self.document.to_dict(),
            "chunks": [c.to_dict() for c in self.chunks],
            "created_at": self.created_at,
        }


# ----------------------------------------------------------------------
# SQLite DDL (must stay in sync with to_flat_dict() above)
# ----------------------------------------------------------------------
CHUNK_COLUMNS = (
    "chunk_id", "document_id", "version_id", "chunk_idx", "chunk_type",
    "chunk_strategy", "chunking_version", "heading_path",
    "page_number", "page_range", "line_range",
    "owasp_category", "vulnerability_type", "cwe_ids", "wstg_ids", "asvs_levels",
    "severity", "cvss_base_score",
    "has_code", "has_table", "has_poc", "token_count",
)

DOCUMENT_METADATA_DDL = """
CREATE TABLE IF NOT EXISTS document_metadata (
    document_id     TEXT NOT NULL,
    version_id      TEXT NOT NULL,
    title           TEXT,
    document_format TEXT,
    domain_category TEXT,
    total_pages     INTEGER,
    PRIMARY KEY (document_id, version_id)
)
"""

CHUNK_METADATA_DDL = """
CREATE TABLE IF NOT EXISTS chunk_metadata (
    chunk_id           TEXT PRIMARY KEY,
    document_id        TEXT NOT NULL,
    version_id         TEXT NOT NULL,
    chunk_idx          INTEGER,
    chunk_type         TEXT,
    chunk_strategy     TEXT,
    chunking_version   TEXT,
    heading_path       TEXT,      -- breadcrumb 'A > B > C'
    page_number        INTEGER,   -- first page of the chunk
    page_range         TEXT,      -- JSON [start_page, end_page]
    line_range         TEXT,      -- JSON [start_line, end_line]
    owasp_category     TEXT,
    vulnerability_type TEXT,
    cwe_ids            TEXT,      -- JSON list
    wstg_ids           TEXT,      -- JSON list
    asvs_levels        TEXT,      -- JSON list
    severity           TEXT,
    cvss_base_score    REAL,
    has_code           INTEGER,   -- 0/1
    has_table          INTEGER,   -- 0/1
    has_poc            INTEGER,   -- 0/1
    token_count        INTEGER,
    FOREIGN KEY (document_id, version_id)
        REFERENCES document_metadata(document_id, version_id)
)
"""

INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_chunk_doc ON chunk_metadata(document_id, version_id)",
    "CREATE INDEX IF NOT EXISTS idx_owasp ON chunk_metadata(owasp_category)",
    "CREATE INDEX IF NOT EXISTS idx_vuln ON chunk_metadata(vulnerability_type)",
    "CREATE INDEX IF NOT EXISTS idx_severity ON chunk_metadata(severity)",
)
