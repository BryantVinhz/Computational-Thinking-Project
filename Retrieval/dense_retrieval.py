import json
import logging
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

# Đảm bảo import được các module trong Retrieval và Document Ingestion and Parsing
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
    Hướng 2: Dense Retrieval sử dụng ChromaDB và mô hình Embedding đã cấu hình ở Phase 1.
    ChromaDB tự động nhúng câu truy vấn (qua SentenceTransformerEmbeddingFunction)
    hoặc nhận trực tiếp query_vector và tìm kiếm ngữ nghĩa theo khoảng cách Cosine (HNSW).
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
        """Khởi tạo hoặc trả về ChromaDB collection (lazy-load 1 lần duy nhất)."""
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
        """Khôi phục heading_path từ chuỗi JSON trong ChromaDB về lại list[str]."""
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
        Truy xuất top_k chunks có ngữ nghĩa gần nhất từ ChromaDB.

        Args:
            query_text: Câu truy vấn văn bản (ChromaDB sẽ tự động gọi hàm Embedding đã cấu hình).
            query_vector: Vector embedding của câu truy vấn (nếu đã tính trước qua QueryVectorizer).
            chroma_filter: Bộ lọc metadata (where clause của ChromaDB từ ScopeBuilder).
            top_k: Số lượng kết quả tối đa cần lấy.
            fallback_unfiltered: Tự động tìm trên toàn bộ DB nếu bộ lọc quá chặt trả về 0 kết quả.

        Returns:
            Danh sách các chunk đã xếp hạng (rank = 1, 2, ...) kèm điểm tương đồng Cosine.
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

        # Nếu lọc theo scope không ra kết quả nào và cho phép nới lỏng (fallback)
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
            # ChromaDB dùng "hnsw:space": "cosine" => distance = 1 - cosine_similarity
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
        fallback_unfiltered: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Nhận trực tiếp ScopedRetrievalPayload từ bước Scope Building và thực hiện Dense Retrieval.
        """
        chroma_filter = payload.scope.chroma_filter if payload.scope else None
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
    Hàm tiện ích gọn nhẹ để gọi trực tiếp Dense Retrieval với ChromaDB.
    """
    retriever = DenseRetriever(collection=collection)
    return retriever.retrieve(
        query_text=query_text,
        query_vector=query_vector,
        chroma_filter=chroma_filter,
        top_k=top_k,
    )

