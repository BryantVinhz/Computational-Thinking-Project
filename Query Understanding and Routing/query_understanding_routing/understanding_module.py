"""
Query Understanding Module (Contract v1.0).
Part of Stage 2: Query Understanding & Routing.

Responsibilities:
- 2.1: Utilize ontology.yaml (5 standard slugs: xss, sqli, bac, auth_failures, misconfig).
- 2.2: Intent classification (SEARCH, COMPARE, SYNTHESIS, CLARIFICATION, OUT_OF_SCOPE),
       entity & filter extraction, multi-task decomposition into task[].
- 2.3: Strict context_used evaluation (true ONLY when history actually resolves pronouns/ellipses; false for independent queries).
       History truncation algorithm (Section 7.1).
- 2.4: Clarification logic with confidence thresholding (CLARIFY_THRESHOLD = 0.6)
       and two-way equivalence: intent == CLARIFICATION <=> need_clarify == true.
- Contract v1.0: Output adheres strictly to Section 4 & 7.2 (returns understanding_result or error_code; routing attaches original_query).
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

MODULE_DIR = Path(__file__).resolve().parent
STAGE_DIR = MODULE_DIR.parent
PROJECT_ROOT = STAGE_DIR.parent

for p in [PROJECT_ROOT, STAGE_DIR, MODULE_DIR]:
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

try:
    from query_understanding_routing.config import (
        CLARIFY_THRESHOLD,
        HISTORY_MAX_CHARS,
        HISTORY_MAX_MESSAGES,
        HISTORY_MAX_TURNS,
        LLM_MAX_RETRIES,
        LLM_TIMEOUT_S,
        STANDARD_VULNERABILITY_SLUGS,
        VALID_INTENTS,
        VALID_LLM_ERROR_CODES,
        VALID_TASK_INTENTS,
    )
    from query_understanding_routing.ontology_loader import DEFAULT_ONTOLOGY_PATH, load_ontology
except ImportError:
    try:
        from .config import (
            CLARIFY_THRESHOLD,
            HISTORY_MAX_CHARS,
            HISTORY_MAX_MESSAGES,
            HISTORY_MAX_TURNS,
            LLM_MAX_RETRIES,
            LLM_TIMEOUT_S,
            STANDARD_VULNERABILITY_SLUGS,
            VALID_INTENTS,
            VALID_LLM_ERROR_CODES,
            VALID_TASK_INTENTS,
        )
        from .ontology_loader import DEFAULT_ONTOLOGY_PATH, load_ontology
    except ImportError:
        from config import (
            CLARIFY_THRESHOLD,
            HISTORY_MAX_CHARS,
            HISTORY_MAX_MESSAGES,
            HISTORY_MAX_TURNS,
            LLM_MAX_RETRIES,
            LLM_TIMEOUT_S,
            STANDARD_VULNERABILITY_SLUGS,
            VALID_INTENTS,
            VALID_LLM_ERROR_CODES,
            VALID_TASK_INTENTS,
        )
        from ontology_loader import DEFAULT_ONTOLOGY_PATH, load_ontology

# Regex patterns for co-reference and pronouns requiring context (Section 2.3)
PRONOUN_PATTERNS = [
    r"\bnó\b",
    r"\bchúng\b",
    r"\blỗi này\b",
    r"\blỗ hổng này\b",
    r"\bvấn đề này\b",
    r"\bvấn đề đó\b",
    r"\bcái đó\b",
    r"\blỗi trên\b",
    r"\blỗ hổng trên\b",
    r"\bnguyên nhân của nó\b",
    r"\bcách vá nó\b",
    r"\bcách phòng tránh nó\b",
    r"\bcách phòng chống nó\b",
    r"\bkhắc phục nó\b",
    r"\bthis vulnerability\b",
    r"\bthis flaw\b",
    r"\bhow to fix it\b",
    r"\bhow to prevent it\b",
    r"\bmitigate it\b",
    r"\babout it\b",
]

ELLIPSIS_STARTERS = [
    r"^còn\b",
    r"^thế còn\b",
    r"^thế thì\b",
    r"^vậy còn\b",
    r"^còn về\b",
    r"^how about\b",
    r"^what about\b",
    r"^and then\b",
]

# Patterns for obvious out-of-scope requests
OUT_OF_SCOPE_PATTERNS = [
    r"\bthời tiết\b",
    r"\bhôm nay ăn gì\b",
    r"\bbóng đá\b",
    r"\bthời sự\b",
    r"\bgiá vàng\b",
    r"\bchứng khoán\b",
    r"\blập trình game unity\b",
    r"\bweather\b",
    r"\bcook\b",
    r"\brecipe\b",
    r"\bfootball\b",
    r"\bmovie\b",
]

# Ambiguity / clarification patterns when query is too generic without specifics
VAGUE_QUERY_PATTERNS = [
    r"^các lỗ hổng nguy hiểm\??$",
    r"^lỗ hổng nguy hiểm\??$",
    r"^các lỗ hổng bảo mật\??$",
    r"^lỗ hổng web\??$",
    r"^lỗi bảo mật phổ biến\??$",
    r"^top vulnerabilities\??$",
    r"^dangerous vulnerabilities\??$",
    r"^security issues\??$",
    r"^web security\??$",
    r"^bảo mật trang web\??$",
]


def clean_json_codeblock(raw: str) -> str:
    """Clean markdown code fences from JSON output."""
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if text.startswith("```"):
        pattern = r"^```(?:json)?\s*\n?(.*?)\n?```$"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def truncate_conversation_history(
    conversation_history: Optional[List[Dict[str, Any]]],
    max_turns: int = HISTORY_MAX_TURNS,
    max_chars: int = HISTORY_MAX_CHARS,
) -> List[Dict[str, str]]:
    """
    Truncate conversation history according to Contract v1.0 Section 7.1:
    - Format: [{"role": "user" | "assistant", "content": str}, ...]
    - Take at most max_turns pairs of recent turns (2 * max_turns messages).
    - If a single message > max_chars, cut the head and keep the tail (last max_chars).
    - If total characters across messages > max_chars, drop oldest messages until total <= max_chars.
    - Non-list or malformed items are safely discarded.
    """
    if not isinstance(conversation_history, list):
        return []

    valid_messages: List[Dict[str, str]] = []
    for turn in conversation_history:
        if isinstance(turn, dict) and "role" in turn and "content" in turn:
            role = str(turn.get("role", "user"))
            content = str(turn.get("content", ""))
            # Truncate head of individual message if > max_chars
            if len(content) > max_chars:
                content = content[-max_chars:]
            valid_messages.append({"role": role, "content": content})

    # Keep at most last 2 * max_turns messages
    max_msgs = max_turns * 2
    truncated = valid_messages[-max_msgs:]

    # Drop oldest messages until total length <= max_chars
    while truncated and sum(len(m["content"]) for m in truncated) > max_chars:
        truncated.pop(0)

    return truncated


def validate_understanding_schema(
    result: Any,
    allowed_slugs: Set[str] = STANDARD_VULNERABILITY_SLUGS,
    document_ids: Optional[Set[str]] = None,
) -> bool:
    """
    Validate output against Section 7.2 of Contract v1.0.
    Returns True if fully compliant, False otherwise.
    """
    if not isinstance(result, dict):
        return False

    required_keys = {
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
    if not required_keys.issubset(result.keys()):
        return False

    # Check resolved_query
    if not isinstance(result["resolved_query"], str) or not result["resolved_query"].strip():
        return False

    # Check intent
    intent = result["intent"]
    if intent not in VALID_INTENTS:
        return False

    # Check confidence (must be finite float in [0, 1], not bool)
    confidence = result["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return False
    if math.isnan(confidence) or math.isinf(confidence) or not (0.0 <= confidence <= 1.0):
        return False

    # Check context_used
    if not isinstance(result["context_used"], bool):
        return False

    # Check entities
    entities = result["entities"]
    if not isinstance(entities, dict):
        return False
    if "document" not in entities or "vulnerabilities" not in entities:
        return False
    if not isinstance(entities["document"], list) or not isinstance(entities["vulnerabilities"], list):
        return False
    for v in entities["vulnerabilities"]:
        if v not in allowed_slugs:
            return False

    # Check filter
    flt = result["filter"]
    if not isinstance(flt, dict):
        return False
    if not (flt.get("version") is None or isinstance(flt.get("version"), str)):
        return False
    if not (flt.get("document_type") is None or isinstance(flt.get("document_type"), str)):
        return False
    if not isinstance(flt.get("source"), list):
        return False

    # Check two-way equivalence: intent == CLARIFICATION <=> need_clarify == true
    need_clarify = result["need_clarify"]
    if not isinstance(need_clarify, bool):
        return False

    clar_msg = result["clarification_message"]
    if need_clarify:
        if intent != "CLARIFICATION":
            return False
        if not isinstance(clar_msg, str) or not clar_msg.strip():
            return False
    else:
        if intent == "CLARIFICATION":
            return False
        if clar_msg is not None:
            return False

    # Check task
    tasks = result["task"]
    if not isinstance(tasks, list):
        return False

    if intent in {"SEARCH", "COMPARE", "SYNTHESIS"}:
        if len(tasks) == 0:
            return False
        seen_task_ids: Set[str] = set()
        for idx, t in enumerate(tasks):
            if not isinstance(t, dict):
                return False
            task_id = t.get("task_id")
            if not isinstance(task_id, str) or not task_id or task_id in seen_task_ids:
                return False
            seen_task_ids.add(task_id)

            if t.get("intent_task") not in VALID_TASK_INTENTS:
                return False
            if not isinstance(t.get("query"), str) or not t.get("query").strip():
                return False

            obj = t.get("objective")
            if not isinstance(obj, dict):
                return False
            obj_type = obj.get("type")
            obj_ids = obj.get("ids")
            if obj_type not in {"vulnerabilities", "documents"} or not isinstance(obj_ids, list):
                return False

            if obj_type == "vulnerabilities":
                for vid in obj_ids:
                    if vid not in allowed_slugs:
                        return False
            elif obj_type == "documents" and document_ids is not None:
                for doc_id in obj_ids:
                    if doc_id not in document_ids:
                        return False

            depends_on = t.get("depends_on")
            if depends_on is not None:
                earlier_ids = {tasks[i]["task_id"] for i in range(idx)}
                if depends_on not in earlier_ids:
                    return False
    return True


class QueryUnderstandingEngine:
    """Core Query Understanding Engine implementing Section 2 requirements under Contract v1.0."""

    def __init__(self, ontology_path: Optional[Path | str] = None):
        self.ontology_path = Path(ontology_path or DEFAULT_ONTOLOGY_PATH)
        self.ontology = self._init_ontology()
        self._build_alias_index()

    def _init_ontology(self) -> Dict[str, Dict[str, Any]]:
        """Load and validate ontology via load_ontology."""
        try:
            return load_ontology(self.ontology_path)
        except Exception:
            if DEFAULT_ONTOLOGY_PATH.exists():
                return load_ontology(DEFAULT_ONTOLOGY_PATH)
            raise

    def _build_alias_index(self) -> None:
        """Map aliases and canonical names to standard slugs."""
        self.term_to_slug: Dict[str, str] = {}
        self.related_to_slug: Dict[str, str] = {}

        for slug, details in self.ontology.items():
            canonical = details.get("canonical", "").strip().lower()
            if canonical:
                self.term_to_slug[canonical] = slug

            for alias in details.get("aliases", []):
                cleaned = str(alias).strip().lower()
                if cleaned:
                    self.term_to_slug[cleaned] = slug

            for subtype in details.get("subtypes", []):
                cleaned = str(subtype).strip().lower()
                if cleaned:
                    self.term_to_slug[cleaned] = slug

            for related in details.get("related_terms", []):
                cleaned = str(related).strip().lower()
                if cleaned:
                    self.related_to_slug[cleaned] = slug

    def extract_vulnerabilities(self, text: str) -> List[str]:
        """Extract ontology vulnerability slugs from text."""
        lowered = text.lower()
        matched_slugs = set()

        for term, slug in self.term_to_slug.items():
            pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
            if re.search(pattern, lowered):
                matched_slugs.add(slug)

        if not matched_slugs:
            for term, slug in self.related_to_slug.items():
                pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
                if re.search(pattern, lowered):
                    matched_slugs.add(slug)

        return sorted(list(matched_slugs))

    def extract_documents_and_filters(self, text: str) -> Tuple[List[str], Dict[str, Any]]:
        """Extract referenced documents and filter constraints."""
        lowered = text.lower()
        documents = []
        filter_dict: Dict[str, Any] = {
            "version": None,
            "document_type": None,
            "source": []
        }

        if "cvss" in lowered:
            documents.append("CVSS Specification")
            if "first" not in filter_dict["source"]:
                filter_dict["source"].append("FIRST")
            filter_dict["document_type"] = "specification"

        if "owasp" in lowered or "top 10" in lowered or "top10" in lowered:
            if "top 10" in lowered or "top10" in lowered:
                documents.append("OWASP Top 10")
            if "owasp" not in filter_dict["source"]:
                filter_dict["source"].append("OWASP")

        if "asvs" in lowered or "application security verification standard" in lowered:
            documents.append("OWASP ASVS")
            filter_dict["document_type"] = "standard"
            if "owasp" not in filter_dict["source"]:
                filter_dict["source"].append("OWASP")

        if "cheatsheet" in lowered or "cheat sheet" in lowered or "remediation" in lowered:
            filter_dict["document_type"] = "cheatsheet"
            if "owasp" not in filter_dict["source"]:
                filter_dict["source"].append("OWASP")

        version_match = re.search(r"\b(v?\d+(?:\.\d+)+|202\d)\b", lowered)
        if version_match:
            filter_dict["version"] = version_match.group(1)

        return documents, filter_dict

    def detect_coreference(self, query: str) -> Tuple[bool, Optional[str]]:
        lowered = query.lower().strip()
        for pattern in PRONOUN_PATTERNS:
            match = re.search(pattern, lowered)
            if match:
                return True, match.group(0)

        for pattern in ELLIPSIS_STARTERS:
            match = re.search(pattern, lowered)
            if match:
                return True, match.group(0)

        return False, None

    def resolve_coreference_from_history(
        self, query: str, conversation_history: Optional[List[Dict[str, str]]]
    ) -> Tuple[bool, str, List[str]]:
        has_ref, ref_text = self.detect_coreference(query)

        if not has_ref:
            return False, query, self.extract_vulnerabilities(query)

        safe_history = truncate_conversation_history(conversation_history)
        if not safe_history:
            return False, query, self.extract_vulnerabilities(query)

        history_slugs: List[str] = []
        antecedent_canonical = ""

        for turn in reversed(safe_history):
            content = turn.get("content", "")
            slugs = self.extract_vulnerabilities(content)
            if slugs:
                history_slugs = slugs
                canonical = self.ontology.get(slugs[0], {}).get("canonical", slugs[0])
                antecedent_canonical = canonical
                break

        if not history_slugs:
            return False, query, []

        resolved = query
        if ref_text and ref_text in ["nó", "lỗi này", "lỗ hổng này", "cái đó", "it", "this vulnerability"]:
            resolved = re.sub(
                r"\b" + re.escape(ref_text) + r"\b",
                antecedent_canonical,
                query,
                flags=re.IGNORECASE
            )
        elif re.search(r"^(còn|thế còn|vậy còn)\b", query.strip(), flags=re.IGNORECASE):
            clean_sub = re.sub(r"^(còn|thế còn|vậy còn)\s*", "", query.strip(), flags=re.IGNORECASE)
            resolved = f"{clean_sub} {antecedent_canonical}".strip()
        else:
            resolved = f"{query} ({antecedent_canonical})"

        return True, resolved, history_slugs

    def decompose_tasks(
        self,
        query: str,
        overall_intent: str,
        vulnerabilities: List[str],
        documents: List[str]
    ) -> List[Dict[str, Any]]:
        if overall_intent in {"CLARIFICATION", "OUT_OF_SCOPE"}:
            return []

        lowered = query.lower()
        multi_task_split = re.split(
            r",\s*(?:đồng thời|rồi|sau đó|và đồng thời|và tóm tắt|and also|then)\s*|\s+(?:đồng thời|rồi|sau đó)\s+",
            query,
            flags=re.IGNORECASE
        )

        if len(multi_task_split) >= 2 and (len(vulnerabilities) >= 2 or "so sánh" in lowered or "compare" in lowered):
            tasks = []
            for idx, part in enumerate(multi_task_split, start=1):
                part_clean = part.strip()
                part_slugs = self.extract_vulnerabilities(part_clean)
                if not part_slugs:
                    part_slugs = vulnerabilities

                part_intent = "SEARCH"
                if any(k in part_clean.lower() for k in ["so sánh", "compare", "khác nhau"]):
                    part_intent = "COMPARE"
                elif any(k in part_clean.lower() for k in ["tổng hợp", "tóm tắt", "synthesis", "overview"]):
                    part_intent = "SYNTHESIS"

                task_obj = {
                    "task_id": f"task_{idx:03d}",
                    "intent_task": part_intent,
                    "query": part_clean,
                    "objective": {
                        "type": "vulnerabilities" if part_slugs else ("documents" if documents else "vulnerabilities"),
                        "ids": part_slugs if part_slugs else (documents if documents else [])
                    },
                    "depends_on": f"task_{idx-1:03d}" if idx > 1 and part_intent != "COMPARE" else None
                }
                tasks.append(task_obj)
            return tasks

        objective_ids = vulnerabilities if vulnerabilities else (documents if documents else [])
        objective_type = "vulnerabilities" if vulnerabilities else ("documents" if documents else "vulnerabilities")

        return [
            {
                "task_id": "task_001",
                "intent_task": overall_intent,
                "query": query,
                "objective": {
                    "type": objective_type,
                    "ids": objective_ids
                },
                "depends_on": None
            }
        ]

    def classify_intent_and_clarification(
        self,
        query: str,
        vulnerabilities: List[str],
        documents: List[str],
        has_unresolved_ref: bool
    ) -> Tuple[str, float, bool, Optional[str]]:
        lowered = query.lower().strip()

        # 1. Out of Scope Check
        for oos_pattern in OUT_OF_SCOPE_PATTERNS:
            if re.search(oos_pattern, lowered):
                return "OUT_OF_SCOPE", 0.98, False, None

        # 2. Unresolved pronoun / reference without history
        if has_unresolved_ref:
            msg = (
                "Bạn đang đề cập đến lỗ hổng hoặc chủ đề bảo mật cụ thể nào? "
                "Hệ thống hiện hỗ trợ: Cross-Site Scripting (XSS), SQL Injection (SQLi), "
                "Broken Access Control (BAC), Authentication Failures, và Security Misconfiguration."
            )
            return "CLARIFICATION", 0.40, True, msg

        # 3. Vague query check
        for vague_pattern in VAGUE_QUERY_PATTERNS:
            if re.search(vague_pattern, lowered):
                msg = (
                    "Bạn đang quan tâm đến nhóm lỗ hổng bảo mật cụ thể nào "
                    "(ví dụ: XSS, SQL Injection, Broken Access Control, Authentication Failures, hay Security Misconfiguration) "
                    "hoặc tiêu chuẩn nào (OWASP Top 10, ASVS, CVSS v4.0)?"
                )
                return "CLARIFICATION", 0.45, True, msg

        # 4. Compare Intent
        if any(w in lowered for w in ["so sánh", "compare", "khác nhau giữa", "sự khác biệt", "phân biệt", "vs", "versus"]):
            return "COMPARE", 0.94, False, None
        if len(vulnerabilities) >= 2 and any(w in lowered for w in ["và", "với", "and", "or", "giữa"]):
            if any(w in lowered for w in ["điểm giống", "khác", "ưu nhược", "nguy cơ"]):
                return "COMPARE", 0.91, False, None

        # 5. Synthesis Intent
        if any(w in lowered for w in ["tổng hợp", "tóm tắt toàn bộ", "liệt kê tất cả", "overview", "synthesis", "summary of"]):
            return "SYNTHESIS", 0.90, False, None

        # 6. Specific Search Intent
        if vulnerabilities or documents:
            return "SEARCH", 0.95, False, None

        # 7. Fallback: Security keywords without specific slug
        security_keywords = [
            "lỗ hổng", "bảo mật", "tấn công", "vulnerability", "security",
            "exploit", "cwe", "cve", "owasp", "injection", "prompt injection", "tiêm mã"
        ]
        if any(kw in lowered for kw in security_keywords):
            msg = (
                "Câu hỏi của bạn có liên quan đến bảo mật nhưng chưa rõ mục tiêu tra cứu. "
                "Bạn muốn tìm hiểu định nghĩa, cách khai thác thử nghiệm hay biện pháp phòng chống "
                "cho lỗ hổng nào (XSS, SQLi, BAC, Auth Failures, Misconfiguration)?"
            )
            return "CLARIFICATION", 0.50, True, msg

        # 8. Unrecognized query outside security domain
        return "OUT_OF_SCOPE", 0.85, False, None

    def execute_understanding(
        self,
        normalized_query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        confidence_threshold: float = CLARIFY_THRESHOLD,
        llm_client: Any = None
    ) -> Dict[str, Any]:
        clean_query = str(normalized_query).strip()

        if not clean_query:
            return {
                "resolved_query": "",
                "intent": "CLARIFICATION",
                "confidence": 0.0,
                "context_used": False,
                "entities": {"document": [], "vulnerabilities": []},
                "filter": {"version": None, "document_type": None, "source": []},
                "task": [],
                "need_clarify": True,
                "clarification_message": "Vui lòng nhập câu hỏi hoặc yêu cầu cần tra cứu về an toàn thông tin."
            }

        has_ref, _ = self.detect_coreference(clean_query)
        context_used, query_after_context, vulns_from_context = self.resolve_coreference_from_history(
            clean_query, conversation_history
        )

        has_unresolved_ref = has_ref and not context_used and (not conversation_history or not vulns_from_context)

        entities_vulns = vulns_from_context if vulns_from_context else self.extract_vulnerabilities(clean_query)
        documents, filter_info = self.extract_documents_and_filters(clean_query)

        intent, confidence, need_clarify, clar_msg = self.classify_intent_and_clarification(
            clean_query, entities_vulns, documents, has_unresolved_ref
        )

        if confidence < confidence_threshold:
            need_clarify = True
            intent = "CLARIFICATION"
            if not clar_msg:
                clar_msg = (
                    f"Hệ thống chưa đủ độ chắc chắn (độ tin cậy: {confidence:.2f} < ngưỡng: {confidence_threshold:.2f}). "
                    "Bạn vui lòng mô tả chi tiết hơn yêu cầu tra cứu của mình."
                )

        if need_clarify:
            intent = "CLARIFICATION"
            if not clar_msg:
                clar_msg = "Vui lòng cung cấp thêm thông tin để làm rõ câu hỏi."
        else:
            if intent == "CLARIFICATION":
                need_clarify = True
                if not clar_msg:
                    clar_msg = "Vui lòng cung cấp thêm thông tin để làm rõ câu hỏi."
            else:
                clar_msg = None

        tasks = self.decompose_tasks(clean_query, intent, entities_vulns, documents)

        result: Dict[str, Any] = {
            "resolved_query": query_after_context if context_used else clean_query,
            "intent": intent,
            "confidence": round(float(confidence), 2),
            "context_used": bool(context_used),
            "entities": {
                "document": documents,
                "vulnerabilities": entities_vulns
            },
            "filter": filter_info,
            "task": tasks,
            "need_clarify": bool(need_clarify),
            "clarification_message": clar_msg
        }

        if not validate_understanding_schema(result, STANDARD_VULNERABILITY_SLUGS):
            return {"error_code": "llm_schema_violation"}

        return result


# Singleton instance
_default_engine: Optional[QueryUnderstandingEngine] = None
_default_llm_client: Any = None


def get_engine() -> QueryUnderstandingEngine:
    """Get or create singleton QueryUnderstandingEngine."""
    global _default_engine
    if _default_engine is None:
        _default_engine = QueryUnderstandingEngine()
    return _default_engine


def set_default_llm_client(client: Any) -> None:
    """Set global default LLM client for understanding and pipeline orchestration."""
    global _default_llm_client
    _default_llm_client = client


def get_default_llm_client() -> Any:
    """Get global default LLM client if configured."""
    global _default_llm_client
    return _default_llm_client


def understand_query(
    normalized_query: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    *,
    confidence_threshold: float = CLARIFY_THRESHOLD,
    llm_client: Any = None
) -> Dict[str, Any]:
    """
    Standard interface function as agreed in Contract v1.0 Section 4:
    understand_query(normalized_query: str,
                     conversation_history: list[dict] | None = None) -> dict

    Supports pluggable LLM with automated retries (Section 7.3)
    and deterministic semantic engine.

    Returns:
        understanding_result dict (WITHOUT original_query) OR {"error_code": "..."}
    """
    active_client = llm_client if llm_client is not None else get_default_llm_client()

    # If an LLM client is supplied or configured:
    if active_client is not None:
        for attempt in range(1 + LLM_MAX_RETRIES):
            try:
                raw_response = active_client.call(
                    normalized_query=normalized_query,
                    conversation_history=conversation_history,
                    timeout=LLM_TIMEOUT_S
                )
            except TimeoutError:
                return {"error_code": "llm_unavailable"}
            except ConnectionError:
                return {"error_code": "llm_unavailable"}
            except Exception:
                return {"error_code": "llm_unavailable"}

            # Parse JSON (cleaning markdown ```json codeblock fences)
            try:
                if isinstance(raw_response, str):
                    cleaned_text = clean_json_codeblock(raw_response)
                    parsed = json.loads(cleaned_text)
                elif isinstance(raw_response, dict):
                    parsed = raw_response
                else:
                    raise ValueError("Non-dict LLM response")
            except Exception:
                if attempt < LLM_MAX_RETRIES:
                    continue
                return {"error_code": "llm_invalid_json"}

            # Validate schema
            if validate_understanding_schema(parsed, STANDARD_VULNERABILITY_SLUGS):
                parsed.pop("original_query", None)
                return parsed

            if attempt < LLM_MAX_RETRIES:
                continue
            return {"error_code": "llm_schema_violation"}

    # Default robust deterministic engine path
    engine = get_engine()
    return engine.execute_understanding(
        normalized_query=normalized_query,
        conversation_history=conversation_history,
        confidence_threshold=confidence_threshold,
        llm_client=active_client
    )
