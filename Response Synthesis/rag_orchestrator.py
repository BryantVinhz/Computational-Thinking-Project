"""
Module: rag_orchestrator.py
Stage 4: Response Synthesis, Evidence Verification, and Traceable Response

Central RAG Orchestrator connecting the end-to-end RAG pipeline across:
1. Stage 2: Query Understanding & Routing (Intent classification, safety check, Query Plan)
2. Stage 3: Retrieval (BM25 + Dense + RRF + Reranking + MMR -> Assembled Evidence)
3. Stage 4: Response Synthesis -> Verification & Footnotes -> Final Envelope
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
    from .verifier import SEMANTIC_SUPPORT_THRESHOLD, EvidenceVerifier, MultilingualSemanticSimilarity
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
    from verifier import SEMANTIC_SUPPORT_THRESHOLD, EvidenceVerifier, MultilingualSemanticSimilarity
    from traceable_formatter import TraceableFormatter  

logger = logging.getLogger(__name__)


@runtime_checkable
class RetrievalSystemProtocol(Protocol):
    """Standard interface for the Retrieval System."""
    def retrieve(self, plan: dict[str, Any]) -> dict[str, Any]:
        ...


@runtime_checkable
class VerifierProtocol(Protocol):
    """Interface for the Evidence Verifier module."""
    def verify(
        self,
        draft: SynthesizedDraftSchema,
        evidence_list: list[dict[str, Any]],
    ) -> VerificationReportSchema:
        ...


@runtime_checkable
class FormatterProtocol(Protocol):
    """Interface for the TraceableFormatter module."""
    def format_citations(
        self,
        draft: SynthesizedDraftSchema,
        evidence_list: list[dict[str, Any]],
    ) -> tuple[str, list[TraceableCitationSchema]]:
        ...


class DefaultTraceableFormatter:
    """
    Fallback Traceable Formatter that operates independently when a custom formatter is not provided.
    """

    @staticmethod
    def format_citations(
        draft: SynthesizedDraftSchema,
        evidence_list: list[dict[str, Any]],
    ) -> tuple[str, list[TraceableCitationSchema]]:
        """
        Extract citation metadata from evidence_list corresponding to tags [1], [2]...
        and append a Footnotes section at the end of the Markdown response.
        """
        if draft.is_abstained or not evidence_list:
            return draft.draft_answer, []

        ev_by_tag: dict[str, dict[str, Any]] = {}
        for idx, ev in enumerate(evidence_list, start=1):
            tag = f"[{idx}]"
            ev_by_tag[tag] = ev

        citations: list[TraceableCitationSchema] = []
        cited_tags = draft.cited_evidence_ids or list(ev_by_tag.keys())

        # Build list of TraceableCitationSchema records
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

        # Append footnotes to the end of the answer if citations are present
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
    End-to-end orchestrator for the RAG system.
    Connects Stage 2 -> Stage 3 (Retrieval) -> Stage 4 (Synthesis & Verification).
    """

    def __init__(
        self,
        retrieval_system: Any | None = None,
        synthesizer: ResponseSynthesizer | None = None,
        verifier: Any | None = None,
        traceable_formatter: Any | None = None,
    ):
        self.synthesizer = synthesizer or ResponseSynthesizer()
        # Vietnamese answers are verified against an English corpus, so the default verifier must be
        # cross-lingual. If the model cannot load, verify() raises and run_from_retrieval records FAILED.
        self.verifier = verifier or EvidenceVerifier(
            similarity_fn=MultilingualSemanticSimilarity(),
            support_threshold=SEMANTIC_SUPPORT_THRESHOLD,
        )
        self.traceable_formatter = traceable_formatter or TraceableFormatter()
        self.retrieval_system = retrieval_system

    def _get_retrieval_system(self) -> Any:
        """Lazy-initialize RetrievalSystem if not already provided."""
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
        """Execute Stage 2: Query Understanding & Routing."""
        try:
            from query_understanding_routing.pipeline import process_query
            return process_query(query, conversation_history=conversation_history)
        except Exception:
            # Minimal fallback if Stage 2 pipeline cannot be imported
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
        Run Stage 4 on an existing Retrieval output.
        A draft whose verification FAILED is never returned as an "ok" answer: it is withheld
        (status "abstained") and kept in raw_draft for audit.
        """
        start_time = time.perf_counter()
        active_query = resolved_query or query
        resolved_intent = intent or retrieval_output.get("intent", "SEARCH")

        draft: SynthesizedDraftSchema = self.synthesizer.synthesize(
            query=active_query,
            retrieval_output=retrieval_output,
            intent=resolved_intent,
            conversation_history=conversation_history,
        )

        evidence_list = retrieval_output.get("evidence_list", [])

        try:
            verification_report = self.verifier.verify(draft, evidence_list)
        except Exception as e:
            logger.error("Error executing Verifier: %s", e)
            verification_report = VerificationReportSchema(
                faithfulness_score=0.0,
                status=VerificationStatus.FAILED,
                warnings=[f"Verifier error: {e}"],
            )

        final_markdown, citations = self.traceable_formatter.format_citations(draft, evidence_list)

        verification_gate = "skipped" if draft.is_abstained else "passed"
        if not draft.is_abstained and verification_report.status == VerificationStatus.FAILED:
            verification_gate = "blocked"
            final_markdown = (
                ">>> Câu trả lời đã bị chặn: nội dung sinh ra không được tài liệu nguồn chứng thực "
                f"(faithfulness {verification_report.faithfulness_score:.0%}). "
                "Vui lòng diễn đạt lại câu hỏi hoặc thu hẹp phạm vi tài liệu."
            )
            citations = []
        elif not draft.is_abstained and verification_report.status == VerificationStatus.WARNING:
            verification_gate = "warning"
            final_markdown = (
                ">>> Một số nhận định chưa được tài liệu nguồn chứng thực đầy đủ "
                f"(faithfulness {verification_report.faithfulness_score:.0%}).\n\n{final_markdown}"
            )
            
        total_latency = time.perf_counter() - start_time

        # 4. Determine response status
        status = "ok"
        if retrieval_output.get("status") == "clarification_needed":
            status = "clarification_needed"
        elif draft.is_abstained or verification_gate == "blocked":
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
                "verification_gate": verification_gate,
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
        Execute the complete end-to-end RAG workflow:
        Query -> Stage 2 (Understanding & Routing) -> Stage 3 (Retrieval) -> Stage 4 (Synthesis).
        """
        # Step 1: Stage 2 - Query Understanding & Routing
        s2 = self.execute_stage2(query, conversation_history)
        status = s2.get("status", "error")

        # Handle requests rejected at Stage 2 (Safety, Out of Scope, Invalid)
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

        # Step 2: Stage 3 - Retrieval System
        retrieval_sys = self._get_retrieval_system()
        if retrieval_sys is None:
            # Fallback when RetrievalSystem is unavailable
            retrieval_output = {
                "status": "ok",
                "evidence_status": "NONE",
                "intent": intent,
                "total_evidence": 0,
                "evidence_list": [],
                "llm_context": "Không thể kết nối đến hệ thống Retrieval.",
            }
        else:
            retrieval_output = retrieval_sys.retrieve(query_plan)

        # Step 3 & 4: Stage 4 - Synthesis & Final Packaging
        return self.run_from_retrieval(
            query=query,
            retrieval_output=retrieval_output,
            resolved_query=resolved_query,
            intent=intent,
            conversation_history=conversation_history,
        )
