"""Step 3: Text Cleaning & Normalization for RAG pipeline."""
from __future__ import annotations

import argparse
import copy
import json
import logging
import re
import sys
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

try:
    from path import NORMALIZATION_IN_DIR, NORMALIZATION_OUT_DIR, PARSING_OUT_DIR
except ImportError:
    PARSING_OUT_DIR = PARENT_DIR / "output" / "Parsing"
    NORMALIZATION_IN_DIR = PARSING_OUT_DIR
    NORMALIZATION_OUT_DIR = PARENT_DIR / "output" / "Normalization"

LOCAL_INPUT_DIR = CURRENT_DIR / "input"
LOCAL_OUTPUT_DIR = CURRENT_DIR / "output"

logger = logging.getLogger(__name__)


class _ExactHTMLSpanParser(HTMLParser):
    """HTML parser that partitions text into exact markup, protected content, and safe text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.segments: list[tuple[str, int, int, str]] = []
        self.cursor: int = 0
        self.protected_depth: int = 0
        self.protected_tags: frozenset[str] = frozenset({"script", "style", "pre", "code"})

    def parse_starttag(self, i: int) -> int:
        if i > self.cursor:
            self._add_text(self.cursor, i)
        k = super().parse_starttag(i)
        if k > 0:
            tag = (self.lasttag or "").lower()
            text = self.rawdata[i:k]
            is_self_closing = text.rstrip().endswith("/>")
            if self.protected_depth > 0:
                self.segments.append(("protected", i, k, text))
            else:
                if tag in self.protected_tags and not is_self_closing:
                    self.protected_depth += 1
                self.segments.append(("markup", i, k, text))
            self.cursor = k
        return k

    def parse_endtag(self, i: int) -> int:
        if i > self.cursor:
            self._add_text(self.cursor, i)
        k = super().parse_endtag(i)
        if k > 0:
            text = self.rawdata[i:k]
            inner = text[2:-1].strip()
            end_tag = inner.split()[0].lower() if inner else ""
            if end_tag in self.protected_tags and self.protected_depth > 0:
                self.protected_depth -= 1
                self.segments.append(("markup", i, k, text))
            elif self.protected_depth > 0:
                self.segments.append(("protected", i, k, text))
            else:
                self.segments.append(("markup", i, k, text))
            self.cursor = k
        return k

    def parse_comment(self, i: int, report: int = 1) -> int:
        if i > self.cursor:
            self._add_text(self.cursor, i)
        k = super().parse_comment(i, report)
        if k > 0:
            text = self.rawdata[i:k]
            self.segments.append(("protected", i, k, text))
            self.cursor = k
        return k

    def parse_html_declaration(self, i: int) -> int:
        if i > self.cursor:
            self._add_text(self.cursor, i)
        k = super().parse_html_declaration(i)
        if k > 0:
            text = self.rawdata[i:k]
            self.segments.append(("protected", i, k, text))
            self.cursor = k
        return k

    def parse_bogus_comment(self, i: int, report: int = 1) -> int:
        if i > self.cursor:
            self._add_text(self.cursor, i)
        k = super().parse_bogus_comment(i, report)
        if k > 0:
            text = self.rawdata[i:k]
            self.segments.append(("protected", i, k, text))
            self.cursor = k
        return k

    def parse_pi(self, i: int) -> int:
        if i > self.cursor:
            self._add_text(self.cursor, i)
        k = super().parse_pi(i)
        if k > 0:
            text = self.rawdata[i:k]
            self.segments.append(("protected", i, k, text))
            self.cursor = k
        return k

    def _add_text(self, start: int, end: int) -> None:
        if end <= start:
            return
        text = self.rawdata[start:end]
        if self.protected_depth > 0:
            self.segments.append(("protected", start, end, text))
        else:
            self.segments.append(("text", start, end, text))
        self.cursor = end

    def finalize(self) -> None:
        if self.cursor < len(self.rawdata):
            self._add_text(self.cursor, len(self.rawdata))


class TextNormalizer:
    SAFETY_PATTERNS: list[str] = [
        r"<script",
        r"union\s+select",
        r"\.\./",
        r"%2e%2e|%252e",
        r"'\s*or\s*'",
        r"javascript:",
        r"\bon(?:error|load|click)\s*=",
        r"<(?:svg|img|iframe)",
        r"\bdrop\s+table",
        r"\bor\s+1\s*=\s*1",
        r"etc/passwd",
        r"%3cscript",
        r"\b(?:GET|POST|PUT|DELETE)\s+/\S*\s+HTTP/\d",
    ]
    SAFETY_NET_RE: str = "|".join(SAFETY_PATTERNS)

    PROTECT_RE: re.Pattern[str] = re.compile(
        r"(?m)"
        r"(?:^(`{3,}|~{3,})[^\n]*\n.*?^\1[ \t]*$)"
        r"|"
        r"(?:`[^`\n]+`)"
        r"|"
        # Targeted protection for HTML/script/code/pre/style tags, events, HTTP requests, SQL, and path traversals
        r"(?:<(?:script|style|pre|code|svg|iframe)\b[^>]*>.*?</(?:script|style|pre|code|svg|iframe)>|<(?:script|style|pre|code|svg|img|iframe)\b[^>]*/>|<img\b[^>]*>)"
        r"|"
        r"(?:\bon(?:error|load|click)[ \t]*=[ \t]*(?:'[^'\n]*'|\"[^\"\n]*\"|\S+))"
        r"|"
        r"(?:\b(?:GET|POST|PUT|DELETE)[ \t]+/\S*[ \t]+HTTP/\d(?:\.\d)?)"
        r"|"
        r"(?:\bunion[ \t]+(?:all[ \t]+)?select\b|\bdrop[ \t]+table\b(?:[ \t]+\S+)?|['\"][ \t]*or[ \t]*['\"](?:\S*[ \t]*=[ \t]*\S+)?|\bor[ \t]+1[ \t]*=[ \t]*1\b)"
        r"|"
        r"(?:\S{0,200}(?:\.\./|%2e%2e|%252e|etc/passwd|javascript:|%3cscript)\S{0,200})",
        re.IGNORECASE | re.DOTALL,
    )

    PLACEHOLDER_RE: re.Pattern[str] = re.compile(r"\ue000(\d+)\ue001")
    HTML_TABLE_RE: re.Pattern[str] = re.compile(
        r"<\s*(?:table|thead|tbody|tfoot|tr|th|td|colgroup|col|caption)\b",
        re.IGNORECASE,
    )

    KEEP_HYPHEN_PREFIXES: frozenset[str] = frozenset({
        "cross",
        "non",
        "anti",
        "pre",
        "post",
        "self",
        "same",
        "out",
        "in",
        "man",
        "single",
        "multi",
        "client",
        "server",
        "user",
        "third",
        "open",
        "real",
        "least",
        "role",
        "time",
        "case",
    })
    PDF_HYPHEN_RE: re.Pattern[str] = re.compile(
        r"([A-Za-z]+)-\n(?=([a-z]+(?:-[A-Za-z]+)+|[a-z]))"
    )

    LIGATURE_MAP: dict[int, str] = str.maketrans({
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬀ": "ff",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
    })
    UNICODE_EXOTIC_SPACES: re.Pattern[str] = re.compile(
        r"[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]"
    )
    ZERO_WIDTH_AND_BOM: re.Pattern[str] = re.compile(r"[\u200b\u2060\ufeff\u00ad]")
    CONTROL_CHARS: re.Pattern[str] = re.compile(
        r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"
    )
    MULTI_BLANK_RE: re.Pattern[str] = re.compile(r"\n{3,}")
    SPACES_RE: re.Pattern[str] = re.compile(r"[ \t]+")

    @staticmethod
    def normalize_newlines(text: str) -> str:
        """Normalize line endings to standard Unix newline."""
        return text.replace("\r\n", "\n").replace("\r", "\n")

    @staticmethod
    def normalize_unicode(text: str) -> str:
        """Normalize Unicode with NFC, translate ligatures, and strip control and zero-width chars."""
        text = unicodedata.normalize("NFC", text)
        text = text.translate(TextNormalizer.LIGATURE_MAP)
        text = TextNormalizer.UNICODE_EXOTIC_SPACES.sub(" ", text)
        text = TextNormalizer.ZERO_WIDTH_AND_BOM.sub("", text)
        text = TextNormalizer.CONTROL_CHARS.sub("", text)
        return text

    @staticmethod
    def collapse_spaces(text: str) -> str:
        """Collapse runs of horizontal spaces while preserving line indentation."""
        lines: list[str] = []
        for line in text.split("\n"):
            stripped = line.rstrip(" \t")
            if not stripped:
                lines.append("")
                continue
            indent_len = len(line) - len(line.lstrip(" \t"))
            indent = line[:indent_len]
            content = TextNormalizer.SPACES_RE.sub(" ", line[indent_len:]).rstrip(" \t")
            lines.append(indent + content)
        return "\n".join(lines)

    @staticmethod
    def collapse_blank_lines(text: str) -> str:
        """Reduce 3+ consecutive newlines to 2 and strip surrounding blank lines."""
        text = TextNormalizer.MULTI_BLANK_RE.sub("\n\n", text)
        return text.strip("\n")

    @staticmethod
    def fix_pdf_hyphens(text: str) -> str:
        """Join PDF line-broken words, preserving hyphen for compound prefixes or hyphenated suffixes."""
        def _repl(m: re.Match[str]) -> str:
            word = m.group(1)
            suffix_compound = m.group(2)
            if word.lower() in TextNormalizer.KEEP_HYPHEN_PREFIXES:
                return f"{word}-"
            if suffix_compound and "-" in suffix_compound:
                return f"{word}-"
            return word

        return TextNormalizer.PDF_HYPHEN_RE.sub(_repl, text)

    def normalize_text(self, text: str, *, is_pdf: bool = False) -> str:
        """Apply cleaning rules in a fixed order ensuring idempotence."""
        text = self.normalize_newlines(text)
        text = self.normalize_unicode(text)
        text = self.collapse_spaces(text)
        text = self.collapse_blank_lines(text)
        if is_pdf:
            text = self.fix_pdf_hyphens(text)
        return text

    @staticmethod
    def mask(text: str) -> tuple[str, list[str]]:
        """Replace protected code/payload matches with indexed placeholders."""
        spans: list[str] = []

        def _repl(m: re.Match[str]) -> str:
            idx = len(spans)
            spans.append(m.group(0))
            return f"\ue000{idx}\ue001"

        masked_text = TextNormalizer.PROTECT_RE.sub(_repl, text)
        return masked_text, spans

    @staticmethod
    def unmask(text: str, spans: list[str]) -> str | None:
        """Restore placeholders in a single pass without rescanning restored text."""
        if not spans:
            return text
        found = TextNormalizer.PLACEHOLDER_RE.findall(text)
        if len(found) != len(spans):
            return None
        if set(int(i) for i in found) != set(range(len(spans))):
            return None

        return TextNormalizer.PLACEHOLDER_RE.sub(lambda m: spans[int(m.group(1))], text)

    @classmethod
    def is_html_table(cls, text: str) -> bool:
        """Check if text contains HTML table markup."""
        if not text or not isinstance(text, str):
            return False
        return cls.HTML_TABLE_RE.search(text) is not None

    def clean_html_table(self, text: str, *, is_pdf: bool = False) -> tuple[str, bool]:
        """Clean HTML table text nodes safely while preserving markup and attributes byte-for-byte."""
        if not text:
            return "", False
        if "\ue000" in text or "\ue001" in text:
            return text, False

        normalized_nl = self.normalize_newlines(text)
        try:
            parser = _ExactHTMLSpanParser()
            parser.feed(normalized_nl)
            parser.finalize()
        except Exception as exc:
            logger.debug("Failed parsing HTML table, preserving raw text: %s", exc)
            return text, False

        if "".join(s[3] for s in parser.segments) != normalized_nl:
            return text, False

        masked_parts: list[str] = []
        spans: list[str] = []
        has_protected = False

        for kind, start, end, seg_text in parser.segments:
            if kind in ("markup", "protected"):
                idx = len(spans)
                spans.append(seg_text)
                masked_parts.append(f"\ue000{idx}\ue001")
                if kind == "protected":
                    has_protected = True
            else:
                def _repl_text(m: re.Match[str]) -> str:
                    nonlocal has_protected
                    has_protected = True
                    idx = len(spans)
                    spans.append(m.group(0))
                    return f"\ue000{idx}\ue001"

                masked_seg = self.PROTECT_RE.sub(_repl_text, seg_text)
                masked_parts.append(masked_seg)

        masked_text = "".join(masked_parts)
        cleaned = self.normalize_text(masked_text, is_pdf=is_pdf)
        unmasked = self.unmask(cleaned, spans)

        if unmasked is None:
            return text, has_protected
        return unmasked, has_protected

    def clean_text_payload_safe(self, text: str, *, is_pdf: bool = False) -> tuple[str, bool]:
        """Clean text while preserving code and payloads byte-for-byte."""
        if not text:
            return "", False
        if "\ue000" in text or "\ue001" in text:
            return text, False

        if self.is_html_table(text) and not text.lstrip().startswith(("```", "~~~")):
            return self.clean_html_table(text, is_pdf=is_pdf)

        # Normalize newlines before masking so CRLF does not break code fences.
        text = self.normalize_newlines(text)
        masked, spans = self.mask(text)
        cleaned = self.normalize_text(masked, is_pdf=is_pdf)
        unmasked = self.unmask(cleaned, spans)

        if unmasked is None:
            return text, len(spans) > 0
        return unmasked, len(spans) > 0

    def _clean_cell(self, cell: Any, *, is_pdf: bool) -> tuple[Any, bool, bool]:
        """Clean a table cell value if string, otherwise return untouched."""
        if not isinstance(cell, str):
            return cell, False, False
        clean_val, is_payload = self.clean_text_payload_safe(cell, is_pdf=is_pdf)
        return clean_val, is_payload, clean_val != cell

    @staticmethod
    def _build_output(
        block: dict[str, Any],
        raw: str,
        normalized: str,
        is_payload: bool,
        has_modified: bool | None = None,
        delta: int | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Build normalized block preserving all original Step 2 keys."""
        out = copy.deepcopy(block)
        char_delta = (len(raw) - len(normalized)) if delta is None else delta
        modified = (raw != normalized) if has_modified is None else has_modified

        block_id = block.get("block_id")
        if block_id:
            norm_id = f"nrm_{block_id}"
        else:
            doc_id = block.get("document_id") or "doc"
            norm_id = f"nrm_{doc_id}_{block.get('block_idx')}"

        out.update({
            "normalized_id": norm_id,
            "normalized_text": normalized,
            "normalization_flags": {
                "is_code_or_payload": is_payload,
                "has_modified": modified,
                "char_count_delta": char_delta,
            },
        })
        if error is not None:
            out["normalization_error"] = error
        return out

    def normalize_block(self, block: dict[str, Any]) -> dict[str, Any]:
        """Normalize a single content block dictionary."""
        if not block.get("block_id"):
            logger.warning(
                "Missing block_id (document_id=%s, block_idx=%s)",
                block.get("document_id"),
                block.get("block_idx"),
            )

        locator = block.get("locator") or block.get("source_locator") or {}
        file_format = str(block.get("file_format") or "").strip().upper()
        if file_format:
            is_pdf = file_format == "PDF"
        else:
            parser_info = f"{block.get('parser_name') or ''} {block.get('parser_type') or ''}".lower()
            is_pdf = "pdf" in parser_info or locator.get("page_number") is not None

        raw_text = block.get("text_content") or ""
        block_type = block.get("block_type") or ""

        metadata = block.get("metadata") or {}
        code_language = block.get("code_language") or metadata.get("code_language")

        if block_type == "code" or bool(code_language):
            normalized_text = raw_text
            is_code_or_payload = True
        elif block_type == "table" and self.is_html_table(raw_text):
            normalized_text, is_code_or_payload = self.clean_html_table(
                raw_text, is_pdf=is_pdf
            )
        else:
            normalized_text, is_code_or_payload = self.clean_text_payload_safe(
                raw_text, is_pdf=is_pdf
            )

        norm_table_header_cols: list[Any] | None = None
        norm_table_rows: list[Any] | None = None
        normalized_sc: dict[str, Any] | None = None
        any_cell_changed = False
        table_delta = 0

        if block_type == "table":
            raw_headers = block.get("table_header_cols")
            if isinstance(raw_headers, list):
                clean_headers: list[Any] = []
                for h in raw_headers:
                    clean_h, cell_payload, cell_changed = self._clean_cell(
                        h, is_pdf=is_pdf
                    )
                    clean_headers.append(clean_h)
                    if cell_payload:
                        is_code_or_payload = True
                    if cell_changed:
                        any_cell_changed = True
                    if isinstance(h, str):
                        table_delta += len(h) - len(clean_h)
                norm_table_header_cols = clean_headers

            raw_rows = block.get("table_rows")
            if isinstance(raw_rows, list):
                clean_rows: list[Any] = []
                for row_or_cell in raw_rows:
                    if isinstance(row_or_cell, list):
                        clean_row: list[Any] = []
                        for cell in row_or_cell:
                            clean_c, cell_payload, cell_changed = self._clean_cell(
                                cell, is_pdf=is_pdf
                            )
                            clean_row.append(clean_c)
                            if cell_payload:
                                is_code_or_payload = True
                            if cell_changed:
                                any_cell_changed = True
                            if isinstance(cell, str):
                                table_delta += len(cell) - len(clean_c)
                        clean_rows.append(clean_row)
                    else:
                        clean_c, cell_payload, cell_changed = self._clean_cell(
                            row_or_cell, is_pdf=is_pdf
                        )
                        clean_rows.append(clean_c)
                        if cell_payload:
                            is_code_or_payload = True
                        if cell_changed:
                            any_cell_changed = True
                        if isinstance(row_or_cell, str):
                            table_delta += len(row_or_cell) - len(clean_c)
                norm_table_rows = clean_rows

            sc = block.get("structured_content")
            if isinstance(sc, dict):
                normalized_sc = copy.deepcopy(sc)

                for header_key in ("header_cols", "headers"):
                    if header_key in sc and isinstance(sc[header_key], list):
                        cleaned_sc_headers: list[Any] = []
                        for h in sc[header_key]:
                            clean_h, cell_payload, cell_changed = self._clean_cell(
                                h, is_pdf=is_pdf
                            )
                            cleaned_sc_headers.append(clean_h)
                            if cell_payload:
                                is_code_or_payload = True
                            if cell_changed:
                                any_cell_changed = True
                            if isinstance(h, str):
                                table_delta += len(h) - len(clean_h)
                        normalized_sc[header_key] = cleaned_sc_headers

                if "rows" in sc and isinstance(sc["rows"], list):
                    cleaned_sc_rows: list[Any] = []
                    for row in sc["rows"]:
                        if isinstance(row, list):
                            clean_sc_row: list[Any] = []
                            for cell in row:
                                clean_c, cell_payload, cell_changed = self._clean_cell(
                                    cell, is_pdf=is_pdf
                                )
                                clean_sc_row.append(clean_c)
                                if cell_payload:
                                    is_code_or_payload = True
                                if cell_changed:
                                    any_cell_changed = True
                                if isinstance(cell, str):
                                    table_delta += len(cell) - len(clean_c)
                            cleaned_sc_rows.append(clean_sc_row)
                        else:
                            clean_c, cell_payload, cell_changed = self._clean_cell(
                                row, is_pdf=is_pdf
                            )
                            cleaned_sc_rows.append(clean_c)
                            if cell_payload:
                                is_code_or_payload = True
                            if cell_changed:
                                any_cell_changed = True
                            if isinstance(row, str):
                                table_delta += len(row) - len(clean_c)
                    normalized_sc["rows"] = cleaned_sc_rows

        # Character count delta captures text reductions in text_content and table cells.
        char_count_delta = (len(raw_text) - len(normalized_text)) + table_delta
        has_modified = (raw_text != normalized_text) or any_cell_changed

        out = self._build_output(
            block,
            raw=raw_text,
            normalized=normalized_text,
            is_payload=is_code_or_payload,
            has_modified=has_modified,
            delta=char_count_delta,
        )

        if norm_table_header_cols is not None:
            out["normalized_table_header_cols"] = norm_table_header_cols
        if norm_table_rows is not None:
            out["normalized_table_rows"] = norm_table_rows
        if normalized_sc is not None:
            out["normalized_structured_content"] = normalized_sc

        return out

    def normalize_document(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize a document preserving block order and isolating per-block errors."""
        results: list[dict[str, Any]] = []
        for block in blocks:
            try:
                results.append(self.normalize_block(block))
            except Exception as exc:
                block_id = block.get("block_id", "unknown")
                logger.warning("Error normalizing block %s: %s", block_id, exc)
                raw_text = block.get("text_content") or ""
                out = self._build_output(
                    block,
                    raw=raw_text,
                    normalized=raw_text,
                    is_payload=False,
                    error=str(exc),
                )
                if block.get("block_type") == "table":
                    if isinstance(block.get("table_header_cols"), list):
                        out["normalized_table_header_cols"] = copy.deepcopy(block["table_header_cols"])
                    if isinstance(block.get("table_rows"), list):
                        out["normalized_table_rows"] = copy.deepcopy(block["table_rows"])
                    sc = block.get("structured_content")
                    if isinstance(sc, dict):
                        out["normalized_structured_content"] = copy.deepcopy(sc)
                results.append(out)
        return results

    @staticmethod
    def _load_blocks_with_stats(path: Path) -> tuple[list[dict[str, Any]], int]:
        """Internal helper to load blocks and count skipped invalid items."""
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".jsonl":
            non_empty_lines = 0
            skipped_count = 0
            valid_blocks: list[dict[str, Any]] = []
            for line_no, line in enumerate(text.splitlines(), start=1):
                line_str = line.strip()
                if not line_str:
                    continue
                non_empty_lines += 1
                try:
                    parsed = json.loads(line_str)
                except json.JSONDecodeError as exc:
                    logger.warning("Invalid JSON at line %d in %s: %s", line_no, path, exc)
                    skipped_count += 1
                    continue
                if isinstance(parsed, dict):
                    valid_blocks.append(parsed)
                else:
                    logger.warning(
                        "Skipping non-dict block at line %d in %s: expected dict, got %s",
                        line_no,
                        path,
                        type(parsed).__name__,
                    )
                    skipped_count += 1
            if non_empty_lines > 0 and not valid_blocks:
                raise ValueError(f"No valid block dictionaries found in {path}")
            return valid_blocks, skipped_count

        data = json.loads(text)
        raw_items: list[Any] | None = None
        if isinstance(data, list):
            raw_items = data
        elif isinstance(data, dict):
            if "blocks" in data and isinstance(data["blocks"], list):
                raw_items = data["blocks"]
            elif "content_blocks" in data and isinstance(data["content_blocks"], list):
                raw_items = data["content_blocks"]
            elif "block_id" in data or "text_content" in data:
                raw_items = [data]
        if raw_items is None:
            raise ValueError(f"Unsupported JSON structure in {path}")

        valid_blocks: list[dict[str, Any]] = []
        skipped_count = 0
        for idx, item in enumerate(raw_items):
            if isinstance(item, dict):
                valid_blocks.append(item)
            else:
                logger.warning(
                    "Skipping non-dict block at index %d in %s: expected dict, got %s",
                    idx,
                    path,
                    type(item).__name__,
                )
                skipped_count += 1
        if raw_items and not valid_blocks:
            raise ValueError(f"No valid block dictionaries found in {path}")
        return valid_blocks, skipped_count

    @staticmethod
    def load_blocks(path: Path) -> list[dict[str, Any]]:
        """Load content blocks from a JSON or JSONL file."""
        blocks, _ = TextNormalizer._load_blocks_with_stats(path)
        return blocks

    @staticmethod
    def save_blocks(blocks: list[dict[str, Any]], path: Path) -> None:
        """Save normalized blocks to a JSON or JSONL file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() == ".jsonl":
            with path.open("w", encoding="utf-8") as f:
                for b in blocks:
                    f.write(json.dumps(b, ensure_ascii=False) + "\n")
        else:
            with path.open("w", encoding="utf-8") as f:
                json.dump(blocks, f, ensure_ascii=False, indent=2)

    def normalize_file(self, input_path: Path | str, output_path: Path | str) -> dict[str, Any]:
        """Normalize blocks in a single JSON or JSONL file and write to output path."""
        in_p = Path(input_path)
        out_p = Path(output_path)
        blocks, skipped_count = self._load_blocks_with_stats(in_p)
        results = self.normalize_document(blocks)
        self.save_blocks(results, out_p)

        total = len(results)
        protected = sum(
            1 for b in results if b.get("normalization_flags", {}).get("is_code_or_payload")
        )
        modified = sum(
            1 for b in results if b.get("normalization_flags", {}).get("has_modified")
        )
        total_delta = sum(
            b.get("normalization_flags", {}).get("char_count_delta", 0) for b in results
        )
        errors = sum(1 for b in results if "normalization_error" in b)
        status = "partial" if (errors > 0 or skipped_count > 0) else "success"
        stats = {
            "file": in_p.name,
            "input_path": str(in_p),
            "output_path": str(out_p),
            "status": status,
            "total": total,
            "protected": protected,
            "modified": modified,
            "delta": total_delta,
            "errors": errors,
            "skipped": skipped_count,
        }
        logger.info(
            "Normalized %s -> %s (Status: %s | Total: %d | Protected: %d | Modified: %d | Delta: %d | Errors: %d | Skipped: %d)",
            in_p.name,
            out_p.name,
            status,
            total,
            protected,
            modified,
            total_delta,
            errors,
            skipped_count,
        )
        return stats

    def normalize_directory(
        self,
        input_dir: Path | str,
        output_dir: Path | str,
    ) -> list[dict[str, Any]]:
        """Normalize all JSON and JSONL block files from input_dir to output_dir."""
        in_dir = Path(input_dir)
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        if not in_dir.exists():
            logger.warning("Input directory does not exist: %s", in_dir)
            return []

        input_files = sorted(
            [p for p in in_dir.glob("*.json")] + [p for p in in_dir.glob("*.jsonl")]
        )
        if not input_files:
            logger.warning("No JSON or JSONL files found in input directory: %s", in_dir)
            return []

        stats_list: list[dict[str, Any]] = []
        for in_file in input_files:
            name = in_file.name
            if name.endswith("_parsing.json"):
                out_name = name.replace("_parsing.json", "_normalization.json")
            elif name.endswith("_parsing.jsonl"):
                out_name = name.replace("_parsing.jsonl", "_normalization.jsonl")
            else:
                out_name = f"{in_file.stem}_normalized{in_file.suffix}"

            out_file = out_dir / out_name
            try:
                stat = self.normalize_file(in_file, out_file)
                stats_list.append(stat)
            except Exception as exc:
                logger.error("Failed to normalize %s: %s", in_file.name, exc)
                stats_list.append({
                    "file": in_file.name,
                    "input_path": str(in_file),
                    "output_path": str(out_file),
                    "status": "failed",
                    "error": str(exc),
                    "total": 0,
                    "protected": 0,
                    "modified": 0,
                    "delta": 0,
                    "errors": 0,
                    "skipped": 0,
                })

        return stats_list


def _has_blocks(directory: Path) -> bool:
    """Check if directory contains any JSON or JSONL files."""
    if not directory.exists() or not directory.is_dir():
        return False
    return any(directory.glob("*.json")) or any(directory.glob("*.jsonl"))


def main() -> None:
    """Command-line entry point for text normalization."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Step 3: Text Cleaning & Normalization.")
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        default=None,
        help="Input JSON/JSONL file or directory",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output JSON/JSONL file or directory",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity level",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.input is not None and not args.input.exists():
        parser.error(f"Input path does not exist: {args.input}")

    normalizer = TextNormalizer()

    if args.input is not None and args.input.is_file():
        in_file = args.input
        if args.output is not None:
            if args.output.suffix:
                out_file = args.output
            else:
                out_file = args.output / f"{in_file.stem}_normalized{in_file.suffix}"
        else:
            default_out = (
                LOCAL_OUTPUT_DIR
                if in_file.parent == LOCAL_INPUT_DIR
                else NORMALIZATION_OUT_DIR
            )
            out_file = default_out / f"{in_file.stem}_normalized{in_file.suffix}"

        in_dir_display = in_file.parent
        out_dir_display = out_file.parent

        results: list[dict[str, Any]] = []
        try:
            stat = normalizer.normalize_file(in_file, out_file)
            results.append(stat)
        except Exception as exc:
            logger.error("Failed to normalize %s: %s", in_file.name, exc)
            results.append({
                "file": in_file.name,
                "input_path": str(in_file),
                "output_path": str(out_file),
                "status": "failed",
                "error": str(exc),
                "total": 0,
                "protected": 0,
                "modified": 0,
                "delta": 0,
                "errors": 0,
                "skipped": 0,
            })
    else:
        if args.input is None:
            if _has_blocks(NORMALIZATION_IN_DIR):
                in_dir = NORMALIZATION_IN_DIR
                out_dir = NORMALIZATION_OUT_DIR
            elif _has_blocks(LOCAL_INPUT_DIR):
                in_dir = LOCAL_INPUT_DIR
                out_dir = LOCAL_OUTPUT_DIR
            else:
                in_dir = NORMALIZATION_IN_DIR
                out_dir = NORMALIZATION_OUT_DIR
        else:
            in_dir = args.input
            out_dir = (
                LOCAL_OUTPUT_DIR
                if in_dir == LOCAL_INPUT_DIR
                else NORMALIZATION_OUT_DIR
            )

        if args.output is not None:
            out_dir = args.output

        in_dir_display = in_dir
        out_dir_display = out_dir
        results = normalizer.normalize_directory(in_dir, out_dir)

    if not results:
        print("No input files found.")
        sys.exit(1)

    files_success = sum(1 for r in results if r.get("status") == "success")
    files_partial = sum(1 for r in results if r.get("status") == "partial")
    files_failed = sum(1 for r in results if r.get("status") == "failed")
    total_blocks = sum(r.get("total", 0) for r in results)
    total_protected = sum(r.get("protected", 0) for r in results)
    total_modified = sum(r.get("modified", 0) for r in results)
    total_delta = sum(r.get("delta", 0) for r in results)
    total_errors = sum(r.get("errors", 0) for r in results)
    total_skipped = sum(r.get("skipped", 0) for r in results)

    print("=== STEP 3: TEXT NORMALIZATION SUMMARY ===")
    print(f"- Input directory   : {in_dir_display}")
    print(f"- Output directory  : {out_dir_display}")
    print(f"- Files Success/Partial/Failed: {files_success}/{files_partial}/{files_failed}")
    print(f"- Total blocks      : {total_blocks}")
    print(f"- Protected blocks  : {total_protected}")
    print(f"- Modified blocks   : {total_modified}")
    print(f"- Total char delta  : {total_delta}")
    print(f"- Blocks with errors: {total_errors}")
    print(f"- Skipped input items: {total_skipped}")
    for r in results:
        if r.get("status") == "failed":
            print(f"  * FAILED: {r.get('file')} - {r.get('error')}")

    if files_failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
