import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

# Ensure path.py and sibling modules can be imported when running this file directly
CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
for _dir in (CURRENT_DIR, PARENT_DIR):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

from extractor_sqlite import MetadataExtractor, MetadataStore
from path import CHUNKING_OUT_DIR, INGESTION_OUT_DIR, VECTOR_DB_DIR
from schema_metadata import (
    ChunkMetadataSchema,
    DocumentMetadataSchema,
    VectorDBRecordSchema,
)

# Select an appropriate embedding model:
# - "BAAI/bge-base-en-v1.5": Good baseline for English security documents (OWASP, CVSS)
# - "all-MiniLM-L6-v2": Lightweight and fast on CPU
# - "BAAI/bge-m3": Multilingual support (e.g. Vietnamese queries against English documents)
MODEL_NAME = "BAAI/bge-m3"
CHROMA_DB_DIR = VECTOR_DB_DIR / "chroma_security_db"
COLLECTION_NAME = "owasp_security_knowledge"


def get_chroma_collection(
    db_path: Path | str = CHROMA_DB_DIR,
    collection_name: str = COLLECTION_NAME,
    model_name: str = MODEL_NAME,
    device: str = "cpu",
):
    """Initialize ChromaDB PersistentClient and return the collection with its embedding function."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    embedding_fn = SentenceTransformerEmbeddingFunction(
        model_name=model_name,
        device=device,              # Change to "cuda" if running on a machine/Colab with GPU
        normalize_embeddings=True,  # Normalize vectors for accurate cosine similarity computation
    )

    chroma_client = chromadb.PersistentClient(path=str(db_path))
    collection = chroma_client.get_or_create_collection(
        name=collection_name,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},  # Use cosine distance
    )
    return collection


def export_records_from_chroma(
    collection=None,
) -> list[dict]:
    """
    Read all records from ChromaDB and convert them to the standard JSON structure:
    {
      "id": "...",
      "text": "...",
      "metadata": {
        "document_id": "...",
        "version_id": "...",
        "title": "...",
        "file_format": "...",
        "chunk_idx": 0,
        "chunk_type": "text",
        "token_count": 32,
        "chunk_strategy": "structure_aware",
        "heading_path": ["A05: Injection", "SQL Injection"]
      }
    }
    """
    if collection is None:
        collection = get_chroma_collection()

    raw = collection.get(include=["documents", "metadatas"])
    ids = raw.get("ids") or []
    docs = raw.get("documents") or []
    metas = raw.get("metadatas") or []

    formatted_records: list[dict] = []
    for c_id, text, meta in zip(ids, docs, metas):
        meta_copy = dict(meta or {})
        # Restore heading_path from JSON string in ChromaDB back to list[str]
        hp = meta_copy.get("heading_path", [])
        if isinstance(hp, str):
            try:
                parsed = json.loads(hp)
                meta_copy["heading_path"] = parsed if isinstance(parsed, list) else [hp]
            except json.JSONDecodeError:
                meta_copy["heading_path"] = [hp] if hp else []

        # Keep metadata keys in a consistent order
        ordered_meta = {
            "document_id": meta_copy.get("document_id", ""),
            "version_id": meta_copy.get("version_id", ""),
            "title": meta_copy.get("title", ""),
            "file_format": meta_copy.get("file_format", ""),
            "chunk_idx": meta_copy.get("chunk_idx", 0),
            "chunk_type": meta_copy.get("chunk_type", "text"),
            "token_count": meta_copy.get("token_count", 0),
            "chunk_strategy": meta_copy.get("chunk_strategy", "structure_aware"),
            "heading_path": meta_copy.get("heading_path", []),
        }

        formatted_records.append(
            {
                "id": c_id,
                "text": text,
                "metadata": ordered_meta,
            }
        )

    return formatted_records


def prepare_and_ingest_to_chroma(
    chunks_from_chunking: list[dict],
    doc_meta_from_diep: dict | DocumentMetadataSchema,
    chunk_meta_records_from_diep: list[dict | ChunkMetadataSchema] | None = None,
    collection=None,
    version_record: dict | None = None,
) -> list[dict]:
    
    if collection is None:
        collection = get_chroma_collection()

    if isinstance(doc_meta_from_diep, DocumentMetadataSchema):
        doc_meta = doc_meta_from_diep.to_dict()
    else:
        doc_meta = doc_meta_from_diep

    # Map chunk_id -> chunk metadata record (if passed separately from Step 5)
    meta_by_id: dict[str, dict] = {}
    if chunk_meta_records_from_diep:
        for rec in chunk_meta_records_from_diep:
            rec_dict = rec.to_dict() if hasattr(rec, "to_dict") else rec
            meta_by_id[rec_dict["chunk_id"]] = rec_dict

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []

    for idx, chunk in enumerate(chunks_from_chunking):
        chunk_dict = chunk.to_dict() if hasattr(chunk, "to_dict") else chunk
        c_id = chunk_dict["chunk_id"]
        text_content = chunk_dict.get("text_content") or chunk_dict.get("text") or ""
        c_meta = meta_by_id.get(c_id, chunk_dict)

        raw_heading_path = c_meta.get("heading_path")
        if raw_heading_path is None:
            raw_heading_path = chunk_dict.get("heading_path") or []
        if isinstance(raw_heading_path, str):
            try:
                parsed = json.loads(raw_heading_path)
                heading_path_list = parsed if isinstance(parsed, list) else [raw_heading_path]
            except json.JSONDecodeError:
                heading_path_list = [raw_heading_path] if raw_heading_path else []
        else:
            heading_path_list = list(raw_heading_path)

        file_format = doc_meta.get("file_format") or doc_meta.get("document_format") or "PDF"

        combined_meta = {
            # Attributes from Document Metadata
            "document_id": doc_meta["document_id"],
            "version_id": doc_meta["version_id"],
            "title": doc_meta.get("title") or doc_meta.get("canonical_title") or "",
            "file_format": file_format,
            "file_name": (version_record or {}).get("file_name") or "",
            "version_tag": str((version_record or {}).get("version_tag") or ""),

            # Attributes from ChunkSchema (excluding ChunkSource, chunk_id, text_content)
            "chunk_idx": int(c_meta.get("chunk_idx", chunk_dict.get("chunk_idx", idx))),
            "chunk_type": c_meta.get("chunk_type") or chunk_dict.get("chunk_type") or "text",
            "token_count": int(c_meta.get("token_count", chunk_dict.get("token_count", 0))),
            "chunk_strategy": (
                c_meta.get("chunk_strategy")
                or chunk_dict.get("chunk_strategy")
                or "structure_aware"
            ),
            "heading_path": heading_path_list,
        }

        record = VectorDBRecordSchema(
            id=c_id,
            text=text_content,
            metadata=combined_meta,
        )

        ids.append(record.id)
        documents.append(record.text)
        metadatas.append(record.to_chroma_metadata())
        
    # Mirror the SQLite provenance purge: drop chunks of older versions of the same document that share
    # this file_name or version_tag, then drop this version's own chunks before re-upserting.
    doc_id = doc_meta["document_id"]
    curr_ver_id = doc_meta["version_id"]
    curr_file_name = (version_record or {}).get("file_name") or ""
    curr_version_tag = str((version_record or {}).get("version_tag") or "")
    same_lineage = [{"file_name": curr_file_name}] if curr_file_name else []
    if curr_version_tag:
        same_lineage.append({"version_tag": curr_version_tag})
    if same_lineage:
        lineage_clause = same_lineage[0] if len(same_lineage) == 1 else {"$or": same_lineage}
        collection.delete(where={"$and": [
            {"document_id": doc_id},
            {"version_id": {"$ne": curr_ver_id}},
            lineage_clause,
        ]})
    collection.delete(where={"version_id": curr_ver_id})
    
    if ids:
        collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )
    exported_records = export_records_from_chroma(
        collection=collection,
    )
    print(
        f"Đã lưu {len(ids)} chunks vào ChromaDB ('{CHROMA_DB_DIR}')!"
    )
    return exported_records


def main() -> None:
    # 1. Read Document & Version data from Step 1 (Ingestion)
    documents_data = json.loads((INGESTION_OUT_DIR / "documents.json").read_text(encoding="utf-8"))
    versions_data = json.loads((INGESTION_OUT_DIR / "documents_version.json").read_text(encoding="utf-8"))

    doc_by_id = {d["document_id"]: d for d in documents_data}
    ver_by_id = {v["version_id"]: v for v in versions_data}

    # 2. Initialize Store, Extractor, and Chroma Collection once
    # (Avoids reloading the embedding model multiple times)
    meta_store = MetadataStore()
    extractor = MetadataExtractor(meta_store)
    collection = get_chroma_collection()

    # 3. Locate chunking output files (prefer root directory, or Structure aware)
    chunk_files = sorted(CHUNKING_OUT_DIR.glob("*.json"))
    if not chunk_files and (CHUNKING_OUT_DIR / "Structure aware").exists():
        chunk_files = sorted((CHUNKING_OUT_DIR / "Structure aware").glob("*.json"))
    if not chunk_files and (CHUNKING_OUT_DIR / "Fixed size").exists():
        chunk_files = sorted((CHUNKING_OUT_DIR / "Fixed size").glob("*.json"))

    if not chunk_files:
        print(f"Không tìm thấy file chunking JSON nào trong '{CHUNKING_OUT_DIR}'!")
        return

    # 4. Iterate over all chunking files in the folder and store them in Vector DB
    total_chunks = 0
    for chunk_file in chunk_files:
        chunking_output = json.loads(chunk_file.read_text(encoding="utf-8"))
        doc_id = chunking_output.get("document_id")
        ver_id = chunking_output.get("version_id")

        ver = ver_by_id.get(ver_id)
        doc = doc_by_id.get(doc_id) if doc_id else (doc_by_id.get(ver["document_id"]) if ver else None)

        if not doc or not ver:
            print(f"[BỎ QUA] Không tìm thấy metadata Ingestion cho file '{chunk_file.name}'")
            continue

        print(f"Đang xử lý: {chunk_file.name} ({len(chunking_output.get('chunks', []))} chunks)...")
        meta_output = extractor.run(doc, ver, chunking_output)

        prepare_and_ingest_to_chroma(
            chunks_from_chunking=chunking_output["chunks"],
            doc_meta_from_diep=meta_output.document,
            chunk_meta_records_from_diep=meta_output.chunks,
            collection=collection,
            version_record=ver,
        )
        total_chunks += len(chunking_output.get("chunks", []))

    print(
        f"\nHoàn tất xử lý {len(chunk_files)} file ({total_chunks} chunks). "
        f"Toàn bộ output đã được lưu tại '{VECTOR_DB_DIR}'!"
    )


if __name__ == "__main__":
    main()
