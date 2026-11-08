import json
import logging
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

# Ensure modules in Retrieval and Document Ingestion and Parsing can be imported
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
METADATA_DIR = PROJECT_ROOT / "Document Ingestion and Parsing" / "Metadata"
PIPELINE_DIR = PROJECT_ROOT / "Document Ingestion and Parsing"

for _dir in (CURRENT_DIR, METADATA_DIR, PIPELINE_DIR):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

from schema import RetrievalScope, RetrievedChunk, ScopedRetrievalPayload
from vector_store import (
    CHROMA_DB_DIR,
    COLLECTION_NAME,
    MODEL_NAME,
    get_chroma_collection,
)

logger = logging.getLogger(__name__)


class DenseRetriever:
    """
    Dense Retrieval using ChromaDB and the embedding model configured in Stage 1.
    ChromaDB automatically embeds the query (via SentenceTransformerEmbeddingFunction)
    or accepts a precomputed query_vector and performs semantic search using cosine distance (HNSW).
    """

    def __init__(
        self,
        db_path: Path | str = CHROMA_DB_DIR,
        collection_name: str = COLLECTION_NAME,
        model_name: str = MODEL_NAME,
        device: str = "cpu",
        collection: Any | None = None,
    ):
        self.db_path = Path(db_path)
        self.collection_name = collection_name
        self.model_name = model_name
        self.device = device
        self._collection = collection

    @property
    def collection(self):
        """Initialize or return the ChromaDB collection (lazy-loaded once)."""
        if self._collection is None:
            self._collection = get_chroma_collection(
                db_path=self.db_path,
                collection_name=self.collection_name,
                model_name=self.model_name,
                device=self.device,
            )
        return self._collection

    @staticmethod
    def _normalize_metadata(raw_meta: dict[str, Any] | None) -> dict[str, Any]:
        """Restore heading_path from a JSON string in ChromaDB back to list[str]."""
        meta_copy = dict(raw_meta or {})
        hp = meta_copy.get("heading_path", [])
        if isinstance(hp, str):
            try:
                parsed = json.loads(hp)
                meta_copy["heading_path"] = parsed if isinstance(parsed, list) else [hp]
            except json.JSONDecodeError:
                meta_copy["heading_path"] = [hp] if hp else []

        return {
            "document_id": meta_copy.get("document_id", ""),
            "version_id": meta_copy.get("version_id", ""),
            "title": meta_copy.get("title", ""),
            "file_format": meta_copy.get("file_format", ""),
            "chunk_idx": meta_copy.get("chunk_idx", 0),
            "chunk_type": meta_copy.get("chunk_type", "text"),
            "token_count": meta_copy.get("token_count", 0),
            "chunk_strategy": meta_copy.get("chunk_strategy", "structure_aware"),
            "heading_path": meta_copy.get("heading_path", []),
        }

    def retrieve(
        self,
        query_text: str | None = None,
        query_vector: list[float] | None = None,
        chroma_filter: dict[str, Any] | None = None,
        top_k: int = 10,
        fallback_unfiltered: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Retrieve the top_k semantically closest chunks from ChromaDB.

        Args:
            query_text: Text query (ChromaDB will automatically invoke the configured embedding function).
            query_vector: Precomputed query embedding vector (if computed via QueryVectorizer).
            chroma_filter: Metadata filter (ChromaDB where clause from ScopeBuilder).
            top_k: Maximum number of results to retrieve.
            fallback_unfiltered: Automatically search the entire DB if strict filtering returns 0 results.

        Returns:
            List of ranked chunks (rank = 1, 2, ...) with cosine similarity scores.
        """
        if not query_vector and (not query_text or not query_text.strip()):
            raise ValueError("Cần cung cấp 'query_text' hoặc 'query_vector' để thực hiện Dense Retrieval.")

        query_kwargs: dict[str, Any] = {
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }

        if query_vector:
            query_kwargs["query_embeddings"] = [query_vector]
        else:
            query_kwargs["query_texts"] = [query_text.strip()]

        if chroma_filter:
            query_kwargs["where"] = chroma_filter

        raw_results = self.collection.query(**query_kwargs)
        ids_list = (raw_results.get("ids") or [[]])[0]

        # Fall back to unfiltered search if scoped filtering returns no results and relaxation is enabled
        if not ids_list and chroma_filter and fallback_unfiltered:
            logger.warning(
                "Dense Retrieval với chroma_filter=%s không có kết quả. Tự động nới lỏng bộ lọc.",
                chroma_filter,
            )
            query_kwargs.pop("where", None)
            raw_results = self.collection.query(**query_kwargs)
            ids_list = (raw_results.get("ids") or [[]])[0]

        docs_list = (raw_results.get("documents") or [[]])[0]
        metas_list = (raw_results.get("metadatas") or [[]])[0]
        dists_list = (raw_results.get("distances") or [[]])[0]

        results: list[dict[str, Any]] = []
        for rank, (c_id, text, meta, dist) in enumerate(
            zip(ids_list, docs_list, metas_list, dists_list), start=1
        ):
            # ChromaDB uses "hnsw:space": "cosine" => distance = 1 - cosine_similarity
            cosine_similarity = round(max(0.0, 1.0 - float(dist)), 6)
            normalized_meta = self._normalize_metadata(meta)

            results.append(
                {
                    "id": c_id,
                    "chunk_id": c_id,
                    "text": text,
                    "metadata": normalized_meta,
                    "score": cosine_similarity,
                    "dense_score": cosine_similarity,
                    "distance": round(float(dist), 6),
                    "rank": rank,
                    "dense_rank": rank,
                    "source": "dense",
                }
            )

        return results

    def retrieve_from_payload(
        self,
        payload: ScopedRetrievalPayload,
        top_k: int = 10,
        fallback_unfiltered=None,
    ) -> list[dict[str, Any]]:
        """
        Accept a ScopedRetrievalPayload from Scope Building and execute Dense Retrieval.
        """
        chroma_filter = payload.scope.chroma_filter if payload.scope else None
        # Scope filters are hard constraints: never widen to the whole corpus behind the caller's back.
        if fallback_unfiltered is None:
            fallback_unfiltered = False
        return self.retrieve(
            query_text=payload.original_query,
            query_vector=payload.query_vector if payload.query_vector else None,
            chroma_filter=chroma_filter,
            top_k=top_k,
            fallback_unfiltered=fallback_unfiltered,
        )


def dense_search(
    query_text: str | None = None,
    query_vector: list[float] | None = None,
    chroma_filter: dict[str, Any] | None = None,
    top_k: int = 10,
    collection: Any | None = None,
) -> list[dict[str, Any]]:
    """
    Convenience helper to invoke Dense Retrieval directly with ChromaDB.
    """
    retriever = DenseRetriever(collection=collection)
    return retriever.retrieve(
        query_text=query_text,
        query_vector=query_vector,
        chroma_filter=chroma_filter,
        top_k=top_k,
    )
