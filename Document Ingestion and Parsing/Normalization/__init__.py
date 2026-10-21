"""Package initialiser for step3_normalization."""
from .text_cleaner import (
    normalize_block,
    normalize_document,
    normalize_text,
)

__all__ = [
    "normalize_block",
    "normalize_document",
    "normalize_text",
]
