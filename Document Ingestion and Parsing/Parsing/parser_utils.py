"""
Helper dùng chung cho các parser CSV / JSON / TXT (Step 2: Document Parsing).
"""
import json
import sys
from pathlib import Path

# Đảm bảo import được path.py và package Ingestion/ khi chạy trực tiếp file parser
CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
for _p in (CURRENT_DIR, PARENT_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from path import INGESTION_OUT_DIR, PARSING_OUT_DIR, to_actual_path
from Ingestion.schema_ingestion import DocumentVersionSchema, ProcessingStatus
from schema_parsing import (
    ContentBlockSchema,
    Locator,
    make_block_id,
    make_text_hash,
    validate_block,
)


def read_text(version: DocumentVersionSchema) -> str:
    """Đọc file của version dưới dạng text UTF-8 (tự bỏ BOM)."""
    file_path = to_actual_path(version.file_path)
    try:
        return file_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as e:
        raise ValueError(f"Không decode được UTF-8: {file_path.name} ({e})") from e


def build_block(
    version: DocumentVersionSchema,
    block_idx: int,
    block_type: str,
    text_content: str,
    locator: Locator,
    parser_name: str,
    parser_version: str,
    **extra,
) -> ContentBlockSchema:
    """Tạo ContentBlockSchema đã điền ID, hash và kiểm định theo schema."""
    block = ContentBlockSchema(
        block_id=make_block_id(version.version_id, block_idx),
        document_id=version.document_id,
        version_id=version.version_id,
        block_idx=block_idx,
        file_format=version.file_format,
        block_type=block_type,
        text_content=text_content,
        text_hash=make_text_hash(text_content),
        parser_name=parser_name,
        parser_version=parser_version,
        locator=locator,
        **extra,
    )
    errors = validate_block(block)
    if errors:
        # Lỗi ở đây là bug của parser, không phải lỗi dữ liệu -> không nuốt
        raise ValueError(f"Block {block.block_id} không hợp lệ: {'; '.join(errors)}")
    return block


def run_cli(parser) -> None:
    """Chạy thử parser trên manifest Step 1 và ghi output vào PARSING_OUT_DIR."""
    versions_path = INGESTION_OUT_DIR / "ducuments_version.json"
    versions = [
        DocumentVersionSchema(**item)
        for item in json.loads(versions_path.read_text(encoding="utf-8"))
    ]
    targets = [
        v for v in versions
        if v.file_format == parser.file_format
        and v.status != ProcessingStatus.FAILED.value
        and not v.is_duplicate
    ]
    PARSING_OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"=== PARSING {parser.file_format}: {len(targets)} file ===")
    for version in targets:
        try:
            blocks = parser.parse(version)
        except ValueError as e:
            print(f"- [LỖI] {version.file_name}: {e}")
            continue
        out_path = PARSING_OUT_DIR / f"{version.version_id}_parsing.json"
        out_path.write_text(
            json.dumps([b.to_dict() for b in blocks], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"- {version.file_name}: {len(blocks)} block -> {out_path.name}")
