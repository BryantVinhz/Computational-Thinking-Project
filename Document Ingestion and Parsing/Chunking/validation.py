class ChunkValidator:
    """Validate chunk output and source-offset integrity"""
    def validate(self, output, max_tokens: int | None = None, token_counter=None) -> list[str]:
        errors: list[str] = []
        chunks = output.chunks
        
        ids = [c.chunk_id for c in chunks]
        if len(ids) != len(set(ids)):
            errors.append("chunk_id values must be unique")
        chunk_ids = set(ids)
        
        for chunk in chunks:
            if not chunk.text_content.strip():
                errors.append(f"{chunk.chunk_id}: empty text_content")
            if chunk.chunk_id not in chunk_ids:
                errors.append(f"{chunk.chunk_id}: missing from output")
            if max_tokens is not None and token_counter is not None:
                if token_counter.count(chunk.text_content) > max_tokens:
                    errors.append(f"{chunk.chunk_id}: exceeds max_tokens={max_tokens}")
        
        for source in output.chunk_sources:
            if source.chunk_id not in chunk_ids:
                errors.append(f"{source.chunk_source_id}: references unknown chunk_id")
            if source.start_offset < 0 or source.end_offset < source.start_offset:
                errors.append(f"{source.chunk_source_id}: invalid offsets")
        return errors
