import logging
import math
import sys
from pathlib import Path
from typing import Any
from collections.abc import Callable
from schema_reranking import RerankResult
from schema import ScopedRetrievalPayload

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
RETRIEVAL_DIR = PROJECT_ROOT / "Retrieval"

for _dir in (PROJECT_ROOT, CURRENT_DIR, RETRIEVAL_DIR):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))


logger = logging.getLogger(__name__)

DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
FALLBACK_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
DEFAULT_CONFIDENCE_THRESHOLD = 0.35
ABSTENTION_MESSAGE = "Không tìm thấy thông tin phù hợp trong kho tài liệu bảo mật"


def _sigmoid(x: float) -> float:
    """Map arbitrary logit scores into probabilities within the range [0, 1] via the Sigmoid function"""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    else:
        z = math.exp(x)
        return z / (1.0 + z)


def _to_chunk_dict(item: Any) -> dict[str, Any]:
    """Normalize an input element (either a dict or a RetrievedChunk dataclass instance) to a standard dict"""
    if isinstance(item, dict):
        return dict(item)
    if hasattr(item, "to_dict"):
        return item.to_dict()
    if hasattr(item, "__dict__"):
        return dict(item.__dict__)
    raise TypeError(f"Kiểu dữ liệu chunk không hợp lệ: {type(item)}")


class CrossEncoderReranker:
    """
    Step 1: Cross-Encoder Reranker (Rescoring)
        - Directly feed the (query, chunk_text) pair into a Transformer Cross-Encoder model
        (e.g., BAAI/bge-reranker-v2-m3 or cross-encoder/ms-marco-MiniLM-L-6-v2).
        - The cross-attention mechanism enables full-token interaction between every query token
        and every token in the chunk text.
        - Raw output logits are passed through a Sigmoid function to map scores into probabilities within [0, 1].

    Step 2: Confidence Thresholding & Abstention Policy
        - Completely filter out any chunk with a Cross-Encoder score below the confidence threshold tau (e.g., 0.35 or 0.40).
        - If no chunk exceeds the threshold tau, return an empty list with the flag
        `insufficient_evidence: True` to prevent LLM hallucinations.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_RERANKER_MODEL,
        threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        top_k: int | None = 5,
        device: str | None = None,
        model: Any | None = None,
    ):
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"Ngưỡng tin cậy (threshold) phải nằm trong khoảng [0, 1], nhận được: {threshold}")

        self.model_name = model_name
        self.threshold = threshold
        self.top_k = top_k
        self.device = device
        self._model = model

    def _get_model(self) -> Any:
        """Lazy-load the CrossEncoder model with a Sigmoid activation function at the output"""
        if self._model is None:
            try:
                import torch
                from sentence_transformers import CrossEncoder
            except ImportError as e:
                raise ImportError(
                    "Vui lòng cài đặt sentence-transformers và torch: pip install sentence-transformers torch"
                ) from e

            resolved_device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
            logger.info("Đang tải mô hình Cross-Encoder '%s' trên thiết bị '%s'...", self.model_name, resolved_device)
            self._model = CrossEncoder(
                self.model_name,
                device=resolved_device,
                activation_fn=torch.nn.Sigmoid(),
            )
        return self._model

    def score_pairs(self, query: str, candidate_chunks: list[dict[str, Any] | Any]) -> list[dict[str, Any]]:
        """
        Rescore each (query, chunk_text) pair using a Cross-Encoder and 
        return the chunks with an added rerank_score field in the range [0, 1]
        """
        if not query or not query.strip() or not candidate_chunks:
            return []

        normalized_chunks = [_to_chunk_dict(c) for c in candidate_chunks]
        pairs = [
            [query.strip(), str(c.get("text") or c.get("content") or c.get("text_content") or "")]
            for c in normalized_chunks
        ]

        model = self._get_model()
        raw_scores = model.predict(pairs, show_progress_bar=False)
        if hasattr(raw_scores, "tolist"):
            raw_scores = raw_scores.tolist()

        scored_chunks: list[dict[str, Any]] = []
        for idx, (chunk, score_val) in enumerate(zip(normalized_chunks, raw_scores), start=1):
            prob = float(score_val)
            # Ensure custom model logits outside [0, 1] are still normalized via Sigmoid
            if prob < 0.0 or prob > 1.0:
                prob = _sigmoid(prob)

            chunk_copy = dict(chunk)
            chunk_copy["initial_rank"] = chunk_copy.get("rank", idx)
            chunk_copy["initial_score"] = chunk_copy.get("score", 0.0)
            chunk_copy["rerank_score"] = round(prob, 6)
            chunk_copy["score"] = chunk_copy["rerank_score"]
            scored_chunks.append(chunk_copy)

        return scored_chunks

    def rerank(
        self,
        query: str,
        candidate_chunks: list[dict[str, Any] | Any],
        threshold: float | None = None,
        top_k: int | None = None,
    ) -> RerankResult:
        """
        Execute both Step 1 (Cross-Encoder Reranking) and Step 2 (Confidence Thresholding & Abstention).

        Args:
            query: User query string.
            candidate_chunks: List of candidate chunks retrieved from Dense Retrieval or Hybrid RRF.
            threshold: Confidence threshold tau in the range [0, 1] (defaults to self.threshold if None).
            top_k: Maximum number of chunks to retain after filtering (defaults to self.top_k if None).

        Returns:
            RerankResult containing the list of chunks passing the threshold, the `insufficient_evidence` flag, and an abstention message (if applicable).
        """
        tau = self.threshold if threshold is None else threshold
        limit = self.top_k if top_k is None else top_k

        if not candidate_chunks or not query or not query.strip():
            return RerankResult(
                query=query or "",
                chunks=[],
                insufficient_evidence=True,
                threshold=tau,
                abstention_message=ABSTENTION_MESSAGE,
                total_candidates=len(candidate_chunks) if candidate_chunks else 0,
                passed_count=0,
            )

        # Step 1: Rescore using Cross-Encoder (mapped to [0, 1] via Sigmoid)
        scored_chunks = self.score_pairs(query, candidate_chunks)

        # Step 2: Filter by confidence threshold tau (discard any chunk with score < tau)
        filtered_chunks = [c for c in scored_chunks if c["rerank_score"] >= tau]

        # Sort in descending order by Cross-Encoder score
        filtered_chunks.sort(key=lambda x: x["rerank_score"], reverse=True)

        if limit is not None:
            filtered_chunks = filtered_chunks[:limit]

        # Update new ranks post-reranking
        for new_rank, chunk in enumerate(filtered_chunks, start=1):
            chunk["rerank_rank"] = new_rank
            chunk["rank"] = new_rank

        # Safe abstention policy: If no chunks pass the threshold tau
        if not filtered_chunks:
            logger.warning(
                "Abstention triggered cho query='%s': 0/%d chunks đạt ngưỡng tin cậy tau=%.2f.",
                query,
                len(scored_chunks),
                tau,
            )
            return RerankResult(
                query=query,
                chunks=[],
                insufficient_evidence=True,
                threshold=tau,
                abstention_message=ABSTENTION_MESSAGE,
                total_candidates=len(scored_chunks),
                passed_count=0,
            )

        return RerankResult(
            query=query,
            chunks=filtered_chunks,
            insufficient_evidence=False,
            threshold=tau,
            abstention_message=None,
            total_candidates=len(scored_chunks),
            passed_count=len(filtered_chunks),
        )

    def rerank_from_payload(
        self,
        payload: ScopedRetrievalPayload,
        candidate_chunks: list[dict[str, Any] | Any],
        threshold: float | None = None,
        top_k: int | None = None,
    ) -> RerankResult:
        """Accept ScopedRetrievalPayload and a list of candidate chunks to perform Cross-Encoder reranking and confidence thresholding"""
        return self.rerank(
            query=payload.original_query,
            candidate_chunks=candidate_chunks,
            threshold=threshold,
            top_k=top_k,
        )


def orchestrate_security_qa(
    query: str,
    candidate_chunks: list[dict[str, Any] | Any],
    reranker: CrossEncoderReranker,
    llm_generator: Callable[[str, list[dict[str, Any]]], str] | None = None,
    threshold: float | None = None,
    top_k: int | None = None,
) -> dict[str, Any]:
    """
    Orchestration workflow handling:
        - Invoke the Reranking & Confidence Thresholding layer.
        - If `insufficient_evidence: True` is received, completely block the LLM generation call
        and immediately return a safe abstention message:
        "No matching information found in the security document repository".
        - If evidence is sufficient (`insufficient_evidence: False`), pass the valid chunks to the LLM.
    """
    rerank_result = reranker.rerank(
        query=query,
        candidate_chunks=candidate_chunks,
        threshold=threshold,
        top_k=top_k,
    )

    # Safety guardrail: Short-circuit the workflow before invoking the LLM if evidence is insufficient
    if rerank_result.insufficient_evidence:
        return {
            "query": query,
            "insufficient_evidence": True,
            "threshold": rerank_result.threshold,
            "answer": ABSTENTION_MESSAGE,
            "llm_called": False,
            "chunks": [],
        }

    # Only invoke the LLM when at least one chunk exceeds the confidence threshold tau
    if llm_generator is not None:
        answer = llm_generator(query, rerank_result.chunks)
    else:
        top_chunk = rerank_result.chunks[0]
        answer = (
            f"[Đã xác thực bằng chứng bảo mật - Top Chunk ID: {top_chunk.get('id')} "
            f"(Score: {top_chunk.get('rerank_score'):.4f})]"
        )

    return {
        "query": query,
        "insufficient_evidence": False,
        "threshold": rerank_result.threshold,
        "answer": answer,
        "llm_called": True,
        "chunks": rerank_result.chunks,
    }


if __name__ == "__main__":
    from dense_retrieval import DenseRetriever
    from rrf import reciprocal_rank_fusion

    # End-to-end pipeline walkthrough: Dense Retrieval + RRF -> Cross-Encoder Reranker -> Confidence Thresholding & Abstention
    print("DEMO: CROSS-ENCODER RERANKER & CONFIDENCE THRESHOLDING (TAU = 0.40)")
    print("=" * 80)

    # 1. Retrieve initial candidate chunks from ChromaDB (Dense Retrieval + RRF)
    dense_retriever = DenseRetriever()
    valid_query = "How to prevent SQL Injection and Broken Access Control in web applications?"
    dense_candidates = dense_retriever.retrieve(query_text=valid_query, top_k=5)
    rrf_candidates = reciprocal_rank_fusion(bm25_results=[], dense_results=dense_candidates, k=60, top_k=5)

    # 2. Initialize the Cross-Encoder Reranker
    reranker = CrossEncoderReranker(
        model_name=DEFAULT_RERANKER_MODEL,
        threshold=0.40,
        top_k=3,
    )

    print(f"\n[CASE 1] In-Scope Query with Sufficient Documented Evidence:")
    print(f"  Query: '{valid_query}'")
    response_1 = orchestrate_security_qa(
        query=valid_query,
        candidate_chunks=rrf_candidates,
        reranker=reranker,
    )
    print(f"  -> insufficient_evidence : {response_1['insufficient_evidence']}")
    print(f"  -> llm_called            : {response_1['llm_called']}")
    print(f"  -> Chunks exceeding threshold  : {len(response_1['chunks'])}/{len(rrf_candidates)}")
    for c in response_1["chunks"]:
        print(
            f"     [Rerank #{c['rank']}] ID: {c['id']} | "
            f"Cross-Encoder Score (Sigmoid): {c['rerank_score']:.4f} | "
            f"Initial RRF Rank: {c['initial_rank']}"
        )
    print(f"  -> System Response     : {response_1['answer']}")

    print(f"\n[CASE 2] Out-of-Scope Query / Insufficient Confidence Evidence:")
    unrelated_query = "Traditional Hanoi beef pho recipe and how to choose the spices?"
    response_2 = orchestrate_security_qa(
        query=unrelated_query,
        candidate_chunks=rrf_candidates,
        reranker=reranker,
    )
    print(f"  Query: '{unrelated_query}'")
    print(f"  -> insufficient_evidence : {response_2['insufficient_evidence']}")
    print(f"  -> llm_called            : {response_2['llm_called']}")
    print(f"  -> Chunks exceeding threshold  : {len(response_2['chunks'])}/{len(rrf_candidates)}")
    print(f"  -> System Response     : {response_2['answer']}")
