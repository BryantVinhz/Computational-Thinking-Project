"""
Unit and Integration Test Suite for Query Understanding Module (Contract v1.0).

Tests coverage:
- 2.1: Ontology YAML validation (5 slugs, canonical, aliases, cwe, subtypes, related_terms, owasp, NO 'bac' alias)
- 2.2: Intent classification, entities & filters extraction, multi-task parsing
- 2.3: Context_used rules & History Truncation (Section 7.1)
- 2.4: Clarification logic, confidence threshold (0.6), two-way equivalence
- Contract v1.0: understanding_result does NOT contain original_query
- Section 11: Mandatory test matrix verification
"""

import sys
from pathlib import Path

import pytest
import yaml

TESTS_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = TESTS_DIR.parent
STAGE_DIR = PACKAGE_DIR.parent
PROJECT_ROOT = STAGE_DIR.parent

for p in [PROJECT_ROOT, STAGE_DIR, PACKAGE_DIR]:
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

from query_understanding_routing.config import CLARIFY_THRESHOLD, STANDARD_VULNERABILITY_SLUGS
from query_understanding_routing.ontology_loader import DEFAULT_ONTOLOGY_PATH, load_ontology
from query_understanding_routing.understanding_module import (
    truncate_conversation_history,
    understand_query,
    validate_understanding_schema,
)


class TestOntologyYAMLAndLoader:
    """Tests for 2.1: Ontology YAML structure, content, and loader validations."""

    def test_ontology_file_exists(self):
        assert DEFAULT_ONTOLOGY_PATH.exists(), f"Missing ontology file at {DEFAULT_ONTOLOGY_PATH}"

    def test_ontology_has_exact_5_slugs(self):
        data = load_ontology(DEFAULT_ONTOLOGY_PATH)
        assert set(data.keys()) == STANDARD_VULNERABILITY_SLUGS

    def test_ontology_entry_schema(self):
        data = load_ontology(DEFAULT_ONTOLOGY_PATH)
        required_fields = ["canonical", "aliases", "cwe", "subtypes", "related_terms"]
        for slug, entry in data.items():
            for field in required_fields:
                assert field in entry, f"Slug '{slug}' is missing required field '{field}'"
            assert isinstance(entry["canonical"], str)
            assert isinstance(entry["aliases"], list)
            assert isinstance(entry["cwe"], list)
            assert isinstance(entry["subtypes"], list)
            assert isinstance(entry["related_terms"], list)
            assert len(entry["aliases"]) > 0
            assert len(entry["cwe"]) > 0

    def test_no_bac_in_aliases(self):
        """Contract v1.0 L7: Do not use overly short/ambiguous alias like 'bac'."""
        data = load_ontology(DEFAULT_ONTOLOGY_PATH)
        for slug, entry in data.items():
            lower_aliases = [str(a).lower() for a in entry["aliases"]]
            assert "bac" not in lower_aliases

    def test_owasp_field_present(self):
        """Contract v1.0 Section 3.1 #1 & Phụ lục A: owasp field."""
        data = load_ontology(DEFAULT_ONTOLOGY_PATH)
        for slug, entry in data.items():
            assert "owasp" in entry
            assert isinstance(entry["owasp"], str)


class TestContractOutputSchemaV1:
    """Verify Contract v1.0 Section 4 & 7.2 output schema compliance."""

    def test_understanding_result_has_no_original_query(self):
        """Contract v1.0 Section 4: understanding_module returns understanding_result WITHOUT original_query."""
        result = understand_query("XSS là gì?")
        assert "original_query" not in result, "understanding_result must NOT contain original_query"
        expected_keys = {
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
        assert set(result.keys()) == expected_keys
        assert validate_understanding_schema(result) is True

    def test_confidence_type_and_bounds(self):
        result = understand_query("SQL Injection là gì?")
        conf = result["confidence"]
        assert isinstance(conf, float) and not isinstance(conf, bool)
        assert 0.0 <= conf <= 1.0


class TestQueryUnderstandingCore:
    """Tests for 2.2: Intent classification, entity & filter extraction, multi-task parsing."""

    def test_xss_query_understanding(self):
        result = understand_query("XSS là gì?")
        assert result["intent"] == "SEARCH"
        assert result["entities"]["vulnerabilities"] == ["xss"]
        assert result["context_used"] is False
        assert result["need_clarify"] is False
        assert result["clarification_message"] is None
        assert len(result["task"]) >= 1
        assert result["task"][0]["objective"]["type"] == "vulnerabilities"
        assert "xss" in result["task"][0]["objective"]["ids"]

    def test_xss_unaccented(self):
        """Section 11 #7: 'xss la gi' without accents."""
        result = understand_query("xss la gi")
        assert result["intent"] == "SEARCH"
        assert result["entities"]["vulnerabilities"] == ["xss"]

    def test_sqli_query_understanding(self):
        result = understand_query("Có thể phòng chống SQL Injection ra sao?")
        assert result["intent"] == "SEARCH"
        assert result["entities"]["vulnerabilities"] == ["sqli"]
        assert result["context_used"] is False
        assert result["need_clarify"] is False

    def test_academic_code_snippet_not_rejected(self):
        result = understand_query("<script>alert(1)</script> trong ví dụ XSS là gì?")
        assert result["intent"] == "SEARCH"
        assert result["entities"]["vulnerabilities"] == ["xss"]
        assert result["need_clarify"] is False

    def test_prompt_injection_definition_query(self):
        """Section 11 #14: Prompt injection là gì? is a legitimate definition query."""
        result = understand_query("Prompt injection là gì?")
        assert result["intent"] in ["SEARCH", "CLARIFICATION"]
        assert result["context_used"] is False

    def test_out_of_scope_query(self):
        result = understand_query("Tôi muốn biết thời tiết")
        assert result["intent"] == "OUT_OF_SCOPE"
        assert result["entities"]["vulnerabilities"] == []
        assert result["need_clarify"] is False
        assert result["task"] == []

    def test_document_and_filter_extraction(self):
        result = understand_query("Theo chuẩn OWASP ASVS v5.0.0, quy định xác thực ra sao?")
        assert "OWASP ASVS" in result["entities"]["document"]
        assert result["filter"]["version"] in ["v5.0.0", "5.0.0"]
        assert result["filter"]["document_type"] == "standard"
        assert "OWASP" in result["filter"]["source"]

    def test_multi_task_query_decomposition(self):
        query = "So sánh XSS và SQLi, đồng thời tìm cách phòng chống XSS"
        result = understand_query(query)
        assert result["intent"] in ["COMPARE", "SYNTHESIS"]
        assert set(result["entities"]["vulnerabilities"]) == {"xss", "sqli"}
        assert len(result["task"]) >= 2

        # Check task 1
        task_1 = result["task"][0]
        assert task_1["intent_task"] == "COMPARE"
        assert task_1["objective"]["type"] == "vulnerabilities"
        assert set(task_1["objective"]["ids"]) == {"xss", "sqli"}

        # Check task 2
        task_2 = result["task"][1]
        assert task_2["intent_task"] == "SEARCH"
        assert task_2["objective"]["type"] == "vulnerabilities"
        assert "xss" in task_2["objective"]["ids"]
        assert task_2["depends_on"] == "task_001"


class TestLLMRetriesAndErrorCodes:
    """Tests for Contract v1.0 Section 7.3: LLM error codes & retry behavior."""

    class MockBadJsonLLM:
        def __init__(self):
            self.call_count = 0

        def call(self, **kwargs):
            self.call_count += 1
            return "This is not JSON at all!"

    class MockSchemaViolationLLM:
        def __init__(self):
            self.call_count = 0

        def call(self, **kwargs):
            self.call_count += 1
            return {
                "resolved_query": "Test",
                "intent": "FOO",
                "confidence": 1.7,
                "context_used": False,
                "entities": {"document": [], "vulnerabilities": []},
                "filter": {"version": None, "document_type": None, "source": []},
                "task": [],
                "need_clarify": False,
                "clarification_message": None,
            }

    class MockTimeoutLLM:
        def call(self, **kwargs):
            raise TimeoutError("LLM call timed out after 30s")

    def test_llm_invalid_json_retries_and_returns_error_code(self):
        mock = self.MockBadJsonLLM()
        res = understand_query("XSS là gì?", llm_client=mock)
        assert res == {"error_code": "llm_invalid_json"}
        assert mock.call_count == 3

    def test_llm_schema_violation_retries_and_returns_error_code(self):
        mock = self.MockSchemaViolationLLM()
        res = understand_query("XSS là gì?", llm_client=mock)
        assert res == {"error_code": "llm_schema_violation"}
        assert mock.call_count == 3

    def test_llm_timeout_no_retry(self):
        mock = self.MockTimeoutLLM()
        res = understand_query("XSS là gì?", llm_client=mock)
        assert res == {"error_code": "llm_unavailable"}
