import json
import os
import zipfile
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = next(p for p in [CURRENT_DIR, *CURRENT_DIR.parents] if (p / "path.py").is_file())
ZIP_PATH = CURRENT_DIR / "e2e_eval_v1_v2_colab.zip"

REQUIRED_FILES = [
    "path.py",
    "Evaluation/End-to-end RAG Evals/e2e_eval_dataset.json",
    "Evaluation/End-to-end RAG Evals/e2e_heldout_100.json",
    "Evaluation/End-to-end RAG Evals/eval_e2e.py",
    "Evaluation/End-to-end RAG Evals/evaluators/__init__.py",
    "Evaluation/End-to-end RAG Evals/evaluators/generation_eval.py",
    "Evaluation/End-to-end RAG Evals/evaluators/e2e_task_eval.py",
    "Evaluation/End-to-end RAG Evals/evaluators/system_perf_eval.py",
    "Evaluation/End-to-end RAG Evals/reports/v1_baseline_150/final_evaluation_report.json",
    "Evaluation/End-to-end RAG Evals/reports/v1_baseline_150/v1_freeze_manifest.json",
    "Document Ingestion and Parsing/output/Provenance/semantic/workspace_data.db",
    "Document Ingestion and Parsing/output/Vector DB/semantic/chroma_security_db/chroma.sqlite3",
]

INCLUDE_DIRS = [
    "Query Understanding and Routing",
    "Retrieval",
    "Response Synthesis",
    "Evaluation/End-to-end RAG Evals/evaluators",
    "Evaluation/End-to-end RAG Evals/v1_snapshot",
    "Evaluation/End-to-end RAG Evals/reports/v1_baseline_150",
    "Document Ingestion and Parsing/Metadata",
    "Document Ingestion and Parsing/Provenance and Traceability",
    "Document Ingestion and Parsing/output/Ingestion",
    "Document Ingestion and Parsing/output/Provenance/semantic",
    "Document Ingestion and Parsing/output/Vector DB/semantic",
]

EXCLUDE_DIRS = {"__pycache__", ".git", ".ipynb_checkpoints", "tests"}


def pack() -> None:
    ds_150 = json.loads((PROJECT_ROOT / "Evaluation/End-to-end RAG Evals/e2e_eval_dataset.json").read_text(encoding="utf-8"))
    ds_100 = json.loads((PROJECT_ROOT / "Evaluation/End-to-end RAG Evals/e2e_heldout_100.json").read_text(encoding="utf-8"))
    print(f">>> Đã xác thực datasets: Dev={len(ds_150)} mẫu | Held-Out={len(ds_100)} mẫu.")

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    file_count = 0
    seen: set[str] = set()

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        def add_file(fp: Path) -> None:
            nonlocal file_count
            arcname = fp.relative_to(PROJECT_ROOT).as_posix()
            if arcname not in seen and fp.is_file():
                zf.write(fp, arcname)
                seen.add(arcname)
                file_count += 1

        for rel_fp in REQUIRED_FILES:
            fp = PROJECT_ROOT / rel_fp
            if not fp.is_file():
                raise FileNotFoundError(f"Missing required file: {fp}")
            add_file(fp)

        for rel_dir in INCLUDE_DIRS:
            target = PROJECT_ROOT / rel_dir
            if not target.is_dir():
                raise FileNotFoundError(f"Missing required directory: {target}")
            for root, dirs, files in os.walk(target):
                dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
                for fname in files:
                    if fname.endswith((".pyc", ".zip")):
                        continue
                    add_file(Path(root) / fname)

    size_mb = ZIP_PATH.stat().st_size / (1024 * 1024)
    print(f"=> Successfully packaged {file_count} files ({size_mb:.2f} MB) -> {ZIP_PATH}")


if __name__ == "__main__":
    pack()