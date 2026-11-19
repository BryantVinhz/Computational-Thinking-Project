import json
import sys
from pathlib import Path
from database import SQLiteDatabase
from provenance_repo import ProvenanceRepository
from provenance_resolver import ProvenanceResolver
from provenance import Provenance

CURRENT_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = CURRENT_DIR.parent
PROJECT_ROOT = PIPELINE_DIR.parent

for _p in (PROJECT_ROOT, PIPELINE_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from path import (
    INGESTION_OUT_DIR,
    NORMALIZATION_OUT_DIR,
    CHUNKING_OUT_DIR,
    PROVENANCE_OUT_DIR,
    STRATEGY_FOLDER_MAP,
)

def resolve_strategy_paths(strategy: str) -> tuple[str, Path, Path]:
    norm_strategy = strategy.strip().lower().replace("-", "_").replace(" ", "_")
    folder_name = STRATEGY_FOLDER_MAP.get(norm_strategy, norm_strategy.replace("_", " ").capitalize())
    chunk_folder = CHUNKING_OUT_DIR / folder_name
    db_file = PROVENANCE_OUT_DIR / norm_strategy / "workspace_data.db"
    return norm_strategy, chunk_folder, db_file


def find_chunk_file(chunk_folder: Path, ver_id: str, normalized_strategy: str) -> Path | None:
    exact_file = chunk_folder / f"{ver_id}_{normalized_strategy}.json"
    if exact_file.is_file():
        return exact_file
    candidates = sorted(chunk_folder.glob(f"{ver_id}_*.json"))
    return candidates[0] if candidates else None


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


def build_provenance_for_strategy(strategy: str):
    norm_strategy, chunk_folder, db_file = resolve_strategy_paths(strategy)

    print("\n" + "=" * 60)
    print(f"PROVENANCE INGESTION [{norm_strategy}]")
    print(f"-> Ingestion    : {INGESTION_OUT_DIR}")
    print(f"-> Normalization: {NORMALIZATION_OUT_DIR}")
    print(f"-> Chunking     : {chunk_folder}")
    print(f"-> Target DB    : {db_file}")

    if not chunk_folder.is_dir():
        print(f"[SKIP] Strategy chunking folder does not exist: {chunk_folder}")
        return

    try:
        documents_by_id, versions_by_id = load_ingestion_registry(INGESTION_OUT_DIR)
    except Exception as exc:
        print(f"[ERROR] Cannot load Ingestion registry: {exc}")
        return

    norm_files = sorted(NORMALIZATION_OUT_DIR.glob("*_normalization.json"))
    if not norm_files:
        print("Not found Normalization output")
        return

    db_file.parent.mkdir(parents=True, exist_ok=True)
    db = SQLiteDatabase(db_file)
    db.initialize()

    repo = ProvenanceRepository(db)
    resolver = ProvenanceResolver(repo)
    proven = Provenance(repo=repo, resolver=resolver)
    loaded_count = 0

    for norm_file in norm_files:
        ver_id = norm_file.stem.replace("_normalization", "")
        document_version = versions_by_id.get(ver_id)
        if document_version is None:
            continue
        document_id = document_version["document_id"]
        document = documents_by_id.get(document_id)
        if document is None:
            continue

        try:
            normalized_blocks = load_json_file(norm_file)
        except Exception as exc:
            print(f"[ERROR] Cannot load Normalization {norm_file}: {exc}")
            continue

        chunk_file = find_chunk_file(chunk_folder, ver_id, norm_strategy)
        if chunk_file is None:
            print(f"[WARN] Missing chunk file for {ver_id} in {chunk_folder.name}")
            continue
        try:
            chunk_data = load_json_file(chunk_file)
        except Exception as exc:
            print(f"[ERROR] Cannot load Chunking {chunk_file}: {exc}")
            continue

        chunk_sources = chunk_data.get("chunk_sources", [])
        if document_version["version_id"] != ver_id or document_version["document_id"] != document["document_id"]:
            print(f"[WARN] ID mismatch for {ver_id}")
            continue

        try:
            proven.ingest_provenance(
                document=document,
                document_version=document_version,
                normalized_blocks=normalized_blocks,
                chunk_sources=chunk_sources,
            )
            loaded_count += 1
        except Exception as exc:
            print(f"[ERROR] Cannot ingest {ver_id}: {exc}")

    print(f"COMPLETED [{norm_strategy}]: {loaded_count} documents loaded into SQLite.")
    with db.connect() as conn:
        for table in ["documents", "document_versions", "normalized_blocks", "chunk_sources"]:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"- {table:<20}: {count} rows")


if __name__ == "__main__":
    for strat in ["fixed_size", "structure_aware", "semantic"]:
        build_provenance_for_strategy(strat)