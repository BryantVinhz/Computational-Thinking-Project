import json
import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

random.seed(20261121)

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = next(p for p in [CURRENT_DIR, *CURRENT_DIR.parents] if (p / "path.py").is_file())
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from path import DOCUMENTS_CATALOG_PATH, PROVENANCE_OUT_DIR, VERSIONS_CATALOG_PATH

DEV_DATASET_PATH = CURRENT_DIR / "e2e_eval_dataset.json"
OUT_CANDIDATES = CURRENT_DIR / "e2e_heldout_100_candidates.json"


def _load_catalog_maps() -> tuple[dict[str, str], dict[str, str]]:
    doc_titles: dict[str, str] = {}
    ver_tags: dict[str, str] = {}
    if DOCUMENTS_CATALOG_PATH.exists():
        for d in json.loads(DOCUMENTS_CATALOG_PATH.read_text(encoding="utf-8")):
            doc_titles[d["document_id"]] = d.get("canonical_title") or d.get("title") or d["document_id"]
    if VERSIONS_CATALOG_PATH.exists():
        for v in json.loads(VERSIONS_CATALOG_PATH.read_text(encoding="utf-8")):
            ver_tags[v["version_id"]] = str(v.get("version_tag") or "")
    return doc_titles, ver_tags


def extract_heldout_100_candidates() -> None:
    db_path = PROVENANCE_OUT_DIR / "semantic" / "workspace_data.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Not found database: {db_path}")

    dev_items = json.loads(DEV_DATASET_PATH.read_text(encoding="utf-8"))
    excluded_block_ids: set[str] = {
        bid
        for it in dev_items
        for bid in ((it.get("evidence_ground_truth") or {}).get("source_block_ids") or [])
    }

    doc_titles, ver_tags = _load_catalog_maps()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    ver_cols = {r[1] for r in conn.execute("PRAGMA table_info(document_versions)").fetchall()}
    if "version_tag" in ver_cols:
        for r in conn.execute("SELECT version_id, version_tag FROM document_versions").fetchall():
            if r["version_tag"]:
                ver_tags[r["version_id"]] = str(r["version_tag"])

    rows = conn.execute(
        """
        SELECT normalized_id, document_id, version_id, block_idx, block_type, heading_path_json, normalized_text
        FROM normalized_blocks
        WHERE block_type != 'header_footer'
        ORDER BY document_id, version_id, block_idx
        """
    ).fetchall()
    conn.close()

    by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_doc: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    unseen_pool: list[dict[str, Any]] = []

    for r in rows:
        bid = r["normalized_id"]
        if bid in excluded_block_ids:
            continue
        text = (r["normalized_text"] or "").strip()
        words = text.split()
        if len(words) < 45 or len(words) > 420:
            continue
        hp = json.loads(r["heading_path_json"] or "[]")
        if any("reference" in str(h).lower() for h in hp):
            continue

        doc_id = r["document_id"]
        ver_id = r["version_id"]
        item = {
            "normalized_block_id": bid,
            "document_id": doc_id,
            "canonical_title": doc_titles.get(doc_id, doc_id),
            "version_id": ver_id,
            "version_tag": ver_tags.get(ver_id, ""),
            "block_type": r["block_type"],
            "heading_path": hp,
            "text": text,
        }
        by_version[ver_id].append(item)
        by_doc[doc_id][ver_id].append(item)
        unseen_pool.append(item)

    candidates: list[dict[str, Any]] = []
    used_in_heldout: set[str] = set()

    def pick_unused(pool: list[dict[str, Any]]) -> dict[str, Any]:
        avail = [x for x in pool if x["normalized_block_id"] not in used_in_heldout]
        chosen = random.choice(avail if avail else pool)
        used_in_heldout.add(chosen["normalized_block_id"])
        return chosen

    idx = 1

    # single_document_qa (20 samples: 12 unscoped_natural_qa + 8 scoped_version_qa; 10 VI, 10 EN)
    for i in range(20):
        lang = "vi" if i < 10 else "en"
        is_scoped = (i % 10) >= 6  # 4 VI scoped + 4 EN scoped = 8 scoped
        pool = [b for b in unseen_pool if b["version_tag"]] if is_scoped else unseen_pool
        blk = pick_unused(pool)
        candidates.append({
            "slot_id": f"HO100_{idx:03d}",
            "category": "single_document_qa",
            "sub_type": "scoped_version_qa" if is_scoped else "unscoped_natural_qa",
            "lang": lang,
            "source_blocks": [blk],
        })
        idx += 1

    # multi_document (20 samples: 10 cross_version_compare + 10 multi_source_synthesis; 10 VI, 10 EN)
    multi_ver_docs = [d for d, vmap in by_doc.items() if len(vmap) >= 2]
    for i in range(10):
        lang = "vi" if i < 5 else "en"
        doc_d = random.choice(multi_ver_docs)
        v1, v2 = random.sample(sorted(by_doc[doc_d].keys()), 2)
        b1 = pick_unused(by_doc[doc_d][v1])
        b2 = pick_unused(by_doc[doc_d][v2])
        candidates.append({
            "slot_id": f"HO100_{idx:03d}",
            "category": "multi_document",
            "sub_type": "cross_version_compare",
            "lang": lang,
            "source_blocks": [b1, b2],
        })
        idx += 1

    doc_keys = list(by_doc.keys())
    for i in range(10):
        lang = "vi" if i < 5 else "en"
        chosen_docs = random.sample(doc_keys, 3)
        bundle = [pick_unused(random.choice(list(by_doc[d].values()))) for d in chosen_docs]
        candidates.append({
            "slot_id": f"HO100_{idx:03d}",
            "category": "multi_document",
            "sub_type": "multi_source_synthesis",
            "lang": lang,
            "source_blocks": bundle,
        })
        idx += 1

    # multi_turn_coreference (20 samples: 14 dependent_coreference + 6 independent_topic_shift; 10 VI, 10 EN)
    for i in range(20):
        lang = "vi" if i < 10 else "en"
        is_dep = (i % 10) < 7  # 7 VI + 7 EN = 14 dependent
        b_target = pick_unused(unseen_pool)
        if is_dep:
            same_ver = [
                b for b in by_version[b_target["version_id"]]
                if b["normalized_block_id"] != b_target["normalized_block_id"]
            ]
            b_prev = random.choice(same_ver) if same_ver else b_target
            sub = "dependent_coreference"
        else:
            other_docs = [b for b in unseen_pool if b["document_id"] != b_target["document_id"]]
            b_prev = random.choice(other_docs)
            sub = "independent_topic_shift"
        candidates.append({
            "slot_id": f"HO100_{idx:03d}",
            "category": "multi_turn_coreference",
            "sub_type": sub,
            "lang": lang,
            "context_block": b_prev,
            "source_blocks": [b_target],
        })
        idx += 1

    # traps_hallucination -> false_premise_contradiction (8 samples: 4 VI, 4 EN)
    for i in range(8):
        lang = "vi" if i < 4 else "en"
        blk = pick_unused(unseen_pool)
        candidates.append({
            "slot_id": f"HO100_{idx:03d}",
            "category": "traps_hallucination",
            "sub_type": "false_premise_contradiction",
            "lang": lang,
            "source_blocks": [blk],
        })
        idx += 1

    OUT_CANDIDATES.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Excluded {len(excluded_block_ids)} old blocks and extracted {len(candidates)} Held-Out candidate clusters at: {OUT_CANDIDATES}")


if __name__ == "__main__":
    extract_heldout_100_candidates()