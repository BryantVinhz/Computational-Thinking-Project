"""
Module: traceable_formatter.py
Stage 4: Response Synthesis, Evidence Verification, and Traceable Response
Role: BAO (Traceability)

Extract citation tags [1], [2] from Synthesizer draft, map
each tag to AssembledEvidence (Retrieval) and Provenance data to generate:
- list[TraceableCitationSchema]: detailed source location info for UI/API.
- "References" (Footnotes) section at the end of Markdown answer.

Interface complies with FormatterProtocol in rag_orchestrator.py:
    format_citations(draft, evidence_list) -> (final_markdown, citations)
"""

from __future__ import annotations

import logging
import re
from typing import Any

try:
    from .prompt_templates import extract_citation_tags
    from .schema_response import SynthesizedDraftSchema, TraceableCitationSchema
except ImportError:
    from prompt_templates import extract_citation_tags
    from schema_response import SynthesizedDraftSchema, TraceableCitationSchema

logger = logging.getLogger(__name__)

_TAG_PATTERN = re.compile(r"^\[\d+\]$")
REFERENCES_HEADING = "### Danh mục tài liệu tham chiếu (References & Citations)"
SNIPPET_MAX_CHARS = 200
JSON_PATHS_MAX_SHOWN = 3


def index_evidence_by_tag(evidence_list: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """
    Map tag "[n]" -> evidence.

    Prioritize `evidence_id` assigned by Retrieval (EvidenceAssembler), as that is the
    appearance order in llm_context seen by LLM. If missing, use 1-based index.
    """
    indexed: dict[str, dict[str, Any]] = {}
    for position, ev in enumerate(evidence_list, start=1):
        evidence_id = str(ev.get("evidence_id") or "").strip()
        tag = evidence_id if _TAG_PATTERN.match(evidence_id) else f"[{position}]"
        indexed.setdefault(tag, ev)
    return indexed


def _format_range(label: str, start: int, end: int) -> str:
    return f"{label} {start}" if start == end else f"{label} {start}-{end}"


def summarize_location(sources: list[dict[str, Any]], fallback: str = "") -> str:
    """
    Summarize chunk location in source document from Provenance locators.
    Format-specific locator (schema_parsing.py):
      PDF: page_number (+bbox) | MARKDOWN/TXT: start_line, end_line
      CSV: table_idx, start_row, end_row | JSON: json_path
    """
    locators = [s.get("locator") or {} for s in sources]
    locators = [loc for loc in locators if loc]
    if not locators:
        return fallback

    parts: list[str] = []

    pages = sorted({int(loc["page_number"]) for loc in locators if loc.get("page_number") is not None})
    if pages:
        parts.append(_format_range("Page", pages[0], pages[-1]))

    line_locs = [loc for loc in locators if loc.get("start_line") is not None]
    if line_locs:
        start = min(int(loc["start_line"]) for loc in line_locs)
        end = max(int(loc.get("end_line") or loc["start_line"]) for loc in line_locs)
        parts.append(_format_range("Lines", start, end))

    row_locs = [loc for loc in locators if loc.get("start_row") is not None]
    if row_locs:
        start = min(int(loc["start_row"]) for loc in row_locs)
        end = max(int(loc.get("end_row") or loc["start_row"]) for loc in row_locs)
        tables = sorted({int(loc.get("table_idx") or 0) for loc in row_locs})
        rows = _format_range("Rows", start, end)
        if tables != [0]:
            rows += f" (table {', '.join(map(str, tables))})"
        parts.append(rows)

    json_paths = list(dict.fromkeys(loc["json_path"] for loc in locators if loc.get("json_path")))
    if json_paths:
        shown = ", ".join(json_paths[:JSON_PATHS_MAX_SHOWN])
        if len(json_paths) > JSON_PATHS_MAX_SHOWN:
            shown += f" (+{len(json_paths) - JSON_PATHS_MAX_SHOWN} more)"
        parts.append(f"JSON {shown}")

    return ", ".join(parts) or fallback


def _build_snippet(text: str, max_chars: int = SNIPPET_MAX_CHARS) -> str:
    """Trim snippet preview at word boundaries and collapse whitespace."""
    flat = " ".join((text or "").split())
    if len(flat) <= max_chars:
        return flat
    cut = flat[:max_chars].rsplit(" ", 1)[0]
    return f"{cut}…"


def build_citation(tag: str, evidence: dict[str, Any]) -> TraceableCitationSchema:
    """Convert an AssembledEvidence (dict) into TraceableCitationSchema."""
    meta = evidence.get("metadata") or {}
    prov = evidence.get("provenance") or {}
    sources = sorted(prov.get("sources") or [], key=lambda s: s.get("source_order") or 0)
    chunk_id = str(evidence.get("chunk_id") or prov.get("chunk_id") or "")

    heading_path = list(meta.get("heading_path") or [])
    if not heading_path:
        heading_path = next((list(s["heading_path"]) for s in sources if s.get("heading_path")), [])

    document_id = str(prov.get("document_id") or meta.get("document_id") or "")
    file_name = str(prov.get("file_name") or meta.get("file_name") or "")
    canonical_title = (
        prov.get("canonical_title")
        or meta.get("title")
        or file_name
        or document_id
        or "Untitled document"
    )

    fallback_location = meta.get("location_summary") or (f"Chunk {chunk_id}" if chunk_id else "")
    spans = [
        {
            "normalized_block_id": s.get("normalized_block_id"),
            "source_order": s.get("source_order"),
            "start_offset": s.get("start_offset"),
            "end_offset": s.get("end_offset"),
            **(s.get("locator") or {}),
        }
        for s in sources
    ]

    return TraceableCitationSchema(
        tag=tag,
        chunk_id=chunk_id,
        canonical_title=str(canonical_title),
        heading_path=heading_path,
        document_id=document_id,
        version_id=str(prov.get("version_id") or meta.get("version_id") or ""),
        file_name=file_name,
        file_format=str(prov.get("file_format") or meta.get("file_format") or "").upper(),
        location_summary=summarize_location(sources, fallback=fallback_location),
        locator={"spans": spans} if spans else dict(meta.get("locator") or {}),
        snippet=_build_snippet(evidence.get("text_content") or ""),
    )


def _render_reference_line(citation: TraceableCitationSchema) -> str:
    heading = f" › {' > '.join(citation.heading_path)}" if citation.heading_path else ""
    details = [f"`{citation.file_name}`"] if citation.file_name else []
    if citation.location_summary:
        details.append(citation.location_summary)
    detail_str = f" — {' · '.join(details)}" if details else ""
    return f"- **{citation.tag}** *{citation.canonical_title}*{heading}{detail_str}"


class TraceableFormatter:
    """Generate traceable citation list and append Footnotes to the answer."""

    def format_citations(
        self,
        draft: SynthesizedDraftSchema,
        evidence_list: list[dict[str, Any]],
    ) -> tuple[str, list[TraceableCitationSchema]]:
        if draft.is_abstained:
            return draft.draft_answer, []

        # Text is ground truth: only list tags that actually appear in the answer.
        cited_tags = extract_citation_tags(draft.draft_answer) or list(draft.cited_evidence_ids)
        if not cited_tags:
            return draft.draft_answer, []

        evidence_by_tag = index_evidence_by_tag(evidence_list)
        citations: list[TraceableCitationSchema] = []
        reference_lines: list[str] = []

        for tag in cited_tags:
            evidence = evidence_by_tag.get(tag)
            if evidence is None:
                # Do not silently ignore: reader needs to know this tag cannot be traced.
                logger.warning("Citation %s has no matching evidence", tag)
                reference_lines.append(f"- **{tag}**Source not found in evidence")
                continue
            citation = build_citation(tag, evidence)
            citations.append(citation)
            reference_lines.append(_render_reference_line(citation))

        footnotes = "\n".join(["", "---", REFERENCES_HEADING, *reference_lines])
        final_markdown = f"{draft.draft_answer.rstrip()}\n{footnotes}"
        return final_markdown, citations
