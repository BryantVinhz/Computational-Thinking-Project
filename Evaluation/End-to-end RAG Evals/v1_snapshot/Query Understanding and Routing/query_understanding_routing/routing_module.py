"""Routing: combine validation and understanding results into the final response envelope."""

import copy
import logging

from .config import ERROR_MSG, OUT_OF_SCOPE_MSG

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"

VALIDATION_KEYS = ("is_valid", "reject_reason", "reason", "safety_action", "clarification_message")
REJECT_REASONS = {
    "empty_query",
    "query_too_short",
    "query_too_long",
    "spam_detected",
    "invalid_characters",
    "invalid_type",
    "suspicious_pattern",
    "unsafe_attack_intent",
    "prompt_injection_attempt",
}
SAFETY_REASONS = {"unsafe_attack_intent", "prompt_injection_attempt"}

UNDERSTANDING_KEYS = (
    "resolved_query",
    "intent",
    "confidence",
    "context_used",
    "entities",
    "filter",
    "task",
    "need_clarify",
    "clarification_message",
)
STANDARD_SLUGS = {"xss", "sqli", "bac", "auth_failures", "misconfig"}
TASK_INTENTS = {"SEARCH", "COMPARE", "SYNTHESIS"}
INTENTS = TASK_INTENTS | {"CLARIFICATION", "OUT_OF_SCOPE"}
OBJECTIVE_TYPES = {"vulnerabilities", "documents"}
LLM_ERROR_CODES = {"llm_unavailable", "llm_invalid_json", "llm_schema_violation"}


def _is_one_of(value, allowed: set[str]) -> bool:
    # Checking str first keeps unhashable values from raising inside "in".
    return isinstance(value, str) and value in allowed


def _is_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _envelope(status: str, raw_query, **fields) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "original_query": raw_query if isinstance(raw_query, str) else None,
        **fields,
    }


def _error(raw_query, error_code: str) -> dict:
    return _envelope("error", raw_query, error_code=error_code, reason=ERROR_MSG)


def _is_valid_validation(result) -> bool:
    """Return True if validation_result follows the 5-key contract and is self-consistent."""
    if not isinstance(result, dict) or any(key not in result for key in VALIDATION_KEYS):
        return False
    is_valid, action = result["is_valid"], result["safety_action"]
    reject_reason, reason, message = result["reject_reason"], result["reason"], result["clarification_message"]
    if not isinstance(is_valid, bool):
        return False
    if action is not None and not _is_one_of(action, {"ALLOW", "CLARIFY", "REFUSE"}):
        return False
    if is_valid:
        if reject_reason is not None or reason is not None:
            return False
        if action == "CLARIFY":
            return _is_text(message)
        return action == "ALLOW" and message is None
    if message is not None or not _is_text(reason) or not _is_one_of(reject_reason, REJECT_REASONS):
        return False
    # Safety reasons pair with REFUSE; structural failures carry no safety_action.
    if action == "REFUSE":
        return reject_reason in SAFETY_REASONS
    return action is None and reject_reason not in SAFETY_REASONS


def _is_valid_task(task, seen_ids: set[str], slugs: set[str], document_ids) -> bool:
    if not isinstance(task, dict):
        return False
    task_id = task.get("task_id")
    if not _is_text(task_id) or task_id in seen_ids:
        return False
    if not _is_one_of(task.get("intent_task"), TASK_INTENTS) or not _is_text(task.get("query")):
        return False

    objective = task.get("objective")
    if not isinstance(objective, dict) or not _is_one_of(objective.get("type"), OBJECTIVE_TYPES):
        return False
    ids = objective.get("ids")
    if not _is_str_list(ids):
        return False
    if objective["type"] == "vulnerabilities":
        if not all(item in slugs for item in ids):
            return False
    elif not all(_is_text(item) and (document_ids is None or item in document_ids) for item in ids):
        return False

    if "depends_on" not in task:
        return False
    # seen_ids holds only earlier tasks, so self, forward and dangling references all fail.
    depends_on = task["depends_on"]
    return depends_on is None or _is_one_of(depends_on, seen_ids)


def _is_valid_understanding(result: dict, slugs: set[str], document_ids) -> bool:
    """Return True if understanding_result follows Contract Section 7.2."""
    if any(key not in result for key in UNDERSTANDING_KEYS):
        return False

    intent = result["intent"]
    confidence = result["confidence"]
    if not _is_text(result["resolved_query"]) or not _is_one_of(intent, INTENTS):
        return False
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return False
    # The range check also rejects NaN and inf without math.isfinite overflowing on huge ints.
    if not 0 <= confidence <= 1:
        return False
    if not isinstance(result["context_used"], bool) or not isinstance(result["need_clarify"], bool):
        return False

    entities = result["entities"]
    if not isinstance(entities, dict) or "document" not in entities or "vulnerabilities" not in entities:
        return False
    if not _is_str_list(entities["document"]) or not _is_str_list(entities["vulnerabilities"]):
        return False
    if not all(slug in slugs for slug in entities["vulnerabilities"]):
        return False

    flt = result["filter"]
    if not isinstance(flt, dict) or any(key not in flt for key in ("version", "document_type", "source")):
        return False
    if not all(flt[key] is None or isinstance(flt[key], str) for key in ("version", "document_type")):
        return False
    if not _is_str_list(flt["source"]):
        return False

    need_clarify = result["need_clarify"]
    if (intent == "CLARIFICATION") != need_clarify:
        return False
    message = result["clarification_message"]
    if not (_is_text(message) if need_clarify else message is None):
        return False

    tasks = result["task"]
    if not isinstance(tasks, list):
        return False
    if intent not in TASK_INTENTS:
        return True
    if not tasks:
        return False
    seen_ids: set[str] = set()
    for task in tasks:
        if not _is_valid_task(task, seen_ids, slugs, document_ids):
            return False
        seen_ids.add(task["task_id"])
    return True


def _build_task(task: dict) -> dict:
    objective = task["objective"]
    return {
        "task_id": task["task_id"],
        "intent_task": task["intent_task"],
        "query": task["query"],
        # Dedupe preserving order; duplicates are tolerated, not a contract violation.
        "objective": {"type": objective["type"], "ids": list(dict.fromkeys(objective["ids"]))},
        "depends_on": task["depends_on"],
    }


def _build_query_plan(raw_query, result: dict) -> dict:
    entities = result["entities"]
    flt = result["filter"]
    plan = {
        # Never trust an original_query coming from Understanding.
        "original_query": raw_query if isinstance(raw_query, str) else None,
        "resolved_query": result["resolved_query"],
        "intent": result["intent"],
        "confidence": result["confidence"],
        "context_used": result["context_used"],
        "entities": {"document": entities["document"], "vulnerabilities": entities["vulnerabilities"]},
        "filter": {"version": flt["version"], "document_type": flt["document_type"], "source": flt["source"]},
        "task": [_build_task(task) for task in result["task"]],
        "need_clarify": False,
        "clarification_message": None,
    }
    return copy.deepcopy(plan)


def _route(raw_query, validation_result, understanding_result, ontology, document_ids) -> dict:
    if not _is_valid_validation(validation_result):
        return _error(raw_query, "internal_error")
    if not validation_result["is_valid"]:
        return _envelope(
            "rejected",
            raw_query,
            reject_reason=validation_result["reject_reason"],
            reason=validation_result["reason"],
            safety_action=validation_result["safety_action"],
        )
    if validation_result["safety_action"] == "CLARIFY":
        return _envelope(
            "clarification",
            raw_query,
            source="safety",
            clarification_message=validation_result["clarification_message"],
        )

    if not isinstance(understanding_result, dict):
        return _error(raw_query, "internal_error")
    if "error_code" in understanding_result:
        code = understanding_result["error_code"]
        return _error(raw_query, code if _is_one_of(code, LLM_ERROR_CODES) else "internal_error")

    slugs = set(ontology) if isinstance(ontology, dict) and ontology else STANDARD_SLUGS
    if not _is_valid_understanding(understanding_result, slugs, document_ids):
        return _error(raw_query, "llm_schema_violation")

    if understanding_result["need_clarify"]:
        return _envelope(
            "clarification",
            raw_query,
            source="understanding",
            clarification_message=understanding_result["clarification_message"],
        )
    if understanding_result["intent"] == "OUT_OF_SCOPE":
        return _envelope("out_of_scope", raw_query, reason=OUT_OF_SCOPE_MSG)
    return _envelope("ok", raw_query, query_plan=_build_query_plan(raw_query, understanding_result))


def route(
    raw_query,
    validation_result: dict,
    understanding_result: dict | None = None,
    ontology: dict | None = None,
    document_ids: set[str] | None = None,
) -> dict:
    """Return exactly one response envelope (rejected, clarification, out_of_scope, error or ok)."""
    try:
        return _route(raw_query, validation_result, understanding_result, ontology, document_ids)
    except Exception:
        # Details go to the log only; callers get a generic error envelope.
        logger.exception("Routing failed unexpectedly")
        return _error(raw_query, "internal_error")
