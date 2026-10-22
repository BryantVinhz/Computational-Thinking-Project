"""Package initialiser for step3_normalization."""
from .text_cleaner import (
    TextNormalizer,
    clean_text_payload_safe,
    collapse_blank_lines,
    collapse_spaces,
    fix_pdf_hyphens,
    load_blocks,
    mask,
    normalize_block,
    normalize_document,
    normalize_newlines,
    normalize_text,
    normalize_unicode,
    save_blocks,
    unmask,
)

__all__ = [
    "TextNormalizer",
    "normalize_block",
    "normalize_document",
    "normalize_text",
    "clean_text_payload_safe",
    "collapse_blank_lines",
    "collapse_spaces",
    "fix_pdf_hyphens",
    "load_blocks",
    "mask",
    "normalize_newlines",
    "normalize_unicode",
    "save_blocks",
    "unmask",
]
