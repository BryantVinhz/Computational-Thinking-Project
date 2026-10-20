"""
Schema cho bước 2: Document Parsing.

Quy ước chung (tất cả parser phải tuân thủ):
- Một record = một block (ContentBlockSchema).
- page_number, start_line, end_line, start_row, end_row đều 1-based.
- bbox = [x0, y0, x1, y1], gốc tọa độ ở góc TRÊN-TRÁI trang (chuẩn PyMuPDF), đơn vị point.
- CSV: start_row/end_row đánh số theo dòng dữ liệu, KHÔNG tính dòng header.
- text_content luôn là bản gốc chưa chuẩn hóa (bước 3 sẽ xử lý riêng).
- document_id, version_id lấy từ DocumentVersionSchema (schema_ingestion.py).
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
    # Chủ yếu gặp ở PDF
    FIGURE = "figure"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    HEADER_FOOTER = "header_footer"
    # Dùng cho JSON/CSV: một bản ghi có cấu trúc
    RECORD = "record"


# ---------------------------------------------------------------------------
# Locator riêng cho từng định dạng
# ---------------------------------------------------------------------------
@dataclass
class PdfLocator:
    page_number: int                       # 1-based
    bbox: list[float] | None = None        # [x0, y0, x1, y1], gốc trên-trái
    is_ocr: bool = False
    ocr_confidence: float | None = None    # 0..1, chỉ có khi is_ocr = True


@dataclass
class LineLocator:
    """Dùng cho MARKDOWN và TXT."""
    start_line: int                        # 1-based
    end_line: int                          # 1-based, bao gồm


@dataclass
class CsvLocator:
    table_idx: int = 0                     # 0 nếu file chỉ có 1 bảng
    start_row: int = 1                     # 1-based, không tính header
    end_row: int = 1


@dataclass
class JsonLocator:
    json_path: str                         # ví dụ "$.weaknesses[12]"


Locator = PdfLocator | LineLocator | CsvLocator | JsonLocator

# Định dạng nào dùng locator nào
LOCATOR_BY_FORMAT: dict[str, type] = {
    FileFormat.PDF.value: PdfLocator,
    FileFormat.MARKDOWN.value: LineLocator,
    FileFormat.TXT.value: LineLocator,
    FileFormat.CSV.value: CsvLocator,
    FileFormat.JSON.value: JsonLocator,
}

# Block type hợp lệ theo định dạng
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


# ---------------------------------------------------------------------------
# Schema chính
# ---------------------------------------------------------------------------
@dataclass
class ContentBlockSchema:
    # Định danh
    block_id: str                          # {version_id}_b{block_idx:05d}
    document_id: str
    version_id: str
    block_idx: int                         # thứ tự trong tài liệu, từ 0
    file_format: str                       # giá trị FileFormat

    # Nội dung
    block_type: str                        # giá trị BlockType
    text_content: str                      # bản gốc
    text_hash: str                         # sha256 của text_content

    # Truy vết parser
    parser_name: str                       # ví dụ "markdown-it-py", "pymupdf"
    parser_version: str

    # Locator: đúng 1 loại theo file_format
    locator: Locator | None = None

    # Ngữ cảnh cấu trúc
    heading_path: list[str] = field(default_factory=list)   # gán ở postprocess
    heading_level: int | None = None       # chỉ khi block_type = heading
    code_language: str | None = None       # chỉ khi block_type = code
    lang: str | None = None                # "vi", "en"

    # Bảng (block_type = table)
    table_header_cols: list[str] | None = None
    table_rows: list[list[str]] | None = None
    continues_from_previous: bool = False  # bảng PDF kéo dài nhiều trang

    # Bản ghi có cấu trúc (block_type = record, dùng cho JSON/CSV)
    raw_record: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_flat_dict(self) -> dict:
        """Làm phẳng locator thành cột riêng, tiện lưu SQLite.
        Cột locator không áp dụng cho định dạng này sẽ là None."""
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


# ---------------------------------------------------------------------------
# Helper dùng chung cho mọi parser
# ---------------------------------------------------------------------------
def make_block_id(version_id: str, block_idx: int) -> str:
    return f"{version_id}_b{block_idx:05d}"


def make_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_block(block: ContentBlockSchema) -> list[str]:
    """Trả về danh sách lỗi; rỗng nghĩa là hợp lệ."""
    errors: list[str] = []
    fmt = block.file_format

    if fmt not in LOCATOR_BY_FORMAT:
        return [f"file_format không được hỗ trợ: {fmt}"]

    if block.block_type not in ALLOWED_BLOCK_TYPES[fmt]:
        errors.append(f"block_type '{block.block_type}' không hợp lệ cho {fmt}")

    expected = LOCATOR_BY_FORMAT[fmt]
    if not isinstance(block.locator, expected):
        errors.append(f"{fmt} cần locator kiểu {expected.__name__}")
    else:
        loc = block.locator
        if isinstance(loc, PdfLocator):
            if loc.page_number < 1:
                errors.append("page_number phải >= 1")
            if loc.bbox is not None and len(loc.bbox) != 4:
                errors.append("bbox phải có 4 phần tử")
        elif isinstance(loc, LineLocator):
            if loc.start_line < 1 or loc.end_line < loc.start_line:
                errors.append("line range không hợp lệ")
        elif isinstance(loc, CsvLocator):
            if loc.start_row < 1 or loc.end_row < loc.start_row:
                errors.append("row range không hợp lệ")
        elif isinstance(loc, JsonLocator):
            if not loc.json_path.startswith("$"):
                errors.append("json_path phải bắt đầu bằng '$'")

    if block.block_type == BlockType.HEADING.value and block.heading_level is None:
        errors.append("heading thiếu heading_level")
    if block.block_type == BlockType.TABLE.value:
        if block.table_header_cols is None or block.table_rows is None:
            errors.append("table thiếu table_header_cols/table_rows")
        else:
            n = len(block.table_header_cols)
            if any(len(r) != n for r in block.table_rows):
                errors.append("table có dòng lệch số cột")
    if block.block_type == BlockType.RECORD.value and block.raw_record is None:
        errors.append("record thiếu raw_record")
    if not block.text_content.strip():
        errors.append("text_content rỗng")
    if block.text_hash != make_text_hash(block.text_content):
        errors.append("text_hash không khớp text_content")
    if block.block_id != make_block_id(block.version_id, block.block_idx):
        errors.append("block_id không khớp quy ước")
    return errors