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
            return candidates

        lam = self.default_lambda if lambda_param is None else lambda_param

        if query_vector is not None:
            q_vec = np.array(query_vector, dtype=np.float32)
        elif original_query:
            q_vec = np.array(self.vectorizer.vectorize(original_query), dtype=np.float32)
        else:
            raise ValueError("Either 'query_vector' or 'original_query' must be provided to compute MMR")

        chunk_texts = [c.get("text") or c.get("text_content") or "" for c in candidates]
        raw_chunk_vectors = self.vectorizer.vectorize_batch(chunk_texts)
        chunk_vecs = np.array(raw_chunk_vectors, dtype=np.float32)

        selected_indices = maximal_marginal_relevance(
            query_embedding=q_vec,
            embedding_list=chunk_vecs,
            lambda_mult=lam,
            k=top_k,
        )

        selected_chunks: list[dict[str, Any]] = []
        for rank, idx in enumerate(selected_indices, start=1):
            chunk = dict(candidates[idx])
            chunk["rank"] = rank
            chunk["mmr_rank"] = rank
            selected_chunks.append(chunk)

        return selected_chunks
