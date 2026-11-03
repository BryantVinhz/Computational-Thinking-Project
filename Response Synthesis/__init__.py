"""
Response Synthesis, Evidence Verification and Traceable Response Module.
Contract and Implementation for Stage 4 of the Computational Thinking Project.

Phân công:
- Nguyên: Response Synthesis & Orchestration (schema_response.py, prompt_templates.py, synthesizer.py, rag_orchestrator.py)
- Bảo: Evidence Verification & Traceability (verifier.py, traceable_formatter.py)
"""

from .prompt_templates import (
    SYSTEM_INSTRUCTION_SYNTHESIS,
    build_compare_prompt,
    build_search_prompt,
    build_synthesis_messages,
    build_synthesis_prompt,
    extract_citation_tags,
)
from .rag_orchestrator import (
    DefaultTraceableFormatter,
    RAGOrchestrator,
)
from .schema_response import (
    ClaimStatus,
    ClaimVerificationResult,
    FinalResponseEnvelope,
    SynthesizedDraftSchema,
    TraceableCitationSchema,
    VerificationReportSchema,
    VerificationStatus,
)
from .synthesizer import (
    DefaultDeterministicLLM,
    ResponseSynthesizer,
)
from .traceable_formatter import TraceableFormatter
from .verifier import EvidenceVerifier

__all__ = [
    # Schemas
    "ClaimStatus",
    "VerificationStatus",
    "ClaimVerificationResult",
    "VerificationReportSchema",
    "TraceableCitationSchema",
    "SynthesizedDraftSchema",
    "FinalResponseEnvelope",
    # Synthesis & Templates
    "SYSTEM_INSTRUCTION_SYNTHESIS",
    "build_search_prompt",
    "build_compare_prompt",
    "build_synthesis_prompt",
    "build_synthesis_messages",
    "extract_citation_tags",
    "ResponseSynthesizer",
    "DefaultDeterministicLLM",
    # Orchestrator
    "RAGOrchestrator",
    "DefaultTraceableFormatter",
    # Verification & Traceability
    "EvidenceVerifier",
    "TraceableFormatter",
]
