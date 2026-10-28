import json
import sys
from pathlib import Path
from database import SQLiteDatabase
from provenance_repo import ProvenanceRepository
from provenance_resolver import ProvenanceResolver
from provenance import Provenance

CURRENT_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = CURRENT_DIR.parent

if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from path import INGESTION_OUT_DIR, NORMALIZATION_OUT_DIR, CHUNKING_OUT_DIR, PROVENANCE_OUT_DIR
CHUNKING_STRATEGY = "Fixed size"

def load_json_file(file_path: Path):
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_ingestion_registry(ingestion_dir: Path):
    
    documents_file = ingestion_dir / "documents.json"
    versions_file = ingestion_dir / "documents_version.json"
    if not documents_file.exists():
        raise FileNotFoundError(f"Not found: {documents_file}")
    if not versions_file.exists():
        raise FileNotFoundError(f"Not found: {versions_file}")
    documents_data = load_json_file(documents_file)
    versions_data = load_json_file(versions_file)

    documents_by_id = {document["document_id"]: document for document in documents_data}
    versions_by_id = {version["version_id"]: version for version in versions_data}
    return documents_by_id, versions_by_id


def find_chunk_file(chunk_folder: Path, ver_id: str) -> Path | None:
    candidates = sorted(chunk_folder.glob(f"{ver_id}*.json"))
    if candidates:
        return candidates[0]
    return None

def main():
    chunk_folder = CHUNKING_OUT_DIR/ CHUNKING_STRATEGY

    print(f"-> Ingestion: {INGESTION_OUT_DIR}")
    print(f"-> Normalization: {NORMALIZATION_OUT_DIR}")
    print(f"-> Chunking: {chunk_folder}")

    try:
        documents_by_id, versions_by_id = load_ingestion_registry(INGESTION_OUT_DIR)
    except Exception as exc:
        print(f"[ERROR] Cannot load Ingestion registry: {exc}")
        return

    print(f"-> Documents: {len(documents_by_id)}")
    print(f"-> DocumentVersions: {len(versions_by_id)}")


    norm_files = sorted(NORMALIZATION_OUT_DIR.glob("*_normalization.json"))
    print(f"-> Normalization outputs: {len(norm_files)}")
    if not norm_files:
        print("Not found Normalization output")
        return


    if not chunk_folder.exists():
        print(f"Not found Chunking folder: {chunk_folder}")
        return


    PROVENANCE_OUT_DIR.mkdir(parents=True, exist_ok=True)
    db_file = PROVENANCE_OUT_DIR / "workspace_data.db"
    db = SQLiteDatabase(db_file)
    db.initialize()


    repo = ProvenanceRepository(db)
    resolver = ProvenanceResolver(repo)
    proven = Provenance(repo=repo, resolver=resolver)
    loaded_count = 0

    for norm_file in norm_files:
        ver_id = norm_file.stem.replace("_normalization", "")
        print("\n" + "-" * 60)
        
        print(f"Processing: {ver_id}")


        document_version = versions_by_id.get(ver_id)
        if document_version is None:
            print(f"Not found DocumentVersionSchema for {ver_id}")
            continue
        document_id = document_version["document_id"]
        document = documents_by_id.get(document_id)
        if document is None:
            print(f"Not found DocumentSchema for {document_id}")
            continue


        try:
            normalized_blocks = load_json_file(norm_file)
        except Exception as exc:
            print(f"[ERROR] Cannot load Normalization {norm_file}: {exc}")
            continue


        chunk_file = find_chunk_file(chunk_folder, ver_id)
        if chunk_file is None:
            print(f"Not found Chunking output for {ver_id}")
            continue
        try:
            chunk_data = load_json_file(chunk_file)
        except Exception as exc:
            print(f"[ERROR] Cannot load Chunking {chunk_file}: {exc}")
            continue


        chunk_sources = chunk_data.get("chunk_sources", [])
        if document_version["version_id"] != ver_id:
            print("version_id mismatch")
            continue
        
        if document_version["document_id"] != document["document_id"]:
            print("document_id mismatch between Document and Version")
            continue

        try:
            proven.ingest_provenance(
                document=document,
                document_version=document_version,
                normalized_blocks=normalized_blocks,
                chunk_sources=chunk_sources,
            )

            loaded_count += 1
            print("LOAD SUCCESS")
            print(f"    document_id : {document['document_id']}")
            print(f"    version_id  : {document_version['version_id']}")
            print(f"    title       : {document['canonical_title']}")
            print(f"    blocks      : {len(normalized_blocks)}")
            print(f"    chunk source: {len(chunk_sources)}")

        except Exception as exc:
            print(f"[ERROR] Cannot ingest {ver_id}: {exc}")

    print("\n" + "=" * 60)
    print(f"COMPLETED: {loaded_count} documents successfully loaded into SQLite.")

    with db.connect() as conn:
        tables = [
            "documents",
            "document_versions",
            "normalized_blocks",
            "chunk_sources",
        ]

        for table in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"- {table:<20}: {count} rows")

    print("=" * 60)
    print(f"Database: {db_file.resolve()}")

if __name__ == "__main__":
    main()