from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextPiece:
    text: str
    block: dict[str, Any]
    start: int
    end: int


@dataclass
class ChunkDraft:
    text: str
    pieces: list[TextPiece]
    chunk_type: str = "text"
    heading_path: list[str] = field(default_factory=list)


class ChunkingStrategy(ABC):
    """Shared strategy contract and internal piece representation."""
    name = "strategy_base"

    @abstractmethod
    def chunk(self, blocks: list[dict]) -> list[ChunkDraft]:
        """Create chunk drafts from sorted normalized blocks"""
        raise NotImplementedError