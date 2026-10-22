from fixed_size import FixedSizeChunker
from structure_aware import StructureAwareChunker


class ChunkingStrategyFactory:
    """Factory for the supported chunking strategies."""
    @staticmethod
    def create(name: str, **kwargs):
        normalized = name.strip().lower().replace("-", "_")
        strategies = {
            "fixed_size": FixedSizeChunker,
            "structure_aware": StructureAwareChunker,
        }
        if normalized not in strategies:
            raise ValueError(f"Unknown strategy {name!r}; choose from {', '.join(strategies)}")
        return strategies[normalized](**kwargs)
