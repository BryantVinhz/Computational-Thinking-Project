import logging
from typing import Any

logger = logging.getLogger(__name__)

BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class QueryVectorizer:
    def __init__(self, model_name: str = "BAAI/bge-base-en-v1.5", device: str = "cpu", 
                 use_bge_instruction: bool = True, model: Any | None = None):
        self.model_name = model_name
        self.device = device
        self.use_bge_instruction = use_bge_instruction
        self._model = model

    def _get_model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise ImportError(
                    "sentence-transformers is not installed. Please install it"
                ) from e
            logger.info(
                "Loading embedding model %s on %s...", self.model_name, self.device
            )
            self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model

    def vectorize(self, text: str) -> list[float]:
        if not text or not text.strip():
            raise ValueError("Query text cannot be empty for vectorization")
        return self.vectorize_batch([text])[0]

    def vectorize_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        processed_texts = []
        for t in texts:
            cleaned = t.strip() if t else ""
            if self.use_bge_instruction and "bge" in self.model_name.lower():
                processed_texts.append(f"{BGE_QUERY_INSTRUCTION}{cleaned}")
            else:
                processed_texts.append(cleaned)

        model = self._get_model()
        vectors = model.encode(
            processed_texts,
            normalize_embeddings=True,  
            show_progress_bar=False,
            batch_size=len(processed_texts),
        )
        return (vectors.tolist() if hasattr(vectors, "tolist") else [list(v) for v in vectors])
