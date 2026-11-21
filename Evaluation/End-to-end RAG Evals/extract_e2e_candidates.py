import json
import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

random.seed(2026)

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = next(p for p in [CURRENT_DIR, *CURRENT_DIR.parents] if (p / "path.py").is_file())
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from path import DOCUMENTS_CATALOG_PATH, PROVENANCE_OUT_DIR, VERSIONS_CATALOG_PATH

OUT_CANDIDATES = CURRENT_DIR / "e2e_raw_candidates.json"


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


def extract_candidates() -> None:
    db_path = PROVENANCE_OUT_DIR / "semantic" / "workspace_data.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Không tìm thấy database: {db_path}")

    doc_titles, ver_tags = _load_catalog_maps()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Lấy thông tin bảng document_versions nếu có
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
    all_substantive: list[dict[str, Any]] = []

    for r in rows:
        text = (r["normalized_text"] or "").strip()
        words = text.split()
        if len(words) < 40 or len(words) > 450:
            continue
        hp = json.loads(r["heading_path_json"] or "[]")
        if any("reference" in str(h).lower() for h in hp):
            continue

        doc_id = r["document_id"]
        ver_id = r["version_id"]
        item = {
            "normalized_block_id": r["normalized_id"],
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
        all_substantive.append(item)

    candidates: list[dict[str, Any]] = []
    idx = 1

    # 1. Nhóm single_document_qa (30 mẫu: 15 VI, 15 EN)
    single_pool = random.sample(all_substantive, min(30, len(all_substantive)))
    for i, blk in enumerate(single_pool):
        candidates.append({
            "slot_id": f"SLOT_{idx:03d}",
            "category": "single_document_qa",
            "sub_type": "version_specific" if (i >= 20 and blk["version_tag"]) else "single_doc_lookup",
            "lang": "vi" if i < 15 else "en",
            "source_blocks": [blk],
        })
        idx += 1

    # 2. Nhóm multi_document (30 mẫu: 15 cross-version COMPARE + 15 multi-source SYNTHESIS)
    multi_ver_docs = [d for d, vmap in by_doc.items() if len(vmap) >= 2]
    cross_ver_pairs: list[list[dict[str, Any]]] = []
    for d in multi_ver_docs:
        v_ids = sorted(by_doc[d].keys())
        for _ in range(10):
            v1, v2 = random.sample(v_ids, 2)
            b1 = random.choice(by_doc[d][v1])
            b2 = random.choice(by_doc[d][v2])
            cross_ver_pairs.append([b1, b2])

    for i, pair in enumerate(random.sample(cross_ver_pairs, min(15, len(cross_ver_pairs)))):
        candidates.append({
            "slot_id": f"SLOT_{idx:03d}",
            "category": "multi_document",
            "sub_type": "cross_version_compare",
            "lang": "vi" if i < 8 else "en",
            "source_blocks": pair,
        })
        idx += 1

    doc_keys = list(by_doc.keys())
    for i in range(15):
        chosen_docs = random.sample(doc_keys, min(3, len(doc_keys)))
        bundle = [random.choice(list(by_doc[d].values())[0]) for d in chosen_docs]
        candidates.append({
            "slot_id": f"SLOT_{idx:03d}",
            "category": "multi_document",
            "sub_type": "multi_source_synthesis",
            "lang": "vi" if i < 7 else "en",
            "source_blocks": bundle,
        })
        idx += 1

    # 3. Nhóm multi_turn_coreference (30 mẫu: 20 dependent + 10 topic_shift)
    used_bids = {b["normalized_block_id"] for c in candidates for b in c["source_blocks"]}
    remaining = [b for b in all_substantive if b["normalized_block_id"] not in used_bids]
    mt_pool = random.sample(remaining, min(40, len(remaining)))

    for i in range(30):
        is_dependent = i < 20
        lang = "vi" if i % 2 == 0 else "en"
        if is_dependent:
            # Lấy 2 block cùng 1 phiên bản tài liệu (Lượt 1 hỏi block 1, Lượt 2 hỏi nối tiếp block 2 dùng đại từ)
            b_target = mt_pool[i]
            same_ver = [b for b in by_version[b_target["version_id"]] if b["normalized_block_id"] != b_target["normalized_block_id"]]
            b_prev = random.choice(same_ver) if same_ver else b_target
            candidates.append({
                "slot_id": f"SLOT_{idx:03d}",
                "category": "multi_turn_coreference",
                "sub_type": "dependent_coreference",
                "lang": lang,
                "context_block": b_prev,
                "source_blocks": [b_target],
            })
        else:
            # Topic-shift: Lượt 1 nói về tài liệu A, Lượt 2 hỏi độc lập về tài liệu B (context_used=False)
            b_target = mt_pool[i]
            b_prev = mt_pool[(i + 10) % len(mt_pool)]
            candidates.append({
                "slot_id": f"SLOT_{idx:03d}",
                "category": "multi_turn_coreference",
                "sub_type": "independent_topic_shift",
                "lang": lang,
                "context_block": b_prev,
                "source_blocks": [b_target],
            })
        idx += 1

    # 4. 10 đoạn sự thật gốc cho nhóm con false_premise_contradiction của traps_hallucination
    premise_pool = random.sample(remaining[30:] if len(remaining) > 30 else all_substantive, 10)
    for i, blk in enumerate(premise_pool):
        candidates.append({
            "slot_id": f"SLOT_{idx:03d}",
            "category": "traps_hallucination",
            "sub_type": "false_premise_contradiction",
            "lang": "vi" if i < 5 else "en",
            "source_blocks": [blk],
        })
        idx += 1

    OUT_CANDIDATES.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Đã xuất {len(candidates)} cụm ngữ liệu gốc tại: {OUT_CANDIDATES}")


if __name__ == "__main__":
    extract_candidates()