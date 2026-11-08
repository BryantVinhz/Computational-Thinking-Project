"""
PDF Parser: extracts .pdf documents into HEADING, PARAGRAPH, LIST, TABLE, CODE, HEADER_FOOTER blocks.
Uses PyMuPDF (fitz) to detect layout, bbox coordinates, font sizes, and tables.
Complies with schema_parsing.py standards and uses helpers from parser_utils.py.
"""
import re
from pathlib import Path
import pymupdf

from parser_utils import build_block, run_cli
from path import to_actual_path
from Ingestion.schema_ingestion import DocumentVersionSchema, FileFormat
from schema_parsing import BlockType, ContentBlockSchema, PdfLocator

PARSER_NAME = "pymupdf-parser"
PARSER_VERSION = pymupdf.__version__

_BULLET_RE = re.compile(r"^\s*([•\-*–—+]|\d+[.)]|[a-zA-Z][.)])\s+")
_PAGE_NUM_RE = re.compile(r"^(page\s+\d+(\s+of\s+\d+)?|\d+(\s*/\s*\d+)?)$", re.IGNORECASE)


def _bbox_overlap(b1: list[float], b2: list[float], threshold: float = 0.5) -> bool:
    """Check whether bbox b1 largely overlaps inside bbox b2 (e.g. table)."""
    x0 = max(b1[0], b2[0])
    y0 = max(b1[1], b2[1])
    x1 = min(b1[2], b2[2])
    y1 = min(b1[3], b2[3])
    if x1 <= x0 or y1 <= y0:
        return False
    inter_area = (x1 - x0) * (y1 - y0)
    b1_area = (b1[2] - b1[0]) * (b1[3] - b1[1])
    return (inter_area / b1_area) >= threshold if b1_area > 0 else False


class PdfParser:
    file_format = FileFormat.PDF.value

    def parse(self, version: DocumentVersionSchema) -> list[ContentBlockSchema]:
        file_path = to_actual_path(version.file_path)
        try:
            doc = pymupdf.open(file_path)
        except Exception as e:
            raise ValueError(f"Cannot open PDF file: {file_path.name} ({e})") from e

        if len(doc) == 0:
            raise ValueError("PDF has no pages")

        blocks: list[ContentBlockSchema] = []
        heading_stack: list[tuple[int, str]] = []
        prev_table_cols: list[str] | None = None

        for page_idx, page in enumerate(doc):
            page_num = page_idx + 1
            page_height = page.rect.height

            # 1. Extract tables first
            tab_finder = page.find_tables()
            table_bboxes = []
            tables_on_page = []

            for t in tab_finder.tables:
                raw_extract = t.extract()
                if not raw_extract:
                    continue
                table_bboxes.append(list(t.bbox))

                raw_headers = [str(c or "").strip() for c in (t.header.names or raw_extract[0])]
                if not any(raw_headers):
                    num_cols = len(raw_extract[0])
                    headers = [f"col_{i+1}" for i in range(num_cols)]
                else:
                    seen = {}
                    headers = []
                    for idx, h in enumerate(raw_headers, start=1):
                        name = h.replace("\n", " ").strip() or f"col_{idx}"
                        if name in seen:
                            seen[name] += 1
                            name = f"{name}_{seen[name]}"
                        else:
                            seen[name] = 1
                        headers.append(name)

                num_cols = len(headers)
                data_rows: list[list[str]] = []
                for row in raw_extract[1:]:
                    padded = [str(c or "").replace("\n", " ").strip() for c in row]
                    if len(padded) < num_cols:
                        padded.extend([""] * (num_cols - len(padded)))
                    else:
                        padded = padded[:num_cols]
                    data_rows.append(padded)

                # Check continuation of table from previous page
                continues = False
                if prev_table_cols and len(prev_table_cols) == num_cols and t.bbox[1] < 120:
                    continues = True
                prev_table_cols = headers

                text_lines = [" | ".join(headers)]
                for r in data_rows:
                    text_lines.append(" | ".join(r))
                table_text = "\n".join(text_lines)

                bbox_clean = [round(float(c), 2) for c in t.bbox]
                tables_on_page.append({
                    "bbox": bbox_clean,
                    "headers": headers,
                    "rows": data_rows,
                    "continues": continues,
                    "text": table_text,
                    "y0": bbox_clean[1],
                })

            # 2. Extract text blocks from layout dict
            page_dict = page.get_text("dict")
            text_blocks_on_page = []

            for b in page_dict.get("blocks", []):
                if "lines" not in b:
                    continue
                b_bbox = [round(float(c), 2) for c in b["bbox"]]

                # Skip if block is inside an extracted table
                if any(_bbox_overlap(b_bbox, t_bbox) for t_bbox in table_bboxes):
                    continue

                full_lines = []
                max_size = 0.0
                is_bold = False
                is_mono = False

                for line in b["lines"]:
                    line_text = "".join(s["text"] for s in line["spans"])
                    if line_text:
                        full_lines.append(line_text)
                    for span in line["spans"]:
                        if span["size"] > max_size:
                            max_size = span["size"]
                        if span["flags"] & 4 or "bold" in span.get("font", "").lower():
                            is_bold = True
                        font_name = span.get("font", "").lower()
                        if any(m in font_name for m in ("courier", "mono", "consolas")):
                            is_mono = True

                block_text = "\n".join(full_lines).strip()
                if not block_text:
                    continue

                # Classify block by context & dimensions
                is_header_footer = False
                if b_bbox[1] < 45 or b_bbox[3] > (page_height - 45):
                    if len(full_lines) <= 2 or _PAGE_NUM_RE.match(block_text):
                        is_header_footer = True

                if is_header_footer:
                    b_type = BlockType.HEADER_FOOTER.value
                    h_level = None
                elif is_mono:
                    b_type = BlockType.CODE.value
                    h_level = None
                elif max_size >= 14.0 or (max_size >= 11.5 and is_bold and len(full_lines) <= 2):
                    b_type = BlockType.HEADING.value
                    if max_size >= 18.0:
                        h_level = 1
                    elif max_size >= 14.0:
                        h_level = 2
                    elif max_size >= 12.0:
                        h_level = 3
                    else:
                        h_level = 4
                elif any(_BULLET_RE.match(l) for l in full_lines):
                    b_type = BlockType.LIST.value
                    h_level = None
                else:
                    b_type = BlockType.PARAGRAPH.value
                    h_level = None

                text_blocks_on_page.append({
                    "bbox": b_bbox,
                    "block_type": b_type,
                    "heading_level": h_level,
                    "text": block_text,
                    "y0": b_bbox[1],
                })

            # 3. Sort by reading order (y0 top-to-bottom on page)
            all_page_items = []
            for t in tables_on_page:
                all_page_items.append(("table", t["y0"], t))
            for tb in text_blocks_on_page:
                all_page_items.append(("text", tb["y0"], tb))
            all_page_items.sort(key=lambda x: x[1])

            # 4. Generate ContentBlockSchema
            for kind, _, item in all_page_items:
                if kind == "table":
                    blocks.append(build_block(
                        version,
                        block_idx=len(blocks),
                        block_type=BlockType.TABLE.value,
                        text_content=item["text"],
                        locator=PdfLocator(
                            page_number=page_num,
                            bbox=item["bbox"],
                            is_ocr=False,
                        ),
                        parser_name=PARSER_NAME,
                        parser_version=PARSER_VERSION,
                        table_header_cols=item["headers"],
                        table_rows=item["rows"],
                        continues_from_previous=item["continues"],
                        heading_path=[h[1] for h in heading_stack],
                    ))
                else:
                    if item["block_type"] == BlockType.HEADING.value:
                        lvl = item["heading_level"] or 1
                        heading_title = item["text"].split("\n")[0].strip()
                        while heading_stack and heading_stack[-1][0] >= lvl:
                            heading_stack.pop()
                        heading_stack.append((lvl, heading_title))
                        h_path = [h[1] for h in heading_stack]
                    else:
                        h_path = [h[1] for h in heading_stack]

                    blocks.append(build_block(
                        version,
                        block_idx=len(blocks),
                        block_type=item["block_type"],
                        text_content=item["text"],
                        locator=PdfLocator(
                            page_number=page_num,
                            bbox=item["bbox"],
                            is_ocr=False,
                        ),
                        parser_name=PARSER_NAME,
                        parser_version=PARSER_VERSION,
                        heading_level=item["heading_level"],
                        heading_path=h_path,
                    ))

        if not blocks:
            raise ValueError("PDF generated no blocks")
        return blocks


if __name__ == "__main__":
    run_cli(PdfParser())
