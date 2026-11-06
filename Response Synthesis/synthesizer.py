"""
Module: synthesizer.py
Stage 4: Response Synthesis, Evidence Verification, and Traceable Response
Role: Response Synthesis

Invokes the LLM to generate a synthesized response draft (SynthesizedDraftSchema)
based on retrieved document context (Evidence Chunks) and query Intent,
supporting flexible branching across:
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
    """Standardized protocol for the LLM Client in the response synthesis layer."""
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
    Generates a well-structured sample response with accurate [1], [2] citation tags
    for testing and offline validation.
    """
    model_name: str = "builtin-deterministic-synthesis"

    def generate(
        self,
        prompt: Any,
        system_instruction: str | None = None,
        **kwargs: Any,
    ) -> str:
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
                "### Analysis Based on Retrieved Documents\n\n"
                "Based on the documents retrieved in the system, the security mechanism and solution are identified as follows [1]. "
                "Security configurations are controlled and verified through the corresponding technical standards [2].\n\n"
                "### 💡 General Knowledge Advisory\n\n"
                "(Note: This section is supplemented from expert domain knowledge as internal documents only provided partial evidence)\n"
                "- Implement Defense-in-Depth principles combined with periodic security testing.\n"
                "- Establish centralized monitoring and logging mechanisms for early detection of anomalous behavior."
            )

        return (
            "### Security Analysis Results\n\n"
            "The security issue above is defined and documented in the standards system [1]. "
            "The exploitation mechanism stems from insufficient control over input data flows [1].\n\n"
            "### Prevention and Remediation Measures\n\n"
            "For complete remediation, the system must enforce strict input validation and data normalization principles [2]. "
            "Additionally, configure least-privilege access and follow secure technical guidelines [2]."
        )


class ResponseSynthesizer:
    """
    Response synthesis layer (Stage 4 - Synthesis Layer).
    Accepts Retrieval output and generates a SynthesizedDraftSchema with [1], [2] citation tags.
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
            query: User question (original_query or resolved_query).
            retrieval_output: Output dictionary returned by RetrievalSystem.retrieve(plan).
            intent: Query intent (SEARCH | COMPARE | SYNTHESIS).
            conversation_history: Optional conversation history.
            
        Returns:
            SynthesizedDraftSchema: Contains draft answer, [1], [2] tags, and generation metadata.
        """
        start_time = time.perf_counter()

        status = retrieval_output.get("status", "ok")
        evidence_status = str(retrieval_output.get("evidence_status", "FULL")).upper()
        resolved_intent = str(intent or retrieval_output.get("intent") or "SEARCH").upper()
        llm_context = retrieval_output.get("llm_context", "").strip()
        total_evidence = int(retrieval_output.get("total_evidence", len(retrieval_output.get("evidence_list", []))))

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

        if evidence_status == "NONE" or total_evidence == 0 or not llm_context:
            latency = time.perf_counter() - start_time
            abstention_answer = (
                llm_context
                or f"The system could not find matching security document evidence for the question: '{query}'. "
                f"The Abstention Policy has been activated to prevent inaccurate information."
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

        generation_mode = "HYBRID" if evidence_status == "PARTIAL" else "STRICT_RAG"

        messages = build_synthesis_messages(
            query=query,
            llm_context=llm_context,
            intent=resolved_intent,
            evidence_status=evidence_status,
            conversation_history=conversation_history,
        )

        try:
            draft_answer = self.llm_client.generate(
                prompt=messages,
                system_instruction=SYSTEM_INSTRUCTION_SYNTHESIS,
            )
        except Exception as e:
            logger.error("LLM generate failed: %s", e)
            return SynthesizedDraftSchema(
                draft_answer="The system is temporarily unable to connect to the response generation model. Please try again later.",
                intent=resolved_intent, cited_evidence_ids=[],
                is_abstained=True, abstention_reason="llm_unavailable",
                evidence_status=evidence_status, generation_mode="ABSTENTION",
                generation_metadata={"model_name": getattr(self.llm_client, "model_name", "unknown"),
                                    "latency_s": round(time.perf_counter() - start_time, 4),
                                    "error": str(e)},
            )

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
