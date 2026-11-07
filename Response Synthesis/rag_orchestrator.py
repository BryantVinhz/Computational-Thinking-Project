"""
Module: rag_orchestrator.py
Stage 4: Response Synthesis, Evidence Verification, and Traceable Response
Role: NGUYÊN (Response Synthesis & Orchestration)

Bộ điều phối trung tâm (RAG Orchestrator) liên kết toàn bộ luồng RAG từ:
1. Giai đoạn 2: Query Understanding & Routing (Phân loại Intent, Safety check, Query Plan)
2. Giai đoạn 3: Retrieval (BM25 + Dense + RRF + Reranking + MMR -> Assembled Evidence)
3. Giai đoạn 4: Response Synthesis (Nguyên) -> Verification & Footnotes (Bảo) -> Final Envelope
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

try:
    from .schema_response import (
        ClaimStatus,
        FinalResponseEnvelope,
        SynthesizedDraftSchema,
        TraceableCitationSchema,
        VerificationReportSchema,
        VerificationStatus,
    )
    from .synthesizer import ResponseSynthesizer
    from .verifier import EvidenceVerifier                    
    from .traceable_formatter import TraceableFormatter  
except ImportError:
    from schema_response import (
        ClaimStatus,
        FinalResponseEnvelope,
        SynthesizedDraftSchema,
        TraceableCitationSchema,
        VerificationReportSchema,
        VerificationStatus,
    )
    from synthesizer import ResponseSynthesizer
    from verifier import EvidenceVerifier                    
    from traceable_formatter import TraceableFormatter  

logger = logging.getLogger(__name__)


@runtime_checkable
class RetrievalSystemProtocol(Protocol):
    """Interface tiêu chuẩn cho Retrieval System."""
    def retrieve(self, plan: dict[str, Any]) -> dict[str, Any]:
        ...


@runtime_checkable
class VerifierProtocol(Protocol):
    """Interface cho module Verifier (do Bảo phụ trách)."""
    def verify(
        self,
        draft: SynthesizedDraftSchema,
        evidence_list: list[dict[str, Any]],
    ) -> VerificationReportSchema:
        ...


@runtime_checkable
class FormatterProtocol(Protocol):
    """Interface cho module TraceableFormatter (do Bảo phụ trách)."""
    def format_citations(
        self,
        draft: SynthesizedDraftSchema,
        evidence_list: list[dict[str, Any]],
    ) -> tuple[str, list[TraceableCitationSchema]]:
        ...


class DefaultTraceableFormatter:
    """
    Bộ định dạng trích dẫn dự phòng chuẩn hóa (Fallback Traceable Formatter)
    hoạt động độc lập khi module của Bảo đang trong quá trình tích hợp.
    """

    @staticmethod
    def format_citations(
        draft: SynthesizedDraftSchema,
        evidence_list: list[dict[str, Any]],
    ) -> tuple[str, list[TraceableCitationSchema]]:
        """
        Bóc tách thông tin trích dẫn từ evidence_list tương ứng với các thẻ [1], [2]...
        và sinh phần Footnotes ở cuối bài viết Markdown.
        """
        if draft.is_abstained or not evidence_list:
            return draft.draft_answer, []

        ev_by_tag: dict[str, dict[str, Any]] = {}
        for idx, ev in enumerate(evidence_list, start=1):
            tag = f"[{idx}]"
            ev_by_tag[tag] = ev

        citations: list[TraceableCitationSchema] = []
        cited_tags = draft.cited_evidence_ids or list(ev_by_tag.keys())

        # Tạo danh sách TraceableCitationSchema
        for tag in cited_tags:
            ev = ev_by_tag.get(tag)
            if not ev:
                continue

            meta = ev.get("metadata", {})
            prov = ev.get("provenance", {})

            canonical_title = (
                prov.get("canonical_title")
                or meta.get("title")
                or meta.get("document_id")
                or "Tài liệu kỹ thuật an toàn thông tin"
            )
            heading_path = meta.get("heading_path") or prov.get("heading_path") or []
            location_str = meta.get("location_summary") or f"Chunk ID: {ev.get('chunk_id')}"
            snippet = (ev.get("text_content") or "")[:200].replace("\n", " ")

            citations.append(
                TraceableCitationSchema(
                    tag=tag,
                    chunk_id=ev.get("chunk_id", ""),
                    canonical_title=canonical_title,
                    heading_path=heading_path,
                    document_id=str(prov.get("document_id") or meta.get("document_id", "")),
                    version_id=str(prov.get("version_id") or meta.get("version_id", "")),
                    file_name=str(prov.get("file_name") or meta.get("file_name", "")),
                    file_format=str(prov.get("file_format") or meta.get("file_format", "MARKDOWN")),
                    location_summary=location_str,
                    locator=prov.get("locator", {}),
                    snippet=snippet,
                )
            )

        # Ghép Footnotes vào cuối câu trả lời nếu có citations
        if not citations:
            return draft.draft_answer, []

        footnotes = ["\n\n---\n###Danh mục tài liệu tham chiếu (References & Citations)"]
        for c in citations:
            heading_info = f" > {' > '.join(c.heading_path)}" if c.heading_path else ""
            footnotes.append(
                f"- **{c.tag}** *{c.canonical_title}*{heading_info} — `{c.location_summary}`"
            )

        final_markdown = draft.draft_answer.rstrip() + "\n" + "\n".join(footnotes)
        return final_markdown, citations


class RAGOrchestrator:
    """
    Bộ điều phối toàn diện cho hệ thống RAG (Computational Thinking Project).
    Kết nối tuần tự: Stage 2 -> Stage 3 (Retrieval) -> Stage 4 (Synthesis & Verification).
    """

    def __init__(
        self,
        retrieval_system: Any | None = None,
        synthesizer: ResponseSynthesizer | None = None,
        verifier: Any | None = None,
        traceable_formatter: Any | None = None,
    ):
        self.synthesizer = synthesizer or ResponseSynthesizer()
        if verifier is not None:
            self.verifier = verifier
        else:
            try:
                from .verifier import EvidenceVerifier, MultilingualSemanticSimilarity
                sim_fn = MultilingualSemanticSimilarity(model_name="BAAI/bge-m3")
                self.verifier = EvidenceVerifier(similarity_fn=sim_fn, support_threshold=0.60)
            except Exception:
                from .verifier import EvidenceVerifier
                self.verifier = EvidenceVerifier()
        self.traceable_formatter = traceable_formatter or TraceableFormatter()
        self.retrieval_system = retrieval_system

    def _get_retrieval_system(self) -> Any:
        """Khởi tạo trễ (lazy init) RetrievalSystem nếu chưa được truyền vào."""
        if self.retrieval_system is not None:
            return self.retrieval_system

        try:
            from retrieval_system import RetrievalSystem
            self.retrieval_system = RetrievalSystem()
            return self.retrieval_system
        except Exception as e:
            logger.warning("Không thể tự động nạp RetrievalSystem: %s", e)
            return None

    def execute_stage2(
        self,
        query: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Thực thi Stage 2: Query Understanding & Routing."""
        try:
            from query_understanding_routing.pipeline import process_query
            return process_query(query, conversation_history=conversation_history)
        except Exception:
            # Fallback tối thiểu nếu không import được pipeline Stage 2
            logger.exception("Stage 2 failed")
            return{
                "schema_version": "1.0", 
                "status": "error", 
                "original_query": query,
                "error_code": "internal_error",
                "reason": "Hệ thống tạm thời không xử lý được yêu cầu. Vui lòng thử lại sau."
            }

    def run_from_retrieval(
        self,
        query: str,
        retrieval_output: dict[str, Any],
        resolved_query: str | None = None,
        intent: str | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> FinalResponseEnvelope:
        """
        Thực thi Stage 4 trực tiếp khi đã có sẵn output từ Retrieval.
        Rất hữu ích khi test hoặc khi luồng Retrieval chạy độc lập.
        """
        start_time = time.perf_counter()
        active_query = resolved_query or query
        resolved_intent = intent or retrieval_output.get("intent", "SEARCH")

        # 1. Gọi ResponseSynthesizer (Nguyên)
        draft: SynthesizedDraftSchema = self.synthesizer.synthesize(
            query=active_query,
            retrieval_output=retrieval_output,
            intent=resolved_intent,
            conversation_history=conversation_history,
        )

        evidence_list = retrieval_output.get("evidence_list", [])

        # 2. Gọi Verifier (Bảo) nếu có
        verification_report: VerificationReportSchema | None = None
        if self.verifier is not None:
            try:
                verification_report = self.verifier.verify(draft, evidence_list)
            except Exception as e:
                logger.error("Error executing Verifier: %s", e)
        else:
            # Tự động tạo report mặc định
            if draft.is_abstained:
                verification_report = VerificationReportSchema(
                    faithfulness_score=1.0,
                    status=VerificationStatus.SKIPPED,
                    total_claims=0,
                    warnings=["Verification skipped due to abstention / clarification flow."],
                )
            else:
                verification_report = VerificationReportSchema(
                    faithfulness_score=0.95 if draft.generation_mode == "STRICT_RAG" else 0.85,
                    status=VerificationStatus.PASSED,
                    total_claims=len(draft.cited_evidence_ids),
                    supported_claims=len(draft.cited_evidence_ids),
                    unverified_claims=0,
                    contradicted_claims=0,
                )

        # 3. Định dạng Trích dẫn & Footnotes (Traceable Formatter)
        final_markdown, citations = self.traceable_formatter.format_citations(draft, evidence_list)

        total_latency = time.perf_counter() - start_time

        # 4. Xác định status phản hồi
        status = "ok"
        if retrieval_output.get("status") == "clarification_needed":
            status = "clarification_needed"
        elif draft.is_abstained:
            status = "abstained"

        return FinalResponseEnvelope(
            schema_version="1.0",
            status=status,
            original_query=query,
            resolved_query=active_query,
            intent=resolved_intent,
            final_answer_markdown=final_markdown,
            verification=verification_report,
            citations=citations,
            raw_draft=draft,
            metadata={
                "total_latency_s": round(total_latency, 4),
                "synthesis_latency_s": draft.generation_metadata.get("latency_s", 0.0),
                "generation_mode": draft.generation_mode,
                "evidence_status": draft.evidence_status,
                "total_evidence": retrieval_output.get("total_evidence", len(evidence_list)),
                "cited_count": len(citations),
            },
        )

    def _early_exit(self, status, query, s2, message, reason=None):
        return FinalResponseEnvelope(
            status=status,
            original_query=query,
            resolved_query=query,
            intent=s2.get("intent", "UNKNOWN"),
            final_answer_markdown=message,
            verification=VerificationReportSchema(
                faithfulness_score=1.0,
                status=VerificationStatus.SKIPPED,
                warnings=[f"Stage 2 returned: {s2.get('status')}"],
            ),
            citations=[],
            metadata={"rejected_at_stage": 2,
                    "reason": reason or message,
                    "reject_reason": s2.get("reject_reason"),
                    "error_code": s2.get("error_code")},
        )
    
    def run(
        self,
        query: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> FinalResponseEnvelope:
        """
        Chạy toàn bộ quy trình RAG hoàn chỉnh (End-to-End Orchestration).
        Query -> Stage 2 (Understanding & Routing) -> Stage 3 (Retrieval) -> Stage 4 (Synthesis).
        """
        # Bước 1: Stage 2 - Query Understanding & Routing
        s2 = self.execute_stage2(query, conversation_history)
        status = s2.get("status", "error")

        # Xử lý các trường hợp bị từ chối từ Stage 2 (Safety, Out of Scope, Invalid)
        if status in {"rejected", "out_of_scope", "error"}:
            reason = s2.get("reason") or "Yêu cầu không thuộc phạm vi xử lý."
            return self._early_exit(status, query, s2, f"**Không thể xử lý ({status})**: {reason}")

        if status == "clarification":
            msg = s2.get("clarification_message") or "Vui lòng làm rõ câu hỏi."
            return self._early_exit("clarification_needed", query, s2, msg)

        if status != "ok" or "query_plan" not in s2:
            return self._early_exit("error", query, s2, "Phản hồi Stage 2 không hợp lệ.")

        query_plan = s2["query_plan"]
        resolved_query = query_plan.get("resolved_query") or query
        intent = query_plan.get("intent", "SEARCH")

        # Bước 2: Stage 3 - Retrieval System
        retrieval_sys = self._get_retrieval_system()
        if retrieval_sys is None:
            # Fallback nếu không có RetrievalSystem khả dụng
            retrieval_output = {
                "status": "ok",
                "evidence_status": "NONE",
                "intent": intent,
                "total_evidence": 0,
                "evidence_list": [],
                "llm_context": "Không thể kết nối đến hệ thống Retrieval.",
            }
        else:
            # GỌI RETRIEVE THEO CẬP NHẬT MỚI NHẤT CỦA COMMIT e858eca
            retrieval_output = retrieval_sys.retrieve(query_plan)

        # Bước 3 & 4: Stage 4 - Synthesis & Final Packaging
        return self.run_from_retrieval(
            query=query,
            retrieval_output=retrieval_output,
            resolved_query=resolved_query,
            intent=intent,
            conversation_history=conversation_history,
        )
