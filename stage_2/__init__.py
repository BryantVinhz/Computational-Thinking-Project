"""
agy package: Query Understanding & Routing Module (Contract v1.0).
Maintainer: Long (Query Understanding Lead) & Tri (Validation & Routing)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Setup paths and environment
AGY_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AGY_DIR.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(AGY_DIR) not in sys.path:
    sys.path.insert(0, str(AGY_DIR))

from stage_2.paths import DATA_DIR, DEFAULT_ONTOLOGY_PATH, ONTOLOGY_PATH
from stage_2.config import (
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
from stage_2.ontology_loader import load_ontology
from stage_2.understanding_module import (
    QueryUnderstandingEngine,
    get_engine,
    truncate_conversation_history,
    understand_query,
    validate_understanding_schema,
)

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
