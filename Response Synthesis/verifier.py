"""
Module: verifier.py
Stage 4: Response Synthesis, Evidence Verification, and Traceable Response
Role: BẢO (Evidence Verification)

Kiểm định độ trung thực (faithfulness) của bản thảo do Synthesizer sinh ra:
1. Tách bản thảo Markdown thành các nhận định nguyên tử (atomic claims) theo câu / dòng.
2. Đối chiếu từng claim với evidence mà nó trích dẫn ([1], [2]...).
3. Gán ClaimStatus: SUPPORTED | CONTRADICTED | UNVERIFIED.
4. Tính faithfulness_score và VerificationStatus theo ngưỡng của schema_response.py.

Mặc định dùng độ phủ từ vựng (unigram + bigram) nên chạy offline, không cần model.
Khi câu trả lời và tài liệu khác ngôn ngữ (VD: trả lời tiếng Việt, tài liệu OWASP tiếng Anh),
truyền `similarity_fn` dựa trên embedding đa ngôn ngữ để có kết quả đáng tin cậy.

Interface tuân thủ VerifierProtocol trong rag_orchestrator.py:
    verify(draft, evidence_list) -> VerificationReportSchema
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable

try:
    from .schema_response import (
        ClaimStatus,
        ClaimVerificationResult,
        SynthesizedDraftSchema,
        VerificationReportSchema,
        VerificationStatus,
    )
    from .traceable_formatter import index_evidence_by_tag
except ImportError:
    from schema_response import (
        ClaimStatus,
        ClaimVerificationResult,
        SynthesizedDraftSchema,
        VerificationReportSchema,
        VerificationStatus,
    )
    from traceable_formatter import index_evidence_by_tag

SimilarityFn = Callable[[str, str], float]

# Ngưỡng tổng thể khớp với docstring của VerificationStatus.
PASSED_THRESHOLD = 0.8
WARNING_THRESHOLD = 0.5
DEFAULT_SUPPORT_THRESHOLD = 0.5
MIN_CLAIM_TOKENS = 3
SNIPPET_MAX_CHARS = 300
SEMANTIC_SUPPORT_THRESHOLD = 0.60
ADVISORY_MARKER = "general knowledge advisory"

_TAG_RE = re.compile(r"\[(\d+)\]")
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+")
_ONLY_TAGS_RE = re.compile(r"^(?:\s*\[\d+\]\s*[.,;:]?)+$")
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_MD_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_|`)")

# Từ phủ định dùng cho heuristic phát hiện mâu thuẫn (tiếng Việt + tiếng Anh).
NEGATION_TOKENS = frozenset({
    "không", "chẳng", "chưa", "đừng", "chớ", "cấm",
    "not", "no", "never", "cannot", "without", "nor", "neither",
})

# Hư từ phổ biến: bỏ khỏi tập token để điểm phản ánh nội dung, không phản ánh ngữ pháp.
# Lưu ý: KHÔNG chứa từ phủ định, để chúng vẫn tham gia so khớp.
STOPWORDS = frozenset({
    # Tiếng Việt
    "và", "là", "của", "các", "những", "được", "bị", "cho", "với", "trong", "khi", "này",
    "đó", "để", "một", "có", "thì", "mà", "từ", "theo", "như", "cũng", "đã", "sẽ", "đang",
    "về", "ra", "vào", "tại", "nên", "do", "bởi", "hay", "hoặc", "nếu", "vì", "nhưng",
    # Tiếng Anh
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are", "be",
    "by", "as", "at", "it", "this", "that", "these", "those", "from", "can", "should", "was",
    "were", "has", "have", "which", "its", "into",
})


@dataclass
class ExtractedClaim:
    """Một nhận định nguyên tử được tách ra từ bản thảo."""
    text: str               # Nội dung đã bỏ thẻ trích dẫn và định dạng Markdown
    tags: list[str]         # Các thẻ trích dẫn gắn với claim, VD: ["[1]", "[2]"]
    is_advisory: bool       # Thuộc mục General Knowledge Advisory (HYBRID)


# =====================================================================
# Tiện ích xử lý văn bản
# =====================================================================

def _tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFC", text.lower())
    return [t for t in _TOKEN_RE.findall(normalized) if not t.isdigit()]


def _content_tokens(text: str) -> list[str]:
    return [t for t in _tokens(text) if t not in STOPWORDS]


def _bigrams(tokens: list[str]) -> set[tuple[str, str]]:
    return set(zip(tokens, tokens[1:]))


def lexical_support_score(claim: str, passage: str) -> float:
    """
    Tỷ lệ nội dung của claim xuất hiện trong passage (0.0 -> 1.0).

    Kết hợp unigram recall và bigram recall: tiếng Việt viết theo âm tiết nên unigram
    riêng lẻ ("bảo", "mật") quá dễ trùng; bigram giữ lại được từ ghép ("bảo mật").
    """
    claim_tokens = _content_tokens(claim)
    if not claim_tokens:
        return 0.0
    passage_tokens = _content_tokens(passage)
    passage_set = set(passage_tokens)
    unigram_recall = sum(t in passage_set for t in claim_tokens) / len(claim_tokens)

    claim_bigrams = _bigrams(claim_tokens)
    if not claim_bigrams:
        return unigram_recall
    bigram_recall = len(claim_bigrams & _bigrams(passage_tokens)) / len(claim_bigrams)
    return 0.5 * unigram_recall + 0.5 * bigram_recall


def _has_negation(text: str) -> bool:
    return any(t in NEGATION_TOKENS for t in _tokens(text))


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def _clean_markdown(line: str) -> str:
    line = _LIST_MARKER_RE.sub("", line)
    line = _MD_EMPHASIS_RE.sub("", line)
    return line.strip()


def _truncate(text: str, max_chars: int = SNIPPET_MAX_CHARS) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= max_chars else f"{flat[:max_chars].rsplit(' ', 1)[0]}…"


# =====================================================================
# Tách claim
# =====================================================================

def extract_claims(draft_answer: str, min_tokens: int = MIN_CLAIM_TOKENS) -> list[ExtractedClaim]:
    """
    Tách bản thảo Markdown thành các claim.

    Bỏ qua: tiêu đề (#), đường kẻ, code block, header + separator của bảng, dòng ghi chú
    nằm trọn trong ngoặc đơn, và các mẩu quá ngắn (< min_tokens token nội dung).
    Mỗi dòng bảng được coi là một claim. Thẻ trích dẫn đứng riêng sau dấu chấm
    (VD: "... đầu vào. [1]") được gắn về câu đứng trước.
    """
    lines = (draft_answer or "").splitlines()
    claims: list[ExtractedClaim] = []
    in_code_block = False
    in_advisory = False

    for idx, raw_line in enumerate(lines):
        line = raw_line.strip()
        if line.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block or not line:
            continue
        if line.startswith("#"):
            in_advisory = ADVISORY_MARKER in line.lower()
            continue
        if re.fullmatch(r"[-*_]{3,}", line) or _TABLE_SEPARATOR_RE.match(line):
            continue
        if line.startswith("|"):
            next_line = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
            if _TABLE_SEPARATOR_RE.match(next_line):
                continue  # header của bảng
            # Nối bằng dấu phẩy (không dùng ";") để bộ tách câu giữ nguyên cả dòng là một claim.
            line = ", ".join(cell.strip() for cell in line.strip("|").split("|") if cell.strip())
        line = _clean_markdown(line)
        if line.startswith("(") and line.endswith(")"):
            continue  # dòng ghi chú, VD: "(Lưu ý: ...)"

        segments: list[str] = []
        for sentence in _split_sentences(line):
            if segments and _ONLY_TAGS_RE.match(sentence):
                segments[-1] = f"{segments[-1]} {sentence}"
            else:
                segments.append(sentence)

        for segment in segments:
            tags = list(dict.fromkeys(f"[{n}]" for n in _TAG_RE.findall(segment)))
            text = " ".join(_TAG_RE.sub(" ", segment).split()).strip(" .,;:")
            if len(_content_tokens(text)) < min_tokens:
                continue
            claims.append(ExtractedClaim(text=text, tags=tags, is_advisory=in_advisory))

    return claims

class MultilingualSemanticSimilarity:
    """
    Cross-lingual cosine similarity (Vietnamese claim vs English evidence) with a multilingual encoder.

    The model is loaded once. If it cannot be loaded, calls raise RuntimeError instead of silently
    degrading to lexical overlap, which scores ~0 across languages and fails every grounded answer.
    Embeddings are cached per text because the verifier scores each claim against every evidence sentence.
    """
    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        device: str = "cpu",
        model: Any | None = None,
        max_chars: int = 2000,
        cache_size: int = 4096,
    ):
        self.model_name = model_name
        self.device = device
        self.max_chars = max_chars
        self.cache_size = cache_size
        self._model = model
        self._load_error: Exception | None = None
        self._cache: dict[str, Any] = {}

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        if self._load_error is not None:
            raise RuntimeError(f"Semantic verifier model unavailable: {self._load_error}") from self._load_error
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name, device=self.device)
        except Exception as e:
            self._load_error = e
            raise RuntimeError(f"Semantic verifier model unavailable: {e}") from e
        return self._model
    
    def _embed(self, text: str) -> Any:
        key = text[: self.max_chars]
        vector = self._cache.get(key)
        if vector is None:
            vector = self._get_model().encode([key], normalize_embeddings=True, show_progress_bar=False)[0]
            if len(self._cache) >= self.cache_size:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = vector
        return vector
    
    
    def __call__(self, claim: str, passage: str) -> float:
        if not claim.strip() or not passage.strip():
            return 0.0
        cosine = float((self._embed(claim) * self._embed(passage)).sum())
        return max(0.0, min(1.0, cosine))
        
# =====================================================================
# Verifier
# =====================================================================

class EvidenceVerifier:
    """Đối chiếu claim với evidence và tổng hợp VerificationReportSchema."""

    def __init__(
        self,
        similarity_fn: SimilarityFn | None = None,
        support_threshold: float = DEFAULT_SUPPORT_THRESHOLD,
        min_claim_tokens: int = MIN_CLAIM_TOKENS,
    ):
        """
        Args:
            similarity_fn: Hàm (claim, passage) -> điểm 0..1. Mặc định là lexical_support_score.
            support_threshold: Điểm tối thiểu để một claim được coi là có căn cứ.
            min_claim_tokens: Số token nội dung tối thiểu để một mẩu văn bản được coi là claim.
        """
        self.similarity_fn = similarity_fn or lexical_support_score
        self.support_threshold = support_threshold
        self.min_claim_tokens = min_claim_tokens

    def _best_match(self, claim_text: str, evidence: dict[str, Any]) -> tuple[float, str]:
        """Điểm hỗ trợ cao nhất của evidence cho claim, kèm câu khớp nhất làm snippet."""
        text = evidence.get("text_content") or ""
        if not text.strip():
            return 0.0, ""
        chunk_score = self.similarity_fn(claim_text, text)
        best_sentence, best_sentence_score = "", -1.0
        for sentence in _split_sentences(text):
            score = self.similarity_fn(claim_text, sentence)
            if score > best_sentence_score:
                best_sentence, best_sentence_score = sentence, score
        return max(chunk_score, best_sentence_score), best_sentence or text

    def _verify_claim(
        self,
        claim_id: str,
        claim: ExtractedClaim,
        evidence_by_tag: dict[str, dict[str, Any]],
        warnings: list[str],
    ) -> ClaimVerificationResult:
        cited_tag = "".join(claim.tags) or None

        if claim.is_advisory:
            return ClaimVerificationResult(
                claim_id=claim_id,
                claim_text=claim.text,
                cited_tag=cited_tag,
                status=ClaimStatus.UNVERIFIED,
                confidence_score=0.0,
                explanation="General Knowledge Advisory (HYBRID): không đối chiếu với evidence, không tính vào faithfulness.",
            )

        valid_tags = [t for t in claim.tags if t in evidence_by_tag]
        invalid_tags = [t for t in claim.tags if t not in evidence_by_tag]
        if invalid_tags:
            warnings.append(f"{claim_id}: trích dẫn không tồn tại trong evidence {', '.join(invalid_tags)}")

        if claim.tags and not valid_tags:
            return ClaimVerificationResult(
                claim_id=claim_id,
                claim_text=claim.text,
                cited_tag=cited_tag,
                status=ClaimStatus.UNVERIFIED,
                confidence_score=0.0,
                explanation=f"Thẻ trích dẫn {', '.join(invalid_tags)} không trỏ tới evidence nào.",
            )

        # Có trích dẫn hợp lệ -> chỉ đối chiếu với nguồn được trích (đảm bảo truy vết đúng nguồn).
        # Không trích dẫn -> tìm trong toàn bộ evidence để gợi ý nguồn phù hợp.
        candidate_tags = valid_tags or list(evidence_by_tag)
        best_tag, best_score, best_snippet = None, 0.0, ""
        for tag in candidate_tags:
            score, snippet = self._best_match(claim.text, evidence_by_tag[tag])
            if best_tag is None or score > best_score:
                best_tag, best_score, best_snippet = tag, score, snippet

        score = round(max(0.0, min(1.0, best_score)), 4)
        snippet = _truncate(best_snippet) if best_snippet else None

        if score < self.support_threshold:
            source_desc = f"nguồn {', '.join(valid_tags)}" if valid_tags else "bất kỳ evidence nào"
            if not valid_tags:
                warnings.append(f"{claim_id}: claim không có trích dẫn và không khớp evidence")
            return ClaimVerificationResult(
                claim_id=claim_id,
                claim_text=claim.text,
                cited_tag=cited_tag,
                status=ClaimStatus.UNVERIFIED,
                confidence_score=score,
                matched_snippet=snippet,
                explanation=f"Không tìm thấy căn cứ đủ mạnh trong {source_desc} (score {score:.2f} < {self.support_threshold}).",
            )

        if _has_negation(claim.text) != _has_negation(best_snippet):
            return ClaimVerificationResult(
                claim_id=claim_id,
                claim_text=claim.text,
                cited_tag=cited_tag,
                status=ClaimStatus.CONTRADICTED,
                confidence_score=score,
                matched_snippet=snippet,
                explanation=f"Nội dung khớp với {best_tag} nhưng ngược chiều khẳng định/phủ định.",
            )

        explanation = f"Được chứng minh bởi {best_tag} (score {score:.2f})."
        if not valid_tags:
            warnings.append(f"{claim_id}: claim thiếu thẻ trích dẫn, nguồn gợi ý {best_tag}")
            explanation = f"Khớp với {best_tag} (score {score:.2f}) nhưng câu trả lời thiếu thẻ trích dẫn."
        return ClaimVerificationResult(
            claim_id=claim_id,
            claim_text=claim.text,
            cited_tag=cited_tag,
            status=ClaimStatus.SUPPORTED,
            confidence_score=score,
            matched_snippet=snippet,
            explanation=explanation,
        )

    @staticmethod
    def _status_for(score: float) -> VerificationStatus:
        if score >= PASSED_THRESHOLD:
            return VerificationStatus.PASSED
        if score >= WARNING_THRESHOLD:
            return VerificationStatus.WARNING
        return VerificationStatus.FAILED

    def verify(
        self,
        draft: SynthesizedDraftSchema,
        evidence_list: list[dict[str, Any]],
    ) -> VerificationReportSchema:
        if draft.is_abstained:
            return VerificationReportSchema(
                faithfulness_score=1.0,
                status=VerificationStatus.SKIPPED,
                warnings=[f"Verification skipped: abstention ({draft.abstention_reason or 'no evidence'})."],
            )

        warnings: list[str] = []
        evidence_by_tag = index_evidence_by_tag(evidence_list)
        if not evidence_by_tag:
            warnings.append("Không có evidence để đối chiếu.")

        claims = extract_claims(draft.draft_answer, min_tokens=self.min_claim_tokens)
        # Advisory chỉ được miễn kiểm định khi Synthesizer thực sự chạy HYBRID.
        if draft.generation_mode != "HYBRID":
            for claim in claims:
                claim.is_advisory = False

        results = [
            self._verify_claim(f"claim_{i:03d}", claim, evidence_by_tag, warnings)
            for i, claim in enumerate(claims, start=1)
        ]

        grounded = [r for r, c in zip(results, claims) if not c.is_advisory]
        advisory_count = len(results) - len(grounded)
        supported = sum(r.status == ClaimStatus.SUPPORTED for r in results)
        contradicted = sum(r.status == ClaimStatus.CONTRADICTED for r in results)
        unverified = sum(r.status == ClaimStatus.UNVERIFIED for r in results)

        if advisory_count:
            warnings.append(
                f"{advisory_count} claim thuộc General Knowledge Advisory không được đối chiếu với evidence."
            )

        if grounded:
            score = supported / len(grounded)
            status = self._status_for(score)
        else:
            score = 0.0
            status = VerificationStatus.FAILED
            warnings.append("Không tìm thấy claim nào dựa trên tài liệu để kiểm định.")

        return VerificationReportSchema(
            faithfulness_score=score,
            status=status,
            total_claims=len(results),
            supported_claims=supported,
            contradicted_claims=contradicted,
            unverified_claims=unverified,
            claims_detail=results,
            warnings=warnings,
        )
