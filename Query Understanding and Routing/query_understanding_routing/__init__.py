"""Query Understanding & Routing Package (Contract v1.0)."""

from .config import (
    CLARIFY_THRESHOLD,
    HISTORY_MAX_CHARS,
    HISTORY_MAX_MESSAGES,
    HISTORY_MAX_TURNS,
    LLM_MAX_RETRIES,
    LLM_TIMEOUT_S,
    MAX_QUERY_LEN,
    MIN_QUERY_LEN,
    STANDARD_VULNERABILITY_SLUGS,
    VALID_INTENTS,
    VALID_LLM_ERROR_CODES,
    VALID_TASK_INTENTS,
)
from .llm_client import (
    BaseLLMClient,
    GeminiLLMClient,
    LLMClient,
    MockLLMClient,
    OllamaLLMClient,
    OpenAILikeLLMClient,
    clean_markdown_json,
    create_llm_client,
)
from .normalization_module import normalize_query
from .ontology_loader import DEFAULT_ONTOLOGY_PATH, load_ontology
from .paths import DATA_DIR, PROJECT_ROOT
from .routing_module import SCHEMA_VERSION, route
from .understanding_module import (
    QueryUnderstandingEngine,
    get_default_llm_client,
    get_engine,
    set_default_llm_client,
    truncate_conversation_history,
    understand_query,
    validate_understanding_schema,
)
from .validation_module import validate_query


def __getattr__(name):
    # Lazy: pipeline imports stage_2 / submodules, which other components do not need immediately.
    if name == "process_query":
        from .pipeline import process_query

        return process_query
    if name == "EndToEndRAGPipeline":
        from .rag_processor import EndToEndRAGPipeline

        return EndToEndRAGPipeline
    if name == "RAGGenerator":
        from .rag_processor import RAGGenerator

        return RAGGenerator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "validate_query",
    "normalize_query",
    "understand_query",
    "route",
    "process_query",
    "load_ontology",
    "QueryUnderstandingEngine",
    "get_engine",
    "truncate_conversation_history",
    "validate_understanding_schema",
    "LLMClient",
    "BaseLLMClient",
    "OllamaLLMClient",
    "GeminiLLMClient",
    "OpenAILikeLLMClient",
    "MockLLMClient",
    "create_llm_client",
    "RAGGenerator",
    "EndToEndRAGPipeline",
    "set_default_llm_client",
    "get_default_llm_client",
    "DEFAULT_ONTOLOGY_PATH",
    "CLARIFY_THRESHOLD",
    "STANDARD_VULNERABILITY_SLUGS",
    "VALID_INTENTS",
    "VALID_TASK_INTENTS",
    "VALID_LLM_ERROR_CODES",
    "SCHEMA_VERSION",
    "MIN_QUERY_LEN",
    "MAX_QUERY_LEN",
]
