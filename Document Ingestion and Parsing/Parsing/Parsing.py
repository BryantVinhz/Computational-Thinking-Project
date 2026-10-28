"""
Điểm chạy chính (Entry Point) cho Bước 2: Document Parsing.
Tự động nạp kết quả từ Bước 1 (Ingestion) và điều phối các bộ parser:
- PDF: PdfParser
- Markdown: MarkdownParser
- CSV: CsvParser
- JSON: JsonParser
- TXT: TxtParser
"""
import json
import sys
from pathlib import Path

# Đảm bảo in tiếng Việt trên console Windows không bị lỗi cp1252
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Đảm bảo import được path.py và package Ingestion/ khi chạy trực tiếp file
CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
for _p in (CURRENT_DIR, PARENT_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from path import INGESTION_OUT_DIR, PARSING_OUT_DIR
from Ingestion.schema_ingestion import DocumentVersionSchema, FileFormat, ProcessingStatus
from csv_parser import CsvParser
from json_parser import JsonParser
from markdown_parser import MarkdownParser
from pdf_parser import PdfParser
from txt_parser import TxtParser

PARSER_REGISTRY = {
    FileFormat.PDF.value: PdfParser(),
    FileFormat.MARKDOWN.value: MarkdownParser(),
    FileFormat.CSV.value: CsvParser(),
    FileFormat.JSON.value: JsonParser(),
    FileFormat.TXT.value: TxtParser(),
}


def run_all_parsers() -> dict:
    versions_path = INGESTION_OUT_DIR / "documents_version.json"
    if not versions_path.exists():
        raise FileNotFoundError(f"Không tìm thấy manifest Bước 1 tại {versions_path}")

    versions = [
        DocumentVersionSchema(**item)
        for item in json.loads(versions_path.read_text(encoding="utf-8"))
    ]

    PARSING_OUT_DIR.mkdir(parents=True, exist_ok=True)

    summary = {
        "total_targets": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "total_blocks": 0,
        "blocks_by_type": {},
        "files_by_format": {},
    }

    print("=== BẮT ĐẦU CHẠY BƯỚC 2: DOCUMENT PARSING ===")
    for v in versions:
        if v.status == ProcessingStatus.FAILED.value or v.is_duplicate:
            summary["skipped"] += 1
            continue

        parser = PARSER_REGISTRY.get(v.file_format)
        if not parser:
            print(f"- [BỎ QUA] {v.file_name}: Định dạng {v.file_format} chưa có parser hỗ trợ.")
            summary["skipped"] += 1
            continue

        summary["total_targets"] += 1
        summary["files_by_format"][v.file_format] = summary["files_by_format"].get(v.file_format, 0) + 1

        try:
            blocks = parser.parse(v)
            out_path = PARSING_OUT_DIR / f"{v.version_id}_parsing.json"
            out_path.write_text(
                json.dumps([b.to_dict() for b in blocks], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summary["success"] += 1
            summary["total_blocks"] += len(blocks)

            for b in blocks:
                summary["blocks_by_type"][b.block_type] = summary["blocks_by_type"].get(b.block_type, 0) + 1

            print(f"- [OK] {v.file_name} ({v.file_format}): {len(blocks)} blocks -> {out_path.name}")
        except Exception as e:
            summary["failed"] += 1
            print(f"- [LỖI] {v.file_name} ({v.file_format}): {e}")

    print("\n=== KẾT QUẢ TỔNG KẾT BƯỚC 2: DOCUMENT PARSING ===")
    print(f"- Tổng số file cần parse  : {summary['total_targets']}")
    print(f"- Parse thành công       : {summary['success']}")
    print(f"- Parse thất bại         : {summary['failed']}")
    print(f"- Bỏ qua (trùng/lỗi Step 1): {summary['skipped']}")
    print(f"- Tổng số block sinh ra   : {summary['total_blocks']}")
    print("\n* Thống kê theo loại block (BlockType):")
    for b_type, count in sorted(summary["blocks_by_type"].items()):
        print(f"  + {b_type:<15}: {count:>5} blocks")
    print(f"\n=> Thư mục lưu Output     : {PARSING_OUT_DIR}")

    return summary


if __name__ == "__main__":
    run_all_parsers()
