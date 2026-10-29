"""
Backward compatibility package for stage_2 (redirects to query_understanding_routing).
"""

from __future__ import annotations

import sys
from pathlib import Path

STAGE2_DIR = Path(__file__).resolve().parent
STAGE_DIR = STAGE2_DIR.parent
PROJECT_ROOT = STAGE_DIR.parent

for p in [PROJECT_ROOT, STAGE_DIR, STAGE2_DIR]:
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

from query_understanding_routing.config import (
    CLARIFY_THRESHOLD,
    HISTORY_MAX_CHARS,
    HISTORY_MAX_MESSAGES,
    HISTORY_MAX_TURNS,
    LLM_MAX_RETRIES,
    LLM_TIMEOUT_S,
    STANDARD_VULNERABILITY_SLUGS,
    VALID_INTENTS,
    VALID_LLM_ERROR_CODES,
    VALID_TASK_INTENTS,
)
from query_understanding_routing.ontology_loader import DEFAULT_ONTOLOGY_PATH, load_ontology
from query_understanding_routing.paths import DATA_DIR, ONTOLOGY_PATH, PROJECT_ROOT
from query_understanding_routing.understanding_module import (
    QueryUnderstandingEngine,
    get_engine,
    truncate_conversation_history,
    understand_query,
    validate_understanding_schema,
)

AGY_DIR = STAGE2_DIR

__all__ = [
    "understand_query",
    "load_ontology",
    "QueryUnderstandingEngine",
    "get_engine",
    "truncate_conversation_history",
    "validate_understanding_schema",
    "DEFAULT_ONTOLOGY_PATH",
    "ONTOLOGY_PATH",
    "AGY_DIR",
    "PROJECT_ROOT",
    "DATA_DIR",
    "CLARIFY_THRESHOLD",
    "STANDARD_VULNERABILITY_SLUGS",
    "VALID_INTENTS",
    "VALID_LLM_ERROR_CODES",
    "VALID_TASK_INTENTS",
]
