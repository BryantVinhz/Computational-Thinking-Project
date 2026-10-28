"""Structural and safety validation for query understanding and routing."""

import re
import unicodedata
from typing import Literal, TypedDict

from .config import (
    ATTACK_VERB_PATTERN,
    EXPLAIN_PATTERN,
    INJECTION_PATTERNS,
    INJECTION_TOPIC_PATTERN,
    MAX_QUERY_LEN,
    MESSAGES,
    MIN_QUERY_LEN,
    MITIGATION_PATTERN,
    REQUEST_CUE_PATTERN,
    SAFETY_CLARIFY_MESSAGE,
    SPAM_CHAR_THRESHOLD,
    SPAM_TOKEN_THRESHOLD,
    STRONG_AUTH_PATTERN,
    TARGET_PATTERNS,
    UNAUTH_ACCESS_PATTERN,
    UNAUTHORIZED_PATTERN,
    WEAK_ATTACK_VERB_PATTERN,
)
from .normalization_module import ZERO_WIDTH_PATTERN

SafetyAction = Literal["ALLOW", "CLARIFY", "REFUSE"]


class ValidationResult(TypedDict):
    is_valid: bool
    reject_reason: str | None
    reason: str | None
    safety_action: SafetyAction | None
    clarification_message: str | None


# Precompile regexes
# Match control characters except tab (\t), newline (\n), carriage return (\r)
INVALID_CHARS_PATTERN = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F\x80-\x9F]")

# Match SPAM_CHAR_THRESHOLD or more identical consecutive non-whitespace characters
SPAM_CHAR_PATTERN = re.compile(rf"(\S)\1{{{SPAM_CHAR_THRESHOLD - 1},}}")

_INJECTION_RE = [re.compile(p) for p in INJECTION_PATTERNS]
_INJECTION_TOPIC_RE = re.compile(INJECTION_TOPIC_PATTERN)
_ATTACK_VERB_RE = re.compile(ATTACK_VERB_PATTERN)
_WEAK_ATTACK_VERB_RE = re.compile(WEAK_ATTACK_VERB_PATTERN)
_UNAUTHORIZED_RE = re.compile(UNAUTHORIZED_PATTERN)
_TARGET_RE = [re.compile(p) for p in TARGET_PATTERNS]
_MITIGATION_RE = re.compile(MITIGATION_PATTERN)
_STRONG_AUTH_RE = re.compile(STRONG_AUTH_PATTERN)
_EXPLAIN_RE = re.compile(EXPLAIN_PATTERN)
_REQUEST_CUE_RE = re.compile(REQUEST_CUE_PATTERN)
_UNAUTH_ACCESS_RE = re.compile(UNAUTH_ACCESS_PATTERN)


def _has_spam_tokens(query: str) -> bool:
    """Check if the same token repeats consecutively >= SPAM_TOKEN_THRESHOLD times."""
    tokens = query.split()
    if len(tokens) < SPAM_TOKEN_THRESHOLD:
        return False

    consecutive_count = 1
    for i in range(1, len(tokens)):
        if tokens[i] == tokens[i - 1]:
            consecutive_count += 1
            if consecutive_count >= SPAM_TOKEN_THRESHOLD:
                return True
        else:
            consecutive_count = 1

    return False


def _fold(text: str) -> str:
    """Return a lowercase, diacritic-free copy of text for pattern matching only."""
    text = text.replace("đ", "d").replace("Đ", "d")
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return stripped.lower()


def _safety_decision(query: str) -> tuple[SafetyAction, str | None]:
    """Return (safety_action, reject_reason) for a structurally valid query."""
    f = _fold(query)

    if any(p.search(f) for p in _INJECTION_RE):
        # Defensive questions about prompt injection may quote injection phrases
        if not (_INJECTION_TOPIC_RE.search(f) and _MITIGATION_RE.search(f)):
            return "REFUSE", "prompt_injection_attempt"

    has_target = any(p.search(f) for p in _TARGET_RE)
    # "tan cong" / "xam nhap" alone are common nouns in security text
    has_attack_verb = bool(_ATTACK_VERB_RE.search(f)) or (
        has_target and bool(_WEAK_ATTACK_VERB_RE.search(f))
    )

    has_mitigation = bool(_MITIGATION_RE.search(f))
    has_cue = bool(_REQUEST_CUE_RE.search(f))

    # Unauthorized alone does not refuse: Broken Access Control questions describe it
    if has_attack_verb and _UNAUTHORIZED_RE.search(f):
        # Explanation questions get CLARIFY instead of REFUSE
        if _EXPLAIN_RE.search(f) and not has_cue:
            return "CLARIFY", None
        return "REFUSE", "unsafe_attack_intent"

    if not has_attack_verb:
        # A weak mitigation alone cannot approve unauthorized access to a specific target
        if has_target and has_cue and _UNAUTH_ACCESS_RE.search(f):
            if _STRONG_AUTH_RE.search(f):
                return "ALLOW", None
            if has_mitigation:
                return "CLARIFY", None
            return "REFUSE", "unsafe_attack_intent"
        return "ALLOW", None

    if has_target:
        # A weak mitigation alone cannot approve a specific target
        if _STRONG_AUTH_RE.search(f):
            return "ALLOW", None
        if has_mitigation:
            return "CLARIFY", None
        return "REFUSE", "unsafe_attack_intent"
    if has_mitigation:
        return "ALLOW", None
    return "CLARIFY", None


def _reject(code: str, safety_action: SafetyAction | None = None) -> ValidationResult:
    return {
        "is_valid": False,
        "reject_reason": code,
        "reason": MESSAGES[code],
        "safety_action": safety_action,
        "clarification_message": None,
    }


def _allow() -> ValidationResult:
    return {
        "is_valid": True,
        "reject_reason": None,
        "reason": None,
        "safety_action": "ALLOW",
        "clarification_message": None,
    }


def _clarify() -> ValidationResult:
    return {
        "is_valid": True,
        "reject_reason": None,
        "reason": None,
        "safety_action": "CLARIFY",
        "clarification_message": SAFETY_CLARIFY_MESSAGE,
    }


def validate_query(raw_query) -> ValidationResult:
    """
    Validate query structure, then safety, before routing.

    Returns a dict with exactly 5 keys:
    - is_valid: bool
    - reject_reason: str | None (reason code if rejected)
    - reason: str | None (user-facing message if rejected)
    - safety_action: "ALLOW", "CLARIFY" or "REFUSE" after the safety check;
      None for structural failures
    - clarification_message: str | None (set only when safety_action is "CLARIFY")
    """
    # 1. Check type
    if not isinstance(raw_query, str):
        return _reject("invalid_type")

    # Zero-width and BOM chars must not hide length, spam or attack keywords
    cleaned = ZERO_WIDTH_PATTERN.sub("", raw_query)
    stripped = cleaned.strip()

    # 2. Check emptiness
    if not stripped:
        return _reject("empty_query")

    # 3. Check maximum length
    if len(stripped) > MAX_QUERY_LEN:
        return _reject("query_too_long")

    # 4. Check invalid characters (on original string)
    if INVALID_CHARS_PATTERN.search(raw_query):
        return _reject("invalid_characters")

    # 5. Check minimum length
    if len(stripped) < MIN_QUERY_LEN:
        return _reject("query_too_short")

    # 6. Check spam patterns
    if SPAM_CHAR_PATTERN.search(cleaned) or _has_spam_tokens(cleaned):
        return _reject("spam_detected")

    # 7. Safety check on the current query
    action, reject_reason = _safety_decision(cleaned)
    if action == "REFUSE":
        return _reject(reject_reason, "REFUSE")
    if action == "CLARIFY":
        return _clarify()
    return _allow()
