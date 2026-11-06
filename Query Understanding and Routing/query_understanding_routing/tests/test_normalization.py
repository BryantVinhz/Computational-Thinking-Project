"""Tests for query normalization (Phase A and Phase B)."""

import logging
import os
import random
import unicodedata
from copy import deepcopy

import pytest

from query_understanding_routing.normalization_module import (
    _build_matcher,
    _ontology_entries,
    normalize_query,
)

# Small ontology in the real ontology.yaml schema.
TEST_ONTOLOGY = {
    "xss": {
        "canonical": "Cross-Site Scripting",
        "aliases": ["xss", "cross-site scripting", "cross site scripting", "lỗi xss"],
        "cwe": ["CWE-79"],
        "owasp": "A05:2025",
        "subtypes": ["stored", "stored xss", "dom-based xss"],
        "related_terms": ["csp", "x-xss-protection"],
    },
    "sqli": {
        "canonical": "SQL Injection",
        "aliases": [
            "sqli",
            "sql injection",
            "sql-injection",
            "sql injection vulnerability",
            "lỗi sql injection",
        ],
        "cwe": ["CWE-89"],
        "owasp": "A05:2025",
        "subtypes": ["union-based", "blind"],
        "related_terms": ["prepared statement", "orm"],
    },
    "bac": {
        "canonical": "Broken Access Control",
        "aliases": ["broken access control", "lỗi phân quyền"],
        "cwe": ["CWE-284"],
        "owasp": "A01:2025",
        "subtypes": ["idor", "cors misconfiguration"],
        "related_terms": ["rbac"],
    },
    "auth_failures": {
        "canonical": "Authentication Failures",
        "aliases": ["auth_failures", "broken authentication"],
        "cwe": ["CWE-287"],
        "owasp": "A07:2025",
        "subtypes": ["credential stuffing"],
        "related_terms": ["mfa"],
    },
    "misconfig": {
        "canonical": "Security Misconfiguration",
        "aliases": ["misconfig", "security misconfiguration", "misconfiguration"],
        "cwe": ["CWE-16"],
        "owasp": "A02:2025",
        "subtypes": ["verbose error messages"],
        "related_terms": ["hardening", "cors"],
    },
}


def _normalized(raw: str, ontology: dict | None = TEST_ONTOLOGY) -> str:
    return normalize_query(raw, ontology)["normalized_query"]


def _alias_pairs(ontology: dict) -> list[tuple[str, str]]:
    return [
        (alias, entry["canonical"])
        for entry in ontology.values()
        for alias in (entry["canonical"], *entry["aliases"])
    ]


def _protected_phrases(ontology: dict) -> list[str]:
    _, table = _build_matcher(_ontology_entries(ontology))
    return sorted(key for key, value in table.items() if value is None)


def test_case_26_regular_query_collapses_whitespace() -> None:
    raw = "  XSS   là   gì?\t "
    result = normalize_query(raw)
    assert result["original_query"] == raw
    assert result["normalized_query"] == "XSS là gì?"
    assert result["payload_like"] is False


def test_case_8_script_payload_preserved() -> None:
    raw = "  Giải thích payload <script>alert(1)</script>   trong XSS  "
    result = normalize_query(raw)
    assert result["payload_like"] is True
    assert "<script>alert(1)</script>" in result["normalized_query"]
    assert result["normalized_query"] == raw.strip()


def test_case_9_sql_payload_internal_spacing_preserved() -> None:
    raw = "  Phân tích payload SQL Injection này để hiểu cách phòng chống:   '  OR   1=1  --  \t"
    result = normalize_query(raw)
    assert result["payload_like"] is True
    assert result["normalized_query"] == (
        "Phân tích payload SQL Injection này để hiểu cách phòng chống:   '  OR   1=1  --"
    )


@pytest.mark.parametrize("raw", ["xssx là gì", "XSS là lỗ hổng phổ biến"])
def test_case_28_no_alias_replacement_without_ontology(raw: str) -> None:
    result = normalize_query(raw, None)
    assert result["normalized_query"] == raw
    assert result == normalize_query(raw)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("xssx là gì", "xssx là gì"),
        ("XSS là lỗ hổng phổ biến", "Cross-Site Scripting là lỗ hổng phổ biến"),
    ],
)
def test_case_28_alias_replacement_with_ontology(raw: str, expected: str) -> None:
    assert _normalized(raw) == expected


def test_decomposed_vietnamese_becomes_nfc_in_regular_mode() -> None:
    result = normalize_query("ca\u0301ch va\u0301")
    assert result["normalized_query"] == "c\u00e1ch v\u00e1"


def test_decomposed_vietnamese_untouched_in_conservative_mode() -> None:
    raw = "ca\u0301ch va\u0301 <b>"
    result = normalize_query(raw)
    assert result["payload_like"] is True
    assert result["normalized_query"] == raw


def test_zero_width_and_bom_removed_in_regular_mode() -> None:
    result = normalize_query("\ufeffXSS\u200b là\u200c gì\u200d\u2060")
    assert result["normalized_query"] == "XSS là gì"


def test_zero_width_and_bom_kept_in_conservative_mode() -> None:
    raw = "\ufeffXSS\u200b <b>\u200c gì\u200d\u2060"
    result = normalize_query(raw)
    assert result["payload_like"] is True
    assert result["normalized_query"] == raw


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("XSS\r\nlà\rgì", "XSS\nlà\ngì"),
        ("a = 1\r\nb\rc", "a = 1\nb\nc"),
    ],
)
def test_carriage_returns_become_newlines_in_both_modes(raw: str, expected: str) -> None:
    assert normalize_query(raw)["normalized_query"] == expected


def test_excess_blank_lines_collapse_in_regular_mode() -> None:
    result = normalize_query("XSS\n  \n\t\n\nlà gì")
    assert result["normalized_query"] == "XSS\n\nlà gì"


def test_excess_blank_lines_untouched_in_conservative_mode() -> None:
    raw = "<b>\n  \n\n\nx"
    assert normalize_query(raw)["normalized_query"] == raw


def test_single_unmatched_backtick_is_payload_like() -> None:
    assert normalize_query("Lệnh `ls là gì")["payload_like"] is True


@pytest.mark.parametrize("raw", ["100%", "giảm 50% chi phí", "50% off"])
def test_plain_percent_not_payload_like(raw: str) -> None:
    assert normalize_query(raw)["payload_like"] is False


@pytest.mark.parametrize("raw", ["%3Cscript", "ký tự \\x41 là gì"])
def test_encoded_bytes_are_payload_like(raw: str) -> None:
    assert normalize_query(raw)["payload_like"] is True


def test_known_limitation_parentheses_mark_plain_query_payload_like() -> None:
    raw = "XSS (Cross-Site Scripting) là gì"
    result = normalize_query(raw)
    assert result["payload_like"] is True
    assert result["normalized_query"] == raw


def test_zero_width_only_input_returns_empty_string() -> None:
    raw = "\u200b\u200c\ufeff"
    result = normalize_query(raw)
    assert result["normalized_query"] == ""
    assert result["original_query"] == raw


@pytest.mark.parametrize(
    "raw",
    [
        "  XSS   là   gì?\t ",
        "ca\u0301ch\u00a0\u00a0va\u0301",
        "XSS\r\n\r\n\r\n  là\u3000gì  ",
        "\u200bSQL\tInjection\n\n\n\n\ufeff",
    ],
)
def test_regular_mode_is_idempotent(raw: str) -> None:
    once = normalize_query(raw)["normalized_query"]
    assert normalize_query(once)["normalized_query"] == once


@pytest.mark.parametrize("invalid_input", [None, 123, b"abc"])
def test_non_str_input_raises_type_error(invalid_input) -> None:
    with pytest.raises(TypeError):
        normalize_query(invalid_input)


@pytest.mark.parametrize("raw", ["XSS là gì?", "<script>alert(1)</script>"])
def test_result_has_exactly_three_typed_keys(raw: str) -> None:
    result = normalize_query(raw)
    assert set(result) == {"original_query", "normalized_query", "payload_like"}
    assert isinstance(result["original_query"], str)
    assert isinstance(result["normalized_query"], str)
    assert isinstance(result["payload_like"], bool)


@pytest.mark.parametrize(
    "raw",
    [
        "-\u200b- comment",
        "/\u200b* x",
        "%\u200b3Cscript",
        "\\\\x\u200b41 là gì",
    ],
)
def test_zero_width_cannot_hide_payload_patterns(raw: str) -> None:
    result = normalize_query(raw)
    assert result["payload_like"] is True
    assert result["normalized_query"] == raw.strip()


def test_plain_query_with_zero_width_is_not_payload_like() -> None:
    result = normalize_query("XSS\u200b là gì")
    assert result["payload_like"] is False
    assert result["normalized_query"] == "XSS là gì"


# Phase B: alias mapping


@pytest.mark.parametrize("raw", ["sqli", "SqLi", "SQLI"])
def test_alias_is_case_insensitive(raw: str) -> None:
    assert _normalized(raw) == "SQL Injection"


def test_long_alias_replaced_once() -> None:
    assert _normalized("sql injection vulnerability") == "SQL Injection"


def test_nested_alias_uses_longest_match() -> None:
    assert _normalized("lỗi sql injection") == "SQL Injection"


@pytest.mark.parametrize(
    "raw",
    ["lỗi phân quyền", unicodedata.normalize("NFD", "lỗi phân quyền"), "LỖI PHÂN QUYỀN"],
)
def test_vietnamese_alias_replaced(raw: str) -> None:
    assert _normalized(raw) == "Broken Access Control"


def test_vietnamese_alias_is_diacritic_sensitive() -> None:
    assert _normalized("loi phan quyen") == "loi phan quyen"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("xss và sqli", "Cross-Site Scripting và SQL Injection"),
        ("xss/sqli", "Cross-Site Scripting/SQL Injection"),
        ("sqli, xss", "SQL Injection, Cross-Site Scripting"),
    ],
)
def test_multiple_aliases_replaced(raw: str, expected: str) -> None:
    assert _normalized(raw) == expected


@pytest.mark.parametrize("raw", ["xssx", "my_xss", "x-xss-protection", "xss-based", "my_auth_failures"])
def test_alias_inside_word_unchanged(raw: str) -> None:
    assert _normalized(raw) == raw


def test_alias_with_underscore_replaced() -> None:
    assert _normalized("auth_failures") == "Authentication Failures"


def test_cross_slug_phrase_is_protected() -> None:
    assert _normalized("CORS misconfiguration") == "CORS misconfiguration"
    assert _normalized("misconfiguration là gì") == "Security Misconfiguration là gì"


def test_protected_phrases_computed_from_ontology() -> None:
    assert _protected_phrases(TEST_ONTOLOGY) == ["cors misconfiguration"]


def test_same_slug_subtype_is_not_protected() -> None:
    assert _normalized("stored XSS") == "stored Cross-Site Scripting"


@pytest.mark.parametrize("raw", ["prepared statement", "bac", "BAC", "stored", "cors"])
def test_non_alias_terms_unchanged(raw: str) -> None:
    assert _normalized(raw) == raw


@pytest.mark.parametrize(
    "raw",
    ["`xss`", '<script>alert("xss")</script>', "XSS (Cross-Site Scripting) là gì"],
)
def test_payload_like_query_skips_alias_mapping(raw: str) -> None:
    result = normalize_query(raw, TEST_ONTOLOGY)
    assert result["payload_like"] is True
    assert result == normalize_query(raw)


def test_alias_does_not_match_across_newline() -> None:
    assert _normalized("SQL\nInjection") == "SQL\nInjection"


def test_multi_word_alias_matches_any_horizontal_whitespace() -> None:
    assert _normalized("SQL\t\u00a0Injection") == "SQL Injection"


def test_zero_width_inside_alias_removed_before_mapping() -> None:
    assert _normalized("x\u200bss") == "Cross-Site Scripting"


@pytest.mark.parametrize("ontology", [None, {}])
def test_missing_ontology_keeps_phase_a_result(ontology: dict | None) -> None:
    assert normalize_query("xss là gì", ontology) == normalize_query("xss là gì")


@pytest.mark.parametrize(
    "ontology",
    [
        ["xss"],
        {"xss": "Cross-Site Scripting"},
        {"aliases": {"xss": "Cross-Site Scripting"}},
        {"xss": {"aliases": ["xss"]}},
        {"xss": {"canonical": "  ", "aliases": ["xss"]}},
        {"xss": {"canonical": 42, "aliases": ["xss"]}},
        {"xss": {"canonical": "Cross-Site Scripting", "aliases": "xss"}},
        {"xss": {"canonical": "Cross-Site Scripting", "aliases": [None, 1, ["xss"]]}},
        {"xss": {"canonical": "Cross-Site Scripting", "aliases": None, "subtypes": "xss"}},
        {1: {"canonical": "Cross-Site Scripting", "aliases": ["xss"]}},
    ],
)
def test_malformed_ontology_keeps_query_unchanged(ontology, caplog) -> None:
    raw = "xss là gì"
    with caplog.at_level(logging.WARNING):
        result = normalize_query(raw, ontology)
    assert result == normalize_query(raw)
    assert caplog.records
    assert all(raw not in record.getMessage() for record in caplog.records)


def test_malformed_entry_skipped_valid_entries_still_mapped() -> None:
    ontology = deepcopy(TEST_ONTOLOGY)
    ontology["xss"]["canonical"] = None
    ontology["bac"] = "broken"
    assert _normalized("xss và sqli", ontology) == "xss và SQL Injection"


def test_alias_shared_by_two_slugs_is_ignored(caplog) -> None:
    ontology = deepcopy(TEST_ONTOLOGY)
    ontology["sqli"]["aliases"].append("XSS")
    with caplog.at_level(logging.WARNING):
        assert _normalized("xss và sqli", ontology) == "xss và SQL Injection"
    assert caplog.records


@pytest.mark.parametrize("raw", ["sqli là gì", "<b>sqli", "", "   "])
def test_result_shape_with_ontology(raw: str) -> None:
    result = normalize_query(raw, TEST_ONTOLOGY)
    assert set(result) == {"original_query", "normalized_query", "payload_like"}
    assert result["original_query"] is raw


@pytest.mark.parametrize("alias,canonical", _alias_pairs(TEST_ONTOLOGY))
def test_every_alias_maps_to_canonical(alias: str, canonical: str) -> None:
    assert _normalized(alias) == canonical
    assert _normalized(f"{alias.upper()} là gì") == f"{canonical} là gì"


@pytest.mark.parametrize("alias", [alias for alias, _ in _alias_pairs(TEST_ONTOLOGY)])
def test_alias_mapping_is_idempotent(alias: str) -> None:
    for raw in (alias, f"so sánh {alias} và sqli"):
        once = _normalized(raw)
        assert _normalized(once) == once


def test_shuffled_ontology_gives_same_result() -> None:
    rng = random.Random(0)
    queries = [alias for alias, _ in _alias_pairs(TEST_ONTOLOGY)] + ["CORS misconfiguration", "stored xss"]
    expected = [_normalized(q) for q in queries]
    for _ in range(5):
        slugs = list(TEST_ONTOLOGY)
        rng.shuffle(slugs)
        shuffled = {}
        for slug in slugs:
            entry = deepcopy(TEST_ONTOLOGY[slug])
            for field in ("aliases", "subtypes", "related_terms"):
                rng.shuffle(entry[field])
            shuffled[slug] = entry
        assert [_normalized(q, shuffled) for q in queries] == expected


def test_ontology_is_not_mutated() -> None:
    before = deepcopy(TEST_ONTOLOGY)
    for alias, _ in _alias_pairs(TEST_ONTOLOGY):
        _normalized(alias)
    assert TEST_ONTOLOGY == before


def test_cached_matcher_is_reused() -> None:
    _normalized("xss")
    hits = _build_matcher.cache_info().hits
    _normalized("sqli")
    _normalized("misconfig", deepcopy(TEST_ONTOLOGY))
    assert _build_matcher.cache_info().hits == hits + 2


def test_cache_does_not_hold_caller_dict() -> None:
    ontology = deepcopy(TEST_ONTOLOGY)
    assert _normalized("xss", ontology) == "Cross-Site Scripting"
    ontology["xss"]["canonical"] = "XSS Attack"
    assert _normalized("xss", ontology) == "XSS Attack"


# Integration: real ontology loaded from query_understanding_routing/ontology.yaml;
# run from the repository root.


def _load_real_ontology() -> dict:
    # Called inside each test so REQUIRE_INTEGRATION=1 reports FAILED, not ERROR.
    try:
        import yaml  # noqa: F401

        from query_understanding_routing.ontology_loader import load_ontology
    except ImportError as exc:
        message = f"query_understanding_routing.ontology_loader or PyYAML not importable: {exc}"
        if os.environ.get("REQUIRE_INTEGRATION") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return load_ontology()


def test_real_ontology_aliases_map_to_owning_canonical() -> None:
    real_ontology = _load_real_ontology()
    for alias, canonical in _alias_pairs(real_ontology):
        assert _normalized(alias, real_ontology) == canonical, alias


def test_real_ontology_alias_mapping_is_idempotent() -> None:
    real_ontology = _load_real_ontology()
    for alias, _ in _alias_pairs(real_ontology):
        once = _normalized(alias, real_ontology)
        assert _normalized(once, real_ontology) == once, alias


def test_real_ontology_related_terms_and_protected_phrases_unchanged() -> None:
    real_ontology = _load_real_ontology()
    terms = [term for entry in real_ontology.values() for term in entry["related_terms"]]
    for term in terms + _protected_phrases(real_ontology):
        assert _normalized(term, real_ontology) == term


def test_real_ontology_protected_phrases() -> None:
    real_ontology = _load_real_ontology()
    protected = _protected_phrases(real_ontology)
    assert protected == ["cors misconfiguration"], (
        f"Protected phrases changed to {protected}; review the new cross-slug conflicts "
        "in ontology.yaml and update this test."
    )
    assert _normalized("CORS misconfiguration", real_ontology) == "CORS misconfiguration"
    assert _normalized("misconfiguration", real_ontology) == "Security Misconfiguration"
