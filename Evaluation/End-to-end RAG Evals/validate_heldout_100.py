import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = next(p for p in [CURRENT_DIR, *CURRENT_DIR.parents] if (p / "path.py").is_file())
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from path import PROVENANCE_OUT_DIR, VERSIONS_CATALOG_PATH

DEV_PATH = CURRENT_DIR / "e2e_eval_dataset.json"
RAW_HELDOUT_PATH = CURRENT_DIR / "raw_heldout_100.json"
FINAL_HELDOUT_PATH = CURRENT_DIR / "e2e_heldout_100.json"


def validate_and_finalize() -> None:
    dev_items = json.loads(DEV_PATH.read_text(encoding="utf-8"))
    dev_blocks = {
        bid
        for it in dev_items
        for bid in ((it.get("evidence_ground_truth") or {}).get("source_block_ids") or [])
    }
    dev_queries = {" ".join(it["query"].lower().split()) for it in dev_items}

    ver_tags: dict[str, str] = {}
    if VERSIONS_CATALOG_PATH.exists():
        for v in json.loads(VERSIONS_CATALOG_PATH.read_text(encoding="utf-8")):
            ver_tags[v["version_id"]] = str(v.get("version_tag") or "")

    db_path = PROVENANCE_OUT_DIR / "semantic" / "workspace_data.db"
    conn = sqlite3.connect(str(db_path))
    ver_cols = {r[1] for r in conn.execute("PRAGMA table_info(document_versions)").fetchall()}
    if "version_tag" in ver_cols:
        for vid, vtag in conn.execute("SELECT version_id, version_tag FROM document_versions").fetchall():
            if vtag:
                ver_tags[vid] = str(vtag)

    block_map = {
        bid: (did, vid)
        for bid, did, vid in conn.execute(
            "SELECT normalized_id, document_id, version_id FROM normalized_blocks"
        ).fetchall()
    }
    conn.close()

    heldout_items = json.loads(RAW_HELDOUT_PATH.read_text(encoding="utf-8"))
    assert len(heldout_items) == 100, f"Expected exactly 100 Held-Out samples, currently found {len(heldout_items)}!"

    cat_counter: Counter = Counter()
    lang_counter: Counter = Counter()

    for idx, item in enumerate(heldout_items, start=1):
        norm_q = " ".join(item["query"].lower().split())
        if norm_q in dev_queries:
            raise ValueError(f"Duplicate query leakage with the 150-sample set at sample #{idx}: {item['query']}")

        ev_gt = item.get("evidence_ground_truth") or {}
        doc_ids: list[str] = []
        v_tags: list[str] = []
        for bid in ev_gt.get("source_block_ids") or []:
            if bid in dev_blocks:
                raise ValueError(f"Leakage of block_id '{bid}' already used in the 150-sample set at sample #{idx}!")
            if bid not in block_map:
                raise ValueError(f"Block ID does not exist in DB: '{bid}'")
            did, vid = block_map[bid]
            if did and did not in doc_ids:
                doc_ids.append(did)
            tag = ver_tags.get(vid, "")
            if tag and tag not in v_tags:
                v_tags.append(tag)

        ev_gt["gold_doc_ids"] = doc_ids
        ev_gt["gold_version_tags"] = v_tags
        ev_gt["enforce_version_isolation"] = item.get("sub_type") in {
            "scoped_version_qa",
            "cross_version_compare",
        } and bool(v_tags)

        item["eval_id"] = f"HELDOUT_{idx:03d}"
        cat_counter[item["category"]] += 1
        lang_counter[item["lang"]] += 1

    FINAL_HELDOUT_PATH.write_text(json.dumps(heldout_items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"=> Validation succeeded with 0% data leakage! Saved {len(heldout_items)} samples at: {FINAL_HELDOUT_PATH}")
    print("   Phân bổ category:", dict(cat_counter))
    print("   Phân bổ ngôn ngữ:", dict(lang_counter))


if __name__ == "__main__":
    validate_and_finalize()