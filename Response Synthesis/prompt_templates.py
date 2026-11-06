"""
Module: prompt_templates.py
Stage 4: Response Synthesis, Evidence Verification, and Traceable Response

Standardized Prompt Templates for query Intents:
- SEARCH (Single-topic explanation and lookup)
- COMPARE (Multi-entity / vulnerability comparison)
- SYNTHESIS (In-depth multi-document synthesis)
- Supports STRICT_RAG and HYBRID MODE based on `evidence_status`.
"""

from __future__ import annotations

import re
from typing import Any


SYSTEM_INSTRUCTION_SYNTHESIS = """You are a Senior Information Security and Web Application Security Expert specializing in OWASP Top 10, ASVS, WSTG, CWE, and MITRE ATT&CK standards.

Your task is to synthesize and answer the user's question accurately, professionally, and in well-structured Markdown based on the provided DOCUMENT EVIDENCE (Context Chunks).

MANDATORY CITATION RULES:
1. Each piece of evidence is numbered sequentially as [1], [2], [3]... corresponding to each Evidence item in the Context section.
2. Whenever you state any technical claim, mechanism, code example, or security recommendation derived from the evidence, you MUST insert the citation tag [1], [2] immediately after that statement.
   - Example: "SQL Injection occurs when user input is not properly validated before being concatenated into a query [1]. The standard remediation is to use Prepared Statements [2]."
3. Only cite [i] tags that actually exist in the provided evidence list. Never fabricate citation numbers (such as [99] when only [1], [2] exist).
4. Present the answer coherently in standard Markdown (use ##, ### headings, bullet points, and code blocks for source code or commands).
"""

STRICT_RAG_GUIDELINES = """[EXECUTION MODE: STRICT RAG MODE - FULL EVIDENCE (evidence_status=FULL)]
- You must strictly adhere to closed-book grounding principles.
- ONLY use information and facts present in the DOCUMENT EVIDENCE (Context) section.
- Do NOT extrapolate or add external information that is not grounded in the provided documents.
- Every key technical claim must include a citation tag [i].
"""

HYBRID_MODE_GUIDELINES = """[EXECUTION MODE: HYBRID MODE - PARTIAL EVIDENCE (evidence_status=PARTIAL)]
- The internal knowledge base only found PARTIAL information needed to answer the user's question.
- HYBRID COMBINATION PRINCIPLES:
  1. For content supported by retrieved documents: You MUST prioritize using it and insert accurate citation tags [1], [2] as usual.
  2. For aspects/criteria missing from the documents: You ARE PERMITTED to apply your general security domain expertise to supplement the answer so it is complete and helpful to the user.
  3. TRANSPARENCY NOTICE: Any supplemental information outside the retrieved documents MUST be placed in a dedicated section or clearly noted, for example:
     "### 💡 General Knowledge Advisory"
     or noted as: "(Note: The following section is supplemented from general domain knowledge as internal documents do not cover it)."
  4. Do NOT attach citation tags [i] to information supplemented from your general knowledge outside the internal documents.
"""

ABSTENTION_MESSAGE_TEMPLATE = """The system's knowledge base currently does not contain sufficient reliable security documentation related to the query: "{query}".

Detailed reason: {reason}

The system has activated its Abstention Policy to ensure accuracy and prevent hallucination. Please review your question or provide more specific contextual keywords.
"""


def extract_citation_tags(text: str) -> list[str]:
    """
    Extract all citation tags of the form [1], [2] from Markdown text,
    deduplicate them, and sort them in ascending integer order.
    """
    if not text:
        return []
    matches = re.findall(r"\[(\d+)\]", text)
    unique_ids = sorted({int(m) for m in matches})
    return [f"[{i}]" for i in unique_ids]


def build_search_prompt(
    query: str,
    llm_context: str,
    mode: str = "STRICT_RAG",
) -> str:
    """Build prompt dedicated to Intent SEARCH (Lookup & Explanation)."""
    guideline = HYBRID_MODE_GUIDELINES if mode == "HYBRID" else STRICT_RAG_GUIDELINES
    return f"""{guideline}

=== PROVIDED DOCUMENT EVIDENCE (CONTEXT) ===
{llm_context}
============================================

REQUIREMENTS FOR INTENT 'SEARCH':
- Answer the user's question directly, accurately, and with technical depth.
- Recommended structure:
  1. Core Concept / Definition (with [i])
  2. Mechanism of Action & Attack Vectors / Risks (with [i])
  3. Prevention, Remediation, and Secure Code Example (with [i])

USER QUESTION:
{query}

Generate the complete Markdown response:"""


def build_compare_prompt(
    query: str,
    llm_context: str,
    mode: str = "STRICT_RAG",
) -> str:
    """Build prompt dedicated to Intent COMPARE (Comparison & Contrast)."""
    guideline = HYBRID_MODE_GUIDELINES if mode == "HYBRID" else STRICT_RAG_GUIDELINES
    return f"""{guideline}

=== PROVIDED DOCUMENT EVIDENCE (CONTEXT) ===
{llm_context}
============================================

REQUIREMENTS FOR INTENT 'COMPARE':
- Perform a multi-dimensional comparison across the requested security entities, vulnerabilities, or mechanisms.
- MUST include:
  1. Summary Comparison Table (Markdown Table) covering at least: Concept, Affected Layer/Component, Risk Severity (CVSS/CWE), Exploitation Method, Primary Defense.
  2. Detailed analysis of core similarities and differences (with citation tags [i]).
  3. Practical application scenarios or commonly confused situations between them.

COMPARISON QUESTION:
{query}

Generate the complete comparison Markdown response:"""


def build_synthesis_prompt(
    query: str,
    llm_context: str,
    mode: str = "STRICT_RAG",
) -> str:
    """Build prompt dedicated to Intent SYNTHESIS (Comprehensive Multi-Document Synthesis)."""
    guideline = HYBRID_MODE_GUIDELINES if mode == "HYBRID" else STRICT_RAG_GUIDELINES
    return f"""{guideline}

=== PROVIDED DOCUMENT EVIDENCE (CONTEXT) ===
{llm_context}
============================================

REQUIREMENTS FOR INTENT 'SYNTHESIS':
- Synthesize knowledge from multiple document sources into a comprehensive, well-structured information security advisory.
- Recommended structure:
  ## 1. Executive Summary
  ## 2. Technical Threat Landscape
  ## 3. Defense-in-Depth Roadmap
  ## 4. Standards & Framework Mapping (OWASP / CWE / ASVS Mapping)
- Insert citations [i] throughout all sections.

SYNTHESIS REQUEST:
{query}

Generate the complete synthesized Markdown draft:"""


def build_synthesis_messages(
    query: str,
    llm_context: str,
    intent: str = "SEARCH",
    evidence_status: str = "FULL",
    conversation_history: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """
    Build the Messages Payload for the LLM Client.
    Automatically selects the appropriate prompt builder based on intent and evidence_status.
    """
    mode = "HYBRID" if evidence_status.upper() == "PARTIAL" else "STRICT_RAG"
    normalized_intent = (intent or "SEARCH").upper()

    if normalized_intent == "COMPARE":
        user_prompt = build_compare_prompt(query, llm_context, mode=mode)
    elif normalized_intent == "SYNTHESIS":
        user_prompt = build_synthesis_prompt(query, llm_context, mode=mode)
    else:  # Fallback to SEARCH
        user_prompt = build_search_prompt(query, llm_context, mode=mode)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_INSTRUCTION_SYNTHESIS}
    ]

    # Include up to the 4 most recent conversation turns if available
    if conversation_history:
        for turn in conversation_history[-4:]:
            role = str(turn.get("role", "user"))
            content = str(turn.get("content", ""))
            if content.strip():
                messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_prompt})
    return messages
