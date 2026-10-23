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
    Lấy toàn bộ thuộc tính từ ChunkSchema của bước Chunking (bỏ text_content và ChunkSource).
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
        """Chuyển list heading_path thành chuỗi JSON để lưu vào cột TEXT của SQLite."""
        d = asdict(self)
        d["heading_path"] = json.dumps(self.heading_path, ensure_ascii=False)
        return d


@dataclass
class VectorDBRecordSchema:
    """
    Cấu trúc chuẩn trước khi đưa vào Vector DB (theo yêu cầu của Vĩnh):
    - `id` (chunk_id) và `text` (text_content) nằm bên ngoài `metadata`.
    - `metadata` gộp chung DocumentMetadataSchema + các thuộc tính của ChunkSchema.
    """
    id: str
    text: str
    metadata: dict

    def to_dict(self) -> dict:
        return asdict(self)

    def to_chroma_metadata(self) -> dict:
        """
        Chuẩn hóa metadata để đẩy vào ChromaDB:
        ChromaDB chỉ chấp nhận các kiểu nguyên thủy (str, int, float, bool),
        không nhận None hoặc list trực tiếp.
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
