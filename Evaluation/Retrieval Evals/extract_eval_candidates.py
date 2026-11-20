import json
import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = next(p for p in [CURRENT_DIR, *CURRENT_DIR.parents] if (p / "path.py").is_file())
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from path import DOCUMENTS_CATALOG_PATH, PROVENANCE_OUT_DIR, VERSIONS_CATALOG_PATH

OUT_CANDIDATES_PATH = CURRENT_DIR / "eval_candidates_125.json"
SEED = 42


def load_catalogs() -> tuple[dict[str, dict], dict[str, dict]]:
    docs = json.loads(DOCUMENTS_CATALOG_PATH.read_text(encoding="utf-8")) if DOCUMENTS_CATALOG_PATH.exists() else []
    vers = json.loads(VERSIONS_CATALOG_PATH.read_text(encoding="utf-8")) if VERSIONS_CATALOG_PATH.exists() else []
    doc_map = {d["document_id"]: d for d in docs if isinstance(d, dict) and "document_id" in d}
    ver_map = {v["version_id"]: v for v in vers if isinstance(v, dict) and "version_id" in v}
    return doc_map, ver_map


def extract_candidates() -> None:
    rng = random.Random(SEED)
    doc_map, ver_map = load_catalogs()
    db_path = PROVENANCE_OUT_DIR / "semantic" / "workspace_data.db"

    if not db_path.exists():
        raise FileNotFoundError(f"Provenance database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(normalized_blocks)").fetchall()}
    hp_col = "heading_path_json" if "heading_path_json" in cols else "heading_path"

    rows = conn.execute(
        f"""
        SELECT normalized_id, version_id, block_idx, block_type, {hp_col} AS hp, normalized_text
        FROM normalized_blocks
        WHERE block_type IN ('paragraph', 'list', 'table', 'code', 'record')
        ORDER BY version_id, block_idx
        """
    ).fetchall()
    conn.close()

    blocks_by_ver: dict[str, list[dict[str, Any]]] = defaultdict(list)
    blocks_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    vers_by_doc: dict[str, list[str]] = defaultdict(list)

    for r in rows:
        text = (r["normalized_text"] or "").strip()
        if len(text) < 120 or len(text) > 1800:
            continue
        ver_info = ver_map.get(r["version_id"], {})
        doc_id = ver_info.get("document_id", "")
        doc_info = doc_map.get(doc_id, {})
        hp = json.loads(r["hp"]) if isinstance(r["hp"], str) and r["hp"].startswith("[") else []
        if any("reference" in str(h).lower() for h in hp):
            continue

        item = {
            "document_id": doc_id,
            "canonical_title": doc_info.get("canonical_title") or doc_info.get("title") or doc_id,
            "version_id": r["version_id"],
            "version_tag": ver_info.get("version_tag", ""),
            "normalized_block_id": r["normalized_id"],
            "block_type": r["block_type"],
            "heading_path": hp,
            "normalized_text": text,
        }
        blocks_by_ver[r["version_id"]].append(item)
        if doc_id:
            blocks_by_doc[doc_id].append(item)
            if r["version_id"] not in vers_by_doc[doc_id]:
                vers_by_doc[doc_id].append(r["version_id"])

    candidates: list[dict[str, Any]] = []
    all_vers = list(blocks_by_ver.keys())
    all_docs = list(blocks_by_doc.keys())
    rng.shuffle(all_vers)

    # 1. SINGLE_ASPECT (25 samples: 13 VI, 12 EN)
    for i in range(25):
        ver_id = all_vers[i % len(all_vers)]
        blk = rng.choice(blocks_by_ver[ver_id])
        candidates.append({
            "candidate_id": f"CAND_{len(candidates)+1:03d}",
            "category": "single_aspect",
            "lang": "vi" if i % 2 == 0 else "en",
            "candidate_blocks": [blk],
        })

    # 2. MULTI_EVIDENCE (25 samples: 12 VI, 13 EN) — 2-3 blocks with different heading_paths in the same document
    multi_sec_vers = [v for v, blks in blocks_by_ver.items() if len({tuple(b["heading_path"]) for b in blks}) >= 3]
    for i in range(25):
        ver_id = multi_sec_vers[i % len(multi_sec_vers)]
        by_sec: dict[tuple, list[dict]] = defaultdict(list)
        for b in blocks_by_ver[ver_id]:
            by_sec[tuple(b["heading_path"])].append(b)
        chosen_secs = rng.sample(list(by_sec.keys()), k=min(3, len(by_sec)))
        picked = [rng.choice(by_sec[sec]) for sec in chosen_secs]
        candidates.append({
            "candidate_id": f"CAND_{len(candidates)+1:03d}",
            "category": "multi_evidence",
            "lang": "en" if i % 2 == 0 else "vi",
            "candidate_blocks": picked,
        })

    # 3. MULTI_DOCUMENT (25 samples: 13 VI, 12 EN) — 8 cross-version + 17 cross-document
    multi_ver_docs = [d for d, vlist in vers_by_doc.items() if len(vlist) >= 2]
    for i in range(25):
        if i < 8 and multi_ver_docs:
            doc_id = multi_ver_docs[i % len(multi_ver_docs)]
            v_pair = rng.sample(vers_by_doc[doc_id], k=2)
            picked = [rng.choice(blocks_by_ver[v]) for v in v_pair]
        else:
            chosen_docs = rng.sample(all_docs, k=min(3, len(all_docs)))
            picked = [rng.choice(blocks_by_doc[d]) for d in chosen_docs]
        candidates.append({
            "candidate_id": f"CAND_{len(candidates)+1:03d}",
            "category": "multi_document",
            "lang": "vi" if i % 2 == 0 else "en",
            "candidate_blocks": picked,
        })

    # 4. HARD_SCOPE (25 samples: 12 VI, 13 EN) — Documents with specific identifiers/versions
    scoped_docs = [
        d for d in all_docs
        if any(k in d.lower() for k in ("cvss", "avss", "asvs", "top10", "cwe"))
    ] or all_docs
    for i in range(25):
        doc_id = scoped_docs[i % len(scoped_docs)]
        blk = rng.choice(blocks_by_doc[doc_id])
        candidates.append({
            "candidate_id": f"CAND_{len(candidates)+1:03d}",
            "category": "hard_scope",
            "lang": "en" if i % 2 == 0 else "vi",
            "candidate_blocks": [blk],
        })

    OUT_CANDIDATES_PATH.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
    vi_cnt = sum(1 for c in candidates if c["lang"] == "vi")
    print(f"=> Exported {len(candidates)} candidates ({vi_cnt} VI / {len(candidates) - vi_cnt} EN) at: {OUT_CANDIDATES_PATH}")


if __name__ == "__main__":
    extract_candidates()