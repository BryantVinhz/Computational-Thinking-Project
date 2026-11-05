"""
Module: synthesizer.py
Stage 4: Response Synthesis, Evidence Verification, and Traceable Response
Role: NGUYÊN (Response Synthesis)

Module thực hiện gọi LLM để sinh bản thảo câu trả lời (SynthesizedDraftSchema)
dựa trên ngữ cảnh tài liệu từ Retrieval (Evidence Chunks) và Intent,
hỗ trợ phân nhánh linh hoạt giữa:
- STRICT RAG MODE (evidence_status == FULL)
- HYBRID MODE (evidence_status == PARTIAL)
- ABSTENTION FLOW (evidence_status == NONE hoặc clarification_needed)
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
    """Protocol chuẩn hóa cho LLM Client trong tầng sinh câu trả lời."""
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
    Fallback deterministic LLM khi không cấu hình API key ngoài.
    Sinh câu trả lời mẫu chuẩn cấu trúc và gắn thẻ trích dẫn [1], [2] chính xác
    phục vụ testing và offline validation.
    """
    model_name: str = "builtin-deterministic-synthesis"

    def generate(
        self,
        prompt: Any,
        system_instruction: str | None = None,
        **kwargs: Any,
    ) -> str:
        # Prompt có thể là chuỗi hoặc list messages
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
    Tầng sinh câu trả lời (Stage 4 - Synthesis Layer).
    Nhận đầu vào từ Retrieval và sinh SynthesizedDraftSchema có gắn thẻ [1], [2].
    """

    def __init__(self, llm_client: Any | None = None):
        """
        Khởi tạo ResponseSynthesizer.
        Nếu không truyền `llm_client`, tự động tìm kiếm client từ Stage 2 hoặc dùng DefaultDeterministicLLM.
        """
        if llm_client is not None:
            self.llm_client = llm_client
        else:
            # Thử nạp create_llm_client từ Stage 2 nếu có
            try:
                from query_understanding_routing.llm_client import create_llm_client
                self.llm_client = create_llm_client()
            except Exception:
                self.llm_client = DefaultDeterministicLLM()

    def synthesize(
        self,
        query: str,
        retrieval_output: dict[str, Any],
        intent: str | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> SynthesizedDraftSchema:
        """
        Thực hiện sinh bản thảo câu trả lời từ kết quả Retrieval.
        
        Args:
            query: Câu hỏi của người dùng (original_query hoặc resolved_query).
            retrieval_output: Kết quả trả về từ RetrievalSystem.retrieve(plan).
            intent: Intent của truy vấn (SEARCH | COMPARE | SYNTHESIS).
            conversation_history: Lịch sử hội thoại nếu có.
            
        Returns:
            SynthesizedDraftSchema: Chứa bản thảo câu trả lời, tags [1], [2], và metadata.
        """
        start_time = time.perf_counter()

        status = retrieval_output.get("status", "ok")
        evidence_status = str(retrieval_output.get("evidence_status", "FULL")).upper()
        resolved_intent = str(intent or retrieval_output.get("intent") or "SEARCH").upper()
        llm_context = retrieval_output.get("llm_context", "").strip()
        total_evidence = int(retrieval_output.get("total_evidence", len(retrieval_output.get("evidence_list", []))))

        # -------------------------------------------------------------
        # 1. TRƯỜNG HỢP: CẦN LÀM RÕ (CLARIFICATION NEEDED)
        # -------------------------------------------------------------
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

        # -------------------------------------------------------------
        # 2. TRƯỜNG HỢP: KHÔNG CÓ BẰNG CHỨNG (ABSTENTION POLICY - NONE)
        # -------------------------------------------------------------
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

        # -------------------------------------------------------------
        # 3. TRƯỜNG HỢP CÓ BẰNG CHỨNG: HYBRID MODE hoặc STRICT RAG MODE
        # -------------------------------------------------------------
        generation_mode = "HYBRID" if evidence_status == "PARTIAL" else "STRICT_RAG"

        # Xây dựng messages prompt
        messages = build_synthesis_messages(
            query=query,
            llm_context=llm_context,
            intent=resolved_intent,
            evidence_status=evidence_status,
            conversation_history=conversation_history,
        )

        try:
            # Gọi LLM
            draft_answer = self.llm_client.generate(
                prompt=messages,
                system_instruction=SYSTEM_INSTRUCTION_SYNTHESIS,
            )
        except Exception as e:
            logger.error("Error generating answer with LLM: %s", e)
            # Fallback sang Default deterministic nếu client bị lỗi mạng/timeout
            fallback_client = DefaultDeterministicLLM()
            draft_answer = fallback_client.generate(prompt=messages)

        # Trích xuất toàn bộ các thẻ trích dẫn [1], [2] từ văn bản Markdown
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
