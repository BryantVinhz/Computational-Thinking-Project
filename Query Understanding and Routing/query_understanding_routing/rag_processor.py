"""
End-to-End RAG Processor & Generator Module.
Author: Long (Query Understanding Lead) & Tri (Validation & Routing)

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

RAG_GENERATION_SYSTEM_PROMPT = """Bạn là trợ lý AI chuyên sâu về An toàn thông tin và Bảo mật ứng dụng Web (OWASP, MITRE ATT&CK, CVSS, ASVS).
Nhiệm vụ của bạn là trả lời câu hỏi của người dùng dựa CHÍNH XÁC trên các đoạn văn bản trích dẫn (Context Chunks) được cung cấp dưới đây.

QUY TẮC BẮT BUỘC:
1. Chỉ sử dụng thông tin có trong ngữ cảnh được cung cấp. Không bịa đặt hoặc suy diễn vượt quá tài liệu.
2. Trích dẫn rõ ràng nguồn gốc thông tin bằng định dạng [Nguồn: <Tên tài liệu>, Phần: <Tên mục>].
3. Nếu ngữ cảnh không chứa đủ thông tin để trả lời, hãy thông báo rõ ràng cho người dùng thay vì tự tạo câu trả lời.
4. Trình bày rành mạch, có cấu trúc: định nghĩa/cơ chế, nguy cơ, và phương pháp phòng chống cụ thể.
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
            section = chunk.get("section", "Chung")
            text = chunk.get("text", "")
            context_blocks.append(f"--- ĐOẠN TRÍCH {i} [Tài liệu: {doc} | Mục: {section}] ---\n{text}")

        context_str = "\n\n".join(context_blocks) if context_blocks else "Không tìm thấy đoạn trích phù hợp."

        messages = [
            {"role": "system", "content": RAG_GENERATION_SYSTEM_PROMPT},
        ]

        # Add relevant conversation history if any
        if conversation_history:
            for turn in conversation_history[-4:]:
                messages.append({
                    "role": str(turn.get("role", "user")),
                    "content": str(turn.get("content", "")),
                })

        user_content = (
            f"NGỮ CẢNH TÀI LIỆU (Context Chunks):\n{context_str}\n\n"
            f"CÂU HỎI CỦA NGƯỜI DÙNG:\n{query}\n\n"
            f"Hãy trả lời câu hỏi trên dựa trên ngữ cảnh đã cung cấp kèm trích dẫn."
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
                "text": "XSS xảy ra khi ứng dụng đưa dữ liệu không đáng tin cậy vào trang web mà không được xác thực hoặc mã hóa đúng cách. Phòng chống: Context-aware Output Encoding và Content Security Policy (CSP).",
            })
        if "sqli" in vulns:
            chunks.append({
                "chunk_id": "chunk_sqli_001",
                "document": "OWASP Top 10 2025",
                "section": "A05: Injection - SQL Injection",
                "text": "SQL Injection xảy ra khi dữ liệu người dùng được ghép trực tiếp vào câu truy vấn SQL. Phòng chống: Sử dụng Parameterized Queries (Prepared Statements) hoặc ORM an toàn.",
            })
        if "bac" in vulns:
            chunks.append({
                "chunk_id": "chunk_bac_001",
                "document": "OWASP Top 10 2025",
                "section": "A01: Broken Access Control",
                "text": "Broken Access Control là lỗ hổng nghiêm trọng nhất trong OWASP Top 10. Người dùng có thể hành động vượt ngoài phạm vi quyền hạn được cấp. Phòng chống: Áp dụng nguyên tắc Least Privilege và kiểm tra quyền tại tầng server.",
            })

        if not chunks:
            chunks.append({
                "chunk_id": "chunk_gen_001",
                "document": "Cyber Security Knowledge Base",
                "section": "General Security",
                "text": "Các tiêu chuẩn bảo mật chính gồm OWASP ASVS v5.0, OWASP Top 10, và CVSS v4.0.",
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
        # Step 1: Stage 2 processing
        envelope = process_query(
            raw_query=raw_query,
            conversation_history=conversation_history,
            llm_client=self.llm_client,
        )

        # Early exit if status is not 'ok'
        if envelope.get("status") != "ok":
            return {
                "status": envelope.get("status"),
                "envelope": envelope,
                "generation": None,
            }

        # Step 2: Retrieval
        query_plan = envelope.get("query_plan", {})
        retrieved_chunks = self.retriever_func(query_plan)

        # Step 3: LLM Generation
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
