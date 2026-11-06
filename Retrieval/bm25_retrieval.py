import logging
import re
import sys
from pathlib import Path
from typing import Any
from rank_bm25 import BM25Okapi
from schema import RetrievalScope, ScopedRetrievalPayload
import chromadb

CURRENT_DIR = Path(__file__).resolve().parent               # Folder Retrieval
PROJECT_ROOT = CURRENT_DIR.parent                           # Folder Computational-Thinking-Project


METADATA_DIR = PROJECT_ROOT / "Document Ingestion and Parsing" / "Metadata"

if str(METADATA_DIR) not in sys.path:
    sys.path.insert(0, str(METADATA_DIR))
    
from vector_store import CHROMA_DB_DIR, COLLECTION_NAME, export_records_from_chroma

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")



logger = logging.getLogger(__name__)

def simple_tokenize(text: str) -> list[str]:
    """
    Tokenize and normalize terms for BM25 
    Example: 'CWE-79: Cross-Site Scripting' -> ['cwe', '79', 'cross', 'site', 'scripting']
    """
    if not text:
        return []
    cleaned = text.lower().replace("_", " ").replace("-", " ")
    tokens = re.findall(r"\b[a-z0-9à-ỹ]+\b", cleaned)
    return tokens


class BM25Retriever:
    """
    BM25 Techniques
        Leverage chunks and normalized 9-field metadata stored in ChromaDB
        through the export_records_from_chroma() function from vector_store.py
    """

    def __init__(
        self,
        db_path: Path | str = CHROMA_DB_DIR,
        collection_name: str = COLLECTION_NAME,
        collection: Any | None = None,
        corpus_chunks: list[dict[str, Any]] | None = None,
    ):
        """
        - Open a ChromaDB client to read text and metadata
        - Build an in-memory BM25Okapi index
        """
        if corpus_chunks is not None:
            self.chunks = corpus_chunks
        else:
            if collection is not None:
                self._collection = collection
            else:
                # Open PersistentClient and retrieve the raw collection (without passing an embedding_function)
                target_path = Path(db_path)
                client = chromadb.PersistentClient(path=str(target_path))
                self._collection = client.get_collection(name=collection_name)

            logger.info("Loading chunk data and normalized 9-field metadata from ChromaDB")
            self.chunks = export_records_from_chroma(collection=self._collection)

        logger.info("Building BM25Okapi index for %d chunks", len(self.chunks))
        self.tokenized_corpus = [
            simple_tokenize(c.get("text") or c.get("text_content") or "")
            for c in self.chunks
        ]
        self.bm25 = BM25Okapi(self.tokenized_corpus)
        logger.info("BM25Retriever initialization completed")

    def retrieve(self, query_text: str, scope: RetrievalScope | None = None, top_k: int = 10) -> list[dict[str, Any]]:
        """
        Retrieve top-K chunks using BM25 with filters applied from RetrievalScope
        """
        if not query_text or not query_text.strip() or not self.chunks:
            return []

        tokens = simple_tokenize(query_text)
        if not tokens:
            return []

        # Compute BM25 scores across the entire corpus
        doc_scores = self.bm25.get_scores(tokens)

        # Prepare Hard filter sets
        target_doc_ids = set(scope.document_ids) if scope and scope.document_ids else None
        target_ver_ids = set(scope.version_ids) if scope and scope.version_ids else None
        target_formats = set(scope.file_formats) if scope and scope.file_formats else None

        candidates: list[tuple[float, dict[str, Any]]] = []
        for idx, score in enumerate(doc_scores):
            if score <= 0.0:
                continue

            chunk = self.chunks[idx]
            meta = chunk.get("metadata", {})

            # Hard Filter: document_id
            if target_doc_ids and meta.get("document_id") not in target_doc_ids:
                continue

            # Hard Filter: version_id
            if target_ver_ids and meta.get("version_id") not in target_ver_ids:
                continue

            # Hard Filter: file_format
            if target_formats and meta.get("file_format") not in target_formats:
                continue

            candidates.append((score, chunk))

        # 3. Scope Relaxation (Relax filters if hard filtering yields no results)
        if (
            not candidates
            and (target_doc_ids or target_ver_ids or target_formats)
            and (scope and scope.is_relaxed)
        ):
            for idx, score in enumerate(doc_scores):
                if score > 0.0:
                    candidates.append((score, self.chunks[idx]))

        # Rank in descending order by BM25 score
        candidates.sort(key=lambda x: x[0], reverse=True)
        top_candidates = candidates[:top_k]

        # Package output results
        results: list[dict[str, Any]] = []
        for rank, (score, chunk) in enumerate(top_candidates, start=1):
            c_id = chunk["id"]
            text_val = chunk["text"]
            results.append(
                {
                    "id": c_id,
                    "chunk_id": c_id,
                    "text": text_val,
                    "text_content": text_val,
                    "metadata": chunk["metadata"],
                    "score": round(float(score), 4),
                    "bm25_score": round(float(score), 4),
                    "rank": rank,
                    "bm25_rank": rank,
                    "source": "bm25",
                }
            )

        return results

    def retrieve_from_payload(self, payload: ScopedRetrievalPayload, top_k: int = 10) -> list[dict[str, Any]]:
        """
        Retrieves the query_bm25 string (including ontology-expanded keywords) for scoring
        """
        query_str = payload.query_bm25 if payload.query_bm25 else payload.original_query
        return self.retrieve(
            query_text=query_str,
            scope=payload.scope,
            top_k=top_k,
        )