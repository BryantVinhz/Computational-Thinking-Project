import re
from typing import Any

import numpy as np

from query_vectorizer import get_shared_sentence_transformer
from schema_response import ClaimStatus, VerificationStatus
from verifier import extract_claims


def strip_footnotes(markdown_text: str) -> str:
    """Strip the automated reference list at the end of the text to extract the core answer content."""
    if not markdown_text:
        return ""
    cleaned = re.sub(
        r"\n+---\n+###\s*Danh mục tài liệu tham chiếu[\s\S]*$",
        "",
        markdown_text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^>>>\s*Lưu ý:.*?\n+", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


class SemanticClaimMatcher:
    """Multilingual semantic matching (BAAI/bge-m3) for Answer Correctness & Answer Relevancy."""

    def __init__(self, model_name: str = "BAAI/bge-m3", device: str = "cuda"):
        self.model = get_shared_sentence_transformer(model_name, device=device)
        self._cache: dict[str, np.ndarray] = {}

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1024), dtype=np.float32)
        missing = list(dict.fromkeys(t for t in texts if t not in self._cache))
        if missing:
            vecs = self.model.encode(missing, normalize_embeddings=True, show_progress_bar=False, batch_size=32)
            for t, v in zip(missing, vecs):
                self._cache[t] = np.asarray(v, dtype=np.float32)
        return np.vstack([self._cache[t] for t in texts])

    def sim(self, text_a: str, text_b: str) -> float:
        if not text_a.strip() or not text_b.strip():
            return 0.0
        va, vb = self.encode([text_a, text_b])
        return float(np.clip(np.dot(va, vb), 0.0, 1.0))

    def claim_prf1(
        self,
        generated_claims: list[str],
        reference_claims: list[str],
        threshold: float = 0.62,
    ) -> tuple[float, float, float]:
        """Compute Claim Precision, Claim Recall và Claim F1 (RAGChecker)."""
        if not reference_claims and not generated_claims:
            return 1.0, 1.0, 1.0
        if not reference_claims or not generated_claims:
            return 0.0, 0.0, 0.0

        mat_a = self.encode(generated_claims)
        mat_g = self.encode(reference_claims)
        sim_matrix = mat_a @ mat_g.T

        matched_a = int(np.sum(np.max(sim_matrix, axis=1) >= threshold))
        matched_g = int(np.sum(np.max(sim_matrix, axis=0) >= threshold))

        p_c = matched_a / len(generated_claims)
        r_c = matched_g / len(reference_claims)
        f1_c = (2 * p_c * r_c / (p_c + r_c)) if (p_c + r_c) > 0 else 0.0
        return round(p_c, 4), round(r_c, 4), round(f1_c, 4)


def compute_citation_metrics(
    verification_dict: dict[str, Any] | None,
    generation_mode: str,
) -> tuple[float | None, float | None, float | None]:
    """
    Compute Citation Precision, Citation Coverage, and Citation F1 (ALCE):
    - Citation-worthy claims: factual statements not belonging to General Knowledge Advisory.
    - Valid citation: citation tag [n] pointing to evidence that supports (SUPPORTED) that claim.
    """
    if not verification_dict:
        return None, None, None

    details = verification_dict.get("claims_detail") or []
    worthy_claims = [
        c for c in details
        if not (generation_mode == "HYBRID" and "General Knowledge Advisory" in (c.get("explanation") or ""))
    ]
    if not worthy_claims:
        return None, None, None

    total_citations = 0
    valid_citations = 0
    claims_with_valid_citations = 0

    for c in worthy_claims:
        raw_tag_str = c.get("cited_tag") or ""
        tags = re.findall(r"\[\d+\]", raw_tag_str)
        is_supported = c.get("status") == ClaimStatus.SUPPORTED.value

        if tags:
            total_citations += len(tags)
            if is_supported:
                valid_citations += len(tags)
                claims_with_valid_citations += 1

    cp = (valid_citations / total_citations) if total_citations > 0 else 0.0
    cr = claims_with_valid_citations / len(worthy_claims)
    cf1 = (2 * cp * cr / (cp + cr)) if (cp + cr) > 0 else 0.0
    return round(cp, 4), round(cr, 4), round(cf1, 4)


def evaluate_generation_sample(
    sample: dict[str, Any],
    envelope_dict: dict[str, Any],
    matcher: SemanticClaimMatcher,
    llm_client: Any,
) -> dict[str, float | None]:
    """Evaluate all Answer Quality and Evidence & Citation Quality metrics for 1 sample."""
    actual_status = envelope_dict.get("status", "error")
    if actual_status != "ok":
        return {
            "faithfulness": None,
            "claim_precision": None,
            "claim_recall": None,
            "claim_f1": None,
            "answer_relevancy": None,
            "citation_precision": None,
            "citation_coverage": None,
            "citation_f1": None,
        }

    ver_report = envelope_dict.get("verification") or {}
    gen_mode = (envelope_dict.get("metadata") or {}).get("generation_mode", "ABSTENTION")
    ref_claims = (sample.get("answer_ground_truth") or {}).get("reference_claims") or []

    # Faithfulness (RAGAS)
    faithfulness: float | None = None
    if ver_report.get("status") != VerificationStatus.SKIPPED.value:
        faithfulness = float(ver_report.get("faithfulness_score", 0.0))

    # Answer Correctness: Claim Precision / Recall / F1 (RAGChecker)
    clean_answer = strip_footnotes(envelope_dict.get("final_answer_markdown") or "")
    extracted = extract_claims(clean_answer)
    gen_claim_texts = [
        c.text for c in extracted
        if not (gen_mode == "HYBRID" and c.is_advisory)
    ]

    p_c: float | None = None
    r_c: float | None = None
    f1_c: float | None = None
    if ref_claims:
        p_c, r_c, f1_c = matcher.claim_prf1(gen_claim_texts, ref_claims)

    # Answer Relevancy: Reverse Question Generation (RAGAS)
    ar_score: float | None = None
    rev_questions = llm_client.generate_reverse_questions(clean_answer, lang=sample["lang"], n=3)
    if rev_questions:
        ar_score = round(float(np.mean([matcher.sim(sample["query"], rq) for rq in rev_questions])), 4)

    # Evidence & Citation Quality: Precision, Coverage, F1 (ALCE)
    cp, cr, cf1 = compute_citation_metrics(
        verification_dict=ver_report,
        generation_mode=gen_mode,
    )

    return {
        "faithfulness": faithfulness,
        "claim_precision": p_c,
        "claim_recall": r_c,
        "claim_f1": f1_c,
        "answer_relevancy": ar_score,
        "citation_precision": cp,
        "citation_coverage": cr,
        "citation_f1": cf1,
    }