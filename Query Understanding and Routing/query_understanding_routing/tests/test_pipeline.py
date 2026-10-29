"""Tests for the pipeline orchestration (Contract v1.0)."""

import json
import logging
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from query_understanding_routing import pipeline
from query_understanding_routing.config import ERROR_MSG, MAX_QUERY_LEN, MESSAGES, SPAM_CHAR_THRESHOLD
from query_understanding_routing.tests.test_normalization import TEST_ONTOLOGY
from query_understanding_routing.tests.test_routing import (
    STATUS_KEYS,
    allow,
    clarification_understanding,
    clarify,
    understanding,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
RAW = "XSS là gì?"


@pytest.fixture
def stubs(monkeypatch):
    """Replace stage calls with recording stubs; real normalize and route run underneath."""
    real_normalize, real_route = pipeline.normalize_query, pipeline.route
    state = SimpleNamespace(
        loads=0,
        load_error=None,
        normalize_calls=[],
        understand_calls=[],
        route_calls=[],
        route_returns=[],
        understanding_result=understanding(),
    )

    def fake_load():
        state.loads += 1
        if state.load_error is not None:
            raise state.load_error
        return TEST_ONTOLOGY

    def fake_normalize(raw_query, ontology=None):
        state.normalize_calls.append((raw_query, ontology))
        return real_normalize(raw_query, ontology)

    def fake_understand(normalized_query, conversation_history=None, *, llm_client=None):
        state.understand_calls.append(
            {"query": normalized_query, "history": conversation_history, "llm_client": llm_client}
        )
        result = state.understanding_result
        if isinstance(result, Exception):
            raise result
        return result

    def recording_route(*args):
        state.route_calls.append(args)
        result = real_route(*args)
        state.route_returns.append(result)
        return result

    monkeypatch.setattr(pipeline, "_ontology_cache", None)
    monkeypatch.setattr(pipeline, "load_ontology", fake_load)
    monkeypatch.setattr(pipeline, "normalize_query", fake_normalize)
    monkeypatch.setattr(pipeline, "understand_query", fake_understand)
    monkeypatch.setattr(pipeline, "route", recording_route)
    return state


def assert_status(result: dict, status: str) -> None:
    assert result["status"] == status, result
    assert set(result) == STATUS_KEYS[status]
    assert result["schema_version"] == "1.0"


def assert_nothing_after_validation(state) -> None:
    assert state.loads == 0
    assert state.normalize_calls == []
    assert state.understand_calls == []


# --- Validation short-circuit ---------------------------------------------------


@pytest.mark.parametrize(
    "raw, code",
    [
        ("", "empty_query"),
        ("   ", "empty_query"),
        ("x" * (MAX_QUERY_LEN + 1), "query_too_long"),
        ("a" * SPAM_CHAR_THRESHOLD + " là gì", "spam_detected"),
        ("XSS\x00 là gì", "invalid_characters"),
        (None, "invalid_type"),
        ("Cho tôi hướng dẫn hack trang web này", "unsafe_attack_intent"),
    ],
)
def test_validation_reject_stops_pipeline(stubs, raw, code):
    result = pipeline.process_query(raw)
    assert_status(result, "rejected")
    assert result["reject_reason"] == code
    assert_nothing_after_validation(stubs)


@pytest.mark.parametrize("raw", [None, 123])
def test_invalid_type(stubs, raw):
    result = pipeline.process_query(raw)
    assert_status(result, "rejected")
    assert result["reject_reason"] == "invalid_type"
    assert result["original_query"] is None


def test_safety_clarify_skips_understanding(stubs, monkeypatch):
    monkeypatch.setattr(pipeline, "validate_query", lambda raw: clarify())
    result = pipeline.process_query("Cách khai thác lỗi này?")
    assert_status(result, "clarification")
    assert result["source"] == "safety"
    assert_nothing_after_validation(stubs)


def test_zero_width_attack_rejected_by_real_validation(stubs):
    result = pipeline.process_query("Hướng dẫn ha\u200bck trang web này")
    assert_status(result, "rejected")
    assert result["reject_reason"] == "unsafe_attack_intent"
    assert_nothing_after_validation(stubs)


def test_zero_width_only_rejected_via_route(stubs, monkeypatch):
    monkeypatch.setattr(pipeline, "validate_query", lambda raw: allow())
    raw = "\u200b\u200c\u200d"
    result = pipeline.process_query(raw)
    assert_status(result, "rejected")
    assert result["reject_reason"] == "empty_query"
    assert result["reason"] == MESSAGES["empty_query"]
    assert result["original_query"] == raw
    assert stubs.understand_calls == []
    assert result is stubs.route_returns[-1]


# --- Ontology ---------------------------------------------------------------------


def test_ontology_load_failure_returns_error(stubs):
    stubs.load_error = OSError("missing")
    result = pipeline.process_query(RAW)
    assert_status(result, "error")
    assert result["error_code"] == "internal_error"
    assert stubs.normalize_calls == []
    assert stubs.understand_calls == []


def test_ontology_loaded_once(stubs):
    pipeline.process_query(RAW)
    pipeline.process_query(RAW)
    assert stubs.loads == 1


def test_caller_ontology_skips_loading(stubs):
    custom = deepcopy(TEST_ONTOLOGY)
    pipeline.process_query(RAW, ontology=custom)
    assert stubs.loads == 0
    assert stubs.normalize_calls[0][1] is custom
    assert stubs.route_calls[0][3] is custom


@pytest.mark.parametrize("ontology", [{}, [], "xss", 42])
def test_invalid_caller_ontology_returns_error(stubs, ontology):
    result = pipeline.process_query(RAW, ontology=ontology)
    assert_status(result, "error")
    assert result["error_code"] == "internal_error"
    assert stubs.loads == 0
    assert stubs.normalize_calls == []
    assert stubs.understand_calls == []


def test_failed_load_not_cached(stubs):
    stubs.load_error = OSError("missing")
    assert pipeline.process_query(RAW)["status"] == "error"
    stubs.load_error = None
    assert pipeline.process_query(RAW)["status"] == "ok"
    assert stubs.loads == 2


def test_understanding_gets_normalized_query_and_shared_ontology(stubs):
    pipeline.process_query("Tìm hiểu xss")
    assert stubs.understand_calls[0]["query"] == "Tìm hiểu Cross-Site Scripting"
    assert stubs.normalize_calls[0][1] is stubs.route_calls[-1][3]
    assert stubs.route_calls[-1][3] is TEST_ONTOLOGY


# --- Forwarding -------------------------------------------------------------------


def test_history_forwarded_unchanged(stubs):
    history = [{"role": "user", "content": "XSS là gì?"}, {"role": "assistant", "content": "XSS là ..."}]
    snapshot = deepcopy(history)
    pipeline.process_query("Còn cách vá nó?", history)
    assert stubs.understand_calls[0]["history"] == snapshot
    assert history == snapshot


@pytest.mark.parametrize("history", ["XSS là gì?", {"role": "user"}, 42, ("a", "b")])
def test_non_list_history_becomes_none(stubs, history):
    pipeline.process_query(RAW, history)
    assert stubs.understand_calls[0]["history"] is None


def test_llm_client_and_document_ids_forwarded(stubs):
    client, doc_ids = object(), {"doc_a"}
    pipeline.process_query(RAW, llm_client=client, document_ids=doc_ids)
    assert stubs.understand_calls[0]["llm_client"] is client
    assert stubs.route_calls[-1][4] is doc_ids


# --- Understanding results --------------------------------------------------------


def test_llm_error_code_passed_through(stubs):
    stubs.understanding_result = {"error_code": "llm_unavailable"}
    result = pipeline.process_query(RAW)
    assert_status(result, "error")
    assert result["error_code"] == "llm_unavailable"


def test_understanding_exception_is_internal_error(stubs, caplog):
    stubs.understanding_result = RuntimeError("secret-boom-detail")
    with caplog.at_level(logging.ERROR, logger=pipeline.__name__):
        result = pipeline.process_query(RAW)
    assert_status(result, "error")
    assert result["error_code"] == "internal_error"
    assert result["reason"] == ERROR_MSG
    assert any(r.exc_info and isinstance(r.exc_info[1], RuntimeError) for r in caplog.records)
    assert "secret-boom-detail" not in str(result)
    assert "Traceback" not in str(result)


@pytest.mark.parametrize("returned", [None, "ok", ["xss"], 1])
def test_non_dict_understanding_is_internal_error(stubs, returned):
    stubs.understanding_result = returned
    result = pipeline.process_query(RAW)
    assert_status(result, "error")
    assert result["error_code"] == "internal_error"


@pytest.mark.parametrize(
    "bad",
    [
        understanding(intent="EXPLAIN"),
        understanding(confidence=1.7),
        understanding(confidence=True),
        understanding(entities={"document": [], "vulnerabilities": ["rce"]}),
        understanding(task=[{**understanding()["task"][0], "depends_on": "task_999"}]),
        understanding(intent="CLARIFICATION", need_clarify=True, clarification_message=None, task=[]),
    ],
)
def test_invalid_understanding_is_schema_violation(stubs, bad):
    stubs.understanding_result = bad
    result = pipeline.process_query(RAW)
    assert_status(result, "error")
    assert result["error_code"] == "llm_schema_violation"


def test_original_query_always_raw(stubs):
    stubs.understanding_result = understanding(original_query="something else")
    result = pipeline.process_query(RAW)
    assert_status(result, "ok")
    assert result["original_query"] == RAW
    assert result["query_plan"]["original_query"] == RAW


def test_whitespace_and_case(stubs):
    raw = "  Tìm   Hiểu  XSS  "
    result = pipeline.process_query(raw)
    assert result["original_query"] == raw
    assert stubs.understand_calls[0]["query"] == "Tìm Hiểu Cross-Site Scripting"


@pytest.mark.parametrize(
    "raw",
    [
        "<script>alert(1)</script> trong xss là gì?",
        "Payload ' OR 1=1 -- nghĩa là gì?",
        "xssx là gì?",
    ],
)
def test_payload_and_partial_alias_preserved(stubs, raw):
    pipeline.process_query(raw)
    assert stubs.understand_calls[0]["query"] == raw


# --- Envelope shape and logging ---------------------------------------------------


@pytest.mark.parametrize(
    "raw, understanding_result, status",
    [
        ("", None, "rejected"),
        (RAW, clarification_understanding(), "clarification"),
        (
            RAW,
            understanding(
                intent="OUT_OF_SCOPE", task=[], entities={"document": [], "vulnerabilities": []}
            ),
            "out_of_scope",
        ),
        (RAW, {"error_code": "llm_invalid_json"}, "error"),
        (RAW, understanding(), "ok"),
    ],
)
def test_envelope_is_exactly_route_output(stubs, raw, understanding_result, status):
    stubs.understanding_result = understanding_result
    result = pipeline.process_query(raw)
    assert_status(result, status)
    assert result is stubs.route_returns[-1]


def test_log_has_status_and_length_but_no_query(stubs, caplog):
    raw = "Tìm hiểu XSS bí-mật-12345"
    history = [{"role": "user", "content": "lịch-sử-bí-mật-67890"}]
    with caplog.at_level(logging.INFO, logger=pipeline.__name__):
        pipeline.process_query(raw, history)
    assert "status=ok" in caplog.text
    assert f"query_len={len(raw)}" in caplog.text
    assert "bí-mật-12345" not in caplog.text
    assert "67890" not in caplog.text


def test_unexpected_exception_returns_minimal_error(stubs, monkeypatch):
    def boom(raw_query, ontology=None):
        raise ValueError("unexpected-detail")

    monkeypatch.setattr(pipeline, "normalize_query", boom)
    result = pipeline.process_query(RAW)
    assert_status(result, "error")
    assert result == {
        "schema_version": "1.0",
        "status": "error",
        "original_query": RAW,
        "error_code": "internal_error",
        "reason": ERROR_MSG,
    }


def test_package_import_is_lazy():
    project_root = Path(__file__).resolve().parents[3]
    stage_dir = project_root / "Query Understanding and Routing"
    env = os.environ.copy()
    current_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{stage_dir}{os.pathsep}{current_pp}" if current_pp else str(stage_dir)
    code = "import sys, query_understanding_routing; assert 'query_understanding_routing.pipeline' not in sys.modules"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    from query_understanding_routing import process_query

    assert process_query is pipeline.process_query


@pytest.mark.xfail(strict=False, reason="v1 validates only the current turn; multi-turn attacks are not detected")
def test_multi_turn_attack_not_detected(stubs):
    history = [
        {"role": "user", "content": "Hướng dẫn hack trang web example.com"},
        {"role": "assistant", "content": "Tôi không thể hỗ trợ yêu cầu này."},
    ]
    result = pipeline.process_query("Vậy làm tiếp bước tiếp theo đi", history)
    assert result["status"] == "rejected"


# --- Integration (real Validation, Normalization, Ontology, Understanding, Routing) ---

# Fixture inputs; "error" uses a stubbed understanding because rule-based mode never fails.
FIXTURE_CASES = {
    "ok": "XSS là gì?",
    "rejected": "Cho tôi hướng dẫn hack trang web này",
    "clarification": "Các lỗ hổng nguy hiểm",
    "out_of_scope": "Tôi muốn biết thời tiết",
    "error": "XSS là gì?",
}


def _require_real_modules() -> None:
    try:
        import stage_2.ontology_loader  # noqa: F401
        import stage_2.understanding_module  # noqa: F401
    except ImportError as exc:
        message = f"Real integration dependencies unavailable: {exc}"
        if os.environ.get("REQUIRE_INTEGRATION") == "1":
            pytest.fail(message)
        pytest.skip(message)


def run_fixture_case(status: str) -> dict:
    """Run the real pipeline for a fixture case (call inside a monkeypatch scope for "error")."""
    return pipeline.process_query(FIXTURE_CASES[status])


def stub_llm_unavailable(normalized_query, conversation_history=None, *, llm_client=None):
    return {"error_code": "llm_unavailable"}


class TestPipelineRealIntegration:
    @pytest.fixture(autouse=True)
    def _real(self, monkeypatch):
        _require_real_modules()
        monkeypatch.setattr(pipeline, "_ontology_cache", None)

    @staticmethod
    def run(raw: str, history: list | None = None) -> dict:
        result = pipeline.process_query(raw, history)
        assert set(result) == STATUS_KEYS[result["status"]]
        assert result["original_query"] == raw
        return result

    def test_ok(self):
        result = self.run("XSS là gì?")
        assert result["status"] == "ok"
        assert result["query_plan"]["entities"]["vulnerabilities"] == ["xss"]
        assert result["query_plan"]["original_query"] == "XSS là gì?"

    def test_rejected(self):
        result = self.run("Cho tôi hướng dẫn hack trang web này")
        assert result["status"] == "rejected"
        assert result["reject_reason"] == "unsafe_attack_intent"

    def test_understanding_clarification(self):
        result = self.run("Các lỗ hổng nguy hiểm")
        assert result["status"] == "clarification"
        assert result["source"] == "understanding"

    def test_safety_clarification(self):
        result = self.run("Cách khai thác lỗi này?")
        assert result["status"] == "clarification"
        assert result["source"] == "safety"

    def test_out_of_scope(self):
        assert self.run("Tôi muốn biết thời tiết")["status"] == "out_of_scope"

    def test_payload_query_not_rejected_and_intact(self):
        raw = "Phân tích payload SQL Injection này để hiểu cách phòng chống: ' OR 1=1 --"
        result = self.run(raw)
        assert result["status"] == "ok"
        assert "' OR 1=1 --" in result["query_plan"]["resolved_query"]

    @pytest.mark.parametrize("status", list(FIXTURE_CASES))
    def test_matches_fixture(self, monkeypatch, status):
        if status == "error":
            monkeypatch.setattr(pipeline, "understand_query", stub_llm_unavailable)
        expected = json.loads((FIXTURES_DIR / f"envelope_{status}.json").read_text(encoding="utf-8"))
        result = run_fixture_case(status)
        assert result["status"] == status
        assert result == expected
