from strategy import ChunkDraft, ChunkingStrategy, TextPiece


class FixedSizeChunker(ChunkingStrategy):
    """Fixed-size baseline chunking with character-offset provenance"""
    name = "fixed_size"

    def __init__(self, max_tokens=450, overlap_tokens=50, token_counter=None):
        if max_tokens < 1 or overlap_tokens < 0 or overlap_tokens >= max_tokens:
            raise ValueError("Require max_tokens >= 1 and 0 <= overlap_tokens < max_tokens")
        from token_counter import ApproximateTokenCounter
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.counter = token_counter or ApproximateTokenCounter()

    def chunk(self, blocks: list[dict]) -> list[ChunkDraft]:
        # Concatenate strings and map each character range back to its source block
        stream_parts: list[str] = []
        ranges: list[tuple[int, int, dict, int, int]] = []
        cursor = 0
        for idx, block in enumerate(blocks):
            if idx:
                stream_parts.append("\n\n")
                cursor += 2
            text = block["normalized_text"]
            start = cursor
            stream_parts.append(text)
            cursor += len(text)
            ranges.append((start, cursor, block, 0, len(text)))
        stream = "".join(stream_parts)
        if not stream.strip():
            return []

        # Find cutting boundary by binary search
        drafts: list[ChunkDraft] = []
        start = 0
        while start < len(stream):
            lower, upper = start + 1, len(stream)
            best = start
            while lower <= upper:
                mid = (lower + upper) // 2
                if self.counter.count(stream[start:mid]) <= self.max_tokens:
                    best = mid
                    lower = mid + 1
                else:
                    upper = mid - 1
            if best == start:  # tokenizer edge case: force progress
                best = min(len(stream), start + 1)
            end = best
            # Prefer a word boundary over cutting a word in half
            boundary = max(stream.rfind(" ", start, end), stream.rfind("\n", start, end), stream.rfind("\t", start, end))
            if boundary > start + 1:
                end = boundary
            while start < end and stream[start].isspace():
                start += 1
            while end > start and stream[end - 1].isspace():
                end -= 1
            
            # Create TextPiece and ChunkDraft
            if start < end:
                pieces = []
                for gstart, gend, block, _, _ in ranges:
                    left, right = max(start, gstart), min(end, gend)
                    if left < right:
                        local_start, local_end = left - gstart, right - gstart
                        pieces.append(TextPiece(block["normalized_text"][local_start:local_end], block, local_start, local_end))
                if pieces:
                    drafts.append(ChunkDraft(text=stream[start:end], pieces=pieces,
                                             heading_path=list(pieces[0].block.get("heading_path") or [])))
            if best >= len(stream):
                break
            next_start = best
            if self.overlap_tokens:
                lower, upper = start, best
                earliest = best
                while lower <= upper:
                    mid = (lower + upper) // 2
                    if self.counter.count(stream[mid:best]) <= self.overlap_tokens:
                        earliest = mid
                        upper = mid - 1
                    else:
                        lower = mid + 1
                next_start = max(start + 1, earliest)
                # Align overlap start to the next token boundary
                boundary = stream.find(" ", next_start, best)
                if boundary != -1:
                    next_start = boundary + 1
            start = max(next_start, start + 1)
        return drafts
