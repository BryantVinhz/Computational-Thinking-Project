"""Package initialiser for step3_normalization."""
from .text_cleaner import (
    LOCAL_INPUT_DIR,
    LOCAL_OUTPUT_DIR,
    NORMALIZATION_IN_DIR,
    NORMALIZATION_OUT_DIR,
    TextNormalizer,
)

__all__ = [
    "LOCAL_INPUT_DIR",
    "LOCAL_OUTPUT_DIR",
    "NORMALIZATION_IN_DIR",
    "NORMALIZATION_OUT_DIR",
    "TextNormalizer",
]
