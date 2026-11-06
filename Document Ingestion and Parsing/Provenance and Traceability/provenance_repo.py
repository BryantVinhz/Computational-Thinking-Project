import json
from typing import Any
from database import SQLiteDatabase

class ProvenanceRepository:
    def __init__(self, database: SQLiteDatabase):
        self.database = database

    def ingest_full_provenance(self, document: dict[str, Any], version: dict[str, Any], 
                               blocks: list[dict[str, Any]], chunk_sources: list[dict[str, Any]]) -> None:
        v_id = version["version_id"]
        doc_id = document["document_id"]
        f_name = version.get("file_name")
        v_tag = version.get("version_tag")
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO documents (document_id, canonical_title, publisher)
                VALUES (?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    canonical_title = excluded.canonical_title,
                    publisher = excluded.publisher
                """, 
                (document["document_id"], document["canonical_title"], document.get("publisher")))

            old_versions = conn.execute("""
                SELECT version_id FROM document_versions 
                WHERE document_id = ? AND (file_name = ? OR version_tag = ?) AND version_id != ?
            """, (doc_id, f_name, v_tag, v_id)).fetchall()
            stale_v_ids = [row["version_id"] for row in old_versions]
            stale_v_ids.append(v_id) 
            for target_vid in stale_v_ids:
                conn.execute("""
                    DELETE FROM chunk_sources 
                    WHERE normalized_block_id IN (
                        SELECT normalized_id FROM normalized_blocks WHERE version_id = ?
                    )
                """, (target_vid,))
                conn.execute("DELETE FROM normalized_blocks WHERE version_id = ?", (target_vid,))
                if target_vid != v_id:
                    conn.execute("DELETE FROM document_versions WHERE version_id = ?", (target_vid,))
                    
            conn.execute(
                """
                INSERT INTO document_versions (
                    version_id, document_id, version_tag, file_name, file_path,
                    file_format, file_size_bytes, sha256_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(version_id) DO UPDATE SET
                    file_name = excluded.file_name,
                    file_path = excluded.file_path,
                    file_size_bytes = excluded.file_size_bytes,
                    sha256_hash = excluded.sha256_hash
                """, 
                (
                    version["version_id"], version["document_id"], version["version_tag"],
                    version["file_name"], version["file_path"], version["file_format"],
                    version["file_size_bytes"], version.get("sha256_hash")
                )   
            )

            conn.execute("""
                DELETE FROM chunk_sources 
                WHERE normalized_block_id IN (
                    SELECT normalized_id FROM normalized_blocks WHERE version_id = ?
                )
            """, (v_id,))
            conn.execute("DELETE FROM normalized_blocks WHERE version_id = ?", (v_id,))
            
            blocks_data = [
                (
                    b["normalized_id"], b["block_id"], b["document_id"], b["version_id"],
                    b["block_idx"], b["file_format"], b["block_type"], b.get("normalized_text"),
                    json.dumps(b.get("locator", {}), ensure_ascii=False),
                    json.dumps(b.get("heading_path", []), ensure_ascii=False),
                    b.get("heading_level"), b.get("code_language")
                )
                for b in blocks
            ]
            conn.executemany(
                """
                INSERT INTO normalized_blocks (
                    normalized_id, block_id, document_id, version_id, block_idx,
                    file_format, block_type, normalized_text, locator_json,
                    heading_path_json, heading_level, code_language
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(normalized_id) DO UPDATE SET
                    normalized_text = excluded.normalized_text,
                    locator_json = excluded.locator_json
                """, blocks_data)

            sources_data = [
                (
                    cs["chunk_source_id"], cs["chunk_id"], cs["normalized_block_id"],
                    cs["source_order"], cs["start_offset"], cs["end_offset"]
                )
                for cs in chunk_sources
            ]
            conn.executemany("""
                INSERT INTO chunk_sources (
                    chunk_source_id, chunk_id, normalized_block_id, source_order, start_offset, end_offset
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(chunk_source_id) DO UPDATE SET
                    chunk_id = excluded.chunk_id,
                    normalized_block_id = excluded.normalized_block_id,
                    source_order = excluded.source_order,
                    start_offset = excluded.start_offset,
                    end_offset = excluded.end_offset
            """, sources_data)

            conn.commit()

    def get_trace_data(self, chunk_id: str) -> list[dict[str, Any]]:
        query = """
        SELECT 
            cs.chunk_id, cs.normalized_block_id, cs.source_order, cs.start_offset, cs.end_offset,
            nb.block_idx, nb.block_type, nb.locator_json, nb.heading_path_json, nb.normalized_text,
            dv.version_id, dv.file_name, dv.file_format,
            d.document_id, d.canonical_title
        FROM chunk_sources cs
        JOIN normalized_blocks nb ON cs.normalized_block_id = nb.normalized_id
        JOIN document_versions dv ON nb.version_id = dv.version_id
        JOIN documents d ON nb.document_id = d.document_id
        WHERE cs.chunk_id = ?
        ORDER BY cs.source_order ASC
        """
        with self.database.connect() as conn:
            rows = conn.execute(query, (chunk_id,)).fetchall()
        return [dict(r) for r in rows]
