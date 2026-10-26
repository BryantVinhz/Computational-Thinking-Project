"""
Tests for Clarification Logic and Confidence Threshold (Contract v1.0 Section 2.4).
Author: Long (Query Understanding Lead)
"""

import pytest
from config import CLARIFY_THRESHOLD
from understanding_module import understand_query


class TestClarificationLogic:
    """Tests for Contract v1.0 Section 2.4 and Section 0.2 L8."""

    def test_vague_query_triggers_clarification(self):
        """Matrix #20: 'Các lỗ hổng nguy hiểm' -> clarification, source=understanding."""
        result = understand_query("Các lỗ hổng nguy hiểm")
        assert result["intent"] == "CLARIFICATION"
        assert result["need_clarify"] is True
        assert result["clarification_message"] is not None
        assert len(result["clarification_message"]) > 0
        assert result["task"] == []

    def test_two_way_equivalence_when_clarify_false(self):
        """Contract v1.0 L8: intent=CLARIFICATION iff need_clarify=true."""
        result = understand_query("Cross-Site Scripting hoạt động ra sao?")
        assert result["need_clarify"] is False
        assert result["intent"] != "CLARIFICATION"
        assert result["clarification_message"] is None

    def test_two_way_equivalence_when_clarify_true(self):
        result = understand_query("Lỗ hổng nguy hiểm")
        assert result["need_clarify"] is True
        assert result["intent"] == "CLARIFICATION"
        assert isinstance(result["clarification_message"], str)
        assert len(result["clarification_message"].strip()) > 0

    def test_clarify_threshold_default_is_0_6(self):
        assert CLARIFY_THRESHOLD == 0.6

    def test_low_confidence_forces_clarification(self):
        result = understand_query("Bảo mật ứng dụng", confidence_threshold=0.99)
        assert result["need_clarify"] is True
        assert result["intent"] == "CLARIFICATION"
        assert result["clarification_message"] is not None

    def test_empty_query_safe_clarification(self):
        result = understand_query("   ")
        assert result["need_clarify"] is True
        assert result["intent"] == "CLARIFICATION"
        assert result["clarification_message"] is not None
