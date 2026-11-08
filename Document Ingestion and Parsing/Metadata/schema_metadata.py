import json
from dataclasses import asdict, dataclass, field


@dataclass
class DocumentMetadataSchema:
    """Metadata of one document version (one row of table `document_metadata`)."""
    document_id: str
    version_id: str
    title: str
    file_format: str

    def __post_init__(self) -> None:
        if not self.document_id or not self.version_id:
            raise ValueError("document_id and version_id are required")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ChunkMetadataSchema:
    """
    Metadata of one chunk (one row of table `chunk_metadata`).
    Includes all attributes from ChunkSchema in the Chunking step (excluding text_content and ChunkSource).
    """
    chunk_id: str
    document_id: str
    version_id: str
    chunk_idx: int = 0
    chunk_type: str = "text"
    token_count: int = 0
    chunk_strategy: str = "structure_aware"
    heading_path: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.chunk_id:
            raise ValueError("chunk_id is required")

    def to_dict(self) -> dict:
        return asdict(self)

    def to_sqlite_dict(self) -> dict:
        """Convert heading_path list into a JSON string for storage in a SQLite TEXT column."""
        d = asdict(self)
        d["heading_path"] = json.dumps(self.heading_path, ensure_ascii=False)
        return d


@dataclass
class VectorDBRecordSchema:
    """
    Standard record structure prior to Vector DB ingestion:
    - `id` (chunk_id) and `text` (text_content) reside outside `metadata`.
    - `metadata` combines DocumentMetadataSchema and ChunkSchema attributes.
    """
    id: str
    text: str
    metadata: dict

    def to_dict(self) -> dict:
        return asdict(self)

    def to_chroma_metadata(self) -> dict:
        """
        Normalize metadata for ChromaDB ingestion:
        ChromaDB only accepts primitive types (str, int, float, bool),
        not None or lists directly.
        """
        chroma_meta = {}
        for k, v in self.metadata.items():
            if v is None:
                chroma_meta[k] = ""
            elif isinstance(v, list):
                chroma_meta[k] = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, (str, int, float, bool)):
                chroma_meta[k] = v
            else:
                chroma_meta[k] = str(v)
        return chroma_meta


@dataclass
class MetadataOutputSchema:
    """Output of Step 5 for one document version (handed to the next step)."""
    document: DocumentMetadataSchema
    chunks: list[ChunkMetadataSchema]

    def to_dict(self) -> dict:
        return {
            "document": self.document.to_dict(),
            "chunks": [c.to_dict() for c in self.chunks],
        }
