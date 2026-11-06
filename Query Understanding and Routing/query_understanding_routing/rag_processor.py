"""
End-to-End RAG Processor & Generator Module.

Demonstrates the complete standard RAG process:
1. Stage 2: Query Validation, Normalization, Query Understanding (calling LLM) & Routing.
2. Stage 3: Retrieval of security knowledge chunks matching query plan objectives.
3. Stage 4: Grounded LLM Response Generation synthesizing answers with citations.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

try:
    from query_understanding_routing.llm_client import BaseLLMClient, create_llm_client
    from query_understanding_routing.pipeline import process_query
except ImportError:
    from llm_client import BaseLLMClient, create_llm_client
    from pipeline import process_query

logger = logging.getLogger(__name__)

RAG_GENERATION_SYSTEM_PROMPT = """You are an AI assistant specializing in Information Security and Web Application Security (OWASP, MITRE ATT&CK, CVSS, ASVS).
Your task is to answer the user's question STRICTLY based on the provided document excerpts (Context Chunks) below.

MANDATORY RULES:
1. Use only information present in the provided context. Do not fabricate or extrapolate beyond the documents.
2. Clearly cite the source of information using the format [Source: <Document Title>, Section: <Section Name>].
3. If the context does not contain enough information to answer, clearly inform the user instead of making up an answer.
4. Present a clear, structured response covering: definition/mechanism, risks, and specific prevention/remediation measures.
"""


class RAGGenerator:
    """Stage 4: Grounded Response Generator calling LLM to synthesize answer."""

    def __init__(self, llm_client: Optional[BaseLLMClient] = None):
        self.llm_client = llm_client or create_llm_client(provider="mock")

    def build_generation_prompt(
        self,
        query: str,
        retrieved_chunks: List[Dict[str, Any]],
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, str]]:
        context_blocks = []
        for i, chunk in enumerate(retrieved_chunks, start=1):
            doc = chunk.get("document", "Unknown Doc")
            section = chunk.get("section", "General")
            text = chunk.get("text", "")
            context_blocks.append(f"--- EXCERPT {i} [Document: {doc} | Section: {section}] ---\n{text}")

        context_str = "\n\n".join(context_blocks) if context_blocks else "No matching excerpts found."

        messages = [
            {"role": "system", "content": RAG_GENERATION_SYSTEM_PROMPT},
        ]

        if conversation_history:
            for turn in conversation_history[-4:]:
                messages.append({
                    "role": str(turn.get("role", "user")),
                    "content": str(turn.get("content", "")),
                })

        user_content = (
            f"DOCUMENT CONTEXT (Context Chunks):\n{context_str}\n\n"
            f"USER QUESTION:\n{query}\n\n"
            f"Please answer the question above based on the provided context with citations."
        )
        messages.append({"role": "user", "content": user_content})
        return messages

    def generate_response(
        self,
        query: str,
        retrieved_chunks: List[Dict[str, Any]],
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Call LLM to generate answer and format citations."""
        messages = self.build_generation_prompt(query, retrieved_chunks, conversation_history)
        raw_answer = self.llm_client.generate(prompt=messages, system_instruction=RAG_GENERATION_SYSTEM_PROMPT)

        citations = []
        for chunk in retrieved_chunks:
            citations.append({
                "document": chunk.get("document"),
                "section": chunk.get("section"),
                "chunk_id": chunk.get("chunk_id"),
            })

        return {
            "answer": raw_answer,
            "citations": citations,
            "used_chunks_count": len(retrieved_chunks),
        }


class EndToEndRAGPipeline:
    """
    Complete RAG orchestrator tying Stage 2 Query Understanding & Routing
    with Stage 3 Retrieval and Stage 4 LLM Generation.
    """

    def __init__(
        self,
        llm_client: Optional[BaseLLMClient] = None,
        retriever_func: Optional[Any] = None,
    ):
        self.llm_client = llm_client
        self.generator = RAGGenerator(llm_client=llm_client)
        self.retriever_func = retriever_func or self._mock_retriever

    def _mock_retriever(self, query_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fallback retriever returning relevant knowledge chunks based on query plan."""
        entities = query_plan.get("entities", {})
        vulns = entities.get("vulnerabilities", [])
        chunks = []

        if "xss" in vulns:
            chunks.append({
                "chunk_id": "chunk_xss_001",
                "document": "OWASP Top 10 2025",
                "section": "A05: Injection - Cross-Site Scripting",
                "text": "XSS occurs when an application includes untrusted data in a web page without proper validation or escaping. Prevention: Context-aware Output Encoding and Content Security Policy (CSP).",
            })
        if "sqli" in vulns:
            chunks.append({
                "chunk_id": "chunk_sqli_001",
                "document": "OWASP Top 10 2025",
                "section": "A05: Injection - SQL Injection",
                "text": "SQL Injection occurs when user input is directly concatenated into a SQL query. Prevention: Use Parameterized Queries (Prepared Statements) or safe ORMs.",
            })
        if "bac" in vulns:
            chunks.append({
                "chunk_id": "chunk_bac_001",
                "document": "OWASP Top 10 2025",
                "section": "A01: Broken Access Control",
                "text": "Broken Access Control is a critical vulnerability in OWASP Top 10 where users can act outside of their intended permissions. Prevention: Enforce Least Privilege and server-side authorization checks.",
            })

        if not chunks:
            chunks.append({
                "chunk_id": "chunk_gen_001",
                "document": "Cyber Security Knowledge Base",
                "section": "General Security",
                "text": "Core security standards include OWASP ASVS v5.0, OWASP Top 10, and CVSS v4.0.",
            })
        return chunks

    def process(
        self,
        raw_query: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Execute full RAG workflow:
        1. Query Understanding & Routing (Stage 2)
        2. Early exit if rejected, clarification, or out_of_scope
        3. Retrieval (Stage 3)
        4. LLM Answer Generation (Stage 4)
        """
        envelope = process_query(
            raw_query=raw_query,
            conversation_history=conversation_history,
            llm_client=self.llm_client,
        )

        if envelope.get("status") != "ok":
            return {
                "status": envelope.get("status"),
                "envelope": envelope,
                "generation": None,
            }

        query_plan = envelope.get("query_plan", {})
        retrieved_chunks = self.retriever_func(query_plan)

        generation = self.generator.generate_response(
            query=query_plan.get("resolved_query", raw_query),
            retrieved_chunks=retrieved_chunks,
            conversation_history=conversation_history,
        )

        return {
            "status": "ok",
            "envelope": envelope,
            "retrieved_chunks": retrieved_chunks,
            "generation": generation,
        }
