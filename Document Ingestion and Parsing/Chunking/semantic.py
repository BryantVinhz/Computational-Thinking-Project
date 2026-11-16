import logging
import sys
from typing import Any

import numpy as np

from strategy import ChunkDraft, ChunkingStrategy, TextPiece
from structure_aware import split_oversize_text, split_sentences_with_offsets
from token_counter import ApproximateTokenCounter

logger = logging.getLogger(__name__)


def _get_chunking_embedder(model_name: str = "BAAI/bge-m3", device: str | None = None) -> Any:
    """Load and cache SentenceTransformer once per process, matching Retrieval/Verifier."""
    import torch
    from sentence_transformers import SentenceTransformer

    resolved_device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    cache: dict[tuple[str, str], Any] = getattr(sys, "_agy_shared_sentence_transformers", None)
    if cache is None:
        cache = {}
        setattr(sys, "_agy_shared_sentence_transformers", cache)

    key = (model_name, resolved_device)
    if key not in cache:
        logger.info("Loading semantic chunking embedder '%s' on '%s'...", model_name, resolved_device)
        cache[key] = SentenceTransformer(model_name, device=resolved_device)
    return cache[key]


def _format_with_heading_context(body_text: str, heading_path: list[str]) -> str:
    """Prepend hierarchical heading context [H1 > H2] to content chunks instead of emitting standalone headings."""
    clean_headings = [str(h).strip() for h in (heading_path or []) if str(h).strip()]
    if not clean_headings:
        return body_text
    prefix = f"[{' > '.join(clean_headings)}]"
    stripped = body_text.lstrip()
    if stripped.startswith(prefix):
        return body_text
    return f"{prefix}\n{body_text}"


class SemanticChunker(ChunkingStrategy):
    """
    Structure-Guided Semantic Chunking (v2):
    - Hybrid Breakpoint Thresholding: splits prose only when distance exceeds BOTH the local
      percentile and the absolute dissimilarity floor (1 - similarity_threshold), preventing
      forced splits on short, cohesive paragraphs.
    - Conditional Sentence Overlap: carries over `overlap_sentences` (default 1) ONLY when
      a hard split occurs at `max_tokens`, preserving coreference across chunk boundaries.
    - Heading Context Merging: attaches `heading_path` directly onto content chunks rather
      than emitting noisy 5-15 token standalone heading chunks.
    """

    name = "semantic"

    def __init__(
        self,
        max_tokens: int = 650,
        min_tokens: int = 80,
        similarity_threshold: float = 0.78,
        breakpoint_percentile: float = 80.0,
        overlap_sentences: int = 1,
        model_name: str = "BAAI/bge-m3",
        device: str | None = None,
        token_counter=None,
        embedder: Any | None = None,
    ):
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if min_tokens < 0 or min_tokens >= max_tokens:
            raise ValueError("Require 0 <= min_tokens < max_tokens")
        if not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be in [0.0, 1.0]")
        if overlap_sentences < 0:
            raise ValueError("overlap_sentences must be >= 0")

        self.max_tokens = max_tokens
        self.min_tokens = min_tokens
        self.similarity_threshold = similarity_threshold
        self.breakpoint_percentile = breakpoint_percentile
        self.overlap_sentences = overlap_sentences
        self.model_name = model_name
        self.device = device
        self.counter = token_counter or ApproximateTokenCounter()
        self._embedder = embedder

    def _get_embedder(self) -> Any:
        if self._embedder is None:
            self._embedder = _get_chunking_embedder(self.model_name, self.device)
        return self._embedder

    def _heading_overhead_tokens(self, heading_path: list[str]) -> int:
        clean_headings = [str(h).strip() for h in (heading_path or []) if str(h).strip()]
        if not clean_headings:
            return 0
        return self.counter.count(f"[{' > '.join(clean_headings)}]\n")

    def _find_semantic_breakpoints(self, sentence_texts: list[str]) -> set[int]:
        """
        Return indices `i` where a genuine topic shift occurs BETWEEN sentence `i` and `i + 1`.
        Uses Hybrid Thresholding: distance must exceed BOTH the section percentile AND the
        absolute distance ceiling (1.0 - similarity_threshold).
        """
        n = len(sentence_texts)
        if n <= 1:
            return set()

        embeddings = self._get_embedder().encode(
            sentence_texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=min(64, n),
        )
        vecs = np.asarray(embeddings, dtype=np.float32)

        similarities = np.sum(vecs[:-1] * vecs[1:], axis=1)
        distances = 1.0 - similarities

        abs_cutoff_distance = 1.0 - self.similarity_threshold
        if len(distances) >= 3:
            rel_cutoff_distance = float(np.percentile(distances, self.breakpoint_percentile))
            effective_cutoff = max(rel_cutoff_distance, abs_cutoff_distance)
        else:
            effective_cutoff = abs_cutoff_distance

        return {int(i) for i, dist in enumerate(distances) if float(dist) > effective_cutoff}

    def _build_draft(
        self,
        body_pieces: list[TextPiece],
        heading_path: list[str],
        leading_heading_pieces: list[TextPiece] | None = None,
        forced_type: str | None = None,
    ) -> ChunkDraft:
        body_text = "\n\n".join(p.text for p in body_pieces)
        full_text = _format_with_heading_context(body_text, heading_path)

        if forced_type:
            ctype = forced_type
        else:
            types = {p.block.get("block_type", "paragraph") for p in body_pieces}
            ctype = next(iter(types)) if len(types) == 1 else "mixed"

        # Prepend heading TextPieces on the first chunk of a section so SQLite Provenance
        # includes the heading line/page range alongside the body blocks.
        all_pieces = [*(leading_heading_pieces or []), *body_pieces]
        return ChunkDraft(
            text=full_text,
            pieces=all_pieces,
            chunk_type=ctype,
            heading_path=heading_path.copy(),
        )

    def _flush_prose_section(
        self,
        section_pieces: list[TextPiece],
        heading_path: list[str],
        pending_headings: list[dict],
        drafts: list[ChunkDraft],
    ) -> None:
        """Split a contiguous same-heading prose section using hybrid breakpoints + max_tokens overlap."""
        if not section_pieces:
            return

        header_tokens = self._heading_overhead_tokens(heading_path)
        effective_max_tokens = max(64, self.max_tokens - header_tokens)

        # Break down any single sentence exceeding effective_max_tokens
        atomic_pieces: list[TextPiece] = []
        for piece in section_pieces:
            if self.counter.count(piece.text) > effective_max_tokens:
                for part, pa, pb in split_oversize_text(piece.text, max(1, effective_max_tokens * 4)):
                    atomic_pieces.append(
                        TextPiece(part, piece.block, piece.start + pa, piece.start + pb)
                    )
            else:
                atomic_pieces.append(piece)

        heading_pieces = [
            TextPiece(hb["normalized_text"], hb, 0, len(hb["normalized_text"]))
            for hb in pending_headings
            if hb.get("normalized_text")
        ]
        pending_headings.clear()

        if len(atomic_pieces) == 1:
            drafts.append(
                self._build_draft(
                    body_pieces=atomic_pieces,
                    heading_path=heading_path,
                    leading_heading_pieces=heading_pieces,
                )
            )
            return

        breakpoints = self._find_semantic_breakpoints([p.text for p in atomic_pieces])

        current: list[TextPiece] = []
        current_tokens = 0
        is_first_chunk_in_section = True

        def emit_current() -> list[TextPiece]:
            nonlocal current, current_tokens, is_first_chunk_in_section
            if not current:
                return []
            emitted = current.copy()
            drafts.append(
                self._build_draft(
                    body_pieces=emitted,
                    heading_path=heading_path,
                    leading_heading_pieces=heading_pieces if is_first_chunk_in_section else None,
                )
            )
            is_first_chunk_in_section = False
            current, current_tokens = [], 0
            return emitted

        for idx, piece in enumerate(atomic_pieces):
            p_tokens = self.counter.count(piece.text)

            # 1. Hard ceiling hit: flush and carry over `overlap_sentences` for local continuity
            if current and (current_tokens + p_tokens > effective_max_tokens):
                emitted = emit_current()
                if self.overlap_sentences > 0 and len(emitted) > self.overlap_sentences:
                    overlap_slice = emitted[-self.overlap_sentences :]
                    overlap_tok = sum(self.counter.count(op.text) for op in overlap_slice)
                    if overlap_tok + p_tokens <= effective_max_tokens:
                        current = list(overlap_slice)
                        current_tokens = overlap_tok

            current.append(piece)
            current_tokens += p_tokens

            # 2. Semantic topic shift: clean break without cross-topic overlap
            if idx in breakpoints and current_tokens >= self.min_tokens:
                emit_current()

        emit_current()

    def chunk(self, blocks: list[dict]) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        section_pieces: list[TextPiece] = []
        current_context: list[str] = []
        pending_headings: list[dict] = []

        for block in blocks:
            text = block.get("normalized_text") or ""
            if not text.strip():
                continue

            btype = block.get("block_type", "paragraph")
            context = list(block.get("heading_path") or [])

            # 1. Heading block: flush previous prose and buffer heading to attach to upcoming content
            if btype == "heading":
                self._flush_prose_section(section_pieces, current_context, pending_headings, drafts)
                section_pieces, current_context = [], []
                pending_headings.append(block)
                continue

            # 2. Structured non-prose blocks (table, record, code): keep atomic with heading context
            if btype in {"table", "record", "code"}:
                self._flush_prose_section(section_pieces, current_context, pending_headings, drafts)
                section_pieces, current_context = [], []

                header_tokens = self._heading_overhead_tokens(context)
                effective_max_tokens = max(64, self.max_tokens - header_tokens)
                heading_pieces = [
                    TextPiece(hb["normalized_text"], hb, 0, len(hb["normalized_text"]))
                    for hb in pending_headings
                    if hb.get("normalized_text")
                ]
                pending_headings.clear()

                if self.counter.count(text) <= effective_max_tokens:
                    drafts.append(
                        self._build_draft(
                            body_pieces=[TextPiece(text, block, 0, len(text))],
                            heading_path=context,
                            leading_heading_pieces=heading_pieces,
                            forced_type=btype,
                        )
                    )
                else:
                    parts = split_oversize_text(text, max(1, effective_max_tokens * 4))
                    for part_idx, (piece_text, a, b) in enumerate(parts):
                        drafts.append(
                            self._build_draft(
                                body_pieces=[TextPiece(piece_text, block, a, b)],
                                heading_path=context,
                                leading_heading_pieces=heading_pieces if part_idx == 0 else None,
                                forced_type=btype,
                            )
                        )
                continue

            # 3. Prose blocks (paragraph, list): flush if heading_path changed, else accumulate sentences
            if section_pieces and context != current_context:
                self._flush_prose_section(section_pieces, current_context, pending_headings, drafts)
                section_pieces = []

            if not section_pieces:
                current_context = context

            units = split_sentences_with_offsets(text)
            if not units:
                units = [(text, 0, len(text))]

            for unit_text, a, b in units:
                section_pieces.append(TextPiece(unit_text, block, a, b))

        self._flush_prose_section(section_pieces, current_context, pending_headings, drafts)

        # Fallback: if a document consists solely of headings with zero body blocks, emit them
        # so ChunkValidator still receives non-empty chunks.
        if not drafts and pending_headings:
            for hb in pending_headings:
                htext = hb.get("normalized_text") or ""
                if htext.strip():
                    drafts.append(
                        ChunkDraft(
                            text=htext,
                            pieces=[TextPiece(htext, hb, 0, len(htext))],
                            chunk_type="heading",
                            heading_path=list(hb.get("heading_path") or []),
                        )
                    )

        return drafts