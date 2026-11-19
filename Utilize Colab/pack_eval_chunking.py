import argparse
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = next(
    p for p in [BASE_DIR.parent, BASE_DIR.parent / "Computational-Thinking-Project", BASE_DIR]
    if (p / "path.py").is_file()
)

OUT_ZIP = BASE_DIR / "eval_for_colab.zip"

LAYER1_FILES = [
    "path.py",
    "Evaluation/Chunking Evals/evals_layer1.py",
    "Document Ingestion and Parsing/Chunking/token_counter.py",
    "Retrieval/query_vectorizer.py",
]

LAYER1_DIRS = [
    "Document Ingestion and Parsing/output/Chunking",
    "Document Ingestion and Parsing/output/Provenance",
]

FULL_EXTRA_DIRS = [
    "Evaluation/Chunking Evals",
    "Retrieval",
    "Query Understanding and Routing",
    "Document Ingestion and Parsing/Metadata",
    "Document Ingestion and Parsing/Provenance and Traceability",
    "Document Ingestion and Parsing/output/Ingestion",
    "Document Ingestion and Parsing/output/Vector DB",
]


def pack(full: bool = False) -> None:
    seen: set[str] = set()
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        def add_file(path: Path) -> None:
            arcname = path.relative_to(PROJECT_ROOT).as_posix()
            if arcname not in seen and path.is_file():
                zf.write(path, arcname)
                seen.add(arcname)

        for rel_file in LAYER1_FILES:
            fp = PROJECT_ROOT / rel_file
            if not fp.is_file():
                raise FileNotFoundError(f"Missing required file: {fp}")
            add_file(fp)

        target_dirs = list(LAYER1_DIRS) + (FULL_EXTRA_DIRS if full else [])
        for rel_dir in target_dirs:
            src_dir = PROJECT_ROOT / rel_dir
            if not src_dir.is_dir():
                raise FileNotFoundError(f"Missing required directory: {src_dir}")
            for f in src_dir.rglob("*"):
                parts = f.relative_to(PROJECT_ROOT).parts
                if f.is_file() and f.suffix != ".pyc" and "__pycache__" not in parts and "tests" not in parts:
                    add_file(f)

    size_mb = OUT_ZIP.stat().st_size / (1024 * 1024)
    mode = "Full (Layer 1 + Layer 2)" if full else "Layer 1 Only"
    print(f"Done '{OUT_ZIP}' [{mode}] ({len(seen)} files, {size_mb:.2f} MB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="Bundle with Vector DB and Layer 2 source code.")
    args = parser.parse_args()
    pack(full=args.full)