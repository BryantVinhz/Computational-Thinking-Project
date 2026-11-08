"""
TXT Parser: splits plain text into PARAGRAPH / LIST / CODE blocks separated by blank lines.
TXT lacks heading syntax so it never generates HEADING (not allowed by schema either).
"""
import re

from parser_utils import build_block, read_text, run_cli
from Ingestion.schema_ingestion import DocumentVersionSchema, FileFormat
from schema_parsing import BlockType, ContentBlockSchema, LineLocator

PARSER_NAME = "txt-rule-parser"
PARSER_VERSION = "1.0.0"  # increment when block segmentation rules change

_FENCE = re.compile(r"^\s*(```|~~~)\s*([\w+#.-]*)")
_BULLET = re.compile(r"^\s*([-*•+]|\d+[.)]|[a-zA-Z][.)])\s+")


def _is_blank(line: str) -> bool:
    return not line.strip()


def _is_indented_code(line: str) -> bool:
    return line.startswith(("    ", "\t"))


class TxtParser:
    file_format = FileFormat.TXT.value

    def parse(self, version: DocumentVersionSchema) -> list[ContentBlockSchema]:
        lines = read_text(version).splitlines()
        if all(_is_blank(line) for line in lines):
            raise ValueError("TXT has no content")

        blocks: list[ContentBlockSchema] = []
        for block_type, start, end, code_language in self._segment(lines):
            # start/end are 0-based inclusive indices; locator uses 1-based line numbers
            blocks.append(build_block(
                version,
                block_idx=len(blocks),
                block_type=block_type,
                text_content="\n".join(lines[start:end + 1]),
                locator=LineLocator(start_line=start + 1, end_line=end + 1),
                parser_name=PARSER_NAME,
                parser_version=PARSER_VERSION,
                code_language=code_language,
            ))
        return blocks

    def _segment(self, lines: list[str]) -> list[tuple[str, int, int, str | None]]:
        """Return list of (block_type, start_idx, end_idx, code_language)."""
        segments = []
        i, n = 0, len(lines)
        while i < n:
            if _is_blank(lines[i]):
                i += 1
                continue

            fence = _FENCE.match(lines[i])
            if fence:
                # Fenced code has priority over all rules: includes blank lines inside; unclosed -> extends to EOF
                marker = fence.group(1)
                end = next((j for j in range(i + 1, n) if lines[j].lstrip().startswith(marker)), n - 1)
                segments.append((BlockType.CODE.value, i, end, fence.group(2) or None))
                i = end + 1
                continue

            # Group = consecutive non-blank lines, stops before blank lines or new fence
            start = i
            while i < n and not _is_blank(lines[i]) and not _FENCE.match(lines[i]):
                i += 1
            segments.extend(self._classify_group(lines, start, i - 1))
        return segments

    @staticmethod
    def _classify_group(lines: list[str], start: int, end: int) -> list[tuple[str, int, int, None]]:
        """Classify a line group; splits into multiple blocks if alternating paragraphs and lists."""
        if all(_is_indented_code(lines[j]) for j in range(start, end + 1)):
            return [(BlockType.CODE.value, start, end, None)]

        # Label each line: bullets and indented lines immediately following bullets (continuation lines) are lists
        kinds = []
        for j in range(start, end + 1):
            line = lines[j]
            is_continuation = bool(kinds) and kinds[-1] == BlockType.LIST.value and line[:1].isspace()
            is_list = _BULLET.match(line) or is_continuation
            kinds.append(BlockType.LIST.value if is_list else BlockType.PARAGRAPH.value)

        # Merge consecutive lines with the same label into 1 block
        result = []
        run_start = start
        for offset in range(1, len(kinds) + 1):
            if offset == len(kinds) or kinds[offset] != kinds[offset - 1]:
                result.append((kinds[offset - 1], run_start, start + offset - 1, None))
                run_start = start + offset
        return result


if __name__ == "__main__":
    run_cli(TxtParser())
