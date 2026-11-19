import logging
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from langchain_community.vectorstores.utils import maximal_marginal_relevance

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from query_vectorizer import QueryVectorizer

logger = logging.getLogger(__name__)


class LangChainMMRSelector:
    """
    Use maximal_marginal_relevance function of LangChain
    """

    def __init__(self, vectorizer: QueryVectorizer | None = None, default_lambda: float = 0.80):
        self.vectorizer = vectorizer or QueryVectorizer()
        self.default_lambda = default_lambda
        self._embedding_cache: dict[str, list[float]] = {}

    def _get_chunk_embeddings(self, candidates: list[dict[str, Any]]) -> np.ndarray:
        """Return 2D array of candidate vectors, reusing stored ChromaDB embeddings or cache before encoding."""
        resolved: list[list[float] | None] = [None] * len(candidates)
        missing_indices: list[int] = []
        missing_texts: list[str] = []

        for idx, c in enumerate(candidates):
            emb = c.get("embedding")
            if emb is not None:
                resolved[idx] = emb
                continue
            cache_key = str(c.get("chunk_id") or c.get("id") or "") or (c.get("text") or c.get("text_content") or "")
            if cache_key and cache_key in self._embedding_cache:
                resolved[idx] = self._embedding_cache[cache_key]
            else:
                missing_indices.append(idx)
                missing_texts.append(c.get("text") or c.get("text_content") or "")

        if missing_texts:
            encoded = self.vectorizer.vectorize_passages(missing_texts)
            for idx, vec in zip(missing_indices, encoded):
                resolved[idx] = vec
                c = candidates[idx]
                cache_key = str(c.get("chunk_id") or c.get("id") or "") or (c.get("text") or c.get("text_content") or "")
                if cache_key:
                    self._embedding_cache[cache_key] = vec

        return np.array(resolved, dtype=np.float32)

    def select(self, candidates: list[dict[str, Any]], query_vector: list[float] | None = None,
               original_query: str | None = None, top_k: int = 5, lambda_param: float | None = None) -> list[dict[str, Any]]:
        """
        Select top_k diverse evidence chunks using LangChain MMR

        Args:
            candidates: List of candidate chunks from Reranker or RRF
            query_vector: Query embedding vector (retrieved from ScopedRetrievalPayload.query_vector)
            original_query: Original raw text query 
            top_k: Number of evidence chunks to retain
            lambda_param: Trade-off parameter balancing Relevance and Diversity (0.0 to 1.0)
        """
        if not candidates:
            return []

        if len(candidates) <= top_k:
            cleaned = []
            for rank, c in enumerate(candidates, start=1):
                copy_c = dict(c)
                copy_c.pop("embedding", None)
                copy_c["rank"] = rank
                copy_c["mmr_rank"] = rank
                cleaned.append(copy_c)
            return cleaned

        lam = self.default_lambda if lambda_param is None else lambda_param

        # Prepare query vector (1D array)
        if query_vector is not None:
            q_vec = np.array(query_vector, dtype=np.float32)
        elif original_query:
            q_vec = np.array(self.vectorizer.vectorize(original_query), dtype=np.float32)
        else:
            raise ValueError("Either 'query_vector' or 'original_query' must be provided to compute MMR")

        # Obtain candidate vectors (reusing ChromaDB embeddings or cache)
        chunk_vecs = self._get_chunk_embeddings(candidates)

        selected_indices = maximal_marginal_relevance(
            query_embedding=q_vec,
            embedding_list=chunk_vecs,
            lambda_mult=lam,
            k=top_k,
        )

        # Preserve Cross-Encoder relevance ordering among the MMR-selected subset when rerank_score is present
        if any("rerank_score" in candidates[i] for i in selected_indices):
            selected_indices = sorted(
                selected_indices,
                key=lambda i: float(candidates[i].get("rerank_score", candidates[i].get("score", 0.0))),
                reverse=True,
            )

        selected_chunks: list[dict[str, Any]] = []
        for rank, idx in enumerate(selected_indices, start=1):
            chunk = dict(candidates[idx])
            chunk.pop("embedding", None)
            chunk["rank"] = rank
            chunk["mmr_rank"] = rank
            selected_chunks.append(chunk)

        return selected_chunks