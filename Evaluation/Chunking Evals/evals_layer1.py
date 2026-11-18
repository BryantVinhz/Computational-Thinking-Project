import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parents[1]
RETRIEVAL_DIR = PROJECT_ROOT / "Retrieval"

for p in (PROJECT_ROOT, RETRIEVAL_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from path import CHUNKING_OUT_DIR, PROVENANCE_OUT_DIR, STRATEGY_FOLDER_MAP
from query_vectorizer import QueryVectorizer

REPORT_PATH = CURRENT_DIR / "eval_layer1_report.json"

TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+|\n+")
HEADING_PREFIX_RE = re.compile(r"^\[[^\]]+\]\n?")

STRATEGIES = ["fixed_size", "structure_aware", "semantic"]
ATOMIC_BLOCK_TYPES = {"table", "code", "record"}


def evaluate_strategy_intrinsic(
    strategy_slug: str,
    vectorizer: QueryVectorizer,
) -> dict[str, Any]:
    folder_name = STRATEGY_FOLDER_MAP.get(strategy_slug, strategy_slug)
    db_path = PROVENANCE_OUT_DIR / strategy_slug / "workspace_data.db"
    chunk_dir = CHUNKING_OUT_DIR / folder_name

    if not db_path.exists():
        raise FileNotFoundError(f"Not found Provenance DB: {db_path}")
    if not chunk_dir.is_dir():
        raise FileNotFoundError(f"Not found folder Chunking: {chunk_dir}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Load structural information of all normalized_blocks
    block_rows = conn.execute(
        """
        SELECT normalized_id, version_id, block_type, heading_path_json, normalized_text
        FROM normalized_blocks
        """
    ).fetchall()

    block_meta: dict[str, dict[str, Any]] = {}
    for r in block_rows:
        text = r["normalized_text"] or ""
        block_meta[r["normalized_id"]] = {
            "version_id": r["version_id"],
            "block_type": r["block_type"],
            "heading_path_json": r["heading_path_json"] or "[]",
            "token_len": len(TOKEN_RE.findall(text)),
        }

    # Load chunk <-> block mappings from chunk_sources
    source_rows = conn.execute(
        """
        SELECT chunk_id, normalized_block_id
        FROM chunk_sources
        ORDER BY chunk_id, source_order
        """
    ).fetchall()
    conn.close()

    chunk_blocks: dict[str, list[str]] = defaultdict(list)
    block_to_chunks: dict[str, set[str]] = defaultdict(set)
    for r in source_rows:
        cid = r["chunk_id"]
        bid = r["normalized_block_id"]
        chunk_blocks[cid].append(bid)
        block_to_chunks[bid].add(cid)

    # Calculate Boundary Integrity (table/code/record blocks <= 650 tokens contained intact within 1 chunk)
    eligible_atomic = [
        bid for bid, m in block_meta.items()
        if m["block_type"] in ATOMIC_BLOCK_TYPES and 0 < m["token_len"] <= 650
    ]
    intact_atomic = sum(1 for bid in eligible_atomic if len(block_to_chunks.get(bid, set())) == 1)
    boundary_integrity = (intact_atomic / len(eligible_atomic)) if eligible_atomic else 0.0

    # Calculate Heading Purity (chunks not mixing 2 different heading_paths)
    pure_chunks = sum(
        1 for bids in chunk_blocks.values()
        if len({block_meta[b]["heading_path_json"] for b in bids if b in block_meta}) == 1
    )
    heading_purity = (pure_chunks / len(chunk_blocks)) if chunk_blocks else 0.0

    # Calculate Boundary Continuity (Cosine similarity between the last sentence of chunk i and the first sentence of chunk i+1 in the same section)
    all_chunks: list[dict[str, Any]] = []
    for jf in sorted(chunk_dir.glob("*.json")):
        all_chunks.extend(json.loads(jf.read_text(encoding="utf-8")).get("chunks", []))

    all_chunks.sort(key=lambda c: (c.get("version_id", ""), c.get("chunk_idx", 0)))
    boundary_pairs: list[tuple[str, str]] = []
    for i in range(len(all_chunks) - 1):
        c1, c2 = all_chunks[i], all_chunks[i + 1]
        if c1.get("version_id") != c2.get("version_id"):
            continue
        if not c1.get("heading_path") or c1.get("heading_path") != c2.get("heading_path"):
            continue

        body1 = HEADING_PREFIX_RE.sub("", c1.get("text_content", "")).strip()
        body2 = HEADING_PREFIX_RE.sub("", c2.get("text_content", "")).strip()
        sents1 = [s.strip() for s in SENTENCE_SPLIT_RE.split(body1) if s.strip()]
        sents2 = [s.strip() for s in SENTENCE_SPLIT_RE.split(body2) if s.strip()]
        if sents1 and sents2:
            boundary_pairs.append((sents1[-1], sents2[0]))

    boundary_continuity = 0.0
    if boundary_pairs:
        tails = np.asarray(vectorizer.vectorize_passages([p[0] for p in boundary_pairs]))
        heads = np.asarray(vectorizer.vectorize_passages([p[1] for p in boundary_pairs]))
        boundary_continuity = float(np.mean(np.sum(tails * heads, axis=1)))

    avg_tokens = float(np.mean([c.get("token_count", 0) for c in all_chunks])) if all_chunks else 0.0

    return {
        "total_chunks": len(all_chunks),
        "avg_tokens": round(avg_tokens, 1),
        "eligible_atomic_blocks": len(eligible_atomic),
        "intact_atomic_blocks": intact_atomic,
        "boundary_integrity": round(boundary_integrity * 100, 2),
        "heading_purity": round(heading_purity * 100, 2),
        "boundary_continuity": round(boundary_continuity, 4),
    }


def main() -> None:
    vectorizer = QueryVectorizer()
    report: dict[str, Any] = {}

    for slug in STRATEGIES:
        print(f">>> [Layer 1 - Intrinsic] Đang đánh giá chiến lược: {slug} ...")
        report[slug] = evaluate_strategy_intrinsic(slug, vectorizer)

    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== LAYER 1 · INTRINSIC EVALUATION (ALL 48 DOCUMENTS) ===")
    print("| Strategy | Chunks | Avg Tokens | Boundary Integrity | Heading Purity | Boundary Continuity |")
    print("| :--- | :---: | :---: | :---: | :---: | :---: |")
    for slug in STRATEGIES:
        m = report[slug]
        print(
            f"| **{slug}** | {m['total_chunks']} | {m['avg_tokens']} | "
            f"{m['boundary_integrity']}% ({m['intact_atomic_blocks']}/{m['eligible_atomic_blocks']}) | "
            f"{m['heading_purity']}% | {m['boundary_continuity']} |"
        )

    print(f"\nSaved Layer 1 results at: {REPORT_PATH}")


if __name__ == "__main__":
    main()