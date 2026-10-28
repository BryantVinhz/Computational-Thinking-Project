from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProvenanceSourceSchema:
    normalized_block_id: str
    source_order: int
    start_offset: int
    end_offset: int
    locator: dict[str, Any] = field(default_factory=dict)
    block_idx: int | None = None
    block_type: str | None = None
    heading_path: list[str] = field(default_factory=list)
    normalized_text: str | None = None
    def to_dict(self) -> dict[str, Any]:
        return {
            "normalized_block_id": self.normalized_block_id,
            "source_order": self.source_order,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "locator": self.locator,
            "block_idx": self.block_idx,
            "block_type": self.block_type,
            "heading_path": self.heading_path,
            "normalized_text": self.normalized_text,
        }


@dataclass
class TraceResultSchema:
    """
    Complete trace result from a chunk back to the document source
    """
    chunk_id: str
    document_id: str
    version_id: str
    file_name: str | None
    file_format: str | None
    canonical_title: str | None
    sources: list[ProvenanceSourceSchema] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "version_id": self.version_id,
            "file_name": self.file_name,
            "file_format": self.file_format,
            "canonical_title": self.canonical_title,
            "sources": [
                source.to_dict()
                for source in self.sources
            ],
        }