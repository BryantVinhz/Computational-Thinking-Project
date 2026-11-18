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
from retrieval_system import RetrievalSystem

GT_PATH = CURRENT_DIR / "ground_truth_chunking.json"
REPORT_PATH = CURRENT_DIR / "chunking_eval_report.json"

TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+|\n+")
HEADING_PREFIX_RE = re.compile(r"^\[[^\]]+\]\n?")

STRATEGIES = ["fixed_size", "structure_aware", "semantic"]
ATOMIC_BLOCK_TYPES = {"table", "code", "record"}


def get_token_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in TOKEN_RE.finditer(text or "")]


def tokens_in_range(token_spans: list[tuple[int, int]], start: int, end: int) -> set[int]:
    return {idx for idx, (ts, te) in enumerate(token_spans) if max(ts, start) < min(te, end)}


def load_provenance_index(strategy_slug: str) -> tuple[dict[str, list[tuple[int, int]]], dict[str, dict], dict[str, set[tuple[str, int]]], dict[str, list[str]], dict[str, set[str]]]:
    """Nạp ánh xạ token từ SQLite Provenance của chiến lược."""
    db_path = PROVENANCE_OUT_DIR / strategy_slug / "workspace_data.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Không tìm thấy Provenance DB: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    block_rows = conn.execute(
        "SELECT normalized_id, version_id, block_type, heading_path_json, normalized_text FROM normalized_blocks"
    ).fetchall()

    block_token_spans: dict[str, list[tuple[int, int]]] = {}
    block_meta: dict[str, dict] = {}
    for r in block_rows:
        bid = r["normalized_id"]
        tspans = get_token_spans(r["normalized_text"] or "")
        block_token_spans[bid] = tspans
        block_meta[bid] = {
            "version_id": r["version_id"],
            "block_type": r["block_type"],
            "heading_path_json": r["heading_path_json"] or "[]",
            "token_len": len(tspans),
        }

    source_rows = conn.execute(
        "SELECT chunk_id, normalized_block_id, start_offset, end_offset FROM chunk_sources ORDER BY chunk_id, source_order"
    ).fetchall()
    conn.close()

    chunk_tokens: dict[str, set[tuple[str, int]]] = defaultdict(set)
    chunk_blocks: dict[str, list[str]] = defaultdict(list)
    block_to_chunks: dict[str, set[str]] = defaultdict(set)

    for r in source_rows:
        cid = r["chunk_id"]
        bid = r["normalized_block_id"]
        for tidx in tokens_in_range(block_token_spans.get(bid, []), r["start_offset"], r["end_offset"]):
            chunk_tokens[cid].add((bid, tidx))
        chunk_blocks[cid].append(bid)
        block_to_chunks[bid].add(cid)

    return block_token_spans, block_meta, chunk_tokens, chunk_blocks, block_to_chunks


# =============================================================================
# LAYER 1: INTRINSIC EVALUATION (Không chạy Retrieval)
# =============================================================================
def evaluate_layer1_intrinsic(
    strategy_slug: str,
    block_meta: dict[str, dict],
    chunk_blocks: dict[str, list[str]],
    block_to_chunks: dict[str, set[str]],
    vectorizer: QueryVectorizer,
) -> dict[str, Any]:
    folder_name = STRATEGY_FOLDER_MAP.get(strategy_slug, strategy_slug)
    chunk_dir = CHUNKING_OUT_DIR / folder_name
    if not chunk_dir.is_dir():
        raise FileNotFoundError(f"Không tìm thấy thư mục Chunking: {chunk_dir}")

    # 1. Boundary Integrity: Tỷ lệ khối table/code/record <= 650 tokens nằm trọn trong 1 chunk
    eligible_atomic = [
        bid for bid, m in block_meta.items()
        if m["block_type"] in ATOMIC_BLOCK_TYPES and 0 < m["token_len"] <= 650
    ]
    intact_atomic = sum(1 for bid in eligible_atomic if len(block_to_chunks.get(bid, set())) == 1)
    boundary_integrity = (intact_atomic / len(eligible_atomic)) if eligible_atomic else 0.0

    # 2. Document Contextual Coherence:
    #    (a) Heading Purity: Tỷ lệ chunk không trộn nội dung từ nhiều heading_path khác nhau
    pure_chunks = sum(
        1 for bids in chunk_blocks.values()
        if len({block_meta[b]["heading_path_json"] for b in bids if b in block_meta}) == 1
    )
    heading_purity = (pure_chunks / len(chunk_blocks)) if chunk_blocks else 0.0

    #    (b) Boundary Continuity: Cosine similarity giữa câu cuối chunk i và câu đầu chunk i+1 cùng mục
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
        "boundary_integrity": round(boundary_integrity * 100, 2),
        "heading_purity": round(heading_purity * 100, 2),
        "boundary_continuity": round(boundary_continuity, 4),
    }


# =============================================================================
# LAYER 2: RETRIEVAL-BASED EVALUATION (Chroma Token-level Metrics trên Retrieved Chunks)
# =============================================================================
def build_deterministic_search_plan(query: str) -> dict[str, Any]:
    """Tạo QueryPlan chuẩn hóa để cô lập hoàn toàn biến số Retrieval giữa 3 nhánh Chunking."""
    return {
        "resolved_query": query,
        "original_query": query,
        "intent": "SEARCH",
        "confidence": 1.0,
        "context_used": False,
        "entities": {"document": [], "vulnerabilities": []},
        "filter": {"version": None, "document_type": None, "source": []},
        "task": [
            {
                "task_id": "eval_task_001",
                "intent_task": "SEARCH",
                "query": query,
                "objective": {"type": "vulnerabilities", "ids": []},
                "depends_on": None,
            }
        ],
        "need_clarify": False,
        "clarification_message": None,
    }


def evaluate_layer2_retrieval(
    strategy_slug: str,
    gt_units: list[dict[str, Any]],
    block_token_spans: dict[str, list[tuple[int, int]]],
    chunk_tokens: dict[str, set[tuple[str, int]]],
    vectorizer: QueryVectorizer,
    top_k: int = 5,
) -> dict[str, Any]:
    rs = RetrievalSystem(
        query_vectorizer=vectorizer,
        chunking_strategy=strategy_slug,
    )

    overall: dict[str, list[float]] = defaultdict(list)
    by_category: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    for unit in gt_units:
        query = (unit.get("query") or "").strip()
        if not query:
            raise ValueError(f"[{unit.get('evidence_id')}] Thiếu trường 'query' cho đánh giá Layer 2!")

        cat = unit.get("category", "unknown")

        # 1. Tập token chuẩn T_gold
        t_gold: set[tuple[str, int]] = set()
        for sp in unit["spans"]:
            bid = sp["normalized_block_id"]
            for tidx in tokens_in_range(block_token_spans.get(bid, []), sp["start_offset"], sp["end_offset"]):
                t_gold.add((bid, tidx))

        if not t_gold:
            continue

        # 2. Chạy RetrievalSystem lấy Top-K chunks truy xuất được
        plan = build_deterministic_search_plan(query)
        retrieval_out = rs.retrieve(plan)
        retrieved_cids = [ev["chunk_id"] for ev in retrieval_out.get("evidence_list", [])[:top_k]]

        if not retrieved_cids:
            for k in ("token_recall", "token_precision", "token_iou"):
                overall[k].append(0.0)
                by_category[cat][k].append(0.0)
            continue

        # 3. Tính Chroma Token-level Metrics trên tập Retrieved Chunks
        t_ret_union: set[tuple[str, int]] = set()
        total_retrieved_tokens = 0
        for cid in retrieved_cids:
            c_toks = chunk_tokens.get(cid, set())
            t_ret_union |= c_toks
            total_retrieved_tokens += len(c_toks)

        inter_count = len(t_ret_union & t_gold)
        union_count = len(t_ret_union | t_gold)

        recall = inter_count / len(t_gold)
        precision = (inter_count / total_retrieved_tokens) if total_retrieved_tokens > 0 else 0.0
        iou = (inter_count / union_count) if union_count > 0 else 0.0

        for k, val in (("token_recall", recall), ("token_precision", precision), ("token_iou", iou)):
            overall[k].append(val)
            by_category[cat][k].append(val)

    cat_summary = {
        cat: {
            f"token_recall@{top_k}": round(float(np.mean(vals["token_recall"])) * 100, 2),
            f"token_precision@{top_k}": round(float(np.mean(vals["token_precision"])) * 100, 2),
            f"token_iou@{top_k}": round(float(np.mean(vals["token_iou"])) * 100, 2),
        }
        for cat, vals in by_category.items()
    }

    return {
        f"token_recall@{top_k}": round(float(np.mean(overall["token_recall"])) * 100, 2),
        f"token_precision@{top_k}": round(float(np.mean(overall["token_precision"])) * 100, 2),
        f"token_iou@{top_k}": round(float(np.mean(overall["token_iou"])) * 100, 2),
        "by_category": cat_summary,
    }


def main() -> None:
    if not GT_PATH.exists():
        raise FileNotFoundError(f"Không tìm thấy tệp Ground Truth: {GT_PATH}")

    gt_units = json.loads(GT_PATH.read_text(encoding="utf-8"))
    shared_vectorizer = QueryVectorizer()

    report: dict[str, Any] = {}
    for slug in STRATEGIES:
        print(f"\n>>> Đang đánh giá chiến lược: {slug} ...")
        block_token_spans, block_meta, chunk_tokens, chunk_blocks, block_to_chunks = load_provenance_index(slug)

        l1 = evaluate_layer1_intrinsic(slug, block_meta, chunk_blocks, block_to_chunks, shared_vectorizer)
        l2 = evaluate_layer2_retrieval(slug, gt_units, block_token_spans, chunk_tokens, shared_vectorizer, top_k=5)

        report[slug] = {
            "layer1_intrinsic": l1,
            "layer2_retrieval": l2,
        }

    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== LAYER 1 · INTRINSIC EVALUATION (TOÀN BỘ 48 TÀI LIỆU) ===")
    print("| Strategy | Chunks | Avg Tokens | Boundary Integrity | Heading Purity | Boundary Continuity |")
    print("| :--- | :---: | :---: | :---: | :---: | :---: |")
    for slug in STRATEGIES:
        m1 = report[slug]["layer1_intrinsic"]
        print(
            f"| **{slug}** | {m1['total_chunks']} | {m1['avg_tokens']} | "
            f"{m1['boundary_integrity']}% | {m1['heading_purity']}% | {m1['boundary_continuity']} |"
        )

    print("\n=== LAYER 2 · RETRIEVAL-BASED EVALUATION (CHROMA TOKEN-LEVEL @ TOP-5) ===")
    print("| Strategy | Token Recall@5 | Token Precision@5 | Token IoU@5 |")
    print("| :--- | :---: | :---: | :---: |")
    for slug in STRATEGIES:
        m2 = report[slug]["layer2_retrieval"]
        print(
            f"| **{slug}** | {m2['token_recall@5']}% | {m2['token_precision@5']}% | **{m2['token_iou@5']}%** |"
        )

    print("\n=== LAYER 2 · PHÂN RÃ THEO 4 NHÓM KỊCH BẢN (Recall@5 / Precision@5 / IoU@5) ===")
    categories = ["prose_topic", "atomic_structure", "heading_plus_body", "multi_block_rule"]
    print("| Category | Fixed-size | Structure-aware | Semantic |")
    print("| :--- | :---: | :---: | :---: |")
    for cat in categories:
        cells = []
        for slug in STRATEGIES:
            cm = report[slug]["layer2_retrieval"]["by_category"].get(cat, {})
            cells.append(
                f"{cm.get('token_recall@5', 0)}% / {cm.get('token_precision@5', 0)}% / **{cm.get('token_iou@5', 0)}%**"
            )
        print(f"| **{cat}** | {cells[0]} | {cells[1]} | {cells[2]} |")

    print(f"\nĐã lưu báo cáo chi tiết tại: {REPORT_PATH}")


if __name__ == "__main__":
    main()