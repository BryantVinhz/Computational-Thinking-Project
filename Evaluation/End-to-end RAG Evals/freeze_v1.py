import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = next(p for p in [CURRENT_DIR, *CURRENT_DIR.parents] if (p / "path.py").is_file())

REPORTS_DIR = CURRENT_DIR / "reports"
V1_REPORT_DIR = REPORTS_DIR / "v1_baseline_150"
V1_SNAPSHOT_DIR = CURRENT_DIR / "v1_snapshot"

REPORT_FILES = [
    "generation_report.json",
    "task_report.json",
    "performance_report.json",
    "final_evaluation_report.json",
]

# 6 files affected between v1 and v2
VERSIONED_CODE_FILES = [
    "Query Understanding and Routing/query_understanding_routing/ontology.yaml",
    "Query Understanding and Routing/query_understanding_routing/ontology_loader.py",
    "Query Understanding and Routing/query_understanding_routing/routing_module.py",
    "Query Understanding and Routing/query_understanding_routing/normalization_module.py",
    "Query Understanding and Routing/query_understanding_routing/understanding_module.py",
    "Response Synthesis/verifier.py",
]


def _sha256(fp: Path) -> str:
    h = hashlib.sha256()
    with open(fp, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def freeze_v1(from_zip: Path | None = None) -> None:
    V1_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    V1_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    # Seal the 4 report files from Run 1 (150 samples)
    manifest: dict[str, object] = {
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "system_version": "v1_baseline",
        "dataset": "e2e_eval_dataset.json (150 samples)",
        "reports": {},
    }
    for fname in REPORT_FILES:
        src = REPORTS_DIR / fname
        dst = V1_REPORT_DIR / fname
        if src.is_file():
            shutil.copy2(src, dst)
        elif not dst.is_file():
            raise FileNotFoundError(f"Run 1 report not found: {src}")
        manifest["reports"][fname] = {
            "size_bytes": dst.stat().st_size,
            "sha256": _sha256(dst),
        }

    (V1_REPORT_DIR / "v1_freeze_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[1/2] Frozen 4 Run 1 reports at: {V1_REPORT_DIR}")

    # Save a snapshot of the 6 original v1 source files
    # If accidentally modified the code to v2 locally, pass the path to Run 1's e2e_eval_colab.zip into from_zip to restore the original v1
    if from_zip and from_zip.is_file():
        with zipfile.ZipFile(from_zip, "r") as zf:
            for rel_path in VERSIONED_CODE_FILES:
                dst = V1_SNAPSHOT_DIR / rel_path
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(zf.read(rel_path))
        print(f"[2/2] Extracted original 6 v1 source files from '{from_zip.name}' into: {V1_SNAPSHOT_DIR}")
    else:
        for rel_path in VERSIONED_CODE_FILES:
            src = PROJECT_ROOT / rel_path
            if not src.is_file():
                raise FileNotFoundError(f"Missing source file: {src}")
            dst = V1_SNAPSHOT_DIR / rel_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        print(f"[2/2] Saved snapshot of the current 6 v1 source files into: {V1_SNAPSHOT_DIR}")


if __name__ == "__main__":
    # freeze_v1(from_zip=Path("Evaluation/End-to-end RAG Evals/e2e_eval_colab_v1.zip"))
    freeze_v1()