"""Pipeline: validation -> normalization -> understanding -> routing (Contract v1.0)."""

import logging
import time

try:
    from .ontology_loader import load_ontology
    from .understanding_module import understand_query, get_default_llm_client
    from .config import ERROR_MSG, MESSAGES
    from .normalization_module import normalize_query
    from .routing_module import SCHEMA_VERSION, route
    from .validation_module import validate_query
except ImportError:
    try:
        from stage_2.ontology_loader import load_ontology
        from stage_2.understanding_module import understand_query, get_default_llm_client
    except ImportError:
        from ontology_loader import load_ontology
        from understanding_module import understand_query, get_default_llm_client
    from config import ERROR_MSG, MESSAGES
    from normalization_module import normalize_query
    from routing_module import SCHEMA_VERSION, route
    from validation_module import validate_query

logger = logging.getLogger(__name__)

INTERNAL_ERROR = {"error_code": "internal_error"}

_ontology_cache: dict | None = None


def _get_ontology(ontology) -> dict | None:
    """Return the cached default if ontology is None, the caller's non-empty dict, else None."""
    global _ontology_cache
    if ontology is not None:
        return ontology if isinstance(ontology, dict) and ontology else None
    if _ontology_cache is None:
        try:
            loaded = load_ontology()
        except Exception:
            logger.exception("Ontology loading failed")
            return None
        if not isinstance(loaded, dict) or not loaded:
            logger.error("Ontology loader returned an empty or non-dict value")
            return None
        _ontology_cache = loaded
    return _ontology_cache


def _empty_query_validation() -> dict:
    return {
        "is_valid": False,
        "reject_reason": "empty_query",
        "reason": MESSAGES["empty_query"],
        "safety_action": None,
        "clarification_message": None,
    }


def _understand(normalized_query: str, history, llm_client) -> dict:
    try:
        client = llm_client if llm_client is not None else get_default_llm_client()
        result = understand_query(normalized_query, history, llm_client=client)
    except Exception:
        logger.exception("Understanding failed unexpectedly")
        return dict(INTERNAL_ERROR)
    return result if isinstance(result, dict) else dict(INTERNAL_ERROR)


def _run(raw_query, conversation_history, llm_client, ontology, document_ids) -> dict:
    validation_result = validate_query(raw_query)
    if not validation_result.get("is_valid") or validation_result.get("safety_action") == "CLARIFY":
        return route(raw_query, validation_result)

    ontology = _get_ontology(ontology)
    if ontology is None:
        return route(raw_query, validation_result, dict(INTERNAL_ERROR))

    normalized_query = normalize_query(raw_query, ontology)["normalized_query"]
    if not normalized_query.strip():
        return route(raw_query, _empty_query_validation())

    history = conversation_history if isinstance(conversation_history, list) else None
    understanding_result = _understand(normalized_query, history, llm_client)
    return route(raw_query, validation_result, understanding_result, ontology, document_ids)


def process_query(
    raw_query,
    conversation_history=None,
    *,
    llm_client=None,
    ontology=None,
    document_ids=None,
) -> dict:
    """Run the full pipeline and return exactly one Contract v1.0 response envelope."""
    start = time.perf_counter()
    try:
        envelope = _run(raw_query, conversation_history, llm_client, ontology, document_ids)
    except Exception:
        logger.exception("Pipeline failed unexpectedly")
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "status": "error",
            "original_query": raw_query if isinstance(raw_query, str) else None,
            "error_code": "internal_error",
            "reason": ERROR_MSG,
        }
    logger.info(
        "pipeline status=%s reject_reason=%s error_code=%s query_len=%s elapsed_ms=%.1f",
        envelope.get("status"),
        envelope.get("reject_reason"),
        envelope.get("error_code"),
        len(raw_query) if isinstance(raw_query, str) else None,
        (time.perf_counter() - start) * 1000,
    )
    return envelope
