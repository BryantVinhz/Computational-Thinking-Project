from pathlib import Path

"""
Centralized project paths for the whole RAG repository.

1. Define the project root directory ('.../Computational-Thinking-Project')
and the pipeline directory ('.../Document Ingestion and Parsing')
"""
PROJECT_ROOT = Path(__file__).resolve().parent
PIPELINE_DIR = PROJECT_ROOT / "Document Ingestion and Parsing"

"""2. Path to the input data directory"""
DATA_DIR = PROJECT_ROOT / "Data"

"""3. Path to the shared output directory for all steps"""
OUTPUT_DIR = PIPELINE_DIR / "output"
INGESTION_OUT_DIR = OUTPUT_DIR / "Ingestion"
PARSING_OUT_DIR = OUTPUT_DIR / "Parsing"
NORMALIZATION_IN_DIR = PARSING_OUT_DIR
NORMALIZATION_OUT_DIR = OUTPUT_DIR / "Normalization"
CHUNKING_OUT_DIR = OUTPUT_DIR / "Chunking"
PROVENANCE_OUT_DIR = OUTPUT_DIR / "Provenance"
VECTOR_DB_DIR = OUTPUT_DIR / "Vector DB"

"""4. Fixed catalogs shared with Retrieval and Provenance steps"""
DOCUMENTS_CATALOG_PATH = INGESTION_OUT_DIR / "documents.json"
VERSIONS_CATALOG_PATH = INGESTION_OUT_DIR / "documents_version.json"

"""5. Source directories for the downstream stages"""
QUR_DIR = PROJECT_ROOT / "Query Understanding and Routing"
QUR_PACKAGE_DIR = QUR_DIR / "query_understanding_routing"
RETRIEVAL_DIR = PROJECT_ROOT / "Retrieval"
SYNTHESIS_DIR = PROJECT_ROOT / "Response Synthesis"

PIPELINE_METADATA_DIR = PIPELINE_DIR / "Metadata"
PROVENANCE_SRC_DIR = PIPELINE_DIR / "Provenance and Traceability"

"""
6. Default ontology location.

This is the non-environment-aware default used by the Retrieval step.
The Query Understanding & Routing package keeps its own env-overridable
ONTOLOGY_PATH in query_understanding_routing/paths.py.
"""
DEFAULT_ONTOLOGY_PATH = QUR_PACKAGE_DIR / "ontology.yaml"

"""
7. Mapping from a normalized chunking strategy name to its output folder name
(on-disk "Fixed size" / "Structure aware" / "Semantic")
"""
STRATEGY_FOLDER_MAP = {
    "fixed_size": "Fixed size",
    "structure_aware": "Structure aware",
    "semantic": "Semantic",
}


def to_relative_posix(path: Path | str) -> str:
    """
    Convert an absolute local path to a path relative to PROJECT_ROOT
    (Example:'Data/OWASP_Top10_2025.md') for JSON storage
    """
    abs_path = Path(path).resolve()
    try:
        return abs_path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return abs_path.as_posix()


def to_actual_path(rel_posix_path: str | Path) -> Path:
    """
    Resolve a relative path read from JSON into an actual path
    """
    p = Path(rel_posix_path)
    if p.is_absolute():
        return p
    return (PROJECT_ROOT / p).resolve()
