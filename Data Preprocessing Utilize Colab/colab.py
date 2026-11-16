import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
candidates = [
    BASE_DIR.parent / "Document Ingestion and Parsing",
    BASE_DIR.parent / "Computational-Thinking-Project" / "Document Ingestion and Parsing",
    BASE_DIR / "Document Ingestion and Parsing",
]
src_dir = next((p for p in candidates if p.is_dir()), None)

if src_dir is None:
    raise FileNotFoundError(f"Không tìm thấy 'Document Ingestion and Parsing' quanh {BASE_DIR.parent}")

out_zip = BASE_DIR / "for_colab.zip"
skip_output_dirs = {"Chunking", "Provenance", "Vector DB"}

file_count = 0
with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
    for f in src_dir.rglob("*"):
        rel_parts = f.relative_to(src_dir).parts
        if not f.is_file() or f.suffix == ".pyc" or "__pycache__" in rel_parts:
            continue
        if rel_parts[0] == "output" and len(rel_parts) > 1 and rel_parts[1] in skip_output_dirs:
            continue
        zf.write(f, f.relative_to(src_dir.parent).as_posix())
        file_count += 1

print(f"Đã tạo xong '{out_zip}' ({file_count} files)")