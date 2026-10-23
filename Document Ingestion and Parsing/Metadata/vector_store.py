import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

# Đảm bảo import được path.py và các module cùng thư mục khi chạy trực tiếp file này
CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
for _dir in (CURRENT_DIR, PARENT_DIR):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

from extractor_sqlite import MetadataExtractor, MetadataStore
from path import CHUNKING_OUT_DIR, FINAL_OUT_DIR, INGESTION_OUT_DIR
from schema_metadata import (
    ChunkMetadataSchema,
    DocumentMetadataSchema,
    VectorDBRecordSchema,
)

# 1. Chọn mô hình LLM Embedding phù hợp
# - "BAAI/bge-base-en-v1.5": Chuẩn tốt cho tài liệu bảo mật Tiếng Anh (OWASP, CVSS)
# - "all-MiniLM-L6-v2": Siêu nhẹ, chạy CPU rất nhanh theo slide bài giảng
# - "BAAI/bge-m3" hoặc "sentence-transformers/paraphrase-multilingual-mpnet-base-v2": Hỗ trợ đa ngôn ngữ (hỏi Tiếng Việt trên tài liệu Tiếng Anh)
MODEL_NAME = "BAAI/bge-base-en-v1.5"
CHROMA_DB_DIR = FINAL_OUT_DIR / "chroma_security_db"
COLLECTION_NAME = "owasp_security_knowledge"


def get_chroma_collection(
    db_path: Path | str = CHROMA_DB_DIR,
    collection_name: str = COLLECTION_NAME,
    model_name: str = MODEL_NAME,
    device: str = "cpu",
):
    """Khởi tạo ChromaDB PersistentClient và trả về collection kèm hàm Embedding."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    embedding_fn = SentenceTransformerEmbeddingFunction(
        model_name=model_name,
        device=device,              # Đổi thành "cuda" nếu chạy trên máy/Colab có GPU
        normalize_embeddings=True,  # Chuẩn hóa vector để tính Cosine Similarity chính xác
    )

    chroma_client = chromadb.PersistentClient(path=str(db_path))
    collection = chroma_client.get_or_create_collection(
        name=collection_name,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},  # Sử dụng khoảng cách Cosine
    )
    return collection


def prepare_and_ingest_to_chroma(
    chunks_from_chunking: list[dict],
    doc_meta_from_diep: dict | DocumentMetadataSchema,
    chunk_meta_records_from_diep: list[dict | ChunkMetadataSchema] | None = None,
    collection=None,
) -> list[VectorDBRecordSchema]:
    """
    Gộp Document Metadata (của Diệp) + toàn bộ thuộc tính ChunkSchema (của Vĩnh, bỏ ChunkSource),
    tách riêng `id` (chunk_id) và `text` (text_content) ra ngoài `metadata` rồi đẩy vào ChromaDB.
    """
    if collection is None:
        collection = get_chroma_collection()

    if isinstance(doc_meta_from_diep, DocumentMetadataSchema):
        doc_meta = doc_meta_from_diep.to_dict()
    else:
        doc_meta = doc_meta_from_diep

    # Map chunk_id -> bản ghi metadata của chunk (nếu có truyền riêng từ Bước 5)
    meta_by_id: dict[str, dict] = {}
    if chunk_meta_records_from_diep:
        for rec in chunk_meta_records_from_diep:
            rec_dict = rec.to_dict() if hasattr(rec, "to_dict") else rec
            meta_by_id[rec_dict["chunk_id"]] = rec_dict

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    vector_records: list[VectorDBRecordSchema] = []

    for idx, chunk in enumerate(chunks_from_chunking):
        chunk_dict = chunk.to_dict() if hasattr(chunk, "to_dict") else chunk
        c_id = chunk_dict["chunk_id"]
        text_content = chunk_dict.get("text_content") or chunk_dict.get("text") or ""
        c_meta = meta_by_id.get(c_id, chunk_dict)

        # Lấy heading_path (dạng list[str] từ ChunkSchema)
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

        # Cấu trúc metadata chuẩn (gộp Document Metadata + ChunkSchema trừ chunk_id & text_content)
        combined_meta = {
            # --- Thuộc tính từ Document Metadata ---
            "document_id": doc_meta["document_id"],
            "version_id": doc_meta["version_id"],
            "title": doc_meta.get("title") or doc_meta.get("canonical_title") or "",
            "file_format": file_format,

            # --- Toàn bộ thuộc tính từ ChunkSchema (bỏ ChunkSource, chunk_id, text_content) ---
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
        vector_records.append(record)

        ids.append(record.id)
        documents.append(record.text)
        # Chuyển list (như heading_path) sang JSON string vì ChromaDB chỉ nhận str, int, float, bool
        metadatas.append(record.to_chroma_metadata())

    if ids:
        collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )
        print(
            f"Đã nhúng và lưu thành công {len(ids)} chunks vào ChromaDB tại '{CHROMA_DB_DIR}'!"
        )
    else:
        print("Không có chunk nào để lưu vào ChromaDB.")

    return vector_records


if __name__ == "__main__":
    # Đọc dữ liệu Document & Version từ Step 1 (Ingestion)
    documents_data = json.loads((INGESTION_OUT_DIR / "documents.json").read_text(encoding="utf-8"))
    versions_data = json.loads((INGESTION_OUT_DIR / "ducuments_version.json").read_text(encoding="utf-8"))

    doc = next(
        (d for d in documents_data if "injection" in d["document_id"]),
        documents_data[0],
    )
    ver = next(v for v in versions_data if v["document_id"] == doc["document_id"])

    chunk_file = CHUNKING_OUT_DIR / f"{ver['version_id']}_chunking.json"
    if chunk_file.exists():
        chunking_output = json.loads(chunk_file.read_text(encoding="utf-8"))
    else:
        chunking_output = {
            "document_id": doc["document_id"],
            "version_id": ver["version_id"],
            "chunking_strategy": "structure_aware",
            "chunks": [
                {
                    "chunk_id": f"{ver['version_id']}_c00000",
                    "document_id": doc["document_id"],
                    "version_id": ver["version_id"],
                    "chunk_idx": 0,
                    "chunk_type": "text",
                    "text_content": (
                        "Injection flaws, such as SQL, NoSQL, OS, and LDAP injection, occur when "
                        "untrusted data is sent to an interpreter as part of a command or query."
                    ),
                    "token_count": 32,
                    "chunk_strategy": "structure_aware",
                    "heading_path": ["A05:2025 - Injection", "Overview"],
                },
                {
                    "chunk_id": f"{ver['version_id']}_c00001",
                    "document_id": doc["document_id"],
                    "version_id": ver["version_id"],
                    "chunk_idx": 1,
                    "chunk_type": "text",
                    "text_content": (
                        "Use a safe API, which avoids the use of the interpreter entirely, "
                        "provides a parameterized interface, or migrates to Object Relational Mapping Tools (ORMs)."
                    ),
                    "token_count": 29,
                    "chunk_strategy": "structure_aware",
                    "heading_path": ["A05:2025 - Injection", "How to Prevent"],
                },
            ],
            "chunk_sources": [],
        }

    # Bước 5.1: Trích xuất & lưu Metadata vào SQLite
    extractor = MetadataExtractor(MetadataStore())
    meta_output = extractor.run(doc, ver, chunking_output)

    # Bước 5.2: Gộp Metadata và nhúng vào ChromaDB
    prepare_and_ingest_to_chroma(
        chunks_from_chunking=chunking_output["chunks"],
        doc_meta_from_diep=meta_output.document,
        chunk_meta_records_from_diep=meta_output.chunks,
    )

