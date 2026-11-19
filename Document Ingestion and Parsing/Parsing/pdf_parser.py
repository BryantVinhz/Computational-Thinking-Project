"""
PDF Parser: extracts .pdf documents into HEADING, PARAGRAPH, LIST, TABLE, CODE, HEADER_FOOTER blocks.
Uses PyMuPDF (fitz) with word-level geometric cell reconstruction and span-gap spacing to prevent
merged-word artifacts in dense tables and text streams.
Complies with schema_parsing.py standards and uses helpers from parser_utils.py.
"""
import re
import pymupdf

from parser_utils import build_block, run_cli
from path import to_actual_path
from Ingestion.schema_ingestion import DocumentVersionSchema, FileFormat
from schema_parsing import BlockType, ContentBlockSchema, PdfLocator

PARSER_NAME = "pymupdf-parser"
PARSER_VERSION = pymupdf.__version__

_BULLET_RE = re.compile(r"^\s*([•\-*–—+]|\d+[.)]|[a-zA-Z][.)])\s+")
_PAGE_NUM_RE = re.compile(r"^(page\s+\d+(\s+of\s+\d+)?|\d+(\s*/\s*\d+)?)$", re.IGNORECASE)
_HYPHENS_RE = re.compile(r"[\u2010\u2011\u00ad]")
_BROKEN_LIGATURE_RE = re.compile(r"(?:^|\s)[if](?=\s+[A-Z])")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def _clean_extracted_pdf_text(text: str) -> str:
    """Normalize non-breaking hyphens and stray broken-ligature glyphs from PDF text layers."""
    if not text:
        return ""
    cleaned = _HYPHENS_RE.sub("-", text)
    cleaned = _BROKEN_LIGATURE_RE.sub(" ", cleaned)
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned)
    return cleaned.strip()


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


def _extract_cell_text_from_words(
    page_words: list[tuple],
    cell_bbox: tuple[float, float, float, float] | list[float] | None,
    fallback_text: str,
) -> str:
    """
    Reconstruct table cell text using physical word bounding boxes from `page.get_text('words')`.
    Prevents merged-word artifacts caused by tight kerning in PDF table cells.
    """
    if not cell_bbox or not page_words:
        return _clean_extracted_pdf_text(fallback_text.replace("\n", " "))

    cx0, cy0, cx1, cy1 = cell_bbox
    matched = [
        w for w in page_words
        if (cx0 - 1.0) <= ((w[0] + w[2]) * 0.5) <= (cx1 + 1.0)
        and (cy0 - 1.0) <= ((w[1] + w[3]) * 0.5) <= (cy1 + 1.0)
    ]
    if not matched:
        return _clean_extracted_pdf_text(fallback_text.replace("\n", " "))

    # Sort top-to-bottom (bucketed by 3pt line height), then left-to-right
    matched.sort(key=lambda w: (round(w[1] / 3.0), w[0]))
    joined = " ".join(str(w[4]) for w in matched if str(w[4]).strip())
    return _clean_extracted_pdf_text(joined)


def _join_line_spans(spans: list[dict]) -> str:
    """
    Join spans on the same line, inserting a space when horizontal bbox gap exceeds
    ~0.22 * font_size to avoid merged words across adjacent PDF text spans.
    """
    if not spans:
        return ""

    parts: list[str] = []
    prev_x1: float | None = None

    for span in spans:
        raw = span.get("text") or ""
        if not raw:
            continue

        bbox = span.get("bbox")
        font_size = float(span.get("size") or 10.0)
        curr_x0 = float(bbox[0]) if bbox and len(bbox) >= 4 else None
        curr_x1 = float(bbox[2]) if bbox and len(bbox) >= 4 else None

        if (
            parts
            and prev_x1 is not None
            and curr_x0 is not None
            and not parts[-1][-1].isspace()
            and not raw[0].isspace()
        ):
            gap = curr_x0 - prev_x1
            if gap > max(1.5, font_size * 0.22):
                parts.append(" ")

        parts.append(raw)
        if curr_x1 is not None:
            prev_x1 = curr_x1

    return _clean_extracted_pdf_text("".join(parts))


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
            page_words = page.get_text("words")

            # 1. Extract tables first using word-level cell reconstruction
            tab_finder = page.find_tables()
            table_bboxes: list[list[float]] = []
            tables_on_page: list[dict] = []

            for t in tab_finder.tables:
                raw_extract = t.extract()
                if not raw_extract:
                    continue
                table_bboxes.append(list(t.bbox))

                # Reconstruct every row's cells via cell bounding boxes + page_words
                reconstructed_rows: list[list[str]] = []
                t_rows = getattr(t, "rows", None) or []
                for r_idx, raw_row in enumerate(raw_extract):
                    row_cells_bbox = (
                        t_rows[r_idx].cells
                        if r_idx < len(t_rows) and getattr(t_rows[r_idx], "cells", None)
                        else []
                    )
                    clean_row: list[str] = []
                    for c_idx, raw_cell in enumerate(raw_row):
                        c_bbox = row_cells_bbox[c_idx] if c_idx < len(row_cells_bbox) else None
                        fallback = str(raw_cell or "")
                        clean_row.append(_extract_cell_text_from_words(page_words, c_bbox, fallback))
                    reconstructed_rows.append(clean_row)

                header_candidates = reconstructed_rows[0] if reconstructed_rows else []
                if not any(header_candidates):
                    num_cols = len(raw_extract[0])
                    headers = [f"col_{i+1}" for i in range(num_cols)]
                else:
                    seen: dict[str, int] = {}
                    headers = []
                    for idx, h in enumerate(header_candidates, start=1):
                        name = h.strip() or f"col_{idx}"
                        if name in seen:
                            seen[name] += 1
                            name = f"{name}_{seen[name]}"
                        else:
                            seen[name] = 1
                        headers.append(name)

                num_cols = len(headers)
                data_rows: list[list[str]] = []
                for row in reconstructed_rows[1:]:
                    padded = list(row)
                    if len(padded) < num_cols:
                        padded.extend([""] * (num_cols - len(padded)))
                    else:
                        padded = padded[:num_cols]
                    if any(cell.strip() for cell in padded):
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

            # 2. Extract text blocks from layout dict with span-gap spacing
            page_dict = page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT)
            text_blocks_on_page: list[dict] = []

            for b in page_dict.get("blocks", []):
                if "lines" not in b:
                    continue
                b_bbox = [round(float(c), 2) for c in b["bbox"]]

                # Skip if block is inside an extracted table
                if any(_bbox_overlap(b_bbox, t_bbox) for t_bbox in table_bboxes):
                    continue

                full_lines: list[str] = []
                max_size = 0.0
                is_bold = False
                is_mono = False

                for line in b["lines"]:
                    line_text = _join_line_spans(line.get("spans", []))
                    if line_text:
                        full_lines.append(line_text)
                    for span in line.get("spans", []):
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