import inspect
import json
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import torch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parents[1]
RETRIEVAL_DIR = PROJECT_ROOT / "Retrieval"

for p in (PROJECT_ROOT, RETRIEVAL_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from path import PROVENANCE_OUT_DIR
from query_vectorizer import QueryVectorizer
from reranker import CrossEncoderReranker
from retrieval_system import RetrievalSystem

GT_PATH = CURRENT_DIR / "ground_truth_chunking.json"
REPORT_PATH = CURRENT_DIR / "eval_layer2_report.json"

TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
STRATEGIES = ["fixed_size", "structure_aware", "semantic"]


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Gộp các khoảng ký tự [start, end) giao nhau hoặc liền kề trong cùng một tài liệu."""
    valid = sorted((s, e) for s, e in intervals if e > s)
    if not valid:
        return []
    merged = [valid[0]]
    for s, e in valid[1:]:
        prev_s, prev_e = merged[-1]
        if s <= prev_e:
            merged[-1] = (prev_s, max(prev_e, e))
        else:
            merged.append((s, e))
    return merged


def intervals_to_token_set(
    ver_id: str,
    doc_token_spans: list[tuple[int, int]],
    merged_intervals: list[tuple[int, int]],
) -> set[tuple[str, int]]:
    """Ánh xạ tập khoảng ký tự tuyệt đối sang tập chỉ số token của version_id."""
    tokens: set[tuple[str, int]] = set()
    if not merged_intervals or not doc_token_spans:
        return tokens

    i_idx, n_int = 0, len(merged_intervals)
    for t_idx, (ts, te) in enumerate(doc_token_spans):
        while i_idx < n_int and merged_intervals[i_idx][1] <= ts:
            i_idx += 1
        if i_idx >= n_int:
            break
        ms, me = merged_intervals[i_idx]
        if max(ts, ms) < min(te, me):
            tokens.add((ver_id, t_idx))
    return tokens


def build_document_coordinate_index(
    strategy_slug: str,
) -> tuple[dict[str, int], dict[str, list[tuple[int, int]]], dict[str, set[tuple[str, int]]]]:
    """
    Xây dựng hệ tọa độ ký tự tuyệt đối cho từng version_id (các block nối bằng '\\n\\n')
    và ánh xạ mọi chunk đầu ra về tập token tương ứng trên trục tọa độ tài liệu.
    """
    db_path = PROVENANCE_OUT_DIR / strategy_slug / "workspace_data.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Không tìm thấy Provenance DB: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    block_rows = conn.execute(
        """
        SELECT normalized_id, version_id, block_idx, normalized_text
        FROM normalized_blocks
        ORDER BY version_id, block_idx ASC
        """
    ).fetchall()

    blocks_by_ver: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in block_rows:
        blocks_by_ver[r["version_id"]].append(r)

    block_doc_start: dict[str, int] = {}
    block_ver_map: dict[str, str] = {}
    doc_token_spans: dict[str, list[tuple[int, int]]] = {}

    for ver_id, v_blocks in blocks_by_ver.items():
        doc_parts: list[str] = []
        cursor = 0
        for idx, r in enumerate(v_blocks):
            if idx > 0:
                doc_parts.append("\n\n")
                cursor += 2
            text = r["normalized_text"] or ""
            bid = r["normalized_id"]
            block_doc_start[bid] = cursor
            block_ver_map[bid] = ver_id
            doc_parts.append(text)
            cursor += len(text)

        full_doc_text = "".join(doc_parts)
        doc_token_spans[ver_id] = [(m.start(), m.end()) for m in TOKEN_RE.finditer(full_doc_text)]

    source_rows = conn.execute(
        """
        SELECT chunk_id, normalized_block_id, start_offset, end_offset
        FROM chunk_sources
        ORDER BY chunk_id, source_order
        """
    ).fetchall()
    conn.close()

    raw_chunk_intervals: dict[str, dict[str, list[tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
    for r in source_rows:
        cid = r["chunk_id"]
        bid = r["normalized_block_id"]
        if bid not in block_doc_start:
            continue
        ver_id = block_ver_map[bid]
        base = block_doc_start[bid]
        raw_chunk_intervals[cid][ver_id].append((base + r["start_offset"], base + r["end_offset"]))

    chunk_tokens: dict[str, set[tuple[str, int]]] = {}
    for cid, ver_map in raw_chunk_intervals.items():
        c_toks: set[tuple[str, int]] = set()
        for ver_id, ivs in ver_map.items():
            merged = merge_intervals(ivs)
            c_toks |= intervals_to_token_set(ver_id, doc_token_spans[ver_id], merged)
        chunk_tokens[cid] = c_toks

    return block_doc_start, doc_token_spans, chunk_tokens


def build_search_plan(query: str) -> dict[str, Any]:
    """Tạo QueryPlan chuẩn hóa để cô lập biến số Retrieval giữa 3 chiến lược Chunking."""
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


def evaluate_strategy_retrieval(
    strategy_slug: str,
    gt_units: list[dict[str, Any]],
    vectorizer: QueryVectorizer,
    reranker: CrossEncoderReranker,
    top_k: int = 5,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    block_doc_start, doc_token_spans, chunk_tokens = build_document_coordinate_index(strategy_slug)
    rs = RetrievalSystem(
        query_vectorizer=vectorizer,
        chunking_strategy=strategy_slug,
        reranker=reranker,
    )

    overall: dict[str, list[float]] = defaultdict(list)
    by_category: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_lang: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    total_units = len(gt_units)
    for idx, unit in enumerate(gt_units, start=1):
        spans = unit.get("spans") or []
        if not spans:
            continue

        query = (unit.get("query") or "").strip()
        cat = unit.get("category", "unknown")
        lang = unit.get("lang", "unknown")
        ver_id = unit["version_id"]

        raw_gold_intervals: list[tuple[int, int]] = []
        for sp in spans:
            if "doc_char_start" in sp and "doc_char_end" in sp:
                raw_gold_intervals.append((sp["doc_char_start"], sp["doc_char_end"]))
            else:
                base = block_doc_start[sp["normalized_block_id"]]
                raw_gold_intervals.append((base + sp["start_offset"], base + sp["end_offset"]))

        merged_gold = merge_intervals(raw_gold_intervals)
        t_gold = intervals_to_token_set(ver_id, doc_token_spans.get(ver_id, []), merged_gold)
        if not t_gold:
            continue

        plan = build_search_plan(query)
        retrieval_out = rs.retrieve(plan)
        retrieved_cids = [ev["chunk_id"] for ev in retrieval_out.get("evidence_list", [])[:top_k]]

        t_ret: set[tuple[str, int]] = set()
        for cid in retrieved_cids:
            t_ret |= chunk_tokens.get(cid, set())

        if not t_ret:
            recall, precision, iou = 0.0, 0.0, 0.0
        else:
            inter_count = len(t_ret & t_gold)
            union_count = len(t_ret | t_gold)
            recall = inter_count / len(t_gold)
            precision = inter_count / len(t_ret)
            iou = inter_count / union_count

        for k, val in (("token_recall", recall), ("token_precision", precision), ("token_iou", iou)):
            overall[k].append(val)
            by_category[cat][k].append(val)
            by_lang[lang][k].append(val)

        if idx % 10 == 0 or idx == total_units:
            print(f"   ... [{strategy_slug}] {idx}/{total_units} queries ({time.perf_counter() - t0:.1f}s)")

    def _summarize(bucket: dict[str, dict[str, list[float]]]) -> dict[str, dict[str, float]]:
        return {
            key: {
                f"token_recall@{top_k}": round(float(np.mean(vals["token_recall"])) * 100, 2),
                f"token_precision@{top_k}": round(float(np.mean(vals["token_precision"])) * 100, 2),
                f"token_iou@{top_k}": round(float(np.mean(vals["token_iou"])) * 100, 2),
            }
            for key, vals in bucket.items()
        }

    return {
        f"token_recall@{top_k}": round(float(np.mean(overall["token_recall"])) * 100, 2),
        f"token_precision@{top_k}": round(float(np.mean(overall["token_precision"])) * 100, 2),
        f"token_iou@{top_k}": round(float(np.mean(overall["token_iou"])) * 100, 2),
        "by_category": _summarize(by_category),
        "by_lang": _summarize(by_lang),
    }


def main() -> None:
    if not GT_PATH.exists():
        raise FileNotFoundError(f"Không tìm thấy tệp Ground Truth: {GT_PATH}")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f">>> Khởi tạo Vectorizer & Reranker trên thiết bị: {device}")

    gt_units = json.loads(GT_PATH.read_text(encoding="utf-8"))

    vec_kwargs = {"device": device} if "device" in inspect.signature(QueryVectorizer.__init__).parameters else {}
    vectorizer = QueryVectorizer(**vec_kwargs)

    rerank_kwargs: dict[str, Any] = {"threshold": 0.0, "top_k": None}
    if "device" in inspect.signature(CrossEncoderReranker.__init__).parameters:
        rerank_kwargs["device"] = device
    shared_reranker = CrossEncoderReranker(**rerank_kwargs)

    report: dict[str, Any] = {}
    for slug in STRATEGIES:
        print(f"\n>>> [Layer 2 - Retrieval] Đang đánh giá chiến lược: {slug} ...")
        report[slug] = evaluate_strategy_retrieval(
            slug, gt_units, vectorizer, reranker=shared_reranker, top_k=5
        )

    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== LAYER 2 · RETRIEVAL-BASED EVALUATION (CHROMA TOKEN-LEVEL @ TOP-5) ===")
    print("| Strategy | Token Recall@5 | Token Precision@5 | Token IoU@5 |")
    print("| :--- | :---: | :---: | :---: |")
    for slug in STRATEGIES:
        m = report[slug]
        print(
            f"| **{slug}** | {m['token_recall@5']}% | {m['token_precision@5']}% | **{m['token_iou@5']}%** |"
        )

    print("\n=== LAYER 2 · PHÂN RÃ THEO NGÔN NGỮ TRUY VẤN (Recall@5 / Precision@5 / IoU@5) ===")
    print("| Language | Fixed-size | Structure-aware | Semantic |")
    print("| :--- | :---: | :---: | :---: |")
    for lang in ("en", "vi"):
        cells = []
        for slug in STRATEGIES:
            lm = report[slug]["by_lang"].get(lang, {})
            cells.append(
                f"{lm.get('token_recall@5', 0)}% / {lm.get('token_precision@5', 0)}% / **{lm.get('token_iou@5', 0)}%**"
            )
        print(f"| **{lang.upper()}** | {cells[0]} | {cells[1]} | {cells[2]} |")

    print("\n=== LAYER 2 · PHÂN RÃ THEO 4 NHÓM KỊCH BẢN (Recall@5 / Precision@5 / IoU@5) ===")
    categories = ["prose_topic", "atomic_structure", "heading_plus_body", "multi_block_rule"]
    print("| Category | Fixed-size | Structure-aware | Semantic |")
    print("| :--- | :---: | :---: | :---: |")
    for cat in categories:
        cells = []
        for slug in STRATEGIES:
            cm = report[slug]["by_category"].get(cat, {})
            cells.append(
                f"{cm.get('token_recall@5', 0)}% / {cm.get('token_precision@5', 0)}% / **{cm.get('token_iou@5', 0)}%**"
            )
        print(f"| **{cat}** | {cells[0]} | {cells[1]} | {cells[2]} |")

    print(f"\nĐã lưu kết quả Layer 2 tại: {REPORT_PATH}")


if __name__ == "__main__":
    main()