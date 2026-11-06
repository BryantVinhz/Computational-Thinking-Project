import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
INGESTION_DIR = PROJECT_ROOT / "Document Ingestion and Parsing"
PROVENANCE_DIR = INGESTION_DIR / "Provenance and Traceability"

for _dir in (CURRENT_DIR, PROVENANCE_DIR, INGESTION_DIR):
    if _dir.exists() and str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))


from database import SQLiteDatabase
from provenance_repo import ProvenanceRepository
from provenance_resolver import ProvenanceResolver
from schema_provenance import TraceResultSchema

logger = logging.getLogger(__name__)

DEFAULT_PROVENANCE_DB_PATH = INGESTION_DIR / "output" / "Provenance" / "workspace_data.db"



@dataclass
class AssembledEvidence:
    evidence_id: str                          # Example: "[1]", "[2]"
    chunk_id: str                             
    text_content: str                         
    score: float                              
    citation_tag: str                         # Citation: [1] Title > Heading
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvidenceAssembler:
    """
    Package post-MMR chunks and link them to the Provenance table in SQLite
    """

    def __init__(
        self,
        resolver: ProvenanceResolver | None = None,
        db_path: Path | str = DEFAULT_PROVENANCE_DB_PATH,
    ):
        """
        Assembler Initialization
        """
        if resolver is not None:
            self.resolver = resolver
        else:
            db_target = Path(db_path)
            if not db_target.exists():
                logger.warning("Không tìm thấy DB Provenance")
                self.resolver = None
            else:
                db = SQLiteDatabase(db_target)
                repo = ProvenanceRepository(db)
                self.resolver = ProvenanceResolver(repo)

    @staticmethod
    def _build_citation_tag(index: int, chunk: dict[str, Any], trace: TraceResultSchema | None = None) -> str:
        """Generate citation strings for readers"""
        meta = chunk.get("metadata", {})
        title = (trace.canonical_title if trace else None) or meta.get("title") or meta.get("document_id") or "Document"
        
        heading_list = meta.get("heading_path") or []
        heading_str = " > ".join(heading_list) if heading_list else "General"
        
        return f"[{index}] {title} ({heading_str})"

    def assemble_chunk(self, index: int, chunk: dict[str, Any]) -> AssembledEvidence:
        """Trace provenance and assemble a chunk into AssembledEvidence"""
        chunk_id = chunk.get("chunk_id") or chunk.get("id") or ""
        text_content = chunk.get("text") or chunk.get("text_content") or ""
        score = float(chunk.get("score") or chunk.get("rrf_score") or 0.0)
        meta = dict(chunk.get("metadata") or {})

        trace_data: TraceResultSchema | None = None
        provenance_dict: dict[str, Any] = {}

        # Deep query into SQLite Provenance
        if self.resolver and chunk_id:
            try:
                trace_data = self.resolver.trace_chunk(chunk_id)
                provenance_dict = trace_data.to_dict()
            except Exception as e:
                logger.debug("No detailed trace found for chunk %s: %s", chunk_id, e)

        # Fallback if no provenance data is found in DB
        if not provenance_dict:
            provenance_dict = {
                "chunk_id": chunk_id,
                "document_id": meta.get("document_id", ""),
                "version_id": meta.get("version_id", ""),
                "file_name": meta.get("title", ""),
                "file_format": meta.get("file_format", ""),
                "canonical_title": meta.get("title", ""),
                "sources": [],
            }

        evidence_id = f"[{index}]"
        citation_tag = self._build_citation_tag(index, chunk, trace_data)

        return AssembledEvidence(
            evidence_id=evidence_id,
            chunk_id=chunk_id,
            text_content=text_content,
            score=round(score, 4),
            citation_tag=citation_tag,
            metadata=meta,
            provenance=provenance_dict,
        )

    def assemble(self, selected_chunks: list[dict[str, Any]]) -> list[AssembledEvidence]:
        """Package the full list of post-MMR chunks"""
        if not selected_chunks:
            return []

        return [
            self.assemble_chunk(index=idx, chunk=chunk)
            for idx, chunk in enumerate(selected_chunks, start=1)
        ]

    @staticmethod
    def format_llm_context(evidence_list: list[AssembledEvidence]) -> str:
        """
        Convert a list of evidences into a single text block ready for injection into the LLM's System Prompt
        """
        if not evidence_list:
            return "No relevant evidence was found"

        context_blocks = []
        for ev in evidence_list:
            header = f"--- Evidence {ev.evidence_id} | Sources: {ev.citation_tag} ---"
            body = ev.text_content.strip()
            context_blocks.append(f"{header}\n{body}")

        return "\n\n".join(context_blocks)