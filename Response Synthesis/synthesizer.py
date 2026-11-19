"""
Module: synthesizer.py
Stage 4: Response Synthesis, Evidence Verification, and Traceable Response
Role: NGUYEN (Response Synthesis)

Module calls LLM to generate answer drafts (SynthesizedDraftSchema)
based on document context from Retrieval (Evidence Chunks) and Intent,
supporting flexible branching between:
- STRICT RAG MODE (evidence_status == FULL)
- HYBRID MODE (evidence_status == PARTIAL)
- ABSTENTION FLOW (evidence_status == NONE or clarification_needed)
"""

from __future__ import annotations

import logging, re
import time
from typing import Any, Protocol, runtime_checkable

try:
    from .prompt_templates import (
        SYSTEM_INSTRUCTION_SYNTHESIS,
        build_synthesis_messages,
        extract_citation_tags,
    )
    from .schema_response import SynthesizedDraftSchema
except ImportError:
    from prompt_templates import (
        SYSTEM_INSTRUCTION_SYNTHESIS,
        build_synthesis_messages,
        extract_citation_tags,
    )
    from schema_response import SynthesizedDraftSchema

logger = logging.getLogger(__name__)


# ---- R4: grouped citation normalization ----
_MAX_RANGE_EXPAND = 20  # hard cap: refuse to expand ranges larger than this

# Matches bracketed expressions whose interior is entirely digits, commas,
# spaces, hyphens, and en-dashes.  Guard (?!\() excludes Markdown links.
_GROUPED_CITE_RE = re.compile(
    r"\[(\d+(?:\s*[-–]\s*\d+)?(?:\s*,\s*\d+(?:\s*[-–]\s*\d+)?)*)\](?!\()"
)
# Splits on fenced (``` … ```) and inline (` … `) code — never alter code content.
_CODE_SPLIT_RE = re.compile(r"(```[\s\S]*?```|`[^`\n]+`)")


def _expand_group_match(m: "re.Match[str]") -> str:
    """Expand one grouped citation bracket into individual canonical tags.

    Deduplication policy: preserve first-occurrence order within the group so
    claim meaning is not changed.  E.g. [2, 2, 1] -> [2] [1].
    Returns the original match string unchanged when the range is inverted,
    exceeds _MAX_RANGE_EXPAND, or contains a non-numeric component.
    """
    ids: list[int] = []
    seen: set[int] = set()
    for part in m.group(1).split(","):
        part = part.strip()
        if not part:
            continue
        range_m = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if range_m:
            lo, hi = int(range_m.group(1)), int(range_m.group(2))
            if lo > hi or (hi - lo) > _MAX_RANGE_EXPAND:
                return m.group(0)  # inverted or unbounded — leave unchanged
            for n in range(lo, hi + 1):
                if n not in seen:
                    ids.append(n)
                    seen.add(n)
        elif re.match(r"^\d+$", part):
            n = int(part)
            if n not in seen:
                ids.append(n)
                seen.add(n)
        else:
            return m.group(0)  # non-numeric component — not a citation group
    return " ".join(f"[{n}]" for n in ids) if ids else m.group(0)


def _sanitize_draft_answer(
    draft_answer: str,
    max_evidence_idx: int,
    allowed_ids: frozenset[int] | None = None,
) -> str:
    """
    Clean up common small-LLM formatting artifacts:
    0. (R4) Normalize grouped citations like [1,2], [1-3] into individual [1] [2] [3].
    1. Strip literal '[i]' placeholders.
    2. Remove any LLM-generated trailing 'References' / 'Tham khảo' section.
    3. Remove out-of-range / disallowed numeric citation tags [n].

    Steps 0 and 3 are applied segment-by-segment using _CODE_SPLIT_RE so that
    fenced (``` … ```) and inline (` … `) code spans are never modified by any
    citation transformation.

    allowed_ids: when provided (non-None), a tag [n] is kept only if n is in this
    set.  When None, the existing behaviour applies: keep [n] iff 1 <= n <=
    max_evidence_idx.  Passing an empty frozenset removes all numeric tags.
    """
    raw = draft_answer or ""

    # Steps 0 + 1 (R4 expansion and [i] removal): operate on non-code segments only.
    segments = _CODE_SPLIT_RE.split(raw)
    step01_parts: list[str] = []
    for seg in segments:
        if _CODE_SPLIT_RE.match(seg):
            step01_parts.append(seg)   # code span — leave untouched
        else:
            expanded = _GROUPED_CITE_RE.sub(_expand_group_match, seg)
            expanded = re.sub(r"\[i\]", "", expanded)
            step01_parts.append(expanded)
    cleaned = "".join(step01_parts)

    # Step 2: strip trailing LLM-generated References section (not code-sensitive).
    cleaned = re.sub(
        r"\n+(?:#{1,4}|\*\*)\s*(?:Danh mục tài liệu tham chiếu|Tài liệu tham khảo|Tham khảo|References(?:\s*&\s*Citations)?)\b[\s\S]*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    # Step 3: remove disallowed numeric citation tags — code spans protected.
    if allowed_ids is not None:
        def _keep_allowed(m: re.Match) -> str:  # type: ignore[type-arg]
            return f" [{m.group(1)}]" if int(m.group(1)) in allowed_ids else ""
        step3_parts: list[str] = []
        for seg in _CODE_SPLIT_RE.split(cleaned):
            if _CODE_SPLIT_RE.match(seg):
                step3_parts.append(seg)
            else:
                step3_parts.append(re.sub(r"[ \t]*\[(\d+)\]", _keep_allowed, seg))
        cleaned = "".join(step3_parts)
    elif max_evidence_idx > 0:
        def _keep_range(m: re.Match) -> str:  # type: ignore[type-arg]
            return f" [{m.group(1)}]" if 1 <= int(m.group(1)) <= max_evidence_idx else ""
        step3_parts = []
        for seg in _CODE_SPLIT_RE.split(cleaned):
            if _CODE_SPLIT_RE.match(seg):
                step3_parts.append(seg)
            else:
                step3_parts.append(re.sub(r"[ \t]*\[(\d+)\]", _keep_range, seg))
        cleaned = "".join(step3_parts)

    return cleaned.strip()


@runtime_checkable
class GenerativeLLM(Protocol):
    """Standard protocol for LLM Client in the synthesis layer."""
    model_name: str

    def generate(
        self,
        prompt: Any,
        system_instruction: str | None = None,
        **kwargs: Any,
    ) -> str:
        ...


class DefaultDeterministicLLM:
    """
    Fallback deterministic LLM when external API key is not configured.
    Generates structured sample answers with accurate [1], [2] citation tags
    for testing and offline validation.
    """
    model_name: str = "builtin-deterministic-synthesis"

    def generate(
        self,
        prompt: Any,
        system_instruction: str | None = None,
        **kwargs: Any,
    ) -> str:
        # Prompt can be string or list of messages
        query_text = ""
        context_text = ""
        mode = "STRICT_RAG"

        if isinstance(prompt, list):
            for m in prompt:
                if m.get("role") == "user":
                    content = m.get("content", "")
                    if "HYBRID MODE" in content:
                        mode = "HYBRID"
                    query_text = content
        else:
            query_text = str(prompt)
            if "HYBRID MODE" in query_text:
                mode = "HYBRID"

        if mode == "HYBRID":
            return (
                "### Phân tích dựa trên tài liệu đã tìm thấy\n\n"
                "Dựa trên các tài liệu thu thập được trong hệ thống, cơ chế an ninh và giải pháp được xác định như sau [1]. "
                "Cấu hình bảo mật được kiểm soát và kiểm chứng thông qua các quy chuẩn kỹ thuật tương ứng [2].\n\n"
                "###  Khuyến nghị & Bổ sung từ kiến thức chung (General Knowledge Advisory)\n\n"
                "(Lưu ý: Phần này được bổ sung từ tri thức chuyên gia do tài liệu nội bộ chỉ tìm thấy một phần chứng cứ)\n"
                "- Cần triển khai nguyên tắc phòng thủ theo chiều sâu (Defense-in-Depth) kết hợp kiểm thử định kỳ.\n"
                "- Thiết lập cơ chế giám sát và ghi log tập trung để phát hiện sớm các hành vi bất thường."
            )

        return (
            "### Kết quả phân tích bảo mật\n\n"
            "Vấn đề bảo mật nêu trên được định nghĩa và ghi nhận trong hệ thống tiêu chuẩn [1]. "
            "Cơ chế khai thác bắt nguồn từ việc thiếu kiểm soát chặt chẽ đối với các luồng dữ liệu đầu vào [1].\n\n"
            "### Biện pháp phòng chống và khắc phục\n\n"
            "Để khắc phục triệt để, hệ thống bắt buộc phải áp dụng nguyên tắc kiểm tra và chuẩn hóa dữ liệu nghiêm ngặt [2]. "
            "Đồng thời, cần cấu hình phân quyền tối thiểu và tuân thủ các chỉ dẫn kỹ thuật an toàn [2]."
        )


class ResponseSynthesizer:
    """
    Response synthesis layer (Stage 4 - Synthesis Layer).
    Receives input from Retrieval and generates SynthesizedDraftSchema with tags [1], [2].
    """

    def __init__(self, llm_client: Any | None = None):
        """
        Initialize ResponseSynthesizer.
        If `llm_client` is not provided, automatically find client from Stage 2 or use DefaultDeterministicLLM.
        """
        if llm_client is not None:
            self.llm_client = llm_client
        else:
            from llm import ColabLLMClient
            self.llm_client = ColabLLMClient()


    def synthesize(
        self,
        query: str,
        retrieval_output: dict[str, Any],
        intent: str | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> SynthesizedDraftSchema:
        """
        Synthesize answer draft from Retrieval results.
        
        Args:
            query: User question (original_query or resolved_query).
            retrieval_output: Result returned from RetrievalSystem.retrieve(plan).
            intent: Query intent (SEARCH | COMPARE | SYNTHESIS).
            conversation_history: Conversation history if available.
            
        Returns:
            SynthesizedDraftSchema: Contains answer draft, tags [1], [2], and metadata.
        """
        start_time = time.perf_counter()

        status = retrieval_output.get("status", "ok")
        evidence_status = str(retrieval_output.get("evidence_status", "FULL")).upper()
        resolved_intent = str(intent or retrieval_output.get("intent") or "SEARCH").upper()
        llm_context = retrieval_output.get("llm_context", "").strip()
        total_evidence = int(retrieval_output.get("total_evidence", len(retrieval_output.get("evidence_list", []))))


        # 1. CASE: CLARIFICATION NEEDED
        if status == "clarification_needed":
            latency = time.perf_counter() - start_time
            clarify_text = llm_context or "Your request is unclear. Please provide more specific details."
            return SynthesizedDraftSchema(
                draft_answer=clarify_text,
                intent=resolved_intent,
                cited_evidence_ids=[],
                is_abstained=True,
                abstention_reason="clarification_needed",
                evidence_status="NONE",
                generation_mode="ABSTENTION",
                generation_metadata={
                    "model_name": getattr(self.llm_client, "model_name", "unknown"),
                    "latency_s": round(latency, 4),
                    "evidence_status": "NONE",
                    "total_evidence": 0,
                },
            )


        # 2. CASE: INSUFFICIENT EVIDENCE (ABSTENTION POLICY - NONE)
        if evidence_status == "NONE" or total_evidence == 0 or not llm_context:
            latency = time.perf_counter() - start_time
            abstention_answer = (
                llm_context
                or f"The system found insufficient security documentation evidence for the question: '{query}'. "
                f"Abstention policy is activated to avoid misinformation."
            )
            return SynthesizedDraftSchema(
                draft_answer=abstention_answer,
                intent=resolved_intent,
                cited_evidence_ids=[],
                is_abstained=True,
                abstention_reason="insufficient_evidence",
                evidence_status="NONE",
                generation_mode="ABSTENTION",
                generation_metadata={
                    "model_name": getattr(self.llm_client, "model_name", "unknown"),
                    "latency_s": round(latency, 4),
                    "evidence_status": "NONE",
                    "total_evidence": 0,
                },
            )


        # 3. CASE: GROUNDED EVIDENCE AVAILABLE (HYBRID MODE or STRICT RAG MODE)
        generation_mode = "HYBRID" if evidence_status == "PARTIAL" else "STRICT_RAG"

        # Build messages prompt
        messages = build_synthesis_messages(
            query=query,
            llm_context=llm_context,
            intent=resolved_intent,
            evidence_status=evidence_status,
            conversation_history=conversation_history,
        )

        try:
            # Call LLM
            draft_answer = self.llm_client.generate(
                prompt=messages,
                system_instruction=None,
            )
        except Exception as e:
            logger.error("LLM generate failed: %s", e)
            return SynthesizedDraftSchema(
                draft_answer="The system is temporarily unable to connect to the synthesis model. Please try again later.",
                intent=resolved_intent, cited_evidence_ids=[],
                is_abstained=True, abstention_reason="llm_unavailable",
                evidence_status=evidence_status, generation_mode="ABSTENTION",
                generation_metadata={"model_name": getattr(self.llm_client, "model_name", "unknown"),
                                    "latency_s": round(time.perf_counter() - start_time, 4),
                                    "error": str(e)},
            )

        # Derive exact allowed evidence IDs from the context headers that were
        # sent to the LLM.  This is more precise than max_evidence_idx alone:
        # it correctly rejects gap IDs (e.g. [2] when only [1] and [3] exist)
        # and correctly keeps non-contiguous IDs like [3] when total_evidence=2.
        # Falls back to None (max-index behaviour) when no headers are found.
        _ev_header_matches = re.findall(r"---\s*Evidence\s*\[(\d+)\]", llm_context)
        allowed_ids: frozenset[int] | None = (
            frozenset(int(x) for x in _ev_header_matches) if _ev_header_matches else None
        )
        draft_answer = _sanitize_draft_answer(draft_answer, total_evidence, allowed_ids=allowed_ids)

        # Extract all citation tags [1], [2] from Markdown text
        cited_tags = extract_citation_tags(draft_answer)

        latency = time.perf_counter() - start_time

        return SynthesizedDraftSchema(
            draft_answer=draft_answer,
            intent=resolved_intent,
            cited_evidence_ids=cited_tags,
            is_abstained=False,
            abstention_reason=None,
            evidence_status=evidence_status,
            generation_mode=generation_mode,
            generation_metadata={
                "model_name": getattr(self.llm_client, "model_name", "unknown"),
                "latency_s": round(latency, 4),
                "generation_mode": generation_mode,
                "evidence_status": evidence_status,
                "total_evidence": total_evidence,
                "cited_count": len(cited_tags),
            },
        )
