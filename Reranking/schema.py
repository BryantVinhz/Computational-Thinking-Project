from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RerankedChunk:
    """
    Standardized chunk representation after Cross-Encoder Reranking.
    """
    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    rerank_score: float = 0.0
    rank: int = 0
    rerank_rank: int = 0
    initial_rank: int | None = None
    initial_score: float | None = None
    source: str = "reranker"

    @property
    def chunk_id(self) -> str:
        return self.id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RerankResult:
    """
    Output packet after Cross-Encoder Reranking and Confidence Thresholding.
    Attributes:
        query: The query string used for reranking
        chunks: List of chunks that passed the confidence threshold (empty if insufficient_evidence is True)
        insufficient_evidence: True if no chunk achieved rerank_score >= threshold
        threshold: Confidence threshold tau in [0, 1]
        abstention_message: Safe refusal message when insufficient_evidence is True
        total_candidates: Number of candidate chunks before filtering
        passed_count: Number of chunks remaining after threshold filtering
    """
    query: str
    chunks: list[dict[str, Any]] = field(default_factory=list)
    insufficient_evidence: bool = True
    threshold: float = 0.35
    abstention_message: str | None = None
    total_candidates: int = 0
    passed_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
