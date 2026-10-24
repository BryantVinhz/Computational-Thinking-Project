"""
Configuration constants for Query Understanding & Routing (Contract v1.0).
Maintainer: Long (Query Understanding Lead) & Tri (Validation & Routing)
"""

from __future__ import annotations

# Query length boundaries
MIN_QUERY_LEN: int = 3
MAX_QUERY_LEN: int = 500

# Conversation history boundaries (Section 7.1 & Section 10)
# Maximum 3 turn pairs (6 messages) and maximum 2,000 total characters
HISTORY_MAX_TURNS: int = 3  # pairs: user + assistant = 1 pair
HISTORY_MAX_MESSAGES: int = HISTORY_MAX_TURNS * 2  # 6 messages
HISTORY_MAX_CHARS: int = 2000

# LLM execution settings (Section 7.3 & Section 10)
LLM_TEMPERATURE: float = 0.0
LLM_TIMEOUT_S: float = 30.0
LLM_MAX_RETRIES: int = 2  # Only for parse/schema errors (total 3 attempts)

# Clarification threshold (Section 0.2 L1 & Section 10)
CLARIFY_THRESHOLD: float = 0.6

# Ontology standard slugs (Section 2.1 & Section 6.3)
STANDARD_VULNERABILITY_SLUGS: set[str] = {
    "xss",
    "sqli",
    "bac",
    "auth_failures",
    "misconfig",
}

# 5 allowed intent labels (Section 2.2 & Section 7.2)
VALID_INTENTS: set[str] = {
    "SEARCH",
    "COMPARE",
    "SYNTHESIS",
    "CLARIFICATION",
    "OUT_OF_SCOPE",
}

# Intents allowed for task execution leading to status=ok
VALID_TASK_INTENTS: set[str] = {
    "SEARCH",
    "COMPARE",
    "SYNTHESIS",
}

# Error codes permitted from Long's module (Section 7.3)
VALID_LLM_ERROR_CODES: set[str] = {
    "llm_unavailable",
    "llm_invalid_json",
    "llm_schema_violation",
}
