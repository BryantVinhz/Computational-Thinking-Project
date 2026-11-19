import json
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from path import NORMALIZATION_OUT_DIR

GT_PATH = CURRENT_DIR / "ground_truth_chunking.json"


def build_block_doc_offsets() -> dict[str, int]:
    """Calculate the absolute character position of each block in the normalized text of version_id (joined by '\\n\\n')."""
    block_doc_start: dict[str, int] = {}

    for norm_file in sorted(NORMALIZATION_OUT_DIR.glob("*_normalization.json")):
        blocks = json.loads(norm_file.read_text(encoding="utf-8"))
        blocks.sort(key=lambda b: b.get("block_idx", 0))

        cursor = 0
        for idx, b in enumerate(blocks):
            if idx > 0:
                cursor += 2  # Matches the "\n\n" joining convention between blocks
            text = b.get("normalized_text") or ""
            block_doc_start[b["normalized_id"]] = cursor
            cursor += len(text)

    return block_doc_start


def main() -> None:
    block_doc_start = build_block_doc_offsets()
    gt_items = json.loads(GT_PATH.read_text(encoding="utf-8"))

    for item in gt_items:
        for sp in item.get("spans", []):
            bid = sp["normalized_block_id"]
            if bid not in block_doc_start:
                raise KeyError(f"[{item['evidence_id']}] Block not found: {bid}")
            base = block_doc_start[bid]
            sp["doc_char_start"] = base + sp["start_offset"]
            sp["doc_char_end"] = base + sp["end_offset"]

    GT_PATH.write_text(json.dumps(gt_items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Added doc_char_start and doc_char_end for {len(gt_items)} samples at: {GT_PATH}")


if __name__ == "__main__":
    main()