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
        text = "SQL Injection occurs due to [1] and [2]. Remediation includes [2] and [3]. Invalid [abc]."
        tags = extract_citation_tags(text)
        self.assertEqual(tags, ["[1]", "[2]", "[3]"])

    def test_extract_empty_tags(self):
        self.assertEqual(extract_citation_tags("No tags here"), [])
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
        self.assertIn("Bảng so sánh (Markdown Table)", p)
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
        from verifier import EvidenceVerifier
        # Happy-path tests: a verifier that accepts every claim, so the gate passes.
        self.orchestrator = RAGOrchestrator(
            synthesizer=ResponseSynthesizer(llm_client=DefaultDeterministicLLM()),
            verifier=EvidenceVerifier(similarity_fn=lambda claim, passage: 0.9),
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


class TestPhase3APromptEngineering(unittest.TestCase):
    """
    Phase 3A regression tests: R1 citation limits, R2 premise/partial-evidence
    handling, R3 flexible COMPARE formatting and claim-adjacent SYNTHESIS citations.
    These tests verify prompt construction invariants only — they do not assert
    real LLM compliance.
    """

    # ------------------------------------------------------------------ #
    # R1 — consistent citation limits across SEARCH / COMPARE / SYNTHESIS #
    # ------------------------------------------------------------------ #

    def _ctx(self, n: int) -> str:
        """Build a minimal llm_context with n evidence headers."""
        return "\n\n".join(
            f"--- Evidence [{i}] ---\nContent {i}" for i in range(1, n + 1)
        )

    def test_r1_search_citation_bound_present(self):
        ctx = self._ctx(3)
        p = build_search_prompt("What is XSS?", ctx, mode="STRICT_RAG", lang="en")
        self.assertIn("[3]", p)
        self.assertIn("[4]", p)   # the "NEVER cite [4] or higher" clause
        # Must not instruct to use [4] as a valid tag
        self.assertIn("NEVER cite [4]", p)

    def test_r1_compare_citation_bound_present(self):
        ctx = self._ctx(2)
        p = build_compare_prompt("Compare XSS and CSRF", ctx, mode="STRICT_RAG", lang="en")
        self.assertIn("[2]", p)
        self.assertIn("NEVER cite [3]", p)

    def test_r1_synthesis_citation_bound_present(self):
        ctx = self._ctx(4)
        p = build_synthesis_prompt("Synthesize OWASP top risks", ctx, mode="STRICT_RAG", lang="en")
        self.assertIn("[4]", p)
        self.assertIn("NEVER cite [5]", p)

    def test_r1_compare_vi_citation_bound(self):
        ctx = self._ctx(2)
        p = build_compare_prompt("So sánh XSS và CSRF", ctx, mode="STRICT_RAG", lang="vi")
        self.assertIn("[2]", p)
        self.assertIn("KHÔNG trích dẫn [3]", p)

    def test_r1_synthesis_vi_citation_bound(self):
        ctx = self._ctx(3)
        p = build_synthesis_prompt("Tổng hợp OWASP", ctx, mode="STRICT_RAG", lang="vi")
        self.assertIn("[3]", p)
        self.assertIn("KHÔNG trích dẫn [4]", p)

    def test_r1_empty_evidence_no_bound_clause(self):
        """When context has no evidence headers, no citation-bound clause is injected."""
        p_compare = build_compare_prompt("Compare A and B", "", mode="STRICT_RAG", lang="en")
        p_synthesis = build_synthesis_prompt("Synthesize A", "", mode="STRICT_RAG", lang="en")
        self.assertNotIn("NEVER cite", p_compare)
        self.assertNotIn("NEVER cite", p_synthesis)

    def test_r1_search_empty_evidence_no_bound_clause(self):
        p = build_search_prompt("What is XSS?", "", mode="STRICT_RAG", lang="en")
        self.assertNotIn("NEVER cite", p)

    def test_r1_no_invented_index_instruction(self):
        """
        Prompts must never instruct the model to use [i] as a valid citation tag.
        Note: [i] legitimately appears in the STRICT guideline text inside a
        prohibition clause (e.g. "NEVER use the literal '[i]'"). The correct
        invariant is that the evidence-bounds clause is present (R1) and that the
        prohibition of [i] is stated, not that [i] is absent from the full string.
        """
        ctx = self._ctx(2)
        for builder in (build_search_prompt, build_compare_prompt, build_synthesis_prompt):
            p = builder("query", ctx, mode="STRICT_RAG", lang="en")
            # R1: a numeric upper-bound clause must be present
            self.assertIn("NEVER cite [3]", p)
            # The prohibition on [i] must appear (inherited from STRICT guideline)
            self.assertIn("NEVER use the literal", p)

    # ------------------------------------------------- #
    # R2 — false premise & partial evidence handling    #
    # ------------------------------------------------- #

    def test_r2_false_premise_instruction_in_search_en(self):
        ctx = self._ctx(2)
        p = build_search_prompt("Is X true?", ctx, mode="STRICT_RAG", lang="en")
        self.assertIn("does not establish the premise", p)
        self.assertIn("abstain", p)

    def test_r2_false_premise_instruction_in_compare_en(self):
        ctx = self._ctx(2)
        p = build_compare_prompt("Compare X and Y", ctx, mode="STRICT_RAG", lang="en")
        self.assertIn("does not establish the premise", p)

    def test_r2_false_premise_instruction_in_synthesis_en(self):
        ctx = self._ctx(2)
        p = build_synthesis_prompt("Synthesize X", ctx, mode="STRICT_RAG", lang="en")
        self.assertIn("does not establish the premise", p)

    def test_r2_partial_evidence_instruction_en(self):
        ctx = self._ctx(1)
        p = build_search_prompt("Query?", ctx, mode="STRICT_RAG", lang="en")
        self.assertIn("cannot be established from the provided evidence", p)

    def test_r2_no_fabrication_instruction_en(self):
        ctx = self._ctx(2)
        for builder in (build_search_prompt, build_compare_prompt, build_synthesis_prompt):
            p = builder("query", ctx, mode="STRICT_RAG", lang="en")
            self.assertIn("Do NOT fabricate", p)

    def test_r2_false_premise_instruction_vi(self):
        ctx = self._ctx(2)
        p = build_search_prompt("X có đúng không?", ctx, mode="STRICT_RAG", lang="vi")
        self.assertIn("tiền đề", p)
        self.assertIn("từ chối", p)

    def test_r2_no_fabrication_instruction_vi(self):
        ctx = self._ctx(2)
        for builder in (build_search_prompt, build_compare_prompt, build_synthesis_prompt):
            p = builder("query", ctx, mode="STRICT_RAG", lang="vi")
            self.assertIn("KHÔNG bịa", p)

    # -------------------------------------------------- #
    # R3 — flexible COMPARE table, claim-adjacent cites  #
    # -------------------------------------------------- #

    def test_r3_compare_no_fixed_four_columns_en(self):
        ctx = self._ctx(2)
        p = build_compare_prompt("Compare XSS and CSRF", ctx, mode="STRICT_RAG", lang="en")
        # R3: rigid four-column mandate must be gone
        self.assertNotIn(
            "Definition, Affected Layer / Scope of Impact, Risk Severity, Primary Defense Method",
            p,
        )

    def test_r3_compare_flexible_table_instruction_en(self):
        ctx = self._ctx(2)
        p = build_compare_prompt("Compare XSS and CSRF", ctx, mode="STRICT_RAG", lang="en")
        self.assertIn("reflect the actual comparison criteria", p)

    def test_r3_compare_missing_dimension_instruction_en(self):
        ctx = self._ctx(1)
        p = build_compare_prompt("Compare A and B", ctx, mode="STRICT_RAG", lang="en")
        # Clarified wording: missing dimensions get a cell stating no evidence,
        # rather than being dropped entirely.
        self.assertIn("no evidence is available for it", p)
        self.assertNotIn("Only include rows and columns for dimensions supported", p)

    def test_r3_compare_no_fabricated_numbers_en(self):
        ctx = self._ctx(2)
        p = build_compare_prompt("Compare A and B", ctx, mode="STRICT_RAG", lang="en")
        self.assertIn("NOT invent numerical figures", p)

    def test_r3_compare_no_fixed_four_columns_vi(self):
        ctx = self._ctx(2)
        p = build_compare_prompt("So sánh XSS và CSRF", ctx, mode="STRICT_RAG", lang="vi")
        self.assertNotIn("Định nghĩa, Vị trí tác động, Mức độ rủi ro, Phương pháp phòng thủ chính", p)

    def test_r3_synthesis_claim_adjacent_citation_en(self):
        ctx = self._ctx(3)
        p = build_synthesis_prompt("Synthesize OWASP findings", ctx, mode="STRICT_RAG", lang="en")
        # Instruction must tell model to place citation immediately after the claim
        self.assertIn("IMMEDIATELY AFTER", p)

    def test_r3_synthesis_no_end_of_paragraph_grouping_en(self):
        ctx = self._ctx(2)
        p = build_synthesis_prompt("Synthesize", ctx, mode="STRICT_RAG", lang="en")
        self.assertIn("do NOT group all citations at the end of a paragraph", p)

    def test_r3_synthesis_unverified_label_instruction_en(self):
        ctx = self._ctx(2)
        p = build_synthesis_prompt("Synthesize", ctx, mode="STRICT_RAG", lang="en")
        self.assertIn("unverified or unknown", p)

    def test_r3_synthesis_claim_adjacent_citation_vi(self):
        ctx = self._ctx(2)
        p = build_synthesis_prompt("Tổng hợp OWASP", ctx, mode="STRICT_RAG", lang="vi")
        self.assertIn("NGAY SAU", p)

    def test_r3_synthesis_unverified_label_instruction_vi(self):
        ctx = self._ctx(2)
        p = build_synthesis_prompt("Tổng hợp", ctx, mode="STRICT_RAG", lang="vi")
        self.assertIn("chưa được xác minh", p)

    # ------------------------------------------------------------------ #
    # Issue 1 — non-contiguous citation IDs                               #
    # ------------------------------------------------------------------ #

    def _ctx_noncontiguous(self, indices: list) -> str:
        """Build llm_context with the given non-contiguous evidence indices."""
        return "\n\n".join(
            f"--- Evidence [{i}] ---\nContent {i}" for i in indices
        )

    def test_issue1_noncontiguous_enumerated_en(self):
        """Non-contiguous headers must enumerate allowed tags, not use a range."""
        ctx = self._ctx_noncontiguous([1, 3])
        p = build_search_prompt("Query?", ctx, mode="STRICT_RAG", lang="en")
        self.assertIn("[1]", p)
        self.assertIn("[3]", p)
        # Must NOT imply [2] is allowed via a "through [3]" range clause
        self.assertNotIn("through [3]", p)
        self.assertIn("ONLY use the following citation tags", p)

    def test_issue1_noncontiguous_compare_en(self):
        ctx = self._ctx_noncontiguous([1, 3])
        p = build_compare_prompt("Compare A and B", ctx, mode="STRICT_RAG", lang="en")
        self.assertNotIn("through [3]", p)
        self.assertIn("ONLY use the following citation tags", p)

    def test_issue1_noncontiguous_synthesis_en(self):
        ctx = self._ctx_noncontiguous([2, 4])
        p = build_synthesis_prompt("Synthesize", ctx, mode="STRICT_RAG", lang="en")
        self.assertNotIn("through [4]", p)
        self.assertIn("ONLY use the following citation tags", p)

    def test_issue1_contiguous_uses_range_form_en(self):
        """Contiguous [1..N] must keep the compact 'through [N]' form."""
        ctx = self._ctx(3)
        p = build_search_prompt("Query?", ctx, mode="STRICT_RAG", lang="en")
        self.assertIn("through [3]", p)
        self.assertNotIn("ONLY use the following citation tags", p)

    def test_issue1_noncontiguous_vi(self):
        ctx = self._ctx_noncontiguous([1, 3])
        p = build_compare_prompt("So sánh A và B", ctx, mode="STRICT_RAG", lang="vi")
        self.assertNotIn("đến [3]", p)
        self.assertIn("CHỈ dùng các thẻ trích dẫn sau", p)

    def test_issue1_duplicate_and_out_of_order_headers(self):
        """Duplicate and out-of-order headers must be deduplicated and sorted."""
        ctx = "--- Evidence [3] ---\nC\n\n--- Evidence [1] ---\nA\n\n--- Evidence [3] ---\nC2"
        p = build_search_prompt("Query?", ctx, mode="STRICT_RAG", lang="en")
        # Indices are {1, 3} — non-contiguous, so enumerate form applies
        self.assertIn("ONLY use the following citation tags", p)
        self.assertNotIn("through [3]", p)

    def test_issue1_single_evidence_contiguous_range(self):
        """A single evidence [1] is contiguous [1..1]; compact form applies."""
        ctx = "--- Evidence [1] ---\nA"
        p = build_search_prompt("Query?", ctx, mode="STRICT_RAG", lang="en")
        self.assertIn("through [1]", p)

    # ------------------------------------------------------------------ #
    # Issue 2 — HYBRID advisory compatibility                             #
    # ------------------------------------------------------------------ #

    def test_issue2_hybrid_premise_rules_allow_advisory(self):
        """In HYBRID mode the premise rules must permit the advisory section."""
        ctx = self._ctx(1)
        p = build_search_prompt("Query?", ctx, mode="HYBRID", lang="en")
        self.assertIn("HYBRID MODE", p)
        # The advisory escape hatch must be present in the premise rules
        self.assertIn("General Knowledge Advisory", p)
        # Must not carry a blanket 'answer only using evidence' ban in HYBRID
        self.assertNotIn("Answer only using information supported by the provided evidence", p)

    def test_issue2_strict_rag_no_advisory_escape(self):
        """In STRICT_RAG mode the premise rules must NOT mention advisory content."""
        ctx = self._ctx(2)
        p = build_search_prompt("Query?", ctx, mode="STRICT_RAG", lang="en")
        # Strict mode: supplementary content must not be permitted
        self.assertIn("STRICT RAG MODE", p)
        self.assertIn("do not mention it at all", p)

    def test_issue2_hybrid_no_citation_on_advisory_en(self):
        """HYBRID mode must prohibit citation tags on advisory content."""
        ctx = self._ctx(1)
        for builder in (build_search_prompt, build_compare_prompt, build_synthesis_prompt):
            p = builder("query", ctx, mode="HYBRID", lang="en")
            self.assertIn("do NOT attach any citation tags", p)

    def test_issue2_strict_rag_premise_rules_vi(self):
        ctx = self._ctx(2)
        p = build_synthesis_prompt("Tổng hợp", ctx, mode="STRICT_RAG", lang="vi")
        self.assertIn("không được đề cập đến thông tin đó", p)

    def test_issue2_hybrid_premise_rules_vi(self):
        ctx = self._ctx(1)
        p = build_synthesis_prompt("Tổng hợp", ctx, mode="HYBRID", lang="vi")
        self.assertIn("General Knowledge Advisory", p)
        self.assertIn("tuyệt đối không gắn thẻ trích dẫn", p)

    # ------------------------------------------------------------------ #
    # Issue 3 — COMPARE missing-dimension consistency                     #
    # ------------------------------------------------------------------ #

    def test_issue3_compare_missing_dim_keep_row_en(self):
        """COMPARE must instruct to keep the row/column and label it, not drop it."""
        ctx = self._ctx(1)
        p = build_compare_prompt("Compare A and B", ctx, mode="STRICT_RAG", lang="en")
        self.assertIn("keep the row/column", p)
        self.assertIn("no evidence is available for it", p)

    def test_issue3_compare_no_exclusion_clause_en(self):
        """The old 'Only include rows/columns for supported dimensions' must be gone."""
        ctx = self._ctx(2)
        p = build_compare_prompt("Compare A and B", ctx, mode="STRICT_RAG", lang="en")
        self.assertNotIn("Only include rows and columns for dimensions supported", p)

    def test_issue3_compare_missing_dim_vi(self):
        ctx = self._ctx(1)
        p = build_compare_prompt("So sánh A và B", ctx, mode="STRICT_RAG", lang="vi")
        self.assertIn("không có bằng chứng", p)
        self.assertNotIn("Chỉ đưa vào các chiều thực sự được hỗ trợ", p)


if __name__ == "__main__":
    unittest.main()
