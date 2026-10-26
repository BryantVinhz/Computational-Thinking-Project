from schema_chunking import ChunkSchema, ChunkSourceSchema, ChunkingOutputSchema
from token_counter import ApproximateTokenCounter
from validation import ChunkValidator

    
class ChunkingService:
    def __init__(self, strategy, token_counter=None, validate: bool = True):
        self.strategy = strategy
        self.counter = token_counter or getattr(strategy, "counter", None) or ApproximateTokenCounter()
        self.validate_output = validate
        self.validator = ChunkValidator()

    def run(self, blocks: list[dict]) -> ChunkingOutputSchema:
        if not blocks:
            raise ValueError("Input blocks must not be empty")

        drafts = self.strategy.chunk(blocks)
        document_id, version_id = blocks[0]["document_id"], blocks[0]["version_id"]
        chunks: list[ChunkSchema] = []
        sources: list[ChunkSourceSchema] = []

        # Mapping ChunkDraft to ChunkSchema and ChunkSourceSchema
        for chunk_idx, draft in enumerate(drafts):
            chunk_id = f"{version_id}_c{chunk_idx:05d}"
            chunks.append(
                ChunkSchema(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    version_id=version_id,
                    chunk_idx=chunk_idx,
                    chunk_type=draft.chunk_type,
                    text_content=draft.text,
                    token_count=self.counter.count(draft.text),
                    chunk_strategy=self.strategy.name,
                    heading_path=getattr(draft, "heading_path", None) or []
                )
            )
            
            seen_sources = set()
            source_order = 0
            for piece in draft.pieces:
                # One source row per source block per chunk. If a block contributes two
                # separated pieces, retain the full range as distinct provenance rows.
                norm_block_id = piece.block.get("normalized_id") or piece.block.get("block_id")
                key = (norm_block_id, piece.start, piece.end)
                if key in seen_sources:
                    continue
                seen_sources.add(key)
                
                sources.append(
                    ChunkSourceSchema(
                        chunk_source_id=f"{chunk_id}_s{source_order:03d}",
                        chunk_id=chunk_id,
                        normalized_block_id=norm_block_id,
                        source_order=source_order,
                        start_offset=piece.start,
                        end_offset=piece.end
                    )
                )
                source_order += 1

        output = ChunkingOutputSchema(
                document_id=document_id,
                version_id=version_id,
                chunking_strategy=self.strategy.name,
                chunks=chunks,
                chunk_sources=sources
            )
        
        # Validate output
        if self.validate_output:
            errors = self.validator.validate(output)
            if errors:
                raise ValueError("Chunking output validation failed:\n- " + "\n- ".join(errors))
            
        return output

