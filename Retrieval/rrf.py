import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from dense_retrieval import DenseRetriever
from schema import ScopedRetrievalPayload


def _to_dict(item: Any) -> dict[str, Any]:
    """Chuyển đổi phần tử kết quả (dict hoặc dataclass) về dạng dict chuẩn."""
    if isinstance(item, dict):
        return dict(item)
    if hasattr(item, "to_dict"):
        return item.to_dict()
    if hasattr(item, "__dict__"):
        return dict(item.__dict__)
    raise TypeError(f"Kiểu dữ liệu kết quả không hợp lệ: {type(item)}")


def reciprocal_rank_fusion(
    bm25_results: list[dict[str, Any] | Any],
    dense_results: list[dict[str, Any] | Any],
    k: int = 60,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """
    Hướng 3: Thuật toán Reciprocal Rank Fusion (RRF) gộp kết quả từ BM25 (Hướng 1)
    và Dense Retrieval (Hướng 2).

    Công thức chuẩn:
        Score(d) = sum( 1 / (k + rank) )   với k mặc định = 60, rank bắt đầu từ 1.

    Args:
        bm25_results: Danh sách kết quả từ thuật toán BM25 (đã xếp hạng từ cao xuống thấp).
        dense_results: Danh sách kết quả từ thuật toán Dense Retrieval (ChromaDB).
        k: Hằng số làm mượt thứ hạng (thường lấy bằng 60).
        top_k: Số lượng kết quả cuối cùng cần lấy sau khi gộp (None = lấy toàn bộ).

    Returns:
        Danh sách kết quả đã được tính lại điểm RRF và sắp xếp giảm dần theo Score.
    """
    fused_records: dict[str, dict[str, Any]] = {}

    # 1. Cộng điểm RRF từ danh sách BM25 (Hướng 1)
    for idx, raw_item in enumerate(bm25_results or [], start=1):
        item = _to_dict(raw_item)
        chunk_id = item.get("id") or item.get("chunk_id")
        if not chunk_id:
            continue

        rank = int(item.get("rank") or idx)
        rrf_contrib = 1.0 / (k + rank)

        if chunk_id not in fused_records:
            fused_records[chunk_id] = {
                "id": chunk_id,
                "chunk_id": chunk_id,
                "text": item.get("text") or item.get("text_content") or "",
                "metadata": dict(item.get("metadata") or {}),
                "score": 0.0,
                "rrf_score": 0.0,
                "bm25_rank": rank,
                "dense_rank": None,
                "bm25_score": item.get("bm25_score", item.get("score")),
                "dense_score": None,
                "source": "bm25",
            }
        else:
            fused_records[chunk_id]["bm25_rank"] = rank
            fused_records[chunk_id]["bm25_score"] = item.get("bm25_score", item.get("score"))

        fused_records[chunk_id]["score"] += rrf_contrib
        fused_records[chunk_id]["rrf_score"] += rrf_contrib

    # 2. Cộng điểm RRF từ danh sách Dense Retrieval - ChromaDB (Hướng 2)
    for idx, raw_item in enumerate(dense_results or [], start=1):
        item = _to_dict(raw_item)
        chunk_id = item.get("id") or item.get("chunk_id")
        if not chunk_id:
            continue

        rank = int(item.get("rank") or idx)
        rrf_contrib = 1.0 / (k + rank)

        if chunk_id not in fused_records:
            fused_records[chunk_id] = {
                "id": chunk_id,
                "chunk_id": chunk_id,
                "text": item.get("text") or item.get("text_content") or "",
                "metadata": dict(item.get("metadata") or {}),
                "score": 0.0,
                "rrf_score": 0.0,
                "bm25_rank": None,
                "dense_rank": rank,
                "bm25_score": None,
                "dense_score": item.get("dense_score", item.get("score")),
                "source": "dense",
            }
        else:
            # Nếu chunk xuất hiện ở cả BM25 và Dense Retrieval -> gộp thông tin & đánh dấu hybrid
            rec = fused_records[chunk_id]
            rec["dense_rank"] = rank
            rec["dense_score"] = item.get("dense_score", item.get("score"))
            rec["source"] = "hybrid (bm25 + dense)"
            if not rec["text"]:
                rec["text"] = item.get("text") or item.get("text_content") or ""
            # Ưu tiên bổ sung metadata đầy đủ từ ChromaDB nếu BM25 thiếu
            dense_meta = item.get("metadata") or {}
            for mk, mv in dense_meta.items():
                if mk not in rec["metadata"] or not rec["metadata"][mk]:
                    rec["metadata"][mk] = mv

        fused_records[chunk_id]["score"] += rrf_contrib
        fused_records[chunk_id]["rrf_score"] += rrf_contrib

    # 3. Sắp xếp giảm dần theo điểm RRF (Score = sum(1 / (k + rank)))
    sorted_results = sorted(
        fused_records.values(),
        key=lambda x: x["score"],
        reverse=True,
    )

    if top_k is not None:
        sorted_results = sorted_results[:top_k]

    # 4. Cập nhật lại thứ hạng cuối cùng (rank = 1, 2, ...) và làm tròn điểm
    for final_rank, record in enumerate(sorted_results, start=1):
        record["rank"] = final_rank
        record["score"] = round(record["score"], 6)
        record["rrf_score"] = record["score"]

    return sorted_results


class HybridRRFusion:
    """
    Lớp tiện ích kết hợp trực tiếp Hướng 2 (Dense Retrieval với ChromaDB)
    và Hướng 3 (RRF gộp cùng kết quả BM25 từ Hướng 1).
    """

    def __init__(self, dense_retriever: DenseRetriever | None = None, k: int = 60):
        self.dense_retriever = dense_retriever or DenseRetriever()
        self.k = k

    def retrieve_and_fuse(
        self,
        bm25_results: list[dict[str, Any] | Any],
        query_text: str | None = None,
        query_vector: list[float] | None = None,
        chroma_filter: dict[str, Any] | None = None,
        payload: ScopedRetrievalPayload | None = None,
        dense_top_k: int = 20,
        final_top_k: int = 10,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Thực thi Hướng 2 (Dense Retrieval) rồi gộp với kết quả Hướng 1 (BM25) bằng RRF (Hướng 3).
        Trả về đầy đủ kết quả của từng hướng và kết quả cuối cùng sau RRF.
        """
        if payload is not None:
            dense_results = self.dense_retriever.retrieve_from_payload(
                payload=payload,
                top_k=dense_top_k,
            )
        else:
            dense_results = self.dense_retriever.retrieve(
                query_text=query_text,
                query_vector=query_vector,
                chroma_filter=chroma_filter,
                top_k=dense_top_k,
            )

        rrf_results = reciprocal_rank_fusion(
            bm25_results=bm25_results,
            dense_results=dense_results,
            k=self.k,
            top_k=final_top_k,
        )

        return {
            "bm25_results": bm25_results,
            "dense_results": dense_results,
            "rrf_results": rrf_results,
        }


