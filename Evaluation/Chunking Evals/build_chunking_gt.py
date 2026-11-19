import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from path import DOCUMENTS_CATALOG_PATH, NORMALIZATION_OUT_DIR

random.seed(42)

DRAFT_PATH = CURRENT_DIR / "chunking_gt_draft.json"
FINAL_PATH = CURRENT_DIR / "ground_truth_chunking.json"

TARGET_PER_CATEGORY = 10
CATEGORIES = [
    "prose_topic",
    "atomic_structure",
    "heading_plus_body",
    "multi_block_rule",
]

"""
How to run:
    - Method 1: 
        - Step 1: python build_chunking_gt.py
        - Step 2: python build_chunking_gt.py --finalize
    - Method 2:
        - Step 1: python build_chunking_gt.py --exact-40
        - Step 2: python build_chunking_gt.py --finalize
"""
def _collect_pools() -> dict[str, list[dict]]:
    docs_meta: dict[str, str] = {}
    if DOCUMENTS_CATALOG_PATH.exists():
        for d in json.loads(DOCUMENTS_CATALOG_PATH.read_text(encoding="utf-8")):
            docs_meta[d["document_id"]] = d.get("canonical_title", "")

    pools: dict[str, list[dict]] = {cat: [] for cat in CATEGORIES}

    for norm_file in sorted(NORMALIZATION_OUT_DIR.glob("*_normalization.json")):
        blocks = json.loads(norm_file.read_text(encoding="utf-8"))
        clean_blocks = [b for b in blocks if b.get("block_type") != "header_footer"]

        for i, b in enumerate(clean_blocks):
            btype = b.get("block_type", "")
            text = (b.get("normalized_text") or "").strip()
            word_len = len(text.split())
            doc_id = b["document_id"]
            ver_id = b["version_id"]
            fmt = b.get("file_format", "")

            # prose_topic: long prose paragraph
            if btype == "paragraph" and 90 <= word_len <= 450:
                pools["prose_topic"].append({
                    "category": "prose_topic",
                    "file_format": fmt,
                    "doc_title": docs_meta.get(doc_id, ""),
                    "document_id": doc_id,
                    "version_id": ver_id,
                    "spans": [{
                        "normalized_block_id": b["normalized_id"],
                        "text": text,
                    }],
                })

            # atomic_structure: table, code, record contained within the 650-token ceiling
            if btype in {"table", "code", "record"} and 30 <= word_len <= 500:
                pools["atomic_structure"].append({
                    "category": "atomic_structure",
                    "file_format": fmt,
                    "doc_title": docs_meta.get(doc_id, ""),
                    "document_id": doc_id,
                    "version_id": ver_id,
                    "spans": [{
                        "normalized_block_id": b["normalized_id"],
                        "text": text,
                    }],
                })

            # heading_plus_body: 1 heading + 1 adjacent paragraph directly below
            if btype == "heading" and i + 1 < len(clean_blocks):
                nxt = clean_blocks[i + 1]
                nxt_text = (nxt.get("normalized_text") or "").strip()
                if nxt.get("block_type") == "paragraph" and 35 <= len(nxt_text.split()) <= 350:
                    pools["heading_plus_body"].append({
                        "category": "heading_plus_body",
                        "file_format": fmt,
                        "doc_title": docs_meta.get(doc_id, ""),
                        "document_id": doc_id,
                        "version_id": ver_id,
                        "spans": [
                            {"normalized_block_id": b["normalized_id"], "text": text},
                            {"normalized_block_id": nxt["normalized_id"], "text": nxt_text},
                        ],
                    })

            # multi_block_rule: 2 adjacent content blocks sharing the same heading_path
            if btype in {"paragraph", "list"} and i + 1 < len(clean_blocks):
                nxt = clean_blocks[i + 1]
                nxt_text = (nxt.get("normalized_text") or "").strip()
                if (
                    nxt.get("block_type") in {"paragraph", "list"}
                    and b.get("heading_path")
                    and b.get("heading_path") == nxt.get("heading_path")
                    and 60 <= (word_len + len(nxt_text.split())) <= 450
                ):
                    pools["multi_block_rule"].append({
                        "category": "multi_block_rule",
                        "file_format": fmt,
                        "doc_title": docs_meta.get(doc_id, ""),
                        "document_id": doc_id,
                        "version_id": ver_id,
                        "spans": [
                            {"normalized_block_id": b["normalized_id"], "text": text},
                            {"normalized_block_id": nxt["normalized_id"], "text": nxt_text},
                        ],
                    })

    return pools


def generate_draft_pool(per_category: int = 15) -> None:
    """Stratified candidate sampling (default 15/group = 60 samples for manual filtering down to exactly 10/group = 40 samples)."""
    pools = _collect_pools()
    drafts: list[dict] = []
    idx = 1

    for cat in CATEGORIES:
        candidates = pools[cat]
        chosen = random.sample(candidates, min(per_category, len(candidates)))
        for item in chosen:
            drafts.append({"evidence_id": f"EV_{idx:03d}", **item})
            idx += 1

    DRAFT_PATH.write_text(json.dumps(drafts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported {len(drafts)} candidate samples ({per_category} samples/group) to:\n  {DRAFT_PATH}")
    print("Next step: Keep the exact 10 best samples per group (40 samples total),")
    print("trim 'text' in the prose_topic group if needed, then re-run with the --finalize flag.")


def finalize_ground_truth() -> None:
    """Verify verbatim substrings, lock start_offset / end_offset, and ensure 10 samples/group."""
    if not DRAFT_PATH.exists():
        raise FileNotFoundError(f"{DRAFT_PATH} not found. Please run without arguments first.")

    block_text_map: dict[str, str] = {}
    for norm_file in NORMALIZATION_OUT_DIR.glob("*_normalization.json"):
        for b in json.loads(norm_file.read_text(encoding="utf-8")):
            block_text_map[b["normalized_id"]] = b.get("normalized_text") or ""

    drafts = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
    final_items: list[dict] = []
    cat_counts: Counter[str] = Counter()

    for idx, item in enumerate(drafts, start=1):
        locked_spans = []
        for sp in item["spans"]:
            bid = sp["normalized_block_id"]
            if bid not in block_text_map:
                raise KeyError(f"[{item['evidence_id']}] Không tìm thấy normalized_block_id: {bid}")

            source_text = block_text_map[bid]
            span_text = sp["text"].strip()
            start_pos = source_text.find(span_text)
            if start_pos == -1:
                raise ValueError(
                    f"[{item['evidence_id']}] The 'text' segment in {bid} does not verbatim match normalized_text.\n"
                    f"Ensure you only trim the start/end of the sentence and do not modify internal characters."
                )

            locked_spans.append({
                "normalized_block_id": bid,
                "start_offset": start_pos,
                "end_offset": start_pos + len(span_text),
                "text": span_text,
            })

        cat = item["category"]
        cat_counts[cat] += 1
        final_items.append({
            "evidence_id": f"EV_{idx:03d}",
            "category": cat,
            "file_format": item["file_format"],
            "document_id": item["document_id"],
            "version_id": item["version_id"],
            "spans": locked_spans,
        })

    FINAL_PATH.write_text(json.dumps(final_items, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Done! Exported {len(final_items)} standard samples to:\n  {FINAL_PATH}")
    print("Statistics by scenario group:")
    for cat in CATEGORIES:
        cnt = cat_counts[cat]
        flag = "OK" if cnt == TARGET_PER_CATEGORY else f"WARNING: {TARGET_PER_CATEGORY} samples required"
        print(f"  - {cat:<20}: {cnt:>2} mẫu [{flag}]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build 40-sample Ground Truth for Chunking Evaluation.")
    parser.add_argument(
        "--exact-40",
        action="store_true",
        help="Sample directly the exact 10 samples/group (40 samples) instead of oversampling 15 samples/group for manual filtering.",
    )
    parser.add_argument(
        "--finalize",
        action="store_true",
        help="Verify substrings and lock offset coordinates to ground_truth_chunking.json.",
    )
    args = parser.parse_args()

    if args.finalize:
        finalize_ground_truth()
    else:
        generate_draft_pool(per_category=10 if args.exact_40 else 15)