import argparse
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = next(
    p for p in [BASE_DIR.parent, BASE_DIR.parent / "Computational-Thinking-Project", BASE_DIR]
    if (p / "path.py").is_file()
)

OUT_ZIP = BASE_DIR / "eval_for_colab.zip"

CODE_DIRS = [
    "Evaluation/Chunking Evals",
    "Retrieval",
    "Query Understanding and Routing",
    "Document Ingestion and Parsing/Metadata",
    "Document Ingestion and Parsing/Provenance and Traceability",
]

OUTPUT_DIRS = [
    "Document Ingestion and Parsing/output/Ingestion",
    "Document Ingestion and Parsing/output/Chunking",
    "Document Ingestion and Parsing/output/Provenance",
    "Document Ingestion and Parsing/output/Vector DB",
]


def pack(include_output: bool = True) -> None:
    targets = list(CODE_DIRS)
    if include_output:
        targets.extend(OUTPUT_DIRS)

    file_count = 0
    output_file_count = 0

    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(PROJECT_ROOT / "path.py", "path.py")
        file_count += 1

        for rel_dir in targets:
            src_dir = PROJECT_ROOT / rel_dir
            if not src_dir.is_dir():
                raise FileNotFoundError(f"Not found folder: {src_dir}")

            is_output_dir = rel_dir.startswith("Document Ingestion and Parsing/output/")
            for f in src_dir.rglob("*"):
                rel_parts = f.relative_to(PROJECT_ROOT).parts
                if (
                    not f.is_file()
                    or f.suffix == ".pyc"
                    or "__pycache__" in rel_parts
                    or "tests" in rel_parts
                ):
                    continue

                arcname = f.relative_to(PROJECT_ROOT).as_posix()
                if rel_parts[:2] == ("Evaluation", "Chunking Evals") and f.suffix == ".py":
                    code = f.read_text(encoding="utf-8").replace("CURRENT_DIR.parents[2]", "CURRENT_DIR.parents[1]")
                    zf.writestr(arcname, code)
                else:
                    zf.write(f, arcname)

                file_count += 1
                if is_output_dir:
                    output_file_count += 1

    size_mb = OUT_ZIP.stat().st_size / (1024 * 1024)
    print(f"Done '{OUT_ZIP}' ({file_count} files, {size_mb:.2f} MB)")
    print(f"  - Folder output/ (Ingestion, Chunking, Provenance, Vector DB): {output_file_count} files")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-output", action="store_true", help="Chỉ nén mã nguồn, bỏ qua output/.")
    args = parser.parse_args()
    pack(include_output=not args.no_output)