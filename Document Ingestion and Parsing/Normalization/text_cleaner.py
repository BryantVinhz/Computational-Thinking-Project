"""Step 3: Text Cleaning & Normalization for RAG pipeline."""
from __future__ import annotations

import argparse
import copy
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

# 1. CONSTANTS AND REGEXES

SAFETY_PATTERNS: list[str] = [
    r"<script",
    r"union\s+select",
    r"\.\./",
    r"%2e%2e|%252e",
    r"'\s*or\s*'",
    r"javascript:",
]
SAFETY_NET_RE: str = "|".join(SAFETY_PATTERNS)

PROTECT_RE: re.Pattern[str] = re.compile(
    r"(?m)"
    r"(?:^(`{3,}|~{3,})[^\n]*\n.*?^\1[ \t]*$)"
    r"|"
    r"(?:`[^`\n]+`)"
    r"|"
    r"(?:^[^\n`]*(?:" + SAFETY_NET_RE + r")[^\n`]*$)",
    re.IGNORECASE | re.DOTALL,
)

PLACEHOLDER_RE: re.Pattern[str] = re.compile(r"__PAYLOAD_(\d+)__")

UNICODE_EXOTIC_SPACES: re.Pattern[str] = re.compile(
    r"[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]"
)
ZERO_WIDTH_AND_BOM: re.Pattern[str] = re.compile(
    r"[\u200b-\u200d\u2060\ufeff\u00ad]"
)

CONTROL_CHARS: re.Pattern[str] = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"
)
PDF_HYPHEN_RE: re.Pattern[str] = re.compile(r"([A-Za-z])-\n([a-z])")
MULTI_BLANK_RE: re.Pattern[str] = re.compile(r"\n{3,}")
SPACES_RE: re.Pattern[str] = re.compile(r"[ \t]+")

logger = logging.getLogger(__name__)

# 2. HELPER FUNCTIONS (CLEANING AND MASKING)

def normalize_newlines(text: str) -> str:
    """Normalize line endings to standard Unix newline."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalize_unicode(text: str) -> str:
    """Normalize Unicode with NFC and strip control and zero-width characters."""
    text = unicodedata.normalize("NFC", text)
    text = UNICODE_EXOTIC_SPACES.sub(" ", text)
    text = ZERO_WIDTH_AND_BOM.sub("", text)
    text = CONTROL_CHARS.sub("", text)
    return text


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
        content = SPACES_RE.sub(" ", line[indent_len:]).rstrip(" \t")
        lines.append(indent + content)
    return "\n".join(lines)


def collapse_blank_lines(text: str) -> str:
    """Reduce 3+ consecutive newlines to 2 and strip surrounding blank lines."""
    text = MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip("\n")


def fix_pdf_hyphens(text: str) -> str:
    """Join words broken by hyphen at line breaks in PDF extraction."""
    return PDF_HYPHEN_RE.sub(r"\1\2", text)


def normalize_text(text: str, *, is_pdf: bool = False) -> str:
    """Apply cleaning rules in a fixed order ensuring idempotence."""
    text = normalize_newlines(text)
    text = normalize_unicode(text)
    text = collapse_spaces(text)
    text = collapse_blank_lines(text)
    if is_pdf:
        text = fix_pdf_hyphens(text)
    return text


def mask(text: str) -> tuple[str, list[str]]:
    """Replace protected code/payload matches with indexed placeholders."""
    spans: list[str] = []

    def _repl(m: re.Match[str]) -> str:
        idx = len(spans)
        spans.append(m.group(0))
        return f"__PAYLOAD_{idx}__"

    masked_text = PROTECT_RE.sub(_repl, text)
    return masked_text, spans


def unmask(text: str, spans: list[str]) -> str | None:
    """Restore placeholders in a single pass without rescanning restored text."""
    if not spans:
        return text
    found = PLACEHOLDER_RE.findall(text)
    if len(found) != len(spans):
        return None
    if set(int(i) for i in found) != set(range(len(spans))):
        return None

    return PLACEHOLDER_RE.sub(lambda m: spans[int(m.group(1))], text)


def clean_text_payload_safe(text: str, *, is_pdf: bool = False) -> tuple[str, bool]:
    """Clean text while preserving code and payloads byte-for-byte."""
    if not text:
        return "", False
    if "__PAYLOAD_" in text:
        return text, True

    masked, spans = mask(text)
    cleaned = normalize_text(masked, is_pdf=is_pdf)
    unmasked = unmask(cleaned, spans)

    if unmasked is None:
        return text, len(spans) > 0
    return unmasked, len(spans) > 0

# 3. ORCHESTRATION (BLOCK / DOCUMENT LOGIC, IO, CLI)

def _build_output(
    block: dict[str, Any],
    raw: str,
    normalized: str,
    is_payload: bool,
    has_modified: bool | None = None,
) -> dict[str, Any]:
    """Build normalized block preserving all original Step 2 keys."""
    out = copy.deepcopy(block)
    delta = len(raw) - len(normalized)
    modified = (raw != normalized) if has_modified is None else has_modified
    out.update({
        "normalized_id": f"nrm_{block.get('block_id') or ''}",
        "normalized_text": normalized,
        "is_code_or_payload": is_payload,
        "has_modified": modified,
        "char_count_delta": delta,
    })
    return out


def normalize_block(block: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single content block dictionary."""
    if not block.get("block_id"):
        logger.warning(
            "Missing block_id (document_id=%s, block_idx=%s)",
            block.get("document_id"),
            block.get("block_idx"),
        )

    source_locator = block.get("source_locator") or {}
    is_pdf = source_locator.get("page_number") is not None
    raw_text = block.get("text_content") or ""
    block_type = block.get("block_type") or ""

    metadata = block.get("metadata") or {}
    code_language = metadata.get("code_language")

    if block_type == "code" or bool(code_language):
        normalized_text = raw_text
        is_code_or_payload = True
    else:
        normalized_text, is_code_or_payload = clean_text_payload_safe(raw_text, is_pdf=is_pdf)

    sc = block.get("structured_content")
    normalized_sc: dict[str, Any] | None = None
    any_cell_changed = False

    if block_type == "table" and isinstance(sc, dict):
        normalized_sc = copy.deepcopy(sc)

        if "header_cols" in sc and isinstance(sc["header_cols"], list):
            clean_headers: list[str] = []
            for h in sc["header_cols"]:
                if h is None:
                    clean_h = ""
                else:
                    clean_h, _ = clean_text_payload_safe(str(h), is_pdf=is_pdf)
                if clean_h != h:
                    any_cell_changed = True
                clean_headers.append(clean_h)
            normalized_sc["header_cols"] = clean_headers

        if "rows" in sc and isinstance(sc["rows"], list):
            clean_rows: list[list[str]] = []
            for row in sc["rows"]:
                clean_row: list[str] = []
                if isinstance(row, list):
                    for cell in row:
                        if cell is None:
                            clean_cell = ""
                        else:
                            clean_cell, _ = clean_text_payload_safe(str(cell), is_pdf=is_pdf)
                        if clean_cell != cell:
                            any_cell_changed = True
                        clean_row.append(clean_cell)
                clean_rows.append(clean_row)
            normalized_sc["rows"] = clean_rows

    has_modified = (normalized_text != raw_text) or any_cell_changed

    out = _build_output(
        block,
        raw=raw_text,
        normalized=normalized_text,
        is_payload=is_code_or_payload,
        has_modified=has_modified,
    )

    if normalized_sc is not None:
        out["normalized_structured_content"] = normalized_sc

    return out


def normalize_document(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize a document preserving block order and isolating per-block errors."""
    results: list[dict[str, Any]] = []
    for block in blocks:
        try:
            results.append(normalize_block(block))
        except Exception as exc:
            block_id = block.get("block_id", "unknown")
            logger.warning("Error normalizing block %s: %s", block_id, exc)
            raw_text = block.get("text_content") or ""
            out = _build_output(block, raw_text, raw_text, False)
            results.append(out)
    return results


def load_blocks(path: Path) -> list[dict[str, Any]]:
    """Load content blocks from a JSON or JSONL file."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    return data if isinstance(data, list) else [data]


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


def main() -> None:
    """Command-line entry point for text normalization."""
    parser = argparse.ArgumentParser(description="Step 3: Text Cleaning & Normalization.")
    parser.add_argument("--input", required=True, type=Path, help="Input JSON or JSONL file")
    parser.add_argument("--output", required=True, type=Path, help="Output JSON or JSONL file")
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

    blocks = load_blocks(args.input)
    results = normalize_document(blocks)
    save_blocks(results, args.output)

    total = len(results)
    protected = sum(1 for b in results if b.get("is_code_or_payload"))
    modified = sum(1 for b in results if b.get("has_modified"))
    total_delta = sum(b.get("char_count_delta", 0) for b in results)
    print(
        f"Total: {total} | Protected: {protected} | Modified: {modified} | Delta: {total_delta}"
    )


if __name__ == "__main__":
    main()
