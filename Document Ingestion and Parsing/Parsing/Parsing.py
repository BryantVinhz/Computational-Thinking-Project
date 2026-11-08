"""
Main entry point for Step 2: Document Parsing.
Automatically loads outputs from Step 1 (Ingestion) and orchestrates parsers:
- PDF: PdfParser
- Markdown: MarkdownParser
- CSV: CsvParser
- JSON: JsonParser
- TXT: TxtParser
"""
import json
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows console
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure path.py and Ingestion/ package are importable when running directly
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


def run_all_parsers(force_reparse: bool = False) -> dict:
    versions_path = INGESTION_OUT_DIR / "documents_version.json"
    if not versions_path.exists():
        raise FileNotFoundError(f"Step 1 manifest not found at {versions_path}")

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

    print("=== STARTING STEP 2: DOCUMENT PARSING ===")
    for v in versions:
        if v.status == ProcessingStatus.FAILED.value or v.is_duplicate:
            summary["skipped"] += 1
            continue

        parser = PARSER_REGISTRY.get(v.file_format)
        if not parser:
            print(f"- [SKIPPED] {v.file_name}: Format {v.file_format} does not have a supported parser.")
            summary["skipped"] += 1
            continue

        out_path = PARSING_OUT_DIR / f"{v.version_id}_parsing.json"
        if out_path.exists() and not force_reparse:
            print(f"- [SKIPPED] {v.file_name}: Already parsed ({out_path.name})")
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
            print(f"- [ERROR] {v.file_name} ({v.file_format}): {e}")

    print("\n=== SUMMARY: STEP 2: DOCUMENT PARSING ===")
    print(f"- Total files to parse     : {summary['total_targets']}")
    print(f"- Successfully parsed     : {summary['success']}")
    print(f"- Failed to parse         : {summary['failed']}")
    print(f"- Skipped (duplicate/Step 1 error): {summary['skipped']}")
    print(f"- Total blocks generated  : {summary['total_blocks']}")
    print("\n* Statistics by block type (BlockType):")
    for b_type, count in sorted(summary["blocks_by_type"].items()):
        print(f"  + {b_type:<15}: {count:>5} blocks")
    print(f"\n=> Output directory       : {PARSING_OUT_DIR}")

    return summary


if __name__ == "__main__":
    run_all_parsers()
