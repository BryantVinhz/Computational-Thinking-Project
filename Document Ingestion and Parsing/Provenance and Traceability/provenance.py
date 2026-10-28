from typing import Any
from provenance_repo import ProvenanceRepository
from schema_provenance import TraceResultSchema
from provenance_resolver import ProvenanceResolver



class Provenance:
    def __init__(self, repo: ProvenanceRepository, resolver: ProvenanceResolver):
        self.repo = repo
        self.resolver = resolver


    def ingest_provenance(self, document: dict[str, Any], document_version: dict[str, Any], 
                          normalized_blocks: list[dict[str, Any]], chunk_sources: list[dict[str, Any]]) -> None:
        self.repo.ingest_full_provenance(
            document=document,
            version=document_version,
            blocks=normalized_blocks,
            chunk_sources=chunk_sources,
        )

    # Trace
    def trace_chunk(self, chunk_id: str) -> TraceResultSchema:
        return self.resolver.trace_chunk(chunk_id)