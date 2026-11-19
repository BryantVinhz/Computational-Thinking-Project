"""
Unit and Integration Tests for Stage 4: Evidence Verification & Traceability.
Contract v1.0 (VerifierProtocol, FormatterProtocol).
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

STAGE_4_DIR = Path(__file__).resolve().parent
if str(STAGE_4_DIR) not in sys.path:
    sys.path.insert(0, str(STAGE_4_DIR))

from schema_response import ClaimStatus, SynthesizedDraftSchema, VerificationStatus
from rag_orchestrator import DefaultTraceableFormatter, FormatterProtocol, RAGOrchestrator, VerifierProtocol
from synthesizer import DefaultDeterministicLLM, ResponseSynthesizer
from traceable_formatter import (
    TraceableFormatter,
    index_evidence_by_tag,
    summarize_location,
)
from verifier import (
    POLARITY_NEGATIVE,
    POLARITY_POSITIVE,
    POLARITY_UNCERTAIN,
    EvidenceVerifier,
    MultilingualSemanticSimilarity,
    _has_opposing_polarity,
    _negation_count,
    _numeric_relation,
    _polarity,
    extract_claims,
    lexical_support_score,
)


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
            "### Khuyến nghị (General Knowledge Advisory)\n"
            "- Triển khai phòng thủ theo chiều sâu kết hợp kiểm thử định kỳ.\n"
        )
        claims = extract_claims(answer)
        self.assertEqual([c.is_advisory for c in claims], [False, True])

    def test_lexical_score_bounds(self):
        self.assertEqual(lexical_support_score("", "anything"), 0.0)
        self.assertAlmostEqual(lexical_support_score("bảo mật dữ liệu", "bảo mật dữ liệu"), 1.0)
        self.assertEqual(lexical_support_score("bảo mật dữ liệu", "hoàn toàn khác biệt"), 0.0)


class TestComparisonTableClaimExtraction(unittest.TestCase):
    """
    R9C.2: a comparison table must not become one claim asserting every cell.

    A row holding separate 2021 and 2025 values is two assertions; merging them lets
    one version's number refute the other. Each data column becomes its own claim,
    carrying the row label and its column header for context.
    """

    TABLE = (
        "| Aspect | Version 2021 | Version 2025 |\n"
        "| --- | --- | --- |\n"
        "| Ranking Position | #3 | #5 (moved down) |\n"
        "| New Categories | No new categories introduced. | "
        "Two new categories added: A01 and A02. |\n"
    )

    def test_comparison_row_splits_per_version_column(self):
        claims = extract_claims(self.TABLE)
        ranking = [c for c in claims if "Ranking Position" in c.text]
        self.assertEqual(len(ranking), 2)
        self.assertTrue(any("#3" in c.text and "2021" in c.text for c in ranking))
        self.assertTrue(any("#5" in c.text and "2025" in c.text for c in ranking))
        # The two editions never share a claim.
        for claim in ranking:
            self.assertFalse("#3" in claim.text and "#5" in claim.text)

    def test_split_claims_keep_row_and_column_context(self):
        claims = extract_claims(self.TABLE)
        first = [c for c in claims if "#3" in c.text][0]
        self.assertIn("Ranking Position", first.text)
        self.assertIn("Version 2021", first.text)

    def test_negation_in_one_cell_does_not_merge_with_other_cell(self):
        # "No new categories" must not be fused with "Two new categories added".
        claims = extract_claims(self.TABLE)
        cells = [c for c in claims if "categories" in c.text.lower()]
        self.assertEqual(len(cells), 2)
        self.assertTrue(any(c.text.lower().startswith("new categories: version 2021") for c in cells))

    def test_citation_tags_survive_cell_split(self):
        table = (
            "| Aspect | Version 2021 | Version 2025 |\n"
            "| --- | --- | --- |\n"
            "| Ranking Position | #3 [1] | #5 [2] |\n"
        )
        claims = extract_claims(table)
        by_tag = {c.tags[0]: c for c in claims if c.tags}
        self.assertEqual(by_tag["[1]"].tags, ["[1]"])
        self.assertIn("#3", by_tag["[1]"].text)
        self.assertNotIn("#5", by_tag["[1]"].text)
        self.assertIn("#5", by_tag["[2]"].text)

    def test_header_only_row_is_still_skipped(self):
        claims = extract_claims(self.TABLE)
        self.assertFalse(any(c.text.startswith("Aspect") for c in claims))


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


class TestNegationPolarity(unittest.TestCase):
    """R7: cross-lingual negation and negation-scope handling."""

    def _status(self, claim, evidence_text, threshold=0.5):
        # Constant similarity isolates polarity logic from lexical scoring.
        verifier = EvidenceVerifier(similarity_fn=lambda c, p: 0.9, support_threshold=threshold)
        evidence = [make_evidence("[1]", "c1", evidence_text)]
        report = verifier.verify(make_draft(claim + " [1]."), evidence)
        return report.claims_detail[0].status

    def test_same_language_positive_vs_negative(self):
        evidence = "Parameterized queries prevent SQL Injection."
        self.assertEqual(
            self._status("Parameterized queries do not prevent SQL Injection", evidence),
            ClaimStatus.CONTRADICTED,
        )
        self.assertEqual(
            self._status("Parameterized queries prevent SQL Injection", evidence),
            ClaimStatus.SUPPORTED,
        )

    def test_vietnamese_claim_vs_english_evidence(self):
        evidence = "Parameterized queries prevent SQL Injection."
        self.assertEqual(
            self._status("Truy vấn tham số hóa không ngăn chặn SQL Injection", evidence),
            ClaimStatus.CONTRADICTED,
        )

    def test_english_claim_vs_vietnamese_evidence(self):
        evidence = "Truy vấn tham số hóa không ngăn chặn SQL Injection."
        self.assertEqual(
            self._status("Parameterized queries prevent SQL Injection", evidence),
            ClaimStatus.CONTRADICTED,
        )

    def test_equivalent_negative_statements_are_not_contradictions(self):
        pairs = [
            ("Truy vấn tham số hóa không ngăn chặn SQL Injection",
             "Parameterized queries do not prevent SQL Injection."),
            ("Parameterized queries do not prevent SQL Injection",
             "Truy vấn tham số hóa không ngăn chặn SQL Injection."),
        ]
        for claim, evidence in pairs:
            self.assertEqual(self._status(claim, evidence), ClaimStatus.SUPPORTED, msg=claim)

    def test_not_only_is_not_a_negation(self):
        self.assertFalse(_has_opposing_polarity(
            "Parameterized queries not only prevent but also mitigate SQL Injection",
            "Parameterized queries prevent and mitigate SQL Injection.",
        ))
        self.assertEqual(
            self._status(
                "Parameterized queries not only prevent but also mitigate SQL Injection",
                "Parameterized queries prevent and mitigate SQL Injection.",
            ),
            ClaimStatus.SUPPORTED,
        )

    def test_khong_chi_is_not_a_negation(self):
        self.assertFalse(_has_opposing_polarity(
            "Truy vấn tham số hóa không chỉ ngăn chặn mà còn giảm thiểu SQL Injection",
            "Truy vấn tham số hóa ngăn chặn và giảm thiểu SQL Injection.",
        ))
        self.assertEqual(
            self._status(
                "Truy vấn tham số hóa không chỉ ngăn chặn mà còn giảm thiểu SQL Injection",
                "Truy vấn tham số hóa ngăn chặn và giảm thiểu SQL Injection.",
            ),
            ClaimStatus.SUPPORTED,
        )

    def test_double_negation_is_not_forced_to_contradiction(self):
        self.assertFalse(_has_opposing_polarity(
            "Parameterized queries do not fail to prevent SQL Injection",
            "Parameterized queries prevent SQL Injection.",
        ))
        self.assertEqual(
            self._status(
                "Parameterized queries do not fail to prevent SQL Injection",
                "Parameterized queries prevent SQL Injection.",
            ),
            ClaimStatus.SUPPORTED,
        )

    def test_unrelated_negation_in_evidence_is_ignored(self):
        evidence = (
            "Parameterized queries prevent SQL Injection. "
            "Input validation alone does not stop every attack, however."
        )
        self.assertFalse(_has_opposing_polarity(
            "Parameterized queries prevent SQL Injection",
            evidence,
        ))
        self.assertEqual(
            self._status("Parameterized queries prevent SQL Injection", evidence),
            ClaimStatus.SUPPORTED,
        )

    def test_existing_contradiction_regression(self):
        verifier = EvidenceVerifier()
        draft = make_draft("Truy vấn tham số hóa không ngăn chặn tấn công SQL Injection hiệu quả [1].")
        report = verifier.verify(draft, SQLI_EVIDENCE)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.CONTRADICTED)
        self.assertEqual(report.contradicted_claims, 1)

    def test_citation_scope_preserved_under_polarity(self):
        # Contradicting wording is scored only against the cited source.
        draft = make_draft("Parameterized queries prevent SQL Injection [2].")
        report = EvidenceVerifier(similarity_fn=lambda c, p: 0.9).verify(draft, SQLI_EVIDENCE)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.SUPPORTED)
        self.assertEqual(report.claims_detail[0].cited_tag, "[2]")

    def test_uncited_claim_keeps_missing_citation_warning(self):
        draft = make_draft("Parameterized queries do not prevent SQL Injection.")
        report = EvidenceVerifier(similarity_fn=lambda c, p: 0.9).verify(
            draft, [make_evidence("[1]", "c1", "Parameterized queries prevent SQL Injection.")]
        )
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.CONTRADICTED)
        self.assertIsNone(report.claims_detail[0].cited_tag)

    def test_support_threshold_still_gates_polarity(self):
        # Below threshold the claim stays UNVERIFIED; polarity never overrides scoring.
        draft = make_draft("Parameterized queries do not prevent SQL Injection [1].")
        report = EvidenceVerifier(similarity_fn=lambda c, p: 0.2).verify(draft, SQLI_EVIDENCE)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.UNVERIFIED)

    def test_ambiguous_polarity_keeps_similarity_path(self):
        # Two markers on both sides: polarity is uncertain, so similarity decides.
        self.assertFalse(_has_opposing_polarity(
            "Queries do not never prevent injection",
            "Queries do not never prevent injection.",
        ))
        self.assertEqual(
            self._status(
                "Queries do not never prevent injection",
                "Queries do not never prevent injection.",
            ),
            ClaimStatus.SUPPORTED,
        )


    def test_implicit_negation_is_counted(self):
        # R7.1: implicit-only negation must not short-circuit to zero.
        self.assertEqual(_negation_count("The tool fails to prevent SQL Injection"), 1)
        self.assertEqual(_negation_count("The tool is unable to prevent SQL Injection"), 1)
        self.assertEqual(_negation_count("The tool failed to prevent SQL Injection"), 1)
        self.assertEqual(_negation_count("The tool prevents SQL Injection"), 0)

    def test_fail_to_prevent_contradicts_prevent(self):
        self.assertTrue(_has_opposing_polarity(
            "The tool fails to prevent SQL Injection",
            "The tool prevents SQL Injection.",
        ))
        self.assertEqual(
            self._status(
                "The tool fails to prevent SQL Injection",
                "The tool prevents SQL Injection.",
            ),
            ClaimStatus.CONTRADICTED,
        )

    def test_unable_to_prevent_contradicts_prevent(self):
        self.assertTrue(_has_opposing_polarity(
            "The tool is unable to prevent SQL Injection",
            "The tool prevents SQL Injection.",
        ))
        self.assertEqual(
            self._status(
                "The tool is unable to prevent SQL Injection",
                "The tool prevents SQL Injection.",
            ),
            ClaimStatus.CONTRADICTED,
        )

    def test_failed_to_matches_explicit_negation(self):
        self.assertEqual(_polarity("The tool failed to prevent SQL Injection"), POLARITY_NEGATIVE)
        self.assertEqual(_polarity("The tool did not prevent SQL Injection"), POLARITY_NEGATIVE)
        self.assertFalse(_has_opposing_polarity(
            "The tool failed to prevent SQL Injection",
            "The tool did not prevent SQL Injection.",
        ))
        self.assertEqual(
            self._status(
                "The tool failed to prevent SQL Injection",
                "The tool did not prevent SQL Injection.",
            ),
            ClaimStatus.SUPPORTED,
        )

    def test_explicit_plus_implicit_negation_is_uncertain(self):
        # Two markers on one side: no confident polarity, so similarity decides.
        self.assertEqual(_negation_count("The tool does not fail to prevent SQL Injection"), 2)
        self.assertEqual(_polarity("The tool does not fail to prevent SQL Injection"), POLARITY_UNCERTAIN)
        self.assertFalse(_has_opposing_polarity(
            "The tool does not fail to prevent SQL Injection",
            "The tool prevents SQL Injection.",
        ))
        self.assertEqual(
            self._status(
                "The tool does not fail to prevent SQL Injection",
                "The tool prevents SQL Injection.",
            ),
            ClaimStatus.SUPPORTED,
        )

    def test_implicit_negation_does_not_leak_into_contrastives(self):
        self.assertEqual(_negation_count("The tool not only prevents but also detects SQL Injection"), 0)
        self.assertEqual(_negation_count("Công cụ không chỉ ngăn chặn mà còn phát hiện SQL Injection"), 0)
        self.assertFalse(_has_opposing_polarity(
            "The tool not only prevents but also detects SQL Injection",
            "The tool prevents and detects SQL Injection.",
        ))

    def test_explicit_negation_regression_after_fix(self):
        self.assertEqual(_polarity("Truy vấn tham số hóa không ngăn chặn SQL Injection"), POLARITY_NEGATIVE)
        self.assertEqual(_polarity("Parameterized queries prevent SQL Injection"), POLARITY_POSITIVE)
        self.assertTrue(_has_opposing_polarity(
            "Truy vấn tham số hóa không ngăn chặn SQL Injection",
            "Parameterized queries prevent SQL Injection.",
        ))


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

        # The deterministic LLM returns boilerplate unrelated to the evidence: the gate must withhold it.
        self.assertEqual(envelope.verification.status, VerificationStatus.FAILED)
        self.assertEqual(envelope.status, "abstained")
        self.assertEqual(envelope.metadata["verification_gate"], "blocked")
        self.assertEqual(envelope.citations, [])
        self.assertIn("bị chặn", envelope.final_answer_markdown)
        self.assertIsNotNone(envelope.raw_draft)
        json.loads(envelope.to_json())

    def test_default_verifier_fails_closed_without_model(self):
        # Force the default verifier's embedding model to be unavailable so the
        # fail-closed path in run_from_retrieval is exercised deterministically,
        # regardless of whether a real model is cached or downloadable.
        with patch.object(
            MultilingualSemanticSimilarity,
            "_get_model",
            side_effect=RuntimeError("Semantic verifier model unavailable for test"),
        ):
            orchestrator = RAGOrchestrator(
                synthesizer=ResponseSynthesizer(llm_client=DefaultDeterministicLLM()),
            )
            retrieval_output = {
                "status": "ok", "evidence_status": "FULL", "intent": "SEARCH", "total_evidence": 2,
                "evidence_list": SQLI_EVIDENCE, "llm_context": "--- Evidence [1] ---\nA",
            }
            envelope = orchestrator.run_from_retrieval("q", retrieval_output)
            self.assertEqual(envelope.verification.status, VerificationStatus.FAILED)
            self.assertEqual(envelope.status, "abstained")

    def test_semantic_similarity_is_cached_and_cross_lingual(self):
        import numpy as np
        from verifier import MultilingualSemanticSimilarity

        class FakeModel:
            calls = 0
            def encode(self, texts, **kwargs):
                FakeModel.calls += 1
                return np.array([[1.0, 0.0]])

        sim = MultilingualSemanticSimilarity(model=FakeModel())
        self.assertAlmostEqual(sim("Truy vấn tham số hóa", "Parameterized queries"), 1.0)
        sim("Truy vấn tham số hóa", "Parameterized queries")
        self.assertEqual(FakeModel.calls, 2)


class _CountingModel:
    """Deterministic token-frequency embedder that records encode() activity."""

    DIM = 32

    def __init__(self):
        self.encode_calls = 0
        self.encoded_texts = 0
        self.batch_sizes = []
        self.encoded = []

    @classmethod
    def _vector(cls, text):
        import zlib
        import numpy as np
        vec = np.zeros(cls.DIM, dtype=float)
        for token in text.lower().split():
            # Random projection per token: cosine approximates token overlap.
            seed = zlib.crc32(token.encode("utf-8")) & 0xFFFFFFFF
            vec += np.random.RandomState(seed).standard_normal(cls.DIM)
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm else vec

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        import numpy as np
        self.encode_calls += 1
        self.encoded_texts += len(texts)
        self.batch_sizes.append(len(texts))
        self.encoded.extend(texts)
        if not texts:
            return np.zeros((0, self.DIM), dtype=float)
        return np.vstack([self._vector(t) for t in texts])

    def reset(self):
        self.encode_calls = 0
        self.encoded_texts = 0
        self.batch_sizes = []
        self.encoded = []


def _batch_draft():
    return make_draft(
        "Truy vấn tham số hóa ngăn chặn tấn công SQL Injection hiệu quả [1]. "
        "Kiểm tra dữ liệu đầu vào là lớp phòng thủ bổ sung [2]."
    )


class TestBatchEmbedding(unittest.TestCase):
    def setUp(self):
        self.model = _CountingModel()
        self.sim = MultilingualSemanticSimilarity(model=self.model)
        self.verifier = EvidenceVerifier(similarity_fn=self.sim)

    def test_batch_encoding_reduces_encode_calls(self):
        report = self.verifier.verify(_batch_draft(), SQLI_EVIDENCE)
        self.assertEqual(report.supported_claims, 2)
        # One encode() call per _best_match invocation instead of one per text.
        self.assertLess(self.model.encode_calls, self.model.encoded_texts)
        self.assertEqual(self.model.encode_calls, len(self.model.batch_sizes))
        self.assertTrue(any(size > 1 for size in self.model.batch_sizes))

    def test_cold_cache_encodes_each_text_once(self):
        self.verifier.verify(_batch_draft(), SQLI_EVIDENCE)
        self.assertEqual(self.model.encoded_texts, len(set(self.model.encoded)))
        self.assertEqual(self.model.encoded_texts, len(self.sim._cache))

    def test_repeated_texts_are_encoded_once(self):
        draft = make_draft(
            "Truy vấn tham số hóa ngăn chặn tấn công SQL Injection hiệu quả [1]. "
            "Truy vấn tham số hóa ngăn chặn tấn công SQL Injection hiệu quả [1]."
        )
        self.verifier.verify(draft, SQLI_EVIDENCE)
        self.assertEqual(self.model.encoded_texts, len(set(self.model.encoded)))

    def test_warm_cache_requires_zero_calls(self):
        self.verifier.verify(_batch_draft(), SQLI_EVIDENCE)
        self.model.reset()
        self.verifier.verify(_batch_draft(), SQLI_EVIDENCE)
        self.assertEqual(self.model.encode_calls, 0)
        self.assertEqual(self.model.encoded_texts, 0)

    def test_cold_and_warm_reports_are_equivalent(self):
        cold = self.verifier.verify(_batch_draft(), SQLI_EVIDENCE).to_dict()
        self.model.reset()
        warm = self.verifier.verify(_batch_draft(), SQLI_EVIDENCE).to_dict()
        self.assertEqual(cold, warm)

    def test_cache_mapping_matches_direct_encoding(self):
        import numpy as np
        self.verifier.verify(_batch_draft(), SQLI_EVIDENCE)
        self.assertTrue(self.sim._cache)
        for key, stored in self.sim._cache.items():
            expected = _CountingModel._vector(key)
            self.assertTrue(np.allclose(stored, expected))

    def test_citation_scope_is_preserved(self):
        # Claim text only matches evidence [2] but cites [1]: provenance must still fail.
        draft = make_draft("Kiểm tra dữ liệu đầu vào là lớp phòng thủ bổ sung [1].")
        report = self.verifier.verify(draft, SQLI_EVIDENCE)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.UNVERIFIED)

    def test_lexical_only_behaviour_unchanged(self):
        draft = _batch_draft()
        lexical = EvidenceVerifier().verify(draft, SQLI_EVIDENCE)
        self.assertEqual(lexical.status, VerificationStatus.PASSED)
        self.assertEqual(lexical.supported_claims, 2)

    def test_empty_batch_makes_no_model_call(self):
        self.sim._embed_batch([])
        self.assertEqual(self.model.encode_calls, 0)
        self.assertEqual(self.sim._cache, {})

    def test_model_unavailable_fails_closed_through_batch_path(self):
        with patch.object(
            MultilingualSemanticSimilarity,
            "_get_model",
            side_effect=RuntimeError("Semantic verifier model unavailable for test"),
        ):
            with self.assertRaises(RuntimeError):
                self.verifier.verify(_batch_draft(), SQLI_EVIDENCE)

    def test_cache_size_bound_is_respected(self):
        sim = MultilingualSemanticSimilarity(model=_CountingModel(), cache_size=2)
        verifier = EvidenceVerifier(similarity_fn=sim)
        verifier.verify(_batch_draft(), SQLI_EVIDENCE)
        self.assertLessEqual(len(sim._cache), sim.cache_size)

    def test_batch_larger_than_cache_does_not_double_encode(self):
        # A prefetch batch larger than the cache is evicted before _best_match
        # consumes it; the batched path must not encode more texts than lazy
        # per-text encoding would. A bound method hides _embed_batch, so
        # sim.__call__ serves as the lazy reference implementation.
        model = _CountingModel()
        sim = MultilingualSemanticSimilarity(model=model, cache_size=2)
        EvidenceVerifier(similarity_fn=sim).verify(_batch_draft(), SQLI_EVIDENCE)
        self.assertLessEqual(len(sim._cache), sim.cache_size)

        ref_model = _CountingModel()
        ref_sim = MultilingualSemanticSimilarity(model=ref_model, cache_size=2)
        EvidenceVerifier(similarity_fn=ref_sim.__call__).verify(_batch_draft(), SQLI_EVIDENCE)

        self.assertEqual(model.encoded_texts, ref_model.encoded_texts)
        self.assertEqual(model.encode_calls, ref_model.encode_calls)

    def test_small_cache_scores_match_default_cache(self):
        small_model = _CountingModel()
        small = EvidenceVerifier(
            similarity_fn=MultilingualSemanticSimilarity(model=small_model, cache_size=2)
        ).verify(_batch_draft(), SQLI_EVIDENCE)

        big_model = _CountingModel()
        big = EvidenceVerifier(
            similarity_fn=MultilingualSemanticSimilarity(model=big_model)
        ).verify(_batch_draft(), SQLI_EVIDENCE)

        self.assertEqual(
            [(c.status, c.confidence_score) for c in small.claims_detail],
            [(c.status, c.confidence_score) for c in big.claims_detail],
        )
        self.assertEqual(small.status, big.status)


class TestDisclaimerDetection(unittest.TestCase):
    def _texts(self, answer):
        return [c.text for c in extract_claims(answer)]

    def test_vietnamese_disclaimers_are_dropped(self):
        answer = (
            "Ví dụ sau chỉ nhằm mục đích minh họa.\n"
            "Đoạn mã này chỉ mang tính minh họa.\n"
            "Nội dung này không nằm trong tài liệu được trích dẫn.\n"
        )
        self.assertEqual(self._texts(answer), [])

    def test_english_disclaimers_are_dropped(self):
        answer = (
            "The following example is for illustration only.\n"
            "This code is illustrative and not part of the cited documents.\n"
            "In summary, the above holds.\n"
            "Tóm lại, nội dung trên là đúng.\n"
        )
        self.assertEqual(self._texts(answer), [])

    def test_technical_claims_survive(self):
        answer = (
            "Truy vấn tham số hóa ngăn chặn tấn công SQL Injection hiệu quả [1].\n"
            "Parameterized queries prevent SQL injection attacks [1].\n"
            "Tài khoản cơ sở dữ liệu nên được cấp quyền tối thiểu [2].\n"
        )
        claims = extract_claims(answer)
        self.assertEqual(len(claims), 3)
        self.assertEqual([c.tags for c in claims], [["[1]"], ["[1]"], ["[2]"]])

    def test_mixed_claim_keeps_technical_assertion(self):
        # Disclaimer wording plus a substantive assertion: the claim must be verified,
        # not silently dropped, and its citation must stay attached.
        answer = (
            "Ví dụ sau chỉ nhằm mục đích minh họa nhưng vẫn cho thấy truy vấn tham số hóa "
            "ngăn chặn tấn công SQL Injection hiệu quả [1].\n"
            "The following code is illustrative, yet parameterized queries prevent "
            "SQL injection attacks [1].\n"
        )
        claims = extract_claims(answer)
        self.assertEqual(len(claims), 2)
        self.assertEqual([c.tags for c in claims], [["[1]"], ["[1]"]])

    def test_summary_filler_compatibility(self):
        # Existing opener patterns keep working and still yield no claims.
        self.assertEqual(self._texts("Dưới đây là những điểm chính cần lưu ý."), [])
        self.assertEqual(self._texts("Below are the main points to note."), [])

    def test_hybrid_advisory_disclaimer_unaffected(self):
        answer = (
            "Truy vấn tham số hóa ngăn chặn tấn công SQL Injection hiệu quả [1].\n\n"
            "### Khuyến nghị (General Knowledge Advisory)\n"
            "- Triển khai phòng thủ theo chiều sâu kết hợp kiểm thử định kỳ.\n"
        )
        verifier = EvidenceVerifier()
        report = verifier.verify(make_draft(answer, mode="HYBRID"), SQLI_EVIDENCE)
        self.assertEqual(report.total_claims, 2)
        self.assertEqual(report.faithfulness_score, 1.0)
        strict = verifier.verify(make_draft(answer, mode="STRICT_RAG"), SQLI_EVIDENCE)
        self.assertEqual(strict.faithfulness_score, 0.5)

    def test_strict_rag_does_not_exempt_technical_claim(self):
        # A retained mixed claim must still be scored, and a hallucination still fails.
        draft = make_draft(
            "Ví dụ sau chỉ nhằm mục đích minh họa nhưng tường lửa ứng dụng web "
            "tự động vá mọi lỗ hổng zero-day [1]."
        )
        report = EvidenceVerifier().verify(draft, SQLI_EVIDENCE)
        self.assertEqual(report.total_claims, 1)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.UNVERIFIED)


    def test_summary_opener_does_not_drop_substantive_claim(self):
        answer = "In summary, parameterized queries prevent SQL Injection [1]."
        claims = extract_claims(answer)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].tags, ["[1]"])

    def test_vietnamese_summary_opener_does_not_drop_substantive_claim(self):
        answer = "Tóm lại, truy vấn tham số hóa ngăn chặn SQL Injection [1]."
        claims = extract_claims(answer)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].tags, ["[1]"])

    def test_short_technical_residue_after_summary_opener_is_kept(self):
        # Residue is judged on content, not on a long-sentence length heuristic.
        self.assertEqual(len(self._texts("In summary, queries block injection [1].")), 1)
        self.assertEqual(len(self._texts("Tóm lại, truy vấn chặn injection [1].")), 1)

    def test_summary_opener_with_generic_residue_stays_filler(self):
        self.assertEqual(self._texts("As mentioned, the above notes hold."), [])
        self.assertEqual(self._texts("Nhìn chung, các điểm chính đúng."), [])

    def test_summary_opener_alone_is_still_filler(self):
        # Guard the intended behaviour so the defect fix cannot over-correct.
        self.assertEqual(self._texts("In summary, the above holds."), [])
        self.assertEqual(self._texts("Tóm lại, nội dung trên là đúng."), [])

    def test_summary_opener_claim_contradiction_is_detected(self):
        # A negation hidden behind an opener must be verified, not silently skipped.
        similarity = lambda claim, passage: 0.9
        draft = make_draft("In summary, parameterized queries do not prevent SQL Injection [1].")
        report = EvidenceVerifier(similarity_fn=similarity).verify(draft, SQLI_EVIDENCE)

        self.assertEqual(report.total_claims, 1)
        detail = report.claims_detail[0]
        self.assertEqual(detail.cited_tag, "[1]")
        self.assertEqual(detail.status, ClaimStatus.CONTRADICTED)
        self.assertEqual(report.contradicted_claims, 1)
        self.assertEqual(report.faithfulness_score, 0.0)
        self.assertEqual(report.status, VerificationStatus.FAILED)


class TestR8AEvaluation(unittest.TestCase):
    """Cross-cutting checks for the integrated R1-R7 pipeline."""

    def test_ambiguous_polarity_never_claims_semantic_proof(self):
        # High similarity with undecidable polarity must not read as a proven assertion.
        verifier = EvidenceVerifier(similarity_fn=lambda c, p: 0.99)
        draft = make_draft("Queries do not never prevent injection [1].")
        report = verifier.verify(
            draft, [make_evidence("[1]", "c1", "Queries do not never prevent injection.")]
        )
        detail = report.claims_detail[0]
        self.assertEqual(detail.status, ClaimStatus.SUPPORTED)
        # SUPPORTED here means similarity only; the explanation must not assert entailment.
        self.assertNotIn("entail", detail.explanation.lower())

    def test_evidence_and_claim_negation_agree_cross_lingual(self):
        verifier = EvidenceVerifier()
        draft = make_draft("Truy vấn tham số hóa không ngăn chặn SQL Injection [1].")
        evidence = [make_evidence("[1]", "c1", "Parameterized queries do not prevent SQL Injection.")]
        report = verifier.verify(draft, evidence)
        self.assertNotEqual(report.claims_detail[0].status, ClaimStatus.CONTRADICTED)

    def test_unrelated_negation_in_long_evidence_is_not_contradiction(self):
        verifier = EvidenceVerifier(similarity_fn=lambda c, p: 0.9)
        evidence = [make_evidence(
            "[1]", "c1",
            "Parameterized queries prevent SQL Injection. "
            "Logging alone does not remediate injection flaws, however.",
        )]
        draft = make_draft("Parameterized queries prevent SQL Injection [1].")
        report = verifier.verify(draft, evidence)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.SUPPORTED)

    def test_integration_gate_blocks_unsupported_output(self):
        # Unsupported draft (score below threshold) must be withheld by the gate.
        orchestrator = RAGOrchestrator(
            synthesizer=ResponseSynthesizer(llm_client=DefaultDeterministicLLM()),
            verifier=EvidenceVerifier(similarity_fn=lambda claim, passage: 0.1),
            traceable_formatter=DefaultTraceableFormatter(),
        )
        retrieval_output = {
            "status": "ok", "evidence_status": "FULL", "intent": "SEARCH", "total_evidence": 2,
            "evidence_list": SQLI_EVIDENCE, "llm_context": "--- Evidence [1] ---\nA",
        }
        envelope = orchestrator.run_from_retrieval("q", retrieval_output)
        self.assertEqual(envelope.verification.status, VerificationStatus.FAILED)
        self.assertEqual(envelope.metadata["verification_gate"], "blocked")
        self.assertEqual(envelope.citations, [])
        self.assertEqual(envelope.status, "abstained")

    def test_batch_encoding_call_reduction_is_measured(self):
        # Record the actual encode() counts for the report rather than assuming them.
        model = _CountingModel()
        verifier = EvidenceVerifier(similarity_fn=MultilingualSemanticSimilarity(model=model))
        verifier.verify(_batch_draft(), SQLI_EVIDENCE)
        self.assertGreater(model.encoded_texts, 0)
        self.assertLess(model.encode_calls, model.encoded_texts)
        # Cold cache: every distinct text encoded exactly once.
        self.assertEqual(model.encoded_texts, len(set(model.encoded)))


def _one_evidence(text, tag="[1]"):
    return [make_evidence(tag, "c1", text, title="OWASP Top 10")]


class TestR9AVerificationFailures(unittest.TestCase):
    """
    R9A reproductions of the TEST_02 verification defects, fixed in R9B.

    Every test is offline and uses the default lexical scorer, so scores are exact.
    Policy under test: support, contradiction and citation compliance are separate
    dimensions. Only a CONTRADICTED claim vetoes the gate; missing citations cap it
    at WARNING. Unmarked tests guard the boundaries so a fix cannot over-correct.
    """

    # --- Numeric contradictions -------------------------------------------

    def test_numeric_contradiction_same_metric_and_version(self):
        # TEST_02 case 1: claim says 37 CWEs, cited evidence says 38 CWEs for the same category.
        evidence = _one_evidence(
            "The 2025 edition of Injection has the greatest number of CVEs for any category, "
            "with 38 CWEs in this category."
        )
        draft = make_draft(
            "The 2025 edition of Injection has the greatest number of CVEs for any category, "
            "with 37 CWEs in this category [1]."
        )
        report = EvidenceVerifier().verify(draft, evidence)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.CONTRADICTED)

    def test_numeric_contradiction_survives_high_lexical_overlap(self):
        # The mismatched count is masked by a neighbouring metric that does match (TEST_02 case 2).
        evidence = _one_evidence(
            "In version 2025, Injection affects 38 CWEs. Injection covers 100% of applications tested."
        )
        draft = make_draft(
            "In version 2025, Injection affects 37 CWEs and covers 100% of applications tested [1]."
        )
        report = EvidenceVerifier().verify(draft, evidence)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.CONTRADICTED)

    def test_same_numbers_different_version_is_not_a_contradiction(self):
        # Guard: numbers tied to different versions must stay supported.
        evidence = _one_evidence(
            "In version 2021, Injection affected 37 CWEs. In version 2025, Injection affects 38 CWEs."
        )
        draft = make_draft("In version 2021, Injection affected 37 CWEs [1].")
        report = EvidenceVerifier().verify(draft, evidence)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.SUPPORTED)

    def test_same_numbers_different_metric_is_not_a_contradiction(self):
        # Guard: unrelated metrics sharing a value must not be forced into a contradiction.
        evidence = _one_evidence(
            "Injection covers 100% of applications tested. There are 38 CWEs in this category."
        )
        draft = make_draft(
            "Injection covers 100% of applications tested and there are 38 CWEs in this category [1]."
        )
        report = EvidenceVerifier().verify(draft, evidence)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.SUPPORTED)

    def test_unrelated_measurement_in_same_chunk_is_not_a_contradiction(self):
        # R9B.1: the 38-CWE measurement belongs to XSS, so the Injection claim stands.
        evidence = _one_evidence(
            "In 2025, Injection affects 37 CWEs. In 2025, XSS affects 38 CWEs."
        )
        draft = make_draft("In 2025, Injection affects 37 CWEs [1].")
        report = EvidenceVerifier().verify(draft, evidence)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.SUPPORTED)

    def test_conflicting_measurement_only_for_another_entity_is_unverified(self):
        # Guard: a differing value owned by a different entity must not be a contradiction.
        evidence = _one_evidence("In 2025, XSS affects 38 CWEs.")
        draft = make_draft("In 2025, Injection affects 37 CWEs [1].")
        report = EvidenceVerifier().verify(draft, evidence)
        self.assertNotEqual(report.claims_detail[0].status, ClaimStatus.CONTRADICTED)

    def test_internally_inconsistent_evidence_is_not_a_hard_contradiction(self):
        # Guard: the same entity carrying two values is ambiguous, not proven wrong.
        evidence = _one_evidence(
            "In 2025, Injection affects 37 CWEs. In 2025, Injection affects 38 CWEs."
        )
        draft = make_draft("In 2025, Injection affects 37 CWEs [1].")
        report = EvidenceVerifier().verify(draft, evidence)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.UNVERIFIED)

    def test_unverifiable_number_is_not_forced_to_contradiction(self):
        # Guard: an unmatched value with no comparable evidence stays UNVERIFIED.
        evidence = _one_evidence(
            "Injection is the most serious category in the 2025 ranking."
        )
        draft = make_draft("Injection affects 37 CWEs in the 2025 ranking [1].")
        report = EvidenceVerifier().verify(draft, evidence)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.UNVERIFIED)

    # --- Unsupported entity relationships ---------------------------------

    def test_unsupported_entity_relation_is_not_supported(self):
        # TEST_02 case 3: both entities appear in the source, but their causal link does not.
        evidence = _one_evidence(
            "Software Supply Chain Failures is an expansion of Vulnerable and Outdated Components. "
            "Injection falls two spots from #3 to #5 in the ranking, maintaining its position "
            "relative to Cryptographic Failures and Insecure Design."
        )
        draft = make_draft(
            "Software Supply Chain Failures caused Injection to fall two spots from #3 to #5 "
            "in the 2025 ranking [1]."
        )
        report = EvidenceVerifier().verify(draft, evidence)
        detail = report.claims_detail[0]
        # High lexical overlap (0.68) currently reads as support; the relation is unproven.
        self.assertEqual(detail.status, ClaimStatus.UNVERIFIED)

    def test_explicit_causal_link_present_in_evidence_stays_supported(self):
        # Guard: causality needs a narrow marker on both sides; ordinary prose is unaffected.
        evidence = _one_evidence(
            "Parameterized queries prevent SQL Injection because user input is never concatenated."
        )
        draft = make_draft(
            "Parameterized queries prevent SQL Injection because user input is never concatenated [1]."
        )
        report = EvidenceVerifier().verify(draft, evidence)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.SUPPORTED)

    def test_unrelated_entity_claim_stays_unverified(self):
        # Guard: a claim about entities absent from the evidence must not be rescued by relation logic.
        evidence = _one_evidence(
            "Injection includes Cross-site Scripting and SQL Injection vulnerabilities. "
            "Injection falls two spots from #3 to #5 in the ranking."
        )
        draft = make_draft(
            "Software Supply Chain Failures caused Injection to fall two spots in the 2025 ranking [1]."
        )
        report = EvidenceVerifier().verify(draft, evidence)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.UNVERIFIED)

    # --- Citation compliance vs evidence support --------------------------

    def test_missing_citation_caps_status_at_warning(self):
        # TEST_02 case 4: support and citation are separate dimensions. A grounded but
        # uncited claim stays SUPPORTED and keeps its score, while the gate caps at WARNING.
        evidence = _one_evidence(
            "Injection includes a range of issues from Cross-site Scripting to SQL Injection vulnerabilities."
        )
        draft = make_draft(
            "Injection includes a range of issues from Cross-site Scripting to SQL Injection vulnerabilities."
        )
        report = EvidenceVerifier().verify(draft, evidence)
        self.assertIsNone(report.claims_detail[0].cited_tag)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.SUPPORTED)
        self.assertEqual(report.faithfulness_score, 1.0)
        self.assertTrue(any("thiếu thẻ trích dẫn" in w for w in report.warnings))
        self.assertEqual(report.status, VerificationStatus.WARNING)

    def test_uncited_claim_never_fails_the_gate(self):
        # Guard: citation non-compliance is a cap, never a veto. Missing tags must not
        # drag a fully grounded answer below WARNING.
        evidence = _one_evidence(
            "Injection includes a range of issues from Cross-site Scripting to SQL Injection vulnerabilities."
        )
        draft = make_draft(
            "Injection includes a range of issues from Cross-site Scripting to SQL Injection vulnerabilities."
        )
        report = EvidenceVerifier().verify(draft, evidence)
        self.assertEqual(report.faithfulness_score, 1.0)
        self.assertNotEqual(report.status, VerificationStatus.FAILED)

    # --- Overall gate vs contradicted claims ------------------------------

    def test_contradicted_claim_blocks_passed_gate(self):
        # TEST_02 case 5: a contradicted claim is a veto, not diluted by supported ones.
        evidence = [
            make_evidence("[1]", "c1", "Parameterized queries prevent SQL Injection."),
            make_evidence("[2]", "c2", "Input validation is a secondary defense."),
        ]
        draft = make_draft(
            "Parameterized queries prevent SQL Injection [1]. "
            "Input validation is a secondary defense [2]. "
            "Logging is a secondary defense [2]. "
            "Patching is a secondary defense [2]. "
            "Parameterized queries do not prevent SQL Injection [1]."
        )
        report = EvidenceVerifier().verify(draft, evidence)
        self.assertEqual(report.contradicted_claims, 1)
        self.assertEqual(report.faithfulness_score, 0.8)
        self.assertEqual(report.status, VerificationStatus.FAILED)

    def test_dimensions_are_reported_separately(self):
        # Guard: support / contradiction / citation counters stay independent and exact.
        # No contradiction here, so the citation cap is what holds this at WARNING.
        evidence = [
            make_evidence("[1]", "c1", "Parameterized queries prevent SQL Injection."),
            make_evidence("[2]", "c2", "Input validation is a secondary defense."),
        ]
        draft = make_draft(
            "Parameterized queries prevent SQL Injection [1]. "
            "Input validation is a secondary defense. "
            "Web application firewalls patch every zero-day flaw [2]."
        )
        report = EvidenceVerifier().verify(draft, evidence)
        self.assertEqual(report.total_claims, 3)
        self.assertEqual(report.supported_claims, 2)
        self.assertEqual(report.contradicted_claims, 0)
        self.assertEqual(report.unverified_claims, 1)
        self.assertEqual(report.faithfulness_score, 2 / 3)
        self.assertEqual(report.status, VerificationStatus.WARNING)

    def test_uncertain_claim_is_not_a_hard_veto(self):
        # Guard: an unverified (heuristic miss) claim must not be escalated to a veto.
        # Only CONTRADICTED caps the gate at FAILED; UNVERIFIED keeps the score-based status.
        evidence = [
            make_evidence("[1]", "c1", "Parameterized queries prevent SQL Injection."),
            make_evidence("[2]", "c2", "Input validation is a secondary defense."),
        ]
        draft = make_draft(
            "Parameterized queries prevent SQL Injection [1]. "
            "Web application firewalls patch every zero-day flaw [2]."
        )
        report = EvidenceVerifier().verify(draft, evidence)
        self.assertEqual(report.contradicted_claims, 0)
        self.assertEqual(report.unverified_claims, 1)
        self.assertNotEqual(report.status, VerificationStatus.FAILED)

    def test_uncited_claim_is_not_a_contradiction(self):
        # Guard: a missing citation is a compliance defect, never a semantic contradiction.
        evidence = _one_evidence("Parameterized queries prevent SQL Injection.")
        draft = make_draft("Parameterized queries prevent SQL Injection.")
        report = EvidenceVerifier().verify(draft, evidence)
        self.assertIsNone(report.claims_detail[0].cited_tag)
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.SUPPORTED)
        self.assertEqual(report.contradicted_claims, 0)

    def test_clean_grounded_draft_still_passes(self):
        # Guard: the strictest plausible R9B rule must not break the happy path.
        draft = make_draft(
            "Parameterized queries prevent SQL Injection [1]. "
            "Input validation is a secondary defense [2]."
        )
        evidence = [
            make_evidence("[1]", "c1", "Parameterized queries prevent SQL Injection."),
            make_evidence("[2]", "c2", "Input validation is a secondary defense."),
        ]
        report = EvidenceVerifier().verify(draft, evidence)
        self.assertEqual(report.supported_claims, 2)
        self.assertEqual(report.contradicted_claims, 0)
        self.assertEqual(report.faithfulness_score, 1.0)
        self.assertEqual(report.status, VerificationStatus.PASSED)


class TestR9C2NumericAlignment(unittest.TestCase):
    """
    R9C.2: numeric unit extraction, multi-value evidence and evidence attribution.

    Defects reproduced from evaluations/r9c/retry_TEST_02/TEST_02.envelope.json:
    claim_006 (30k CVEs) and claim_013 (643 vs 241 CWEs) were UNVERIFIED although
    the evidence states their values exactly, because the word after the number was
    read as its unit ('30k' -> metric 'k', '643 unique' -> metric 'unique').
    """

    def _status(self, draft_text, evidence_text):
        return EvidenceVerifier().verify(
            make_draft(draft_text), _one_evidence(evidence_text)
        ).claims_detail[0].status

    # --- claim_006: magnitude suffixes ------------------------------------

    def test_magnitude_suffix_matches_same_entity(self):
        # TEST_02 claim_006: XSS count is stated verbatim in the evidence.
        evidence = (
            "Injection includes Cross-site Scripting (high frequency/low impact) with more than 30k CVEs "
            "and SQL Injection (low frequency/high impact) with more than 14k CVEs."
        )
        status = self._status("Cross-site Scripting has more than 30k CVEs [1].", evidence)
        self.assertEqual(status, ClaimStatus.SUPPORTED)

    def test_two_entities_with_different_counts_do_not_collide(self):
        # Two independent measurements in one passage must stay attributable.
        evidence = (
            "Injection includes Cross-site Scripting with more than 30k CVEs "
            "and SQL Injection with more than 14k CVEs."
        )
        self.assertEqual(
            self._status("SQL Injection has more than 14k CVEs [1].", evidence),
            ClaimStatus.SUPPORTED,
        )
        self.assertEqual(
            self._status("Cross-site Scripting has more than 30k CVEs [1].", evidence),
            ClaimStatus.SUPPORTED,
        )

    def test_magnitude_suffix_still_contradicts_a_different_count(self):
        # Guard: fixing the suffix must not blunt genuine contradiction.
        evidence = "Cross-site Scripting has more than 30k CVEs."
        status = self._status("Cross-site Scripting has more than 40k CVEs [1].", evidence)
        self.assertEqual(status, ClaimStatus.CONTRADICTED)

    # --- claim_013: counts and version-qualified assertions ---------------

    def test_count_noun_identifies_metric(self):
        # TEST_02 claim_013: '643 unique CWEs' / '241 in 2021' were misread as units
        # 'unique' and 'in', so neither assertion aligned. Constant similarity isolates
        # numeric alignment from lexical scoring, which this test does not cover.
        evidence = "Additionally, there are 643 unique CWEs mapped to CVEs (up from 241 in 2021)."
        verifier = EvidenceVerifier(similarity_fn=lambda c, p: 0.9, support_threshold=0.5)
        report = verifier.verify(
            make_draft("The total number of unique CWEs increased significantly (643 vs 241 in 2021) [1]."),
            _one_evidence(evidence),
        )
        self.assertEqual(report.claims_detail[0].status, ClaimStatus.SUPPORTED)

    def test_version_qualified_assertions_compare_per_version(self):
        # A historical value stays bound to its year rather than to the document edition.
        evidence = "In version 2021, Injection affected 33 CWEs. In version 2025, Injection affects 37 CWEs."
        self.assertEqual(
            self._status("In version 2021, Injection affected 33 CWEs [1].", evidence),
            ClaimStatus.SUPPORTED,
        )
        self.assertEqual(
            self._status("In version 2021, Injection affected 38 CWEs [1].", evidence),
            ClaimStatus.CONTRADICTED,
        )

    def test_arbitrary_word_after_number_is_not_a_unit(self):
        # Guard: 'vs', 'in', 'unique' and similar nouns must not define a metric.
        evidence = "The report lists 643 unique CWEs."
        self.assertNotEqual(
            self._status("The report lists 241 distinct CWEs [1].", evidence),
            ClaimStatus.SUPPORTED,
        )

    # --- evidence attribution ---------------------------------------------

    def test_unrelated_sentence_in_chunk_cannot_contradict(self):
        # Guard: a measurement in a *different* sentence about another entity must not
        # refute this claim (the 45.2% figure in TEST_02's chunk is unrelated).
        evidence = (
            "Injection falls two spots from #3 to #5 in the ranking. "
            "Authentication Failures covers 38 CWEs in a separate category."
        )
        status = self._status("Injection falls two spots from #3 to #5 in the ranking [1].", evidence)
        self.assertEqual(status, ClaimStatus.SUPPORTED)

    def test_multi_sentence_evidence_still_supports(self):
        # Guard: narrowing attribution must not discard legitimate multi-sentence support.
        evidence = (
            "Injection is one of the most tested categories. "
            "It covers 100% of applications tested."
        )
        status = self._status(
            "Injection is one of the most tested categories and covers 100% of applications tested [1].",
            evidence,
        )
        self.assertEqual(status, ClaimStatus.SUPPORTED)

    # --- preserved genuine contradictions ---------------------------------

    def test_true_contradiction_same_entity_metric_version(self):
        evidence = "In 2025, Injection affects 37 CWEs."
        self.assertEqual(
            self._status("In 2025, Injection affects 38 CWEs [1].", evidence),
            ClaimStatus.CONTRADICTED,
        )

    def test_inconsistent_evidence_stays_unverified(self):
        evidence = "In 2025, Injection affects 37 CWEs. In 2025, Injection affects 38 CWEs."
        status = self._status("In 2025, Injection affects 37 CWEs [1].", evidence)
        self.assertEqual(status, ClaimStatus.UNVERIFIED)

    # --- cross-version value accounting (R9C.2.1) -------------------------

    def test_cross_version_swap_is_contradicted(self):
        # R9C.2.1: the editions have their counts swapped. Each version must be
        # compared with its own measurement. Pooling claim values by metric alone
        # lets the 2025 figure vouch for the 2021 assertion, and vice versa.
        claim = (
            "In version 2021, Injection affects 33 CWEs. "
            "In version 2025, Injection affects 37 CWEs"
        )
        evidence = (
            "In version 2021, Injection affects 37 CWEs. "
            "In version 2025, Injection affects 33 CWEs."
        )
        self.assertEqual(_numeric_relation(claim, evidence), "contradicted")

    def test_identical_values_across_editions_are_not_a_contradiction(self):
        # Guard: the same correct count in both editions is consistent, so the fix
        # must not require editions to hold different values.
        claim = (
            "In version 2021, Injection affects 37 CWEs. "
            "In version 2025, Injection affects 37 CWEs"
        )
        evidence = (
            "In version 2021, Injection affects 37 CWEs. "
            "In version 2025, Injection affects 37 CWEs."
        )
        self.assertIsNone(_numeric_relation(claim, evidence))

    def test_cross_version_swap_blocks_the_gate(self):
        # End-to-end guard: the swapped 2021 count is contradicted and never passes.
        evidence = (
            "In version 2021, Injection affects 37 CWEs. "
            "In version 2025, Injection affects 33 CWEs."
        )
        self.assertEqual(
            self._status("In version 2021, Injection affects 33 CWEs [1].", evidence),
            ClaimStatus.CONTRADICTED,
        )


class TestR9C2PolarityScope(unittest.TestCase):
    """R9C.2: negation is scoped to the clause that shares substance with the claim."""

    def _status(self, claim_text, evidence_text):
        verifier = EvidenceVerifier(similarity_fn=lambda c, p: 0.9, support_threshold=0.5)
        return verifier.verify(
            make_draft(claim_text), [make_evidence("[1]", "c1", evidence_text)]
        ).claims_detail[0].status

    def test_negation_in_unrelated_clause_is_not_a_contradiction(self):
        # TEST_02 claim_016: 'not validated' negates the data, not the assertion.
        evidence = "Server-Side Request Forgery (SSRF) has been rolled into this category."
        status = self._status(
            "New considerations such as SSRF, which is now rolled into A01, while data is not validated [1].",
            evidence,
        )
        self.assertNotEqual(status, ClaimStatus.CONTRADICTED)

    def test_true_polarity_contradiction_is_preserved(self):
        evidence = "Parameterized queries prevent SQL Injection."
        self.assertEqual(
            self._status("Parameterized queries do not prevent SQL Injection [1].", evidence),
            ClaimStatus.CONTRADICTED,
        )

    def test_unrelated_negation_inside_chunk_is_ignored(self):
        evidence = (
            "Parameterized queries prevent SQL Injection. "
            "Legacy drivers do not support this feature."
        )
        self.assertEqual(
            self._status("Parameterized queries prevent SQL Injection [1].", evidence),
            ClaimStatus.SUPPORTED,
        )


if __name__ == "__main__":
    unittest.main()
