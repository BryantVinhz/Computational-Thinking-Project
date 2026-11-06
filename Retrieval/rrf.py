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
    """Convert a result element (dict or dataclass) into a standard dict."""
    if isinstance(item, dict):
        return dict(item)
    if hasattr(item, "to_dict"):
        return item.to_dict()
    if hasattr(item, "__dict__"):
        return dict(item.__dict__)
    raise TypeError(f"Invalid result data type: {type(item)}")


def reciprocal_rank_fusion(
    bm25_results: list[dict[str, Any] | Any],
    dense_results: list[dict[str, Any] | Any],
    k: int = 60,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """
    Direction 3: Reciprocal Rank Fusion (RRF) algorithm merging results from BM25 (Direction 1)
    and Dense Retrieval (Direction 2).

    Standard formula:
        Score(d) = sum( 1 / (k + rank) )   with default k = 60, rank starting at 1.

    Args:
        bm25_results: Ranked result list from BM25 (ordered from highest to lowest).
        dense_results: Ranked result list from Dense Retrieval (ChromaDB).
        k: Rank smoothing constant (typically set to 60).
        top_k: Number of final fused results to return (None = return all).

    Returns:
        List of results with recomputed RRF scores, sorted in descending order by Score.
    """
    fused_records: dict[str, dict[str, Any]] = {}

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
            rec = fused_records[chunk_id]
            rec["dense_rank"] = rank
            rec["dense_score"] = item.get("dense_score", item.get("score"))
            rec["source"] = "hybrid (bm25 + dense)"
            if not rec["text"]:
                rec["text"] = item.get("text") or item.get("text_content") or ""
            dense_meta = item.get("metadata") or {}
            for mk, mv in dense_meta.items():
                if mk not in rec["metadata"] or not rec["metadata"][mk]:
                    rec["metadata"][mk] = mv

        fused_records[chunk_id]["score"] += rrf_contrib
        fused_records[chunk_id]["rrf_score"] += rrf_contrib

    sorted_results = sorted(
        fused_records.values(),
        key=lambda x: x["score"],
        reverse=True,
    )

    if top_k is not None:
        sorted_results = sorted_results[:top_k]

    for final_rank, record in enumerate(sorted_results, start=1):
        record["rank"] = final_rank
        record["score"] = round(record["score"], 6)
        record["rrf_score"] = record["score"]

    return sorted_results


class HybridRRFusion:
    """
    Utility class directly combining Direction 2 (Dense Retrieval with ChromaDB)
    and Direction 3 (RRF fusion with BM25 results from Direction 1).
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
        Execute Direction 2 (Dense Retrieval) and fuse with Direction 1 (BM25) results using RRF (Direction 3).
        Returns complete results from each retrieval direction as well as the final RRF-fused results.
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
