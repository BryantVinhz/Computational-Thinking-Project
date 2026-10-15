from pathlib import Path

"""
1. Define the Pipeline directory ('.../Document Ingestion and Parsing')
and the project root directory ('.../Computational-Thinking-Project')
"""
PIPELINE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PIPELINE_DIR.parent

"""2. Path to the input data directory"""
DATA_DIR = PROJECT_ROOT / "Data"

"""3. Path to the shared output directory for all steps"""
OUTPUT_DIR = PIPELINE_DIR / "output"
INGESTION_OUT_DIR = OUTPUT_DIR / "Ingestion"
PARSING_OUT_DIR = OUTPUT_DIR / "Parsing"
NORMALIZATION_OUT_DIR = OUTPUT_DIR / "Normalization"
CHUNKING_OUT_DIR = OUTPUT_DIR / "Chunking"
FINAL_OUT_DIR = OUTPUT_DIR / "Final"


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
    if p.is_actual():
        return p
    return (PROJECT_ROOT / p).resolve()