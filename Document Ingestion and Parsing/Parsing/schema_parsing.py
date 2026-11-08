"""
Schema for Step 2: Document Parsing.

Common conventions (must be followed by all parsers):
- One record = one block (ContentBlockSchema).
- page_number, start_line, end_line, start_row, end_row are all 1-based.
- bbox = [x0, y0, x1, y1], origin at top-left corner of the page (PyMuPDF standard), in points.
- CSV: start_row/end_row count data rows, NOT including header row.
- text_content is always the raw text before normalization (handled in step 3).
- document_id, version_id come from DocumentVersionSchema (schema_ingestion.py).
"""
import hashlib
import sys
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path

from Ingestion.schema_ingestion import FileFormat

class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    CODE = "code"
    # Primarily found in PDF
    FIGURE = "figure"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    HEADER_FOOTER = "header_footer"
    # Used for JSON/CSV: a structured record
    RECORD = "record"



# Format-specific locators
@dataclass
class PdfLocator:
    page_number: int                       # 1-based
    bbox: list[float] | None = None        # [x0, y0, x1, y1], top-left origin
    is_ocr: bool = False
    ocr_confidence: float | None = None    # 0..1, only present when is_ocr = True


@dataclass
class LineLocator:
    """Used for MARKDOWN and TXT."""
    start_line: int                        # 1-based
    end_line: int                          # 1-based, inclusive


@dataclass
class CsvLocator:
    table_idx: int = 0                     # 0 if file contains only 1 table
    start_row: int = 1                     # 1-based, excluding header
    end_row: int = 1


@dataclass
class JsonLocator:
    json_path: str                         # e.g. "$.weaknesses[12]"


Locator = PdfLocator | LineLocator | CsvLocator | JsonLocator

# Locator mapping by format
LOCATOR_BY_FORMAT: dict[str, type] = {
    FileFormat.PDF.value: PdfLocator,
    FileFormat.MARKDOWN.value: LineLocator,
    FileFormat.TXT.value: LineLocator,
    FileFormat.CSV.value: CsvLocator,
    FileFormat.JSON.value: JsonLocator,
}

# Allowed block types by format
ALLOWED_BLOCK_TYPES: dict[str, set[str]] = {
    FileFormat.PDF.value: {t.value for t in BlockType if t != BlockType.RECORD},
    FileFormat.MARKDOWN.value: {
        BlockType.HEADING.value, BlockType.PARAGRAPH.value, BlockType.LIST.value,
        BlockType.TABLE.value, BlockType.CODE.value,
    },
    FileFormat.TXT.value: {BlockType.PARAGRAPH.value, BlockType.LIST.value, BlockType.CODE.value},
    FileFormat.CSV.value: {BlockType.TABLE.value, BlockType.RECORD.value},
    FileFormat.JSON.value: {BlockType.RECORD.value},
}



# Main Schema
@dataclass
class ContentBlockSchema:
    # Identity
    block_id: str                          # {version_id}_b{block_idx:05d}
    document_id: str
    version_id: str
    block_idx: int                         # order in document, 0-based
    file_format: str                       # FileFormat value

    # Content
    block_type: str                        # BlockType value
    text_content: str                      # raw text
    text_hash: str                         # sha256 of text_content

    # Parser provenance
    parser_name: str                       # e.g. "markdown-it-py", "pymupdf"
    parser_version: str

    # Locator: exactly 1 type according to file_format
    locator: Locator | None = None

    # Structural context
    heading_path: list[str] = field(default_factory=list)   # assigned during postprocessing
    heading_level: int | None = None       # only when block_type = heading
    code_language: str | None = None       # only when block_type = code
    lang: str | None = None                # "vi", "en"

    # Table (block_type = table)
    table_header_cols: list[str] | None = None
    table_rows: list[list[str]] | None = None
    continues_from_previous: bool = False  # multi-page PDF table continuation

    # Structured record (block_type = record, used for JSON/CSV)
    raw_record: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_flat_dict(self) -> dict:
        """Flatten locator into separate columns for SQLite convenience.
        Locator columns not applicable for this format will be None."""
        d = asdict(self)
        loc = d.pop("locator") or {}
        d.update({
            "page_number": loc.get("page_number"),
            "bbox": loc.get("bbox"),
            "is_ocr": loc.get("is_ocr", False),
            "ocr_confidence": loc.get("ocr_confidence"),
            "start_line": loc.get("start_line"),
            "end_line": loc.get("end_line"),
            "table_idx": loc.get("table_idx"),
            "start_row": loc.get("start_row"),
            "end_row": loc.get("end_row"),
            "json_path": loc.get("json_path"),
        })
        return d



# Common helpers for all parsers
def make_block_id(version_id: str, block_idx: int) -> str:
    return f"{version_id}_b{block_idx:05d}"


def make_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_block(block: ContentBlockSchema) -> list[str]:
    """Return list of validation errors; empty means valid."""
    errors: list[str] = []
    fmt = block.file_format

    if fmt not in LOCATOR_BY_FORMAT:
        return [f"Unsupported file_format: {fmt}"]

    if block.block_type not in ALLOWED_BLOCK_TYPES[fmt]:
        errors.append(f"block_type '{block.block_type}' is not valid for {fmt}")

    expected = LOCATOR_BY_FORMAT[fmt]
    if not isinstance(block.locator, expected):
        errors.append(f"{fmt} requires locator of type {expected.__name__}")
    else:
        loc = block.locator
        if isinstance(loc, PdfLocator):
            if loc.page_number < 1:
                errors.append("page_number must be >= 1")
            if loc.bbox is not None and len(loc.bbox) != 4:
                errors.append("bbox must have 4 elements")
        elif isinstance(loc, LineLocator):
            if loc.start_line < 1 or loc.end_line < loc.start_line:
                errors.append("invalid line range")
        elif isinstance(loc, CsvLocator):
            if loc.start_row < 1 or loc.end_row < loc.start_row:
                errors.append("invalid row range")
        elif isinstance(loc, JsonLocator):
            if not loc.json_path.startswith("$"):
                errors.append("json_path must start with '$'")

    if block.block_type == BlockType.HEADING.value and block.heading_level is None:
        errors.append("heading missing heading_level")
    if block.block_type == BlockType.TABLE.value:
        if block.table_header_cols is None or block.table_rows is None:
            errors.append("table missing table_header_cols/table_rows")
        else:
            n = len(block.table_header_cols)
            if any(len(r) != n for r in block.table_rows):
                errors.append("table has ragged rows (mismatched column count)")
    if block.block_type == BlockType.RECORD.value and block.raw_record is None:
        errors.append("record missing raw_record")
    if not block.text_content.strip():
        errors.append("empty text_content")
    if block.text_hash != make_text_hash(block.text_content):
        errors.append("text_hash does not match text_content")
    if block.block_id != make_block_id(block.version_id, block.block_idx):
        errors.append("block_id does not follow convention")
    return errors
