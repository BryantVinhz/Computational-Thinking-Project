import json
from provenance_repo import ProvenanceRepository
from schema_provenance import ProvenanceSourceSchema, TraceResultSchema

class ProvenanceResolver:
    def __init__(self, repo: ProvenanceRepository):
        self.repo = repo

    def trace_chunk(self, chunk_id: str) -> TraceResultSchema:
        rows = self.repo.get_trace_data(chunk_id)
        if not rows:
            raise ValueError(f"No provenance found for chunk_id={chunk_id}")

        first = rows[0]
        sources = [
            ProvenanceSourceSchema(
                normalized_block_id=r["normalized_block_id"],
                source_order=r["source_order"],
                start_offset=r["start_offset"],
                end_offset=r["end_offset"],
                locator=json.loads(r["locator_json"]) if r["locator_json"] else {},
                block_idx=r["block_idx"],
                block_type=r["block_type"],
                heading_path=json.loads(r["heading_path_json"]) if r["heading_path_json"] else [],
                normalized_text=r["normalized_text"],
            )
            for r in rows
        ]

        return TraceResultSchema(
            chunk_id=chunk_id,
            document_id=first["document_id"],
            version_id=first["version_id"],
            file_name=first["file_name"],
            file_format=first["file_format"],
            canonical_title=first["canonical_title"],
            sources=sources,
        )