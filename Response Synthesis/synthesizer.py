"""
Module: synthesizer.py
Stage 4: Response Synthesis, Evidence Verification, and Traceable Response

Calls the LLM to generate a draft answer (SynthesizedDraftSchema)
based on retrieved document context (Evidence Chunks) and query intent,
supporting branching across:
- STRICT RAG MODE (evidence_status == FULL)
- HYBRID MODE (evidence_status == PARTIAL)
- ABSTENTION FLOW (evidence_status == NONE or clarification_needed)
"""

from __future__ import annotations

import logging
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


@runtime_checkable
class GenerativeLLM(Protocol):
    """Standard protocol for LLM clients in the response synthesis layer."""
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
    Fallback deterministic LLM when no external API key is configured.
    Generates structured sample answers with [1], [2] citation tags
    for testing and offline validation.
    """
    model_name: str = "builtin-deterministic-synthesis"

    def generate(
        self,
        prompt: Any,
        system_instruction: str | None = None,
        **kwargs: Any,
    ) -> str:
        # Prompt may be a string or a list of chat messages
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
                "### 💡 Khuyến nghị & Bổ sung từ kiến thức chung (General Knowledge Advisory)\n\n"
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
    Response Synthesis Layer (Stage 4).
    Accepts retrieval output and generates a SynthesizedDraftSchema with [1], [2] citation tags.
    """

    def __init__(self, llm_client: Any | None = None):
        """
        Initialize ResponseSynthesizer.
        If `llm_client` is not provided, defaults to ColabLLMClient.
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
        Synthesize a draft response from Retrieval results.
        
        Args:
            query: User query (original_query or resolved_query).
            retrieval_output: Output dictionary returned by RetrievalSystem.retrieve(plan).
            intent: Query intent (SEARCH | COMPARE | SYNTHESIS).
            conversation_history: Conversation history if available.
            
        Returns:
            SynthesizedDraftSchema: Contains draft answer, citation tags [1], [2], and metadata.
        """
        start_time = time.perf_counter()

        status = retrieval_output.get("status", "ok")
        evidence_status = str(retrieval_output.get("evidence_status", "FULL")).upper()
        resolved_intent = str(intent or retrieval_output.get("intent") or "SEARCH").upper()
        llm_context = retrieval_output.get("llm_context", "").strip()
        total_evidence = int(retrieval_output.get("total_evidence", len(retrieval_output.get("evidence_list", []))))

        # 1. Clarification needed
        if status == "clarification_needed":
            latency = time.perf_counter() - start_time
            clarify_text = llm_context or "Yêu cầu của bạn chưa rõ ràng. Vui lòng cung cấp thêm thông tin chi tiết."
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

        # 2. No evidence available (Abstention Policy - NONE)
        if evidence_status == "NONE" or total_evidence == 0 or not llm_context:
            latency = time.perf_counter() - start_time
            abstention_answer = (
                llm_context
                or f"Hệ thống không tìm thấy bằng chứng tài liệu bảo mật phù hợp cho câu hỏi: '{query}'. "
                f"Chính sách từ chối (Abstention Policy) được kích hoạt để tránh thông tin sai lệch."
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

        # 3. Evidence available: HYBRID MODE or STRICT RAG MODE
        generation_mode = "HYBRID" if evidence_status == "PARTIAL" else "STRICT_RAG"

        # Build prompt messages
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
                system_instruction=SYSTEM_INSTRUCTION_SYNTHESIS,
            )
        except Exception as e:
            logger.error("LLM generate failed: %s", e)
            return SynthesizedDraftSchema(
                draft_answer="Hệ thống tạm thời không kết nối được mô hình sinh câu trả lời. Vui lòng thử lại sau.",
                intent=resolved_intent, cited_evidence_ids=[],
                is_abstained=True, abstention_reason="llm_unavailable",
                evidence_status=evidence_status, generation_mode="ABSTENTION",
                generation_metadata={"model_name": getattr(self.llm_client, "model_name", "unknown"),
                                    "latency_s": round(time.perf_counter() - start_time, 4),
                                    "error": str(e)},
            )

        # Extract all citation tags [1], [2] from the Markdown text
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
