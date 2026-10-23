from dataclasses import asdict, dataclass


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
    """Metadata of one chunk (one row of table `chunk_metadata`)."""
    chunk_id: str

    def __post_init__(self) -> None:
        if not self.chunk_id:
            raise ValueError("chunk_id is required")

    def to_dict(self) -> dict:
        return asdict(self)


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
