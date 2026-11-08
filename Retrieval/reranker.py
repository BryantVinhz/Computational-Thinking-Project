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

DEFAULT_RERANKER_MODEL = "BAAI/bge-m3"
FALLBACK_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
DEFAULT_CONFIDENCE_THRESHOLD = 0.35
ABSTENTION_MESSAGE = "Không tìm thấy thông tin phù hợp trong kho tài liệu bảo mật"


def _sigmoid(x: float) -> float:
    """Quy đổi điểm Logit bất kỳ về xác suất trong khoảng [0, 1] bằng hàm Sigmoid."""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    else:
        z = math.exp(x)
        return z / (1.0 + z)


def _to_chunk_dict(item: Any) -> dict[str, Any]:
    """Chuẩn hóa phần tử đầu vào (dict hoặc dataclass RetrievedChunk) về dạng dict."""
    if isinstance(item, dict):
        return dict(item)
    if hasattr(item, "to_dict"):
        return item.to_dict()
    if hasattr(item, "__dict__"):
        return dict(item.__dict__)
    raise TypeError(f"Kiểu dữ liệu chunk không hợp lệ: {type(item)}")


class CrossEncoderReranker:
    """
    Bước 1: Cross-Encoder Reranker (Chấm điểm lại)
        - Đưa trực tiếp cặp (query, chunk_text) vào mô hình Transformer Cross-Encoder
          (như BAAI/bge-reranker-v2-m3 hoặc cross-encoder/ms-marco-MiniLM-L-6-v2).
        - Cơ chế Cross-Attention cho phép đối chiếu trực tiếp từng từ trong câu truy vấn
          với từng từ trong văn bản chunk.
        - Đầu ra Logits được đưa qua hàm Sigmoid để quy đổi về xác suất trong khoảng [0, 1].

    Bước 2: Lọc ngưỡng tin cậy & Chính sách từ chối (Confidence Thresholding & Abstention Policy)
        - Loại bỏ hoàn toàn mọi chunk có điểm Cross-Encoder < ngưỡng tin cậy tau (ví dụ 0.35 hoặc 0.40).
        - Nếu không có chunk nào vượt qua ngưỡng tau, trả về danh sách rỗng kèm cờ
          `insufficient_evidence: True` để chặn LLM bịa đặt thông tin (Hallucination).
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
        """Lazy-load mô hình CrossEncoder với hàm kích hoạt Sigmoid ở đầu ra."""
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
        Bước 1: Chấm điểm lại từng cặp (query, chunk_text) bằng Cross-Encoder.
        Trả về danh sách các chunk đã được gắn thêm `rerank_score` (trong đoạn [0, 1]).
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
            # Đảm bảo nếu model custom trả về logits ngoài [0, 1] thì vẫn chuẩn hóa qua Sigmoid
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
        Thực hiện trọn vẹn Bước 1 (Cross-Encoder Reranking) và Bước 2 (Confidence Thresholding & Abstention).

        Args:
            query: Câu truy vấn của người dùng.
            candidate_chunks: Danh sách chunk thu được từ bước Dense Retrieval hoặc Hybrid RRF.
            threshold: Ngưỡng tin cậy tau trong khoảng [0, 1] (nếu None sẽ dùng self.threshold).
            top_k: Số lượng chunk tối đa giữ lại sau khi lọc (nếu None sẽ dùng self.top_k).

        Returns:
            RerankResult chứa danh sách chunks vượt ngưỡng, cờ `insufficient_evidence`, và thông báo từ chối (nếu có).
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

        # Bước 1: Chấm điểm bằng Cross-Encoder (đã qua hàm Sigmoid -> [0, 1])
        scored_chunks = self.score_pairs(query, candidate_chunks)

        # Bước 2: Lọc theo ngưỡng tin cậy tau (loại bỏ hoàn toàn chunk có điểm < tau)
        filtered_chunks = [c for c in scored_chunks if c["rerank_score"] >= tau]

        # Sắp xếp giảm dần theo điểm Cross-Encoder
        filtered_chunks.sort(key=lambda x: x["rerank_score"], reverse=True)

        if limit is not None:
            filtered_chunks = filtered_chunks[:limit]

        # Cập nhật thứ hạng mới sau khi rerank
        for new_rank, chunk in enumerate(filtered_chunks, start=1):
            chunk["rerank_rank"] = new_rank
            chunk["rank"] = new_rank

        # Chính sách từ chối an toàn: Nếu không có chunk nào vượt ngưỡng tau
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
        """Nhận trực tiếp ScopedRetrievalPayload và danh sách candidate chunks để Rerank & Lọc ngưỡng."""
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
    Xử lý ở luồng điều phối (Orchestrator):
        - Gọi tầng Rerank & Lọc ngưỡng tin cậy.
        - Nếu nhận được cờ `insufficient_evidence: True`, chặn hoàn toàn việc gọi LLM sinh câu trả lời
          và trả về ngay thông báo từ chối an toàn:
          "Không tìm thấy thông tin phù hợp trong kho tài liệu bảo mật".
        - Nếu đủ bằng chứng (`insufficient_evidence: False`), chuyển danh sách chunk hợp lệ cho LLM.
    """
    rerank_result = reranker.rerank(
        query=query,
        candidate_chunks=candidate_chunks,
        threshold=threshold,
        top_k=top_k,
    )

    # Chốt chặn an toàn: Ngắt luồng trước khi gọi LLM nếu thiếu bằng chứng
    if rerank_result.insufficient_evidence:
        return {
            "query": query,
            "insufficient_evidence": True,
            "threshold": rerank_result.threshold,
            "answer": ABSTENTION_MESSAGE,
            "llm_called": False,
            "chunks": [],
        }

    # Chỉ gọi LLM khi có ít nhất 1 chunk vượt qua ngưỡng tin cậy tau
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

    # Minh họa toàn bộ luồng: Dense Retrieval + RRF -> Cross-Encoder Reranker -> Confidence Thresholding & Abstention
    print("=" * 80)
    print("DEMO: CROSS-ENCODER RERANKER & CONFIDENCE THRESHOLDING (TAU = 0.40)")
    print("=" * 80)

    # 1. Truy xuất các candidate chunks ban đầu từ ChromaDB (Dense Retrieval + RRF)
    dense_retriever = DenseRetriever()
    valid_query = "How to prevent SQL Injection and Broken Access Control in web applications?"
    dense_candidates = dense_retriever.retrieve(query_text=valid_query, top_k=5)
    rrf_candidates = reciprocal_rank_fusion(bm25_results=[], dense_results=dense_candidates, k=60, top_k=5)

    # 2. Khởi tạo Cross-Encoder Reranker
    # Sử dụng cross-encoder/ms-marco-MiniLM-L-6-v2 để demo nhanh (hoặc BAAI/bge-reranker-v2-m3)
    reranker = CrossEncoderReranker(
        model_name=DEFAULT_RERANKER_MODEL,
        threshold=0.40,
        top_k=3,
    )

    print(f"\n[TRƯỜNG HỢP 1] Câu hỏi có bằng chứng trong kho tài liệu:")
    print(f"  Query: '{valid_query}'")
    response_1 = orchestrate_security_qa(
        query=valid_query,
        candidate_chunks=rrf_candidates,
        reranker=reranker,
    )
    print(f"  -> insufficient_evidence : {response_1['insufficient_evidence']}")
    print(f"  -> llm_called            : {response_1['llm_called']}")
    print(f"  -> Số chunk vượt ngưỡng  : {len(response_1['chunks'])}/{len(rrf_candidates)}")
    for c in response_1["chunks"]:
        print(
            f"     [Rerank #{c['rank']}] ID: {c['id']} | "
            f"Cross-Encoder Score (Sigmoid): {c['rerank_score']:.4f} | "
            f"Initial RRF Rank: {c['initial_rank']}"
        )
    print(f"  -> Phản hồi hệ thống     : {response_1['answer']}")

    print(f"\n[TRƯỜNG HỢP 2] Câu hỏi ngoài phạm vi / không đủ bằng chứng tin cậy:")
    unrelated_query = "Công thức nấu món phở bò truyền thống Hà Nội và cách chọn gia vị?"
    response_2 = orchestrate_security_qa(
        query=unrelated_query,
        candidate_chunks=rrf_candidates,
        reranker=reranker,
    )
    print(f"  Query: '{unrelated_query}'")
    print(f"  -> insufficient_evidence : {response_2['insufficient_evidence']}")
    print(f"  -> llm_called            : {response_2['llm_called']}")
    print(f"  -> Số chunk vượt ngưỡng  : {len(response_2['chunks'])}/{len(rrf_candidates)}")
    print(f"  -> Phản hồi hệ thống     : {response_2['answer']}")
