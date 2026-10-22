from dataclasses import dataclass, asdict, field


@dataclass
class ChunkSchema:
    """Schema for detailed chunk information"""
    chunk_id: str
    document_id: str
    version_id: str
    chunk_idx: int
    chunk_type: str
    text_content: str
    token_count: int
    chunk_strategy: str
    heading_path: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ChunkSourceSchema:
    """Schema representing the many-to-many (N:N) relationship between BlockContent and Chunk"""
    chunk_source_id: str
    chunk_id: str
    normalized_block_id: str
    source_order: int
    start_offset: int
    end_offset: int

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass
class ChunkingOutputSchema:
    """Output schema for concise chunk summary"""
    document_id: str
    version_id: str
    chunking_strategy: str
    chunks: list[ChunkSchema]
    chunk_sources: list[ChunkSourceSchema]

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "version_id": self.version_id,
            "chunking_strategy": self.chunking_strategy,
            "chunks": [c.to_dict() for c in self.chunks],
            "chunk_sources": [s.to_dict() for s in self.chunk_sources],
        }