import logging
from typing import Any

logger = logging.getLogger(__name__)

BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class QueryVectorizer:
    """
    Embeds queries and passages with the same model used to index ChromaDB.

    The retrieval instruction is an English prompt for BGE v1.5 models only, and only for queries.
    BGE-M3 (multilingual) is trained without instructions, and passages must never carry one,
    otherwise MMR compares query-polluted chunk vectors.
    """

    def __init__(self, model_name: str = "BAAI/bge-m3", device: str = "cpu",
                 use_bge_instruction: bool = True, model: Any | None = None):
        self.model_name = model_name
        self.device = device
        self.use_bge_instruction = use_bge_instruction
        self._model = model

    @property
    def _uses_query_instruction(self) -> bool:
        name = self.model_name.lower()
        return self.use_bge_instruction and "bge" in name and "v1.5" in name
    
    def _get_model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise ImportError("sentence-transformers is not installed. Please install it") from e
            logger.info("Loading embedding model %s on %s...", self.model_name, self.device)
            self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model

    def _encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._get_model().encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=len(texts),
        )
        return vectors.tolist() if hasattr(vectors, "tolist") else [list(v) for v in vectors]
    
    def vectorize(self, text: str) -> list[float]:
        if not text or not text.strip():
            raise ValueError("Query text cannot be empty for vectorization")
        return self.vectorize_batch([text])[0]

    def vectorize_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed queries (adds the BGE v1.5 instruction when applicable)."""
        prefix = BGE_QUERY_INSTRUCTION if self._uses_query_instruction else ""
        return self._encode([f"{prefix}{(t or '').strip()}" for t in texts])

    def vectorize_passages(self, texts: list[str]) -> list[list[float]]:
        """Embed document chunks exactly as they were indexed: never with a query instruction."""
        return self._encode([(t or "").strip() for t in texts])
