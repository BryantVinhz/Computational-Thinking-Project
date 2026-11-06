from strategy import ChunkDraft, ChunkingStrategy, TextPiece
from token_counter import ApproximateTokenCounter
import re

class StructureAwareChunker(ChunkingStrategy):
    """Structure-aware chunking: respect headings and sentences"""
    name = "structure_aware"

    def __init__(self, max_tokens=450, token_counter=None):
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        self.max_tokens = max_tokens
        self.counter = token_counter or ApproximateTokenCounter()

    def chunk(self, blocks: list[dict]) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        current: list[TextPiece] = []
        current_tokens = 0
        current_context: list[str] = []

        def flush():
            nonlocal current, current_tokens, current_context
            if current:
                text = "\n\n".join(p.text for p in current)
                types = {p.block.get("block_type", "paragraph") for p in current}
                drafts.append(ChunkDraft(text=text, pieces=current.copy(),
                    chunk_type=next(iter(types)) if len(types) == 1 else "mixed",
                    heading_path=current_context.copy()))
            current, current_tokens, current_context = [], 0, []

        
        for block in blocks:
            text = block.get("normalized_text")
            btype = block.get("block_type", "paragraph")
            context = list(block.get("heading_path") or [])
            # Immediately flush the previous chunk upon encountering a heading and create a separate chunk for it.
            if btype == "heading":
                flush()
                if self.counter.count(text) > self.max_tokens:
                    for piece_text, a, b in split_oversize_text(text, max(1, self.max_tokens * 4)):
                        drafts.append(ChunkDraft(piece_text, [TextPiece(piece_text, block, a, b)], "heading", heading_path=context))
                else:
                    drafts.append(ChunkDraft(text, [TextPiece(text, block, 0, len(text))], "heading", heading_path=context))
                continue
            
            # Specially handle non-prose text structures
            if btype in {"table", "record", "code"}:
                flush()
                if self.counter.count(text) <= self.max_tokens:
                    drafts.append(ChunkDraft(text, [TextPiece(text, block, 0, len(text))], btype,
                                             heading_path=context))
                else:
                    for piece_text, a, b in split_oversize_text(text, max(1, self.max_tokens * 4)):
                        drafts.append(ChunkDraft(piece_text, [TextPiece(piece_text, block, a, b)], btype,
                                                 heading_path=context))
                continue
            
            # Process standard text blocks (paragraphs) grouped by sentence
            if current and context != current_context:
                flush()
            if not current:
                current_context = context
            units = split_sentences_with_offsets(text)
            if not units:
                units = [(text, 0, len(text))]
            for unit_text, a, b in units:
                unit_tokens = self.counter.count(unit_text)
                if unit_tokens > self.max_tokens:
                    flush()
                    for part, pa, pb in split_oversize_text(unit_text, max(1, self.max_tokens * 4)):
                        drafts.append(ChunkDraft(part, [TextPiece(part, block, a + pa, a + pb)], btype, heading_path=context))
                    continue
                if current and current_tokens + unit_tokens > self.max_tokens:
                    flush()
                    current_context = context
                current.append(TextPiece(unit_text, block, a, b))
                current_tokens += unit_tokens
                
        flush()
        return drafts
    
# Split after sentence punctuation followed by whitespace, or at paragraph breaks.
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+|\n{2,}")


def split_sentences_with_offsets(text: str) -> list[tuple[str, int, int]]:
    """Return (text, start, end) tuples"""
    pieces: list[tuple[str, int, int]] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        end = match.start()
        left, right = start, end
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if left < right:
            pieces.append((text[left:right], left, right))
        start = match.end()
    left, right = start, len(text)
    while left < right and text[left].isspace():
        left += 1
    while right > left and text[right - 1].isspace():
        right -= 1
    if left < right:
        pieces.append((text[left:right], left, right))
    return pieces


def split_oversize_text(text: str, max_chars: int) -> list[tuple[str, int, int]]:
    """Split a long text by character"""
    out = []
    for start in range(0, len(text), max_chars):
        end = min(len(text), start + max_chars)
        out.append((text[start:end], start, end))
    return out
