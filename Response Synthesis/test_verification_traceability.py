"""
Unit and Integration Tests for Stage 4: Evidence Verification & Traceability.
Contract v1.0 (VerifierProtocol, FormatterProtocol).
"""

import json
import sys
import unittest
from pathlib import Path

STAGE_4_DIR = Path(__file__).resolve().parent
if str(STAGE_4_DIR) not in sys.path:
    sys.path.insert(0, str(STAGE_4_DIR))

from schema_response import ClaimStatus, SynthesizedDraftSchema, VerificationStatus
from rag_orchestrator import FormatterProtocol, RAGOrchestrator, VerifierProtocol
from synthesizer import DefaultDeterministicLLM, ResponseSynthesizer
from traceable_formatter import (
    TraceableFormatter,
    index_evidence_by_tag,
    summarize_location,
)
from verifier import EvidenceVerifier, extract_claims, lexical_support_score


def make_evidence(tag, chunk_id, text, sources=None, **meta):
    return {
        "evidence_id": tag,
        "chunk_id": chunk_id,
        "text_content": text,
        "score": 0.9,
        "metadata": meta,
        "provenance": {
            "chunk_id": chunk_id,
            "document_id": meta.get("document_id", "doc_1"),
            "version_id": "v1.0",
            "file_name": meta.get("file_name", ""),
            "file_format": meta.get("file_format", ""),
            "canonical_title": meta.get("title", ""),
            "sources": sources or [],
        },
    }


SQLI_EVIDENCE = [
    make_evidence(
        "[1]", "c1",
        "Truy vấn tham số hóa ngăn chặn tấn công SQL Injection hiệu quả. "
        "Kẻ tấn công chèn mã SQL độc hại vào dữ liệu đầu vào.",
        title="OWASP SQL Injection Cheat Sheet", file_format="PDF", file_name="sqli.pdf",
    ),
    make_evidence(
        "[2]", "c2",
        "Kiểm tra dữ liệu đầu vào là lớp phòng thủ bổ sung. "
        "Tài khoản cơ sở dữ liệu nên được cấp quyền tối thiểu.",
        title="OWASP SQL Injection Cheat Sheet", file_format="PDF", file_name="sqli.pdf",
    ),
]


def make_draft(answer, mode="STRICT_RAG", **kwargs):
    return SynthesizedDraftSchema(draft_answer=answer, intent="SEARCH", generation_mode=mode, **kwargs)


class TestClaimExtraction(unittest.TestCase):
    def test_skips_headings_notes_and_table_header(self):
        answer = (
            "### Kết quả phân tích\n\n"
            "Truy vấn tham số hóa ngăn chặn SQL Injection [1]. Kẻ tấn công chèn mã độc vào đầu vào. [2]\n\n"
            "(Lưu ý: đây là ghi chú hệ thống)\n"
            "| Tiêu chí | XSS | CSRF |\n"
            "|---|---|---|\n"
            "| Mục tiêu tấn công | trình duyệt người dùng | phiên đăng nhập [1] |\n"
        )
        claims = extract_claims(answer)
        self.assertEqual(len(claims), 3)
        self.assertEqual(claims[0].tags, ["[1]"])
        # Thẻ đứng riêng sau dấu chấm được gắn về câu trước.
        self.assertEqual(claims[1].tags, ["[2]"])
        self.assertNotIn("[", claims[1].text)
        self.assertIn("trình duyệt", claims[2].text)

    def test_marks_advisory_section(self):
        answer = (
            "Nội dung có tài liệu chứng minh rõ ràng [1].\n\n"
            "### 💡 Khuyến nghị (General Knowledge Advisory)\n"
            "- Triển khai phòng thủ theo chiều sâu kết hợp kiểm thử định kỳ.\n"
        )
        claims = extract_claims(answer)
        self.assertEqual([c.is_advisory for c in claims], [False, True])

    def test_lexical_score_bounds(self):
        self.assertEqual(lexical_support_score("", "anything"), 0.0)
        self.assertAlmostEqual(lexical_support_score("bảo mật dữ liệu", "bảo mật dữ liệu"), 1.0)
        self.assertEqual(lexical_support_score("bảo mật dữ liệu", "hoàn toàn khác biệt"), 0.0)


class TestEvidenceVerifier(unittest.TestCase):
    def setUp(self):
        self.verifier = EvidenceVerifier()

    def test_implements_protocol(self):
        self.assertIsInstance(self.verifier, VerifierProtocol)

    def test_abstention_is_skipped(self):
        draft = make_draft("Không đủ bằng chứng.", is_abstained=True, abstention_reason="insufficient_evidence")
        report = self.verifier.verify(draft, [])
        self.assertEqual(report.status, VerificationStatus.SKIPPED)
        self.assertEqual(report.total_claims, 0)

    def test_supported_claims_pass(self):
        draft = make_draft(
            "Truy vấn tham số hóa ngăn chặn tấn công SQL Injection hiệu quả [1]. "
            "Tài khoản cơ sở dữ liệu nên được cấp quyền tối thiểu [2]."
        )
        report = self.verifier.verify(draft, SQLI_EVIDENCE)
        self.assertEqual(report.status, VerificationStatus.PASSED)
        self.assertEqual(report.supported_claims, 2)
        self.assertEqual(report.faithfulness_score, 1.0)
        self.assertTrue(all(c.matched_snippet for c in report.claims_detail))

    def test_claim_checked_only_against_cited_source(self):
        # Nội dung đúng với [2] nhưng lại trích [1] -> không truy vết được.
        draft = make_draft("Tài khoản cơ sở dữ liệu nên được cấp quyền tối thiểu [1].")
        report = self.verifier.verify(draft, SQLI_EVIDENCE)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.UNVERIFIED)
        self.assertEqual(report.status, VerificationStatus.FAILED)

    def test_hallucinated_claim_fails(self):
        draft = make_draft("Tường lửa ứng dụng web tự động vá mọi lỗ hổng zero-day [1].")
        report = self.verifier.verify(draft, SQLI_EVIDENCE)
        self.assertEqual(report.unverified_claims, 1)
        self.assertEqual(report.status, VerificationStatus.FAILED)

    def test_negation_mismatch_is_contradicted(self):
        draft = make_draft("Truy vấn tham số hóa không ngăn chặn tấn công SQL Injection hiệu quả [1].")
        report = self.verifier.verify(draft, SQLI_EVIDENCE)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.CONTRADICTED)
        self.assertEqual(report.contradicted_claims, 1)

    def test_invalid_citation_tag(self):
        draft = make_draft("Truy vấn tham số hóa ngăn chặn tấn công SQL Injection hiệu quả [7].")
        report = self.verifier.verify(draft, SQLI_EVIDENCE)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.UNVERIFIED)
        self.assertTrue(any("[7]" in w for w in report.warnings))

    def test_uncited_but_grounded_claim_warns(self):
        draft = make_draft("Kẻ tấn công chèn mã SQL độc hại vào dữ liệu đầu vào.")
        report = self.verifier.verify(draft, SQLI_EVIDENCE)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.SUPPORTED)
        self.assertIsNone(report.claims_detail[0].cited_tag)
        self.assertTrue(any("thiếu thẻ trích dẫn" in w for w in report.warnings))

    def test_hybrid_advisory_excluded_from_score(self):
        answer = (
            "Truy vấn tham số hóa ngăn chặn tấn công SQL Injection hiệu quả [1].\n\n"
            "### 💡 Khuyến nghị & Bổ sung từ kiến thức chung (General Knowledge Advisory)\n"
            "- Thiết lập cơ chế giám sát và ghi log tập trung để phát hiện bất thường.\n"
        )
        report = self.verifier.verify(make_draft(answer, mode="HYBRID"), SQLI_EVIDENCE)
        self.assertEqual(report.total_claims, 2)
        self.assertEqual(report.faithfulness_score, 1.0)
        self.assertEqual(report.status, VerificationStatus.PASSED)

        # Cùng nội dung ở STRICT_RAG thì phần advisory phải bị tính là chưa chứng thực.
        strict = self.verifier.verify(make_draft(answer, mode="STRICT_RAG"), SQLI_EVIDENCE)
        self.assertEqual(strict.faithfulness_score, 0.5)
        self.assertEqual(strict.status, VerificationStatus.WARNING)

    def test_custom_similarity_fn(self):
        verifier = EvidenceVerifier(similarity_fn=lambda claim, passage: 0.9)
        draft = make_draft("Prepared statements are the primary defense [1].")
        report = verifier.verify(draft, SQLI_EVIDENCE)
        self.assertEqual(report.status, VerificationStatus.PASSED)

    def test_report_serializes(self):
        draft = make_draft("Truy vấn tham số hóa ngăn chặn tấn công SQL Injection hiệu quả [1].")
        data = self.verifier.verify(draft, SQLI_EVIDENCE).to_dict()
        json.dumps(data, ensure_ascii=False)
        self.assertEqual(data["status"], "PASSED")
        self.assertEqual(data["claims_detail"][0]["status"], "SUPPORTED")


class TestTraceableFormatter(unittest.TestCase):
    def setUp(self):
        self.formatter = TraceableFormatter()

    def test_implements_protocol(self):
        self.assertIsInstance(self.formatter, FormatterProtocol)

    def test_location_summaries_per_format(self):
        pdf = [{"locator": {"page_number": 3, "bbox": [0, 0, 1, 1]}}, {"locator": {"page_number": 4}}]
        md = [{"locator": {"start_line": 12, "end_line": 18}}, {"locator": {"start_line": 19, "end_line": 25}}]
        csv = [{"locator": {"table_idx": 0, "start_row": 4, "end_row": 6}}]
        js = [{"locator": {"json_path": "$.weaknesses[12]"}}]
        self.assertEqual(summarize_location(pdf), "Page 3-4")
        self.assertEqual(summarize_location(md), "Lines 12-25")
        self.assertEqual(summarize_location(csv), "Rows 4-6")
        self.assertEqual(summarize_location(js), "JSON $.weaknesses[12]")
        self.assertEqual(summarize_location([], fallback="Chunk c1"), "Chunk c1")

    def test_tag_mapping_uses_evidence_id(self):
        evidence = [make_evidence("[2]", "c2", "B"), make_evidence("[1]", "c1", "A")]
        indexed = index_evidence_by_tag(evidence)
        self.assertEqual(indexed["[1]"]["chunk_id"], "c1")
        self.assertEqual(indexed["[2]"]["chunk_id"], "c2")

    def test_builds_citations_from_provenance(self):
        evidence = [
            make_evidence(
                "[1]", "doc_a_v1.0_c00001", "Prepared statements prevent SQL injection.",
                sources=[{
                    "normalized_block_id": "nb_1", "source_order": 0, "start_offset": 0, "end_offset": 42,
                    "locator": {"page_number": 14}, "heading_path": ["Injection", "Remediation"],
                }],
                title="OWASP Top 10", file_name="owasp.pdf", file_format="pdf",
            )
        ]
        draft = make_draft("Dùng prepared statements [1].", cited_evidence_ids=["[1]"])
        markdown, citations = self.formatter.format_citations(draft, evidence)

        self.assertEqual(len(citations), 1)
        c = citations[0]
        self.assertEqual(c.tag, "[1]")
        self.assertEqual(c.heading_path, ["Injection", "Remediation"])
        self.assertEqual(c.file_format, "PDF")
        self.assertEqual(c.location_summary, "Page 14")
        self.assertEqual(c.locator["spans"][0]["page_number"], 14)
        self.assertIn("Danh mục tài liệu tham chiếu", markdown)
        self.assertIn("*OWASP Top 10* › Injection > Remediation — `owasp.pdf` · Page 14", markdown)

    def test_only_cited_tags_are_listed(self):
        draft = make_draft("Chỉ trích dẫn nguồn thứ hai [2].")
        _, citations = self.formatter.format_citations(draft, SQLI_EVIDENCE)
        self.assertEqual([c.tag for c in citations], ["[2]"])

    def test_unknown_tag_is_flagged(self):
        draft = make_draft("Trích dẫn sai [9].")
        markdown, citations = self.formatter.format_citations(draft, SQLI_EVIDENCE)
        self.assertEqual(citations, [])
        self.assertIn("**[9]** ⚠️", markdown)

    def test_abstention_passthrough(self):
        draft = make_draft("Cần làm rõ câu hỏi.", is_abstained=True)
        markdown, citations = self.formatter.format_citations(draft, SQLI_EVIDENCE)
        self.assertEqual(markdown, "Cần làm rõ câu hỏi.")
        self.assertEqual(citations, [])


class TestOrchestratorIntegration(unittest.TestCase):
    def test_envelope_with_real_verifier_and_formatter(self):
        orchestrator = RAGOrchestrator(
            synthesizer=ResponseSynthesizer(llm_client=DefaultDeterministicLLM()),
            verifier=EvidenceVerifier(),
            traceable_formatter=TraceableFormatter(),
        )
        retrieval_output = {
            "status": "ok",
            "evidence_status": "FULL",
            "intent": "SEARCH",
            "total_evidence": 2,
            "evidence_list": SQLI_EVIDENCE,
            "llm_context": "--- Evidence [1] ---\nA\n\n--- Evidence [2] ---\nB",
        }
        envelope = orchestrator.run_from_retrieval("Chống SQLi thế nào?", retrieval_output)

        self.assertEqual(envelope.status, "ok")
        self.assertEqual([c.tag for c in envelope.citations], ["[1]", "[2]"])
        self.assertGreater(envelope.verification.total_claims, 0)
        self.assertIn(
            envelope.verification.status,
            {VerificationStatus.PASSED, VerificationStatus.WARNING, VerificationStatus.FAILED},
        )
        json.loads(envelope.to_json())


if __name__ == "__main__":
    unittest.main()
