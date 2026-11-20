import json
import zipfile
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = next(p for p in [CURRENT_DIR, *CURRENT_DIR.parents] if (p / "path.py").is_file())

OUT_ZIP = CURRENT_DIR / "retrieval_eval_colab.zip"

REQUIRED_FILES = [
    "path.py",
    "Evaluation/Retrieval Evals/retrieval_eval_dataset.json",
    "Evaluation/Retrieval Evals/eval_retrieval.py",
]

INCLUDE_DIRS = [
    "Retrieval",
    "Query Understanding and Routing",
    "Document Ingestion and Parsing/Metadata",
    "Document Ingestion and Parsing/Provenance and Traceability",
    "Document Ingestion and Parsing/output/Ingestion",
    "Document Ingestion and Parsing/output/Provenance/semantic",
    "Document Ingestion and Parsing/output/Vector DB/semantic",
]


def pack_for_colab() -> None:
    ds_path = PROJECT_ROOT / "Evaluation/Retrieval Evals/retrieval_eval_dataset.json"
    if not ds_path.is_file():
        raise FileNotFoundError(f"Standard dataset file not found: {ds_path}")
    samples = json.loads(ds_path.read_text(encoding="utf-8"))
    print(f">>> Validated retrieval_eval_dataset.json: {len(samples)} samples.")

    file_count = 0
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_fp in REQUIRED_FILES:
            fp = PROJECT_ROOT / rel_fp
            if not fp.is_file():
                raise FileNotFoundError(f"Missing required file: {fp}")
            zf.write(fp, rel_fp)
            file_count += 1

        for rel_dir in INCLUDE_DIRS:
            src_dir = PROJECT_ROOT / rel_dir
            if not src_dir.is_dir():
                raise FileNotFoundError(f"Missing required directory: {src_dir}")

            for f in src_dir.rglob("*"):
                rel_parts = f.relative_to(PROJECT_ROOT).parts
                if (
                    not f.is_file()
                    or f.suffix in {".pyc", ".zip"}
                    or "__pycache__" in rel_parts
                    or "tests" in rel_parts
                ):
                    continue

                zf.write(f, f.relative_to(PROJECT_ROOT).as_posix())
                file_count += 1

    size_mb = OUT_ZIP.stat().st_size / (1024 * 1024)
    print(f"=> Successfully created '{OUT_ZIP}' ({file_count} files, {size_mb:.2f} MB)")


if __name__ == "__main__":
    pack_for_colab()