"""
Markdown Parser: extracts .md documents into HEADING, PARAGRAPH, LIST, TABLE, and CODE blocks.
Complies with schema_parsing.py and uses helpers from parser_utils.py.
"""
import re
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

from parser_utils import build_block, read_text, run_cli
from Ingestion.schema_ingestion import DocumentVersionSchema, FileFormat
from schema_parsing import BlockType, ContentBlockSchema, LineLocator

PARSER_NAME = "markdown-it-py"
PARSER_VERSION = "4.0.0"

_HEADING_CLEAN = re.compile(r"^#+\s*")
_IMAGE_OR_LINK_TAG = re.compile(r"!\[.*?\]\(.*?\)(\{:.*?\})?")


def _clean_heading_title(raw_title: str) -> str:
    """Strip markdown '#' characters and image/icon tags to obtain a clean title for heading_path."""
    cleaned = _HEADING_CLEAN.sub("", raw_title).strip()
    cleaned = _IMAGE_OR_LINK_TAG.sub("", cleaned).strip()
    return cleaned or raw_title.strip()


class MarkdownParser:
    file_format = FileFormat.MARKDOWN.value

    def __init__(self):
        # Disable linkify to avoid requiring linkify-it-py; enable table to support GFM pipe tables
        self.md = MarkdownIt().disable("linkify").enable("table")

    def parse(self, version: DocumentVersionSchema) -> list[ContentBlockSchema]:
        raw_text = read_text(version)
        lines = raw_text.splitlines()
        if not raw_text.strip():
            raise ValueError("Markdown file is empty")

        tokens = self.md.parse(raw_text)
        blocks: list[ContentBlockSchema] = []
        heading_stack: list[tuple[int, str]] = []

        i = 0
        n = len(tokens)
        while i < n:
            token = tokens[i]

            if token.type == "heading_open":
                level = int(token.tag[1]) if len(token.tag) > 1 and token.tag[1].isdigit() else 1
                inline_token = tokens[i + 1] if i + 1 < n and tokens[i + 1].type == "inline" else None
                heading_title = _clean_heading_title(inline_token.content if inline_token else "")

                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, heading_title))
                current_heading_path = [h[1] for h in heading_stack]

                start_idx, end_idx = token.map[0], token.map[1]
                text_content = "\n".join(lines[start_idx:end_idx])
                if text_content.strip():
                    blocks.append(build_block(
                        version,
                        block_idx=len(blocks),
                        block_type=BlockType.HEADING.value,
                        text_content=text_content,
                        locator=LineLocator(start_line=start_idx + 1, end_line=end_idx),
                        parser_name=PARSER_NAME,
                        parser_version=PARSER_VERSION,
                        heading_level=level,
                        heading_path=current_heading_path,
                    ))
                while i < n and tokens[i].type != "heading_close":
                    i += 1
                i += 1
                continue

            if token.type == "fence":
                start_idx, end_idx = token.map[0], token.map[1]
                text_content = "\n".join(lines[start_idx:end_idx])
                code_lang = token.info.strip().split()[0] if token.info.strip() else None
                if text_content.strip():
                    blocks.append(build_block(
                        version,
                        block_idx=len(blocks),
                        block_type=BlockType.CODE.value,
                        text_content=text_content,
                        locator=LineLocator(start_line=start_idx + 1, end_line=end_idx),
                        parser_name=PARSER_NAME,
                        parser_version=PARSER_VERSION,
                        code_language=code_lang,
                        heading_path=[h[1] for h in heading_stack],
                    ))
                i += 1
                continue

            if token.type in ("bullet_list_open", "ordered_list_open"):
                close_type = "bullet_list_close" if token.type == "bullet_list_open" else "ordered_list_close"
                start_idx, end_idx = token.map[0], token.map[1]
                text_content = "\n".join(lines[start_idx:end_idx])
                if text_content.strip():
                    blocks.append(build_block(
                        version,
                        block_idx=len(blocks),
                        block_type=BlockType.LIST.value,
                        text_content=text_content,
                        locator=LineLocator(start_line=start_idx + 1, end_line=end_idx),
                        parser_name=PARSER_NAME,
                        parser_version=PARSER_VERSION,
                        heading_path=[h[1] for h in heading_stack],
                    ))
                depth = 1
                i += 1
                while i < n and depth > 0:
                    if tokens[i].type == token.type:
                        depth += 1
                    elif tokens[i].type == close_type:
                        depth -= 1
                    i += 1
                continue

            if token.type == "table_open":
                start_idx, end_idx = token.map[0], token.map[1]
                text_content = "\n".join(lines[start_idx:end_idx])
                headers: list[str] = []
                rows: list[list[str]] = []
                current_row: list[str] = []
                is_header = False

                i += 1
                while i < n and tokens[i].type != "table_close":
                    sub = tokens[i]
                    if sub.type == "thead_open":
                        is_header = True
                    elif sub.type == "thead_close":
                        is_header = False
                    elif sub.type in ("th_open", "td_open"):
                        cell_text = tokens[i + 1].content.strip() if i + 1 < n and tokens[i + 1].type == "inline" else ""
                        if is_header:
                            headers.append(cell_text)
                        else:
                            current_row.append(cell_text)
                    elif sub.type == "tr_close" and not is_header:
                        if current_row:
                            rows.append(current_row)
                            current_row = []
                    i += 1
                i += 1

                num_cols = len(headers)
                if num_cols == 0 and rows:
                    num_cols = max(len(r) for r in rows)
                    headers = [f"col_{c+1}" for c in range(num_cols)]
                normalized_rows = [r + [""] * (num_cols - len(r)) if len(r) < num_cols else r[:num_cols] for r in rows]

                if text_content.strip():
                    blocks.append(build_block(
                        version,
                        block_idx=len(blocks),
                        block_type=BlockType.TABLE.value,
                        text_content=text_content,
                        locator=LineLocator(start_line=start_idx + 1, end_line=end_idx),
                        parser_name=PARSER_NAME,
                        parser_version=PARSER_VERSION,
                        table_header_cols=headers,
                        table_rows=normalized_rows,
                        heading_path=[h[1] for h in heading_stack],
                    ))
                continue

            if token.type == "html_block":
                start_idx, end_idx = token.map[0], token.map[1]
                text_content = "\n".join(lines[start_idx:end_idx])
                if "<table" in text_content.lower():
                    soup = BeautifulSoup(text_content, "html.parser")
                    table = soup.find("table")
                    if table:
                        raw_trs = table.find_all("tr")
                        extracted_rows = []
                        for tr in raw_trs:
                            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                            if cells:
                                extracted_rows.append(cells)
                        if extracted_rows:
                            headers = extracted_rows[0]
                            rows = extracted_rows[1:]
                            num_cols = len(headers)
                            normalized_rows = [r + [""] * (num_cols - len(r)) if len(r) < num_cols else r[:num_cols] for r in rows]
                            blocks.append(build_block(
                                version,
                                block_idx=len(blocks),
                                block_type=BlockType.TABLE.value,
                                text_content=text_content,
                                locator=LineLocator(start_line=start_idx + 1, end_line=end_idx),
                                parser_name=PARSER_NAME,
                                parser_version=PARSER_VERSION,
                                table_header_cols=headers,
                                table_rows=normalized_rows,
                                heading_path=[h[1] for h in heading_stack],
                            ))
                            i += 1
                            continue

                if text_content.strip():
                    blocks.append(build_block(
                        version,
                        block_idx=len(blocks),
                        block_type=BlockType.PARAGRAPH.value,
                        text_content=text_content,
                        locator=LineLocator(start_line=start_idx + 1, end_line=end_idx),
                        parser_name=PARSER_NAME,
                        parser_version=PARSER_VERSION,
                        heading_path=[h[1] for h in heading_stack],
                    ))
                i += 1
                continue

            if token.type == "paragraph_open":
                if token.map:
                    start_idx, end_idx = token.map[0], token.map[1]
                    text_content = "\n".join(lines[start_idx:end_idx])
                    if text_content.strip():
                        blocks.append(build_block(
                            version,
                            block_idx=len(blocks),
                            block_type=BlockType.PARAGRAPH.value,
                            text_content=text_content,
                            locator=LineLocator(start_line=start_idx + 1, end_line=end_idx),
                            parser_name=PARSER_NAME,
                            parser_version=PARSER_VERSION,
                            heading_path=[h[1] for h in heading_stack],
                        ))
                while i < n and tokens[i].type != "paragraph_close":
                    i += 1
                i += 1
                continue

            i += 1

        if not blocks:
            raise ValueError("Markdown produced no content blocks")
        return blocks


if __name__ == "__main__":
    run_cli(MarkdownParser())
