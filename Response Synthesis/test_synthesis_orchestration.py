"""
Unit and Integration Tests for Stage 4: Response Synthesis & Orchestration.
Contract v1.0 & Hybrid Mode Alignment.
"""

import json
import unittest
from pathlib import Path
import sys

# Ensure Response Synthesis directory is in sys.path
STAGE_4_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = STAGE_4_DIR.parent

for p in (STAGE_4_DIR, PROJECT_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from schema_response import (
    ClaimStatus,
    FinalResponseEnvelope,
    SynthesizedDraftSchema,
    TraceableCitationSchema,
    VerificationReportSchema,
    VerificationStatus,
)
from prompt_templates import (
    build_compare_prompt,
    build_search_prompt,
    build_synthesis_messages,
    build_synthesis_prompt,
    extract_citation_tags,
)
from synthesizer import DefaultDeterministicLLM, ResponseSynthesizer
from rag_orchestrator import DefaultTraceableFormatter, RAGOrchestrator


class TestPromptTemplates(unittest.TestCase):
    def test_extract_citation_tags(self):
        text = "SQL Injection xảy ra do [1] và [2]. Biện pháp phòng vệ là [2] và [3]. Không hợp lệ [abc]."
        tags = extract_citation_tags(text)
        self.assertEqual(tags, ["[1]", "[2]", "[3]"])

    def test_extract_empty_tags(self):
        self.assertEqual(extract_citation_tags("Không có tag nào"), [])
        self.assertEqual(extract_citation_tags(""), [])

    def test_search_prompt_strict_vs_hybrid(self):
        strict_p = build_search_prompt("SQLi là gì?", "Context A", mode="STRICT_RAG")
        self.assertIn("STRICT RAG MODE", strict_p)
        self.assertIn("Context A", strict_p)

        hybrid_p = build_search_prompt("SQLi là gì?", "Context B", mode="HYBRID")
        self.assertIn("HYBRID MODE", hybrid_p)
        self.assertIn("Context B", hybrid_p)

    def test_compare_prompt(self):
        p = build_compare_prompt("So sánh XSS và CSRF", "Context Chunks", mode="STRICT_RAG")
        self.assertIn("Bảng so sánh tổng hợp (Markdown Table)", p)
        self.assertIn("XSS và CSRF", p)

    def test_synthesis_prompt(self):
        p = build_synthesis_prompt("Tổng hợp bảo mật OWASP", "Context Chunks", mode="HYBRID")
        self.assertIn("Tóm tắt điều hành", p)
        self.assertIn("HYBRID MODE", p)

    def test_build_synthesis_messages(self):
        messages = build_synthesis_messages(
            query="Kiểm tra phân quyền",
            llm_context="Context Data",
            intent="COMPARE",
            evidence_status="PARTIAL",
            conversation_history=[{"role": "user", "content": "Câu hỏi trước"}],
        )
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "Câu hỏi trước")
        self.assertEqual(messages[2]["role"], "user")
        self.assertIn("HYBRID MODE", messages[2]["content"])


class TestResponseSynthesizer(unittest.TestCase):
    def setUp(self):
        self.synthesizer = ResponseSynthesizer(llm_client=DefaultDeterministicLLM())

    def test_strict_rag_synthesis(self):
        retrieval_output = {
            "status": "ok",
            "evidence_status": "FULL",
            "intent": "SEARCH",
            "total_evidence": 3,
            "evidence_list": [
                {"chunk_id": "c1", "text_content": "Content 1"},
                {"chunk_id": "c2", "text_content": "Content 2"},
            ],
            "llm_context": "--- Evidence [1] ---\nContent 1\n\n--- Evidence [2] ---\nContent 2",
        }
        draft = self.synthesizer.synthesize(
            query="SQL Injection là gì?",
            retrieval_output=retrieval_output,
        )
        self.assertFalse(draft.is_abstained)
        self.assertEqual(draft.evidence_status, "FULL")
        self.assertEqual(draft.generation_mode, "STRICT_RAG")
        self.assertIn("[1]", draft.cited_evidence_ids)
        self.assertIn("[2]", draft.cited_evidence_ids)
        self.assertGreaterEqual(draft.generation_metadata["latency_s"], 0)

    def test_hybrid_mode_synthesis(self):
        retrieval_output = {
            "status": "ok",
            "evidence_status": "PARTIAL",
            "intent": "SYNTHESIS",
            "total_evidence": 1,
            "evidence_list": [{"chunk_id": "c1", "text_content": "Content 1"}],
            "llm_context": "--- Evidence [1] ---\nContent 1",
        }
        draft = self.synthesizer.synthesize(
            query="Tổng quan và khắc phục SSRF",
            retrieval_output=retrieval_output,
        )
        self.assertFalse(draft.is_abstained)
        self.assertEqual(draft.evidence_status, "PARTIAL")
        self.assertEqual(draft.generation_mode, "HYBRID")
        self.assertIn("General Knowledge Advisory", draft.draft_answer)

    def test_abstention_none_evidence(self):
        retrieval_output = {
            "status": "ok",
            "evidence_status": "NONE",
            "intent": "SEARCH",
            "total_evidence": 0,
            "evidence_list": [],
            "llm_context": "No relevant data was found in the knowledge base",
        }
        draft = self.synthesizer.synthesize(
            query="Câu hỏi không có trong sách",
            retrieval_output=retrieval_output,
        )
        self.assertTrue(draft.is_abstained)
        self.assertEqual(draft.abstention_reason, "insufficient_evidence")
        self.assertEqual(draft.evidence_status, "NONE")
        self.assertEqual(draft.generation_mode, "ABSTENTION")

    def test_clarification_flow(self):
        retrieval_output = {
            "status": "clarification_needed",
            "evidence_status": "NONE",
            "intent": "UNKNOWN",
            "total_evidence": 0,
            "evidence_list": [],
            "llm_context": "Bạn muốn tìm hiểu lỗ hổng nào cụ thể?",
        }
        draft = self.synthesizer.synthesize(
            query="các lỗi nguy hiểm",
            retrieval_output=retrieval_output,
        )
        self.assertTrue(draft.is_abstained)
        self.assertEqual(draft.abstention_reason, "clarification_needed")
        self.assertEqual(draft.draft_answer, "Bạn muốn tìm hiểu lỗ hổng nào cụ thể?")


class TestRAGOrchestrator(unittest.TestCase):
    def setUp(self):
        self.orchestrator = RAGOrchestrator(
            synthesizer=ResponseSynthesizer(llm_client=DefaultDeterministicLLM()),
            traceable_formatter=DefaultTraceableFormatter(),
        )

    def test_run_from_retrieval_strict_rag(self):
        retrieval_output = {
            "status": "ok",
            "evidence_status": "FULL",
            "intent": "SEARCH",
            "total_evidence": 2,
            "evidence_list": [
                {
                    "chunk_id": "chunk_01",
                    "text_content": "Prepared statements prevent SQLi.",
                    "metadata": {
                        "title": "OWASP SQL Injection Prevention",
                        "heading_path": ["Remediation", "Defenses"],
                        "location_summary": "Page 3",
                    },
                    "provenance": {
                        "canonical_title": "OWASP SQL Injection Cheat Sheet",
                        "document_id": "doc_sqli_01",
                    },
                },
                {
                    "chunk_id": "chunk_02",
                    "text_content": "Input validation is secondary defense.",
                    "metadata": {
                        "title": "OWASP SQL Injection Prevention",
                        "heading_path": ["Validation"],
                        "location_summary": "Page 5",
                    },
                    "provenance": {
                        "canonical_title": "OWASP SQL Injection Cheat Sheet",
                        "document_id": "doc_sqli_01",
                    },
                },
            ],
            "llm_context": "--- Evidence [1] ---\nPrepared statements\n\n--- Evidence [2] ---\nValidation",
        }

        envelope = self.orchestrator.run_from_retrieval(
            query="Làm sao để chống SQLi?",
            retrieval_output=retrieval_output,
        )

        self.assertEqual(envelope.status, "ok")
        self.assertEqual(envelope.intent, "SEARCH")
        self.assertIsNotNone(envelope.raw_draft)
        self.assertEqual(envelope.raw_draft.evidence_status, "FULL")
        self.assertEqual(envelope.raw_draft.generation_mode, "STRICT_RAG")
        self.assertIn("Danh mục tài liệu tham chiếu", envelope.final_answer_markdown)
        self.assertEqual(len(envelope.citations), 2)
        self.assertEqual(envelope.citations[0].tag, "[1]")
        self.assertEqual(envelope.citations[1].tag, "[2]")

        # Test JSON serialization
        json_output = envelope.to_json()
        data = json.loads(json_output)
        self.assertEqual(data["schema_version"], "1.0")
        self.assertEqual(data["status"], "ok")

    def test_run_from_retrieval_hybrid_mode(self):
        retrieval_output = {
            "status": "ok",
            "evidence_status": "PARTIAL",
            "intent": "COMPARE",
            "total_evidence": 1,
            "evidence_list": [
                {
                    "chunk_id": "chunk_xss_01",
                    "text_content": "XSS occurs when malicious scripts are injected.",
                    "metadata": {"title": "XSS Cheat Sheet", "heading_path": ["Overview"]},
                }
            ],
            "llm_context": "--- Evidence [1] ---\nXSS overview",
        }

        envelope = self.orchestrator.run_from_retrieval(
            query="So sánh XSS và CSRF",
            retrieval_output=retrieval_output,
        )

        self.assertEqual(envelope.status, "ok")
        self.assertEqual(envelope.metadata["generation_mode"], "HYBRID")
        self.assertEqual(envelope.metadata["evidence_status"], "PARTIAL")
        self.assertIn("General Knowledge Advisory", envelope.final_answer_markdown)


if __name__ == "__main__":
    unittest.main()
