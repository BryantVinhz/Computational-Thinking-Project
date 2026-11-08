"""Tests for the Routing module (Contract)."""

import os
from copy import deepcopy
from pathlib import Path

import pytest

from query_understanding_routing.config import ERROR_MSG, MESSAGES, OUT_OF_SCOPE_MSG, SAFETY_CLARIFY_MESSAGE
from query_understanding_routing.routing_module import REJECT_REASONS, SAFETY_REASONS, route

BASE_KEYS = {"schema_version", "status", "original_query"}
STATUS_KEYS = {
    "rejected": BASE_KEYS | {"reject_reason", "reason", "safety_action"},
    "clarification": BASE_KEYS | {"source", "clarification_message"},
    "out_of_scope": BASE_KEYS | {"reason"},
    "error": BASE_KEYS | {"error_code", "reason"},
    "ok": BASE_KEYS | {"query_plan"},
}
PLAN_KEYS = {
    "original_query",
    "resolved_query",
    "intent",
    "confidence",
    "context_used",
    "entities",
    "filter",
    "task",
    "need_clarify",
    "clarification_message",
}
RAW = "XSS là gì?"


def allow() -> dict:
    return {"is_valid": True, "reject_reason": None, "reason": None, "safety_action": "ALLOW", "clarification_message": None}


def clarify() -> dict:
    return {**allow(), "safety_action": "CLARIFY", "clarification_message": SAFETY_CLARIFY_MESSAGE}


def reject(code: str = "spam_detected", action: str | None = None) -> dict:
    return {"is_valid": False, "reject_reason": code, "reason": MESSAGES[code], "safety_action": action, "clarification_message": None}


def task(task_id: str = "task_001", ids: list | None = None, depends_on: str | None = None, **overrides) -> dict:
    return {
        "task_id": task_id,
        "intent_task": "SEARCH",
        "query": "XSS là gì?",
        "objective": {"type": "vulnerabilities", "ids": ["xss"] if ids is None else ids},
        "depends_on": depends_on,
        **overrides,
    }


def understanding(**overrides) -> dict:
    result = {
        "resolved_query": "XSS là gì?",
        "intent": "SEARCH",
        "confidence": 0.95,
        "context_used": False,
        "entities": {"document": [], "vulnerabilities": ["xss"]},
        "filter": {"version": None, "document_type": None, "source": []},
        "task": [task()],
        "need_clarify": False,
        "clarification_message": None,
    }
    result.update(overrides)
    return result


def clarification_understanding(message="Bạn muốn hỏi về lỗ hổng nào?") -> dict:
    return understanding(intent="CLARIFICATION", confidence=0.4, need_clarify=True, clarification_message=message, task=[])


def assert_status(result: dict, status: str) -> None:
    assert result["status"] == status, result
    assert set(result) == STATUS_KEYS[status]
    assert result["schema_version"] == "1.0"


def assert_error(result: dict, code: str) -> None:
    assert_status(result, "error")
    assert result["error_code"] == code
    assert result["reason"] == ERROR_MSG


# --- Envelopes -----------------------------------------------------------------


@pytest.mark.parametrize(
    "validation, understanding_result, status",
    [
        (reject(), None, "rejected"),
        (clarify(), None, "clarification"),
        (allow(), understanding(intent="OUT_OF_SCOPE", task=[]), "out_of_scope"),
        (allow(), {"error_code": "llm_unavailable"}, "error"),
        (allow(), understanding(), "ok"),
    ],
)
def test_each_status_has_exact_keys(validation, understanding_result, status):
    result = route(RAW, validation, understanding_result)
    assert_status(result, status)
    assert result["original_query"] == RAW
    assert ("query_plan" in result) is (status == "ok")


def test_out_of_scope_and_safety_clarification_messages():
    assert route(RAW, allow(), understanding(intent="OUT_OF_SCOPE", task=[]))["reason"] == OUT_OF_SCOPE_MSG
    result = route(RAW, clarify(), understanding())
    assert result["source"] == "safety"
    assert result["clarification_message"] == SAFETY_CLARIFY_MESSAGE


# --- Precedence ----------------------------------------------------------------


MALICIOUS_UNDERSTANDING = understanding(original_query="spoofed", extra="payload")


@pytest.mark.parametrize(
    "validation, status",
    [
        (reject("query_too_short"), "rejected"),
        (reject("unsafe_attack_intent", "REFUSE"), "rejected"),
        (reject("prompt_injection_attempt", "REFUSE"), "rejected"),
        (clarify(), "clarification"),
    ],
)
def test_validation_decision_ignores_understanding(validation, status):
    result = route(RAW, validation, deepcopy(MALICIOUS_UNDERSTANDING))
    assert_status(result, status)
    if status == "rejected":
        assert result["reject_reason"] == validation["reject_reason"]
        assert result["reason"] == validation["reason"]
        assert result["safety_action"] == validation["safety_action"]


class _Recording(dict):
    """Dict that records any read, proving understanding_result is not touched."""

    touched = False

    def _touch(self, *args):
        type(self).touched = True
        return None

    __getitem__ = __contains__ = __iter__ = get = keys = items = _touch


@pytest.mark.parametrize(
    "validation, status",
    [(reject(), "rejected"), (reject("unsafe_attack_intent", "REFUSE"), "rejected"), (clarify(), "clarification"), ({"bad": 1}, "error")],
)
def test_understanding_not_touched_before_step_3(validation, status):
    _Recording.touched = False
    assert_status(route(RAW, validation, _Recording()), status)
    assert _Recording.touched is False


def test_case_25_rejected_without_understanding():
    assert_status(route("", reject("empty_query"), None), "rejected")


@pytest.mark.parametrize("raw", [None, 123])
def test_case_5_non_str_query_has_null_original_query(raw):
    result = route(raw, reject("invalid_type"))
    assert_status(result, "rejected")
    assert result["original_query"] is None
    assert result["reject_reason"] == "invalid_type"


@pytest.mark.parametrize(
    "validation",
    [
        None,
        "ALLOW",
        [],
        {},
        {k: v for k, v in allow().items() if k != "clarification_message"},
        {**allow(), "is_valid": 1},
        {**allow(), "is_valid": "true"},
        {**allow(), "safety_action": "allow"},
        {**allow(), "safety_action": ["ALLOW"]},
        {**allow(), "safety_action": None},
        {**allow(), "safety_action": "REFUSE"},
        {**allow(), "safety_action": "CLARIFY"},
        {**clarify(), "clarification_message": "   "},
        {**reject(), "reject_reason": None},
        {**reject(), "reject_reason": "unknown_code"},
        {**reject(), "reject_reason": ["spam_detected"]},
        {**reject(), "reason": None},
        {**reject(), "safety_action": "ALLOW"},
        {**allow(), "reject_reason": "spam_detected"},
        {**allow(), "reason": "Blocked"},
        {**allow(), "clarification_message": "Hỏi lại?"},
        {**clarify(), "reason": "x"},
        {**clarify(), "reject_reason": "spam_detected"},
        {**reject(), "clarification_message": "x"},
        {**reject(), "reason": ""},
        {**reject(), "reason": "   "},
        reject("spam_detected", "REFUSE"),
        reject("unsafe_attack_intent", None),
        reject("prompt_injection_attempt", None),
        reject("suspicious_pattern", "REFUSE"),
        {**reject("unsafe_attack_intent", "REFUSE"), "clarification_message": "x"},
        {**reject("spam_detected", "CLARIFY")},
    ],
)
def test_malformed_validation_is_internal_error(validation):
    assert_error(route(RAW, validation, understanding()), "internal_error")


@pytest.mark.parametrize(
    "validation",
    [reject(code) for code in sorted(REJECT_REASONS - SAFETY_REASONS)]
    + [reject(code, "REFUSE") for code in sorted(SAFETY_REASONS)],
    ids=lambda v: f"{v['reject_reason']}-{v['safety_action']}",
)
def test_valid_rejection_shapes_are_routed(validation):
    result = route(RAW, validation, understanding())
    assert_status(result, "rejected")
    assert result["reject_reason"] == validation["reject_reason"]
    assert result["safety_action"] == validation["safety_action"]


def test_contract_messages():
    assert ERROR_MSG == "Hệ thống tạm thời không xử lý được yêu cầu. Vui lòng thử lại sau."
    assert OUT_OF_SCOPE_MSG == (
        "Câu hỏi này nằm ngoài phạm vi kiến thức bảo mật web của hệ thống "
        "(XSS, SQL Injection, Broken Access Control, Authentication Failures, Security Misconfiguration)."
    )
    assert "ERROR_MSG" not in MESSAGES and "OUT_OF_SCOPE_MSG" not in MESSAGES
    assert ERROR_MSG not in MESSAGES.values() and OUT_OF_SCOPE_MSG not in MESSAGES.values()


# --- Understanding errors ------------------------------------------------------


@pytest.mark.parametrize("understanding_result", [None, "SEARCH", [], 42])
def test_missing_understanding_is_internal_error(understanding_result):
    assert_error(route(RAW, allow(), understanding_result), "internal_error")


@pytest.mark.parametrize("code", ["llm_unavailable", "llm_invalid_json", "llm_schema_violation"])
def test_known_understanding_error_codes(code):
    assert_error(route(RAW, allow(), {"error_code": code}), code)


@pytest.mark.parametrize("code", ["boom", None, ["llm_unavailable"], ""])
def test_unknown_understanding_error_code(code):
    assert_error(route(RAW, allow(), {"error_code": code}), "internal_error")


def test_error_code_wins_over_valid_fields():
    assert_error(route(RAW, allow(), {**understanding(), "error_code": "llm_unavailable"}), "llm_unavailable")


# --- Schema check (case 23 / 24) -------------------------------------------------


def _without(key: str) -> dict:
    result = understanding()
    del result[key]
    return result


@pytest.mark.parametrize(
    "understanding_result",
    [
        pytest.param(understanding(intent="FOO"), id="intent-foo"),
        pytest.param(understanding(intent=None), id="intent-none"),
        pytest.param(understanding(confidence=1.7), id="confidence-1.7"),
        pytest.param(understanding(confidence=-0.1), id="confidence-neg"),
        pytest.param(understanding(confidence=True), id="confidence-bool"),
        pytest.param(understanding(confidence=float("nan")), id="confidence-nan"),
        pytest.param(understanding(confidence=float("inf")), id="confidence-inf"),
        pytest.param(understanding(confidence="0.9"), id="confidence-str"),
        pytest.param(understanding(confidence=10**400), id="confidence-huge-int"),
        pytest.param(understanding(resolved_query="  "), id="resolved-blank"),
        pytest.param(understanding(context_used=1), id="context-used-int"),
        pytest.param(understanding(need_clarify="no"), id="need-clarify-str"),
        pytest.param(understanding(entities={"document": [], "vulnerabilities": ["rce"]}), id="unknown-slug-entity"),
        pytest.param(understanding(entities={"document": [1], "vulnerabilities": []}), id="document-non-str"),
        pytest.param(understanding(entities={"vulnerabilities": []}), id="entities-missing-document"),
        pytest.param(understanding(entities=[]), id="entities-list"),
        pytest.param(understanding(filter={"version": None, "source": []}), id="filter-missing-key"),
        pytest.param(understanding(filter={"version": 4, "document_type": None, "source": []}), id="filter-version-int"),
        pytest.param(understanding(filter={"version": None, "document_type": None, "source": "OWASP"}), id="filter-source-str"),
        pytest.param(understanding(task=[]), id="task-empty-search"),
        pytest.param(understanding(task={}), id="task-dict"),
        pytest.param(understanding(task=["task_001"]), id="task-item-str"),
        pytest.param(understanding(task=[task(ids=["rce"])]), id="unknown-slug-task"),
        pytest.param(understanding(task=[task(ids=[["xss"]])]), id="ids-unhashable"),
        pytest.param(understanding(task=[task(depends_on="task_999")]), id="depends-dangling"),
        pytest.param(understanding(task=[task(depends_on="task_001")]), id="depends-self"),
        pytest.param(understanding(task=[task("task_001", depends_on="task_002"), task("task_002")]), id="depends-forward"),
        pytest.param(understanding(task=[task(), task()]), id="duplicate-task-id"),
        pytest.param(
            understanding(task=[{k: v for k, v in task().items() if k != "depends_on"}]),
            id="task-missing-depends-on",
        ),
        pytest.param(understanding(task=[task(task_id="")]), id="empty-task-id"),
        pytest.param(understanding(task=[task(intent_task="CLARIFICATION")]), id="bad-intent-task"),
        pytest.param(understanding(task=[task(query=" ")]), id="blank-task-query"),
        pytest.param(understanding(task=[task(objective={"type": "cwe", "ids": []})]), id="bad-objective-type"),
        pytest.param(understanding(task=[task(objective={"type": "documents", "ids": "OWASP"})]), id="ids-not-list"),
        pytest.param(_without("filter"), id="missing-filter"),
        pytest.param(_without("clarification_message"), id="missing-clarification-message"),
        # Case 24: CLARIFICATION <=> need_clarify, message only when clarifying
        pytest.param(clarification_understanding(message=None), id="case24-clarify-null-message"),
        pytest.param(clarification_understanding(message=" "), id="case24-clarify-blank-message"),
        pytest.param(understanding(clarification_message="Bạn hỏi gì?"), id="case24-message-without-clarify"),
        pytest.param(understanding(need_clarify=True, clarification_message="Bạn hỏi gì?"), id="case24-search-need-clarify"),
        pytest.param(understanding(intent="CLARIFICATION", task=[]), id="case24-clarify-intent-flag-false"),
    ],
)
def test_case_23_schema_violation(understanding_result):
    assert_error(route(RAW, allow(), understanding_result), "llm_schema_violation")


@pytest.mark.parametrize(
    "ids, document_ids, status",
    [
        (["OWASP Top 10"], None, "ok"),
        (["anything goes"], None, "ok"),
        ([""], None, "error"),
        (["OWASP Top 10"], {"OWASP Top 10", "OWASP ASVS"}, "ok"),
        (["Unknown Doc"], {"OWASP Top 10"}, "error"),
    ],
)
def test_document_ids(ids, document_ids, status):
    result = route(RAW, allow(), understanding(task=[task(objective={"type": "documents", "ids": ids})]), document_ids=document_ids)
    assert result["status"] == status
    if status == "error":
        assert result["error_code"] == "llm_schema_violation"


def test_ontology_restricts_valid_slugs():
    ontology = {"sqli": {"canonical": "SQL Injection"}}
    assert_error(route(RAW, allow(), understanding(), ontology=ontology), "llm_schema_violation")
    sqli = understanding(entities={"document": [], "vulnerabilities": ["sqli"]}, task=[task(ids=["sqli"])])
    assert_status(route(RAW, allow(), sqli, ontology=ontology), "ok")
    # A custom slug becomes valid only when the ontology declares it.
    custom = understanding(entities={"document": [], "vulnerabilities": ["rce"]}, task=[task(ids=["rce"])])
    assert_status(route(RAW, allow(), custom, ontology={"rce": {}}), "ok")
    assert_error(route(RAW, allow(), custom), "llm_schema_violation")


@pytest.mark.parametrize("ontology", [None, {}, "xss", ["xss"]])
def test_default_slugs_without_usable_ontology(ontology):
    assert_status(route(RAW, allow(), understanding(), ontology=ontology), "ok")


def test_clarification_and_out_of_scope_tasks_not_validated():
    garbage_tasks = [{"task_id": None}]
    result = route(RAW, allow(), {**clarification_understanding(), "task": garbage_tasks})
    assert_status(result, "clarification")
    assert result["source"] == "understanding"
    assert result["clarification_message"] == "Bạn muốn hỏi về lỗ hổng nào?"
    assert_status(route(RAW, allow(), understanding(intent="OUT_OF_SCOPE", task=garbage_tasks)), "out_of_scope")


# --- query_plan ------------------------------------------------------------------


def test_empty_objective_ids_accepted():
    assert route(RAW, allow(), understanding(task=[task(ids=[])]))["query_plan"]["task"][0]["objective"]["ids"] == []


def test_duplicate_objective_ids_deduplicated_in_order():
    result = route(RAW, allow(), understanding(task=[task(ids=["sqli", "xss", "sqli", "xss"])]))
    assert result["query_plan"]["task"][0]["objective"]["ids"] == ["sqli", "xss"]


def test_query_plan_whitelist_and_overrides():
    tasks = [
        task("t1", intent_task="COMPARE", ids=["xss", "sqli"], extra="drop"),
        task("t2", depends_on="t1"),
        task("t3", depends_on="t1", objective={"type": "documents", "ids": ["OWASP Top 10"], "x": 1}),
    ]
    source = understanding(
        intent="COMPARE",
        original_query="spoofed by understanding",
        entities={"document": ["OWASP Top 10"], "vulnerabilities": ["xss", "sqli"], "extra": 1},
        filter={"version": "2025", "document_type": None, "source": ["OWASP"], "extra": 1},
        task=tasks,
        unknown_key="drop me",
    )
    plan = route(RAW, allow(), source)["query_plan"]

    assert set(plan) == PLAN_KEYS
    assert plan["original_query"] == RAW
    assert plan["intent"] == "COMPARE"
    assert plan["entities"] == {"document": ["OWASP Top 10"], "vulnerabilities": ["xss", "sqli"]}
    assert plan["filter"] == {"version": "2025", "document_type": None, "source": ["OWASP"]}
    assert [t["task_id"] for t in plan["task"]] == ["t1", "t2", "t3"]
    assert [t["depends_on"] for t in plan["task"]] == [None, "t1", "t1"]
    assert all(set(t) == {"task_id", "intent_task", "query", "objective", "depends_on"} for t in plan["task"])
    assert plan["task"][2]["objective"] == {"type": "documents", "ids": ["OWASP Top 10"]}
    assert plan["need_clarify"] is False
    assert plan["clarification_message"] is None


def test_mixed_objective_types_allowed():
    tasks = [task("t1"), task("t2", objective={"type": "documents", "ids": ["OWASP ASVS"]})]
    assert_status(route(RAW, allow(), understanding(intent="SYNTHESIS", task=tasks)), "ok")


# --- Purity --------------------------------------------------------------------


def test_inputs_not_mutated_and_plan_is_independent():
    validation = allow()
    source = understanding(task=[task(ids=["xss", "xss"])], original_query="spoofed")
    ontology = {"xss": {"canonical": "Cross-Site Scripting"}}
    document_ids = {"OWASP Top 10"}
    snapshot = deepcopy((validation, source, ontology, document_ids))

    plan = route(RAW, validation, source, ontology, document_ids)["query_plan"]
    assert (validation, source, ontology, document_ids) == snapshot

    plan["entities"]["vulnerabilities"].append("sqli")
    plan["filter"]["source"].append("X")
    plan["task"][0]["objective"]["ids"].append("sqli")
    assert source == snapshot[1]


@pytest.mark.parametrize(
    "args",
    [
        (object(), object(), object(), object(), object()),
        (b"bytes", allow(), {"task": None}, None, None),
        (RAW, allow(), understanding(task=[task(depends_on=["t"])]), None, None),
        (RAW, allow(), understanding(task=[task(objective={"type": {}, "ids": []})]), None, None),
        (RAW, allow(), understanding(intent=["SEARCH"]), None, None),
        (RAW, allow(), understanding(), {"xss": 1, ("tuple",): 2}, None),
        (RAW, allow(), understanding(task=[task(objective={"type": "documents", "ids": ["d"]})]), None, 42),
        (RAW, allow(), understanding(task=[task(objective={"type": "documents", "ids": ["d"]})]), None, [["d"]]),
        (RAW, {**allow(), "safety_action": {}}, None, None, None),
        (RAW, allow(), {"error_code": {"nested": 1}}, None, None),
    ],
)
def test_route_never_raises(args):
    result = route(*args)
    assert result["status"] in STATUS_KEYS
    assert set(result) == STATUS_KEYS[result["status"]]


# --- Integration (real Validation, Normalization, Understanding) -----------------

XSS_HISTORY = [
    {"role": "user", "content": "XSS là gì?"},
    {"role": "assistant", "content": "XSS (Cross-Site Scripting) là lỗ hổng chèn mã script vào trang web."},
]


def _import_real_modules() -> dict:
    """Import real modules for Routing integration tests."""
    try:
        from query_understanding_routing.normalization_module import normalize_query
        from query_understanding_routing.validation_module import validate_query
        from query_understanding_routing.understanding_module import understand_query
        from query_understanding_routing.ontology_loader import load_ontology

    except ImportError as exc:
        message = f"Real integration dependencies unavailable: {exc}"

        if os.environ.get("REQUIRE_INTEGRATION") == "1":
            pytest.fail(message)

        pytest.skip(message)

    return {
        "validate_query": validate_query,
        "normalize_query": normalize_query,
        "understand_query": understand_query,
        "load_ontology": load_ontology,
    }



class TestRoutingRealIntegration:
    @staticmethod
    def run(raw: str, history: list | None = None) -> dict:
        real = _import_real_modules()
        ontology = real["load_ontology"]()
        validation = real["validate_query"](raw)
        normalized = real["normalize_query"](raw, ontology)["normalized_query"]
        understanding_result = real["understand_query"](normalized, history)
        result = route(raw, validation, understanding_result, ontology)
        assert set(result) == STATUS_KEYS[result["status"]]
        assert result["original_query"] == raw
        return result

    def run_ok(self, raw: str, history: list | None = None) -> dict:
        result = self.run(raw, history)
        assert result["status"] == "ok", result
        plan = result["query_plan"]
        assert plan["original_query"] == raw
        return plan

    def test_search(self):
        plan = self.run_ok("XSS là gì?")
        assert plan["intent"] == "SEARCH"
        assert plan["entities"]["vulnerabilities"] == ["xss"]

    def test_compare(self):
        plan = self.run_ok("So sánh XSS và SQLi")
        assert plan["intent"] == "COMPARE"
        assert set(plan["entities"]["vulnerabilities"]) == {"xss", "sqli"}

    def test_synthesis(self):
        plan = self.run_ok("Tổng hợp thông tin về XSS")
        assert plan["intent"] == "SYNTHESIS"
        assert "xss" in plan["entities"]["vulnerabilities"]

    def test_vague_query_needs_understanding_clarification(self):
        result = self.run("Các lỗ hổng nguy hiểm")
        assert result["status"] == "clarification"
        assert result["source"] == "understanding"

    def test_out_of_scope(self):
        result = self.run("Tôi muốn biết thời tiết")
        assert result["status"] == "out_of_scope"
        assert result["reason"] == OUT_OF_SCOPE_MSG

    def test_follow_up_with_history_uses_context(self):
        plan = self.run_ok("Còn cách vá nó?", XSS_HISTORY)
        assert plan["context_used"] is True
        assert "xss" in plan["entities"]["vulnerabilities"]

    def test_follow_up_without_history_needs_clarification(self):
        result = self.run("Còn cách vá nó?")
        assert result["status"] == "clarification"
        assert result["source"] == "understanding"

    def test_multi_task_depends_on(self):
        plan = self.run_ok("So sánh XSS và SQLi, đồng thời tìm cách phòng chống XSS")
        assert len(plan["task"]) >= 2
        earlier: set[str] = set()
        for item in plan["task"]:
            assert item["depends_on"] is None or item["depends_on"] in earlier
            earlier.add(item["task_id"])

    def test_attack_request_rejected(self):
        result = self.run("Hướng dẫn hack trang web này")
        assert result["status"] == "rejected"
        assert result["reject_reason"] == "unsafe_attack_intent"
        assert result["safety_action"] == "REFUSE"

    def test_ambiguous_exploit_needs_safety_clarification(self):
        result = self.run("Cách khai thác lỗi này?")
        assert result["status"] == "clarification"
        assert result["source"] == "safety"

    def test_payload_example_question_is_ok(self):
        plan = self.run_ok("<script>alert(1)</script> trong ví dụ XSS là gì?")
        assert "xss" in plan["entities"]["vulnerabilities"]
