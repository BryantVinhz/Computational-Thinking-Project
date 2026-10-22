"""Step 3: Text Cleaning & Normalization for RAG pipeline."""
from __future__ import annotations

import argparse
import copy
import json
import logging
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

# Resolve paths
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


class TextNormalizer:
    # 1. CONSTANTS AND REGEX PATTERNS (Class Attributes)
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

    def __init__(self) -> None:
        pass

    # 2. HELPER METHODS (CLEANING AND MASKING)

    @staticmethod
    def normalize_newlines(text: str) -> str:
        """Normalize line endings to standard Unix newline."""
        return text.replace("\r\n", "\n").replace("\r", "\n")

    @staticmethod
    def normalize_unicode(text: str) -> str:
        """Normalize Unicode with NFC and strip control and zero-width characters."""
        text = unicodedata.normalize("NFC", text)
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
        """Join words broken by hyphen at line breaks in PDF extraction."""
        return TextNormalizer.PDF_HYPHEN_RE.sub(r"\1\2", text)

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
            return f"__PAYLOAD_{idx}__"

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

    def clean_text_payload_safe(self, text: str, *, is_pdf: bool = False) -> tuple[str, bool]:
        """Clean text while preserving code and payloads byte-for-byte."""
        if not text:
            return "", False
        if "__PAYLOAD_" in text:
            return text, True

        masked, spans = self.mask(text)
        cleaned = self.normalize_text(masked, is_pdf=is_pdf)
        unmasked = self.unmask(cleaned, spans)

        if unmasked is None:
            return text, len(spans) > 0
        return unmasked, len(spans) > 0

    # 3. ORCHESTRATION (BLOCK / DOCUMENT LOGIC, IO)

    @staticmethod
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

    def normalize_block(self, block: dict[str, Any]) -> dict[str, Any]:
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
            normalized_text, is_code_or_payload = self.clean_text_payload_safe(
                raw_text, is_pdf=is_pdf
            )

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
                        clean_h, _ = self.clean_text_payload_safe(str(h), is_pdf=is_pdf)
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
                                clean_cell, _ = self.clean_text_payload_safe(
                                    str(cell), is_pdf=is_pdf
                                )
                            if clean_cell != cell:
                                any_cell_changed = True
                            clean_row.append(clean_cell)
                    clean_rows.append(clean_row)
                normalized_sc["rows"] = clean_rows

        has_modified = (normalized_text != raw_text) or any_cell_changed

        out = self._build_output(
            block,
            raw=raw_text,
            normalized=normalized_text,
            is_payload=is_code_or_payload,
            has_modified=has_modified,
        )

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
                out = self._build_output(block, raw_text, raw_text, False)
                results.append(out)
        return results

    @staticmethod
    def load_blocks(path: Path) -> list[dict[str, Any]]:
        """Load content blocks from a JSON or JSONL file."""
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".jsonl":
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        data = json.loads(text)
        return data if isinstance(data, list) else [data]

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
        blocks = self.load_blocks(in_p)
        results = self.normalize_document(blocks)
        self.save_blocks(results, out_p)

        total = len(results)
        protected = sum(1 for b in results if b.get("is_code_or_payload"))
        modified = sum(1 for b in results if b.get("has_modified"))
        total_delta = sum(b.get("char_count_delta", 0) for b in results)
        stats = {
            "file": in_p.name,
            "input_path": str(in_p),
            "output_path": str(out_p),
            "total": total,
            "protected": protected,
            "modified": modified,
            "delta": total_delta,
        }
        logger.info(
            "Normalized %s -> %s (Total: %d | Protected: %d | Modified: %d | Delta: %d)",
            in_p.name,
            out_p.name,
            total,
            protected,
            modified,
            total_delta,
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
            elif name == "sample_input.json":
                out_name = "sample_out.json"
            else:
                out_name = f"{in_file.stem}_normalized{in_file.suffix}"

            out_file = out_dir / out_name
            stat = self.normalize_file(in_file, out_file)
            stats_list.append(stat)

        return stats_list


# 4. MODULE-LEVEL CONSTANTS & FUNCTIONS (For backwards compatibility)

SAFETY_PATTERNS = TextNormalizer.SAFETY_PATTERNS
SAFETY_NET_RE = TextNormalizer.SAFETY_NET_RE
PROTECT_RE = TextNormalizer.PROTECT_RE
PLACEHOLDER_RE = TextNormalizer.PLACEHOLDER_RE
UNICODE_EXOTIC_SPACES = TextNormalizer.UNICODE_EXOTIC_SPACES
ZERO_WIDTH_AND_BOM = TextNormalizer.ZERO_WIDTH_AND_BOM
CONTROL_CHARS = TextNormalizer.CONTROL_CHARS
PDF_HYPHEN_RE = TextNormalizer.PDF_HYPHEN_RE
MULTI_BLANK_RE = TextNormalizer.MULTI_BLANK_RE
SPACES_RE = TextNormalizer.SPACES_RE

_default_normalizer = TextNormalizer()
normalize_newlines = TextNormalizer.normalize_newlines
normalize_unicode = TextNormalizer.normalize_unicode
collapse_spaces = TextNormalizer.collapse_spaces
collapse_blank_lines = TextNormalizer.collapse_blank_lines
fix_pdf_hyphens = TextNormalizer.fix_pdf_hyphens
mask = TextNormalizer.mask
unmask = TextNormalizer.unmask
normalize_text = _default_normalizer.normalize_text
clean_text_payload_safe = _default_normalizer.clean_text_payload_safe
normalize_block = _default_normalizer.normalize_block
normalize_document = _default_normalizer.normalize_document
load_blocks = TextNormalizer.load_blocks
save_blocks = TextNormalizer.save_blocks
normalize_file = _default_normalizer.normalize_file
normalize_directory = _default_normalizer.normalize_directory


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
        help="Input JSON/JSONL file or input directory (defaults to input dir)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output JSON/JSONL file or output directory (defaults to output dir)",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="Input directory containing JSON/JSONL files",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory to save normalized files",
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

    normalizer = TextNormalizer()

    input_target = args.input_dir or args.input
    output_target = args.output_dir or args.output

    if input_target is not None and input_target.is_file():
        # Single file mode
        if output_target is None:
            out_file = NORMALIZATION_OUT_DIR / f"{input_target.stem}_normalized{input_target.suffix}"
        elif output_target.is_dir() or (not output_target.suffix):
            output_target.mkdir(parents=True, exist_ok=True)
            out_file = output_target / f"{input_target.stem}_normalized{input_target.suffix}"
        else:
            out_file = output_target

        stat = normalizer.normalize_file(input_target, out_file)
        print(
            f"File: {stat['file']} | Total: {stat['total']} | Protected: {stat['protected']} | "
            f"Modified: {stat['modified']} | Delta: {stat['delta']}"
        )
        print(f"=> Saved to: {out_file}")
    else:
        # Directory mode
        if input_target is None:
            if NORMALIZATION_IN_DIR.exists() and any(NORMALIZATION_IN_DIR.glob("*.json")):
                in_dir = NORMALIZATION_IN_DIR
                out_dir = NORMALIZATION_OUT_DIR
            elif LOCAL_INPUT_DIR.exists() and any(LOCAL_INPUT_DIR.glob("*.json")):
                in_dir = LOCAL_INPUT_DIR
                out_dir = LOCAL_OUTPUT_DIR
            else:
                in_dir = NORMALIZATION_IN_DIR
                out_dir = NORMALIZATION_OUT_DIR
        else:
            in_dir = input_target
            out_dir = output_target or (LOCAL_OUTPUT_DIR if in_dir == LOCAL_INPUT_DIR else NORMALIZATION_OUT_DIR)

        if output_target is not None and output_target.suffix:
            out_dir = output_target.parent
        elif output_target is not None:
            out_dir = output_target

        results = normalizer.normalize_directory(in_dir, out_dir)

        total_files = len(results)
        total_blocks = sum(r.get("total", 0) for r in results)
        total_protected = sum(r.get("protected", 0) for r in results)
        total_modified = sum(r.get("modified", 0) for r in results)
        total_delta = sum(r.get("delta", 0) for r in results)
        print(f"=== STEP 3: TEXT NORMALIZATION ===")
        print(f"- Input directory   : {in_dir}")
        print(f"- Output directory  : {out_dir}")
        print(f"- Files processed   : {total_files}")
        print(f"- Total blocks      : {total_blocks}")
        print(f"- Protected blocks  : {total_protected}")
        print(f"- Modified blocks   : {total_modified}")
        print(f"- Total char delta  : {total_delta}")
        print(f"=> Output saved to  : {out_dir}")


if __name__ == "__main__":
    main()
