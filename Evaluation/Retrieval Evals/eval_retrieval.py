import argparse
import json
import math
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
PROJECT_ROOT = next(p for p in [CURRENT_DIR, *CURRENT_DIR.parents] if (p / "path.py").is_file())

INGESTION_DIR = PROJECT_ROOT / "Document Ingestion and Parsing"
METADATA_DIR = INGESTION_DIR / "Metadata"
PROVENANCE_DIR = INGESTION_DIR / "Provenance and Traceability"
RETRIEVAL_DIR = PROJECT_ROOT / "Retrieval"
STAGE2_PARENT = PROJECT_ROOT / "Query Understanding and Routing"

for p in (PROJECT_ROOT, INGESTION_DIR, METADATA_DIR, PROVENANCE_DIR, RETRIEVAL_DIR, STAGE2_PARENT):
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from path import PROVENANCE_OUT_DIR
from query_vectorizer import QueryVectorizer
from reranker import CrossEncoderReranker
from retrieval_system import RetrievalSystem

DEFAULT_DATASET_PATH = CURRENT_DIR / "retrieval_eval_dataset.json"
DEFAULT_REPORT_PATH = CURRENT_DIR / "retrieval_eval_report.json"

TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
POSITIVE_CATEGORIES = ["single_aspect", "multi_evidence", "multi_document", "hard_scope"]
ALL_CATEGORIES = POSITIVE_CATEGORIES + ["traps_hallucination"]
UNIT_COVERAGE_THRESHOLD = 0.50


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping or adjacent character intervals [start, end)."""
    valid = sorted((s, e) for s, e in intervals if e > s)
    if not valid:
        return []
    merged = [valid[0]]
    for s, e in valid[1:]:
        ps, pe = merged[-1]
        if s <= pe:
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return merged


def intervals_to_token_set(
    ver_id: str,
    doc_token_spans: list[tuple[int, int]],
    merged_intervals: list[tuple[int, int]],
) -> set[tuple[str, int]]:
    """Map an absolute character interval to a set of token identifiers (version_id, token_idx)."""
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


def build_coordinate_index(
    strategy_slug: str = "semantic",
) -> tuple[dict[str, int], dict[str, list[tuple[int, int]]], dict[str, set[tuple[str, int]]]]:
    """
    Build an absolute character coordinate system for each version_id from SQLite Provenance
    and map every chunk_id to its corresponding set of token identifiers.
    """
    db_path = PROVENANCE_OUT_DIR / strategy_slug / "workspace_data.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Not found Provenance DB: {db_path}")

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
        parts: list[str] = []
        cursor = 0
        for idx, r in enumerate(v_blocks):
            if idx > 0:
                parts.append("\n\n")
                cursor += 2
            text = r["normalized_text"] or ""
            bid = r["normalized_id"]
            block_doc_start[bid] = cursor
            block_ver_map[bid] = ver_id
            parts.append(text)
            cursor += len(text)

        full_text = "".join(parts)
        doc_token_spans[ver_id] = [(m.start(), m.end()) for m in TOKEN_RE.finditer(full_text)]

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
            c_toks |= intervals_to_token_set(ver_id, doc_token_spans[ver_id], merge_intervals(ivs))
        chunk_tokens[cid] = c_toks

    return block_doc_start, doc_token_spans, chunk_tokens


def spans_to_token_set(
    spans: list[dict[str, Any]],
    block_doc_start: dict[str, int],
    doc_token_spans: dict[str, list[tuple[int, int]]],
) -> set[tuple[str, int]]:
    """Convert the list of spans of an evidence_unit into a set of token identifiers."""
    by_ver: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for sp in spans:
        bid = sp["normalized_block_id"]
        ver_id = sp["version_id"]
        if bid not in block_doc_start:
            raise KeyError(f"Not found block_id '{bid}' in workspace_data.db")
        base = block_doc_start[bid]
        by_ver[ver_id].append((base + int(sp["start_offset"]), base + int(sp["end_offset"])))

    tokens: set[tuple[str, int]] = set()
    for ver_id, ivs in by_ver.items():
        tokens |= intervals_to_token_set(ver_id, doc_token_spans.get(ver_id, []), merge_intervals(ivs))
    return tokens


def compute_ils(texts: list[str], vectorizer: QueryVectorizer) -> float | None:
    """Compute Intra-List Similarity between chunks in Top-K."""
    clean_texts = [t.strip() for t in texts if t and t.strip()]
    if len(clean_texts) < 2:
        return None
    vecs = np.asarray(vectorizer.vectorize_passages(clean_texts), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs = vecs / np.maximum(norms, 1e-12)
    sim_matrix = vecs @ vecs.T
    triu_idx = np.triu_indices(len(clean_texts), k=1)
    return float(np.mean(sim_matrix[triu_idx]))


def evaluate_retrieval(
    dataset_path: Path = DEFAULT_DATASET_PATH,
    report_path: Path = DEFAULT_REPORT_PATH,
    strategy: str = "semantic",
    device: str | None = None,
    top_k: int = 5,
    rerank_threshold: float = 0.25,
) -> dict[str, Any]:
    if not dataset_path.exists():
        raise FileNotFoundError(f"Evaluation dataset file not found: {dataset_path}")

    resolved_device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    print(f">>> Initializing Retrieval evaluation system (strategy='{strategy}', device='{resolved_device}', top_k={top_k})...")

    dataset: list[dict[str, Any]] = json.loads(dataset_path.read_text(encoding="utf-8"))
    block_doc_start, doc_token_spans, chunk_tokens = build_coordinate_index(strategy)

    vectorizer = QueryVectorizer(device=resolved_device)
    reranker = CrossEncoderReranker(threshold=rerank_threshold, top_k=None, device=resolved_device)
    rs = RetrievalSystem(
        query_vectorizer=vectorizer,
        reranker=reranker,
        chunking_strategy=strategy,
    )

    # Run 1 warm-up pass to fully load BAAI/bge-m3 and BAAI/bge-reranker-v2-m3 into RAM/VRAM before starting the timer
    print(">>> Running model warm-up...")
    warm_sample = next((x for x in dataset if x.get("expected_evidence_status") == "FULL"), dataset[0])
    _ = rs.retrieve(warm_sample["query_plan"])
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    print(">>> Warm-up complete. Starting benchmark on 125 samples...\n")

    # Metric accumulators
    metrics_overall: dict[str, list[float]] = defaultdict(list)
    metrics_by_cat: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    metrics_by_lang: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    latencies_all: list[float] = []
    latencies_by_cat: dict[str, list[float]] = defaultdict(list)

    trap_total = 0
    trap_abstained = 0
    pos_total = 0
    pos_false_abstained = 0
    per_query_logs: list[dict[str, Any]] = []

    t_bench_start = time.perf_counter()
    for idx, sample in enumerate(dataset, start=1):
        qid = sample["query_id"]
        cat = sample["category"]
        lang = sample["lang"]
        expected_status = sample.get("expected_evidence_status", "FULL")
        plan = sample["query_plan"]

        if torch.cuda.is_available():
            torch.cuda.synchronize()
        t0 = time.perf_counter()
        ret_out = rs.retrieve(plan)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        latency_ms = (time.perf_counter() - t0) * 1000.0

        latencies_all.append(latency_ms)
        latencies_by_cat[cat].append(latency_ms)

        actual_status = ret_out.get("evidence_status", "NONE")
        ev_list_top_k = (ret_out.get("evidence_list") or [])[:top_k]

        # Branch 1: Trap / out-of-scope questions (Negative - expected_evidence_status == "NONE")
        if expected_status == "NONE":
            trap_total += 1
            is_rejected = actual_status == "NONE"
            if is_rejected:
                trap_abstained += 1

            per_query_logs.append({
                "query_id": qid,
                "category": cat,
                "lang": lang,
                "expected_status": expected_status,
                "actual_status": actual_status,
                "abstained_correctly": is_rejected,
                "retrieved_count": len(ev_list_top_k),
                "top1_score": ev_list_top_k[0]["score"] if ev_list_top_k else 0.0,
                "latency_ms": round(latency_ms, 2),
            })
            if idx % 25 == 0 or idx == len(dataset):
                print(f"   ... Đã xử lý {idx}/{len(dataset)} mẫu ({time.perf_counter() - t_bench_start:.1f}s)")
            continue

        # Branch 2: Questions with answers in the corpus (Positive - expected_evidence_status == "FULL")
        pos_total += 1
        if actual_status == "NONE":
            pos_false_abstained += 1

        # Prepare ground-truth token sets for each evidence_unit and the entire question
        units = sample.get("evidence_units") or []
        unit_token_sets: list[tuple[str, int, set[tuple[str, int]]]] = []
        t_gold: set[tuple[str, int]] = set()
        for u in units:
            u_toks = spans_to_token_set(u.get("spans") or [], block_doc_start, doc_token_spans)
            if u_toks:
                unit_token_sets.append((u["unit_id"], int(u.get("relevance_grade", 1)), u_toks))
                t_gold |= u_toks

        # Prepare token sets for each chunk in Top-5
        chunk_tok_per_rank: list[set[tuple[str, int]]] = []
        t_ret: set[tuple[str, int]] = set()
        for ev in ev_list_top_k:
            c_toks = chunk_tokens.get(ev["chunk_id"], set())
            chunk_tok_per_rank.append(c_toks)
            t_ret |= c_toks

        # Evidence Coverage (Token-level @ Top-5)
        if not t_gold or not t_ret:
            token_recall = 0.0
            token_precision = 0.0
            token_iou = 0.0
        else:
            inter = len(t_ret & t_gold)
            union = len(t_ret | t_gold)
            token_recall = inter / len(t_gold)
            token_precision = inter / len(t_ret)
            token_iou = inter / union

        # Ranking Quality (MRR@5 & Novelty-Aware Graded nDCG@5) 
        first_hit_rank: int | None = None
        satisfied_unit_ids: set[str] = set()
        dcg = 0.0

        for rank_idx, c_toks in enumerate(chunk_tok_per_rank, start=1):
            if not c_toks:
                continue
            matched_units = [
                (uid, grade)
                for uid, grade, u_toks in unit_token_sets
                if (len(c_toks & u_toks) / len(u_toks)) >= UNIT_COVERAGE_THRESHOLD
            ]
            if matched_units:
                if first_hit_rank is None:
                    first_hit_rank = rank_idx
                # Only count gain for units covered for the first time to ensure nDCG@5 <= 1.0 and penalize duplicate content
                newly_covered_grades = [
                    grade for uid, grade in matched_units if uid not in satisfied_unit_ids
                ]
                for uid, _ in matched_units:
                    satisfied_unit_ids.add(uid)
                if newly_covered_grades:
                    rel_j = max(newly_covered_grades)
                    dcg += ((2 ** rel_j) - 1) / math.log2(rank_idx + 1)

        mrr = (1.0 / first_hit_rank) if first_hit_rank is not None else 0.0

        ideal_grades = sorted([grade for _, grade, _ in unit_token_sets], reverse=True)[:top_k]
        idcg = sum(((2 ** g) - 1) / math.log2(r + 1) for r, g in enumerate(ideal_grades, start=1))
        ndcg = (dcg / idcg) if idcg > 0 else 0.0

        # Retrieval Diversity (ILS@5 & Unique Evidence Coverage@5)
        ils_val = compute_ils([ev.get("text_content", "") for ev in ev_list_top_k], vectorizer)
        uec = (len(satisfied_unit_ids) / len(unit_token_sets)) if unit_token_sets else 0.0

        # Record scores into aggregation groups
        metric_values = {
            "token_recall@5": token_recall,
            "token_precision@5": token_precision,
            "token_iou@5": token_iou,
            "mrr@5": mrr,
            "ndcg@5": ndcg,
            "uec@5": uec,
        }
        for m_key, m_val in metric_values.items():
            metrics_overall[m_key].append(m_val)
            metrics_by_cat[cat][m_key].append(m_val)
            metrics_by_lang[lang][m_key].append(m_val)

        if ils_val is not None:
            metrics_overall["ils@5"].append(ils_val)
            metrics_by_cat[cat]["ils@5"].append(ils_val)
            metrics_by_lang[lang]["ils@5"].append(ils_val)

        per_query_logs.append({
            "query_id": qid,
            "category": cat,
            "lang": lang,
            "expected_status": expected_status,
            "actual_status": actual_status,
            "retrieved_count": len(ev_list_top_k),
            "top1_score": ev_list_top_k[0]["score"] if ev_list_top_k else 0.0,
            "metrics": {
                "token_recall@5": round(token_recall * 100, 2),
                "token_precision@5": round(token_precision * 100, 2),
                "token_iou@5": round(token_iou * 100, 2),
                "mrr@5": round(mrr, 4),
                "ndcg@5": round(ndcg, 4),
                "ils@5": round(ils_val, 4) if ils_val is not None else None,
                "uec@5": round(uec * 100, 2),
            },
            "latency_ms": round(latency_ms, 2),
        })

        if idx % 25 == 0 or idx == len(dataset):
            print(f"   ... Đã xử lý {idx}/{len(dataset)} mẫu ({time.perf_counter() - t_bench_start:.1f}s)")

    def _summarize_metrics(bucket: dict[str, list[float]]) -> dict[str, Any]:
        return {
            "token_recall@5_pct": round(float(np.mean(bucket["token_recall@5"])) * 100, 2) if bucket["token_recall@5"] else 0.0,
            "token_precision@5_pct": round(float(np.mean(bucket["token_precision@5"])) * 100, 2) if bucket["token_precision@5"] else 0.0,
            "token_iou@5_pct": round(float(np.mean(bucket["token_iou@5"])) * 100, 2) if bucket["token_iou@5"] else 0.0,
            "mrr@5": round(float(np.mean(bucket["mrr@5"])), 4) if bucket["mrr@5"] else 0.0,
            "ndcg@5": round(float(np.mean(bucket["ndcg@5"])), 4) if bucket["ndcg@5"] else 0.0,
            "ils@5": round(float(np.mean(bucket["ils@5"])), 4) if bucket["ils@5"] else 0.0,
            "ils_valid_queries": len(bucket["ils@5"]),
            "uec@5_pct": round(float(np.mean(bucket["uec@5"])) * 100, 2) if bucket["uec@5"] else 0.0,
        }

    def _summarize_latency(lats: list[float]) -> dict[str, float]:
        if not lats:
            return {"mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0}
        return {
            "mean_ms": round(float(np.mean(lats)), 1),
            "p50_ms": round(float(np.percentile(lats, 50)), 1),
            "p95_ms": round(float(np.percentile(lats, 95)), 1),
        }

    report: dict[str, Any] = {
        "meta": {
            "chunking_strategy": strategy,
            "pipeline": "Hybrid (BM25 + Dense BAAI/bge-m3) -> RRF (k=60) -> Cross-Encoder (BAAI/bge-reranker-v2-m3, tau=0.25) -> MMR",
            "device": resolved_device,
            "top_k": top_k,
            "unit_coverage_threshold": UNIT_COVERAGE_THRESHOLD,
            "total_queries": len(dataset),
            "positive_queries": pos_total,
            "trap_queries": trap_total,
        },
        "overall_positive_metrics": _summarize_metrics(metrics_overall),
        "by_category": {cat: _summarize_metrics(metrics_by_cat[cat]) for cat in POSITIVE_CATEGORIES},
        "by_language": {lang: _summarize_metrics(metrics_by_lang[lang]) for lang in ("en", "vi")},
        "guardrail_calibration": {
            "abstention_recall_pct": round((trap_abstained / trap_total) * 100, 2) if trap_total else 0.0,
            "traps_blocked": f"{trap_abstained}/{trap_total}",
            "false_abstention_rate_pct": round((pos_false_abstained / pos_total) * 100, 2) if pos_total else 0.0,
            "positive_false_abstained": f"{pos_false_abstained}/{pos_total}",
        },
        "efficiency_latency": {
            "overall_125": _summarize_latency(latencies_all),
            "by_category": {cat: _summarize_latency(latencies_by_cat[cat]) for cat in ALL_CATEGORIES},
        },
        "per_query_results": per_query_logs,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # Print standard Markdown summary tables for the report
    ov = report["overall_positive_metrics"]
    gr = report["guardrail_calibration"]
    lat_ov = report["efficiency_latency"]["overall_125"]

    print("\n" + "=" * 95)
    print("RETRIEVAL PIPELINE EVALUATION RESULTS (FIXED PIPELINE @ TOP-5, N = 125)")
    print("=" * 95)

    print("\n### Table 1. Retrieval Quality Summary (100 Positive Samples)")
    print("| Token Recall@5 | Token Precision@5 | Token IoU@5 | MRR@5 | nDCG@5 | ILS@5 | Unique Ev. Cov@5 |")
    print("| :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    print(
        f"| **{ov['token_recall@5_pct']}%** | **{ov['token_precision@5_pct']}%** | **{ov['token_iou@5_pct']}%** | "
        f"**{ov['mrr@5']}** | **{ov['ndcg@5']}** | **{ov['ils@5']}** | **{ov['uec@5_pct']}%** |"
    )

    print("\n### Table 2. Breakdown by Scenario Categories (Categories)")
    print("| Category | Recall@5 | Precision@5 | IoU@5 | MRR@5 | nDCG@5 | ILS@5 | UEC@5 |")
    print("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    for cat in POSITIVE_CATEGORIES:
        m = report["by_category"][cat]
        print(
            f"| `{cat}` | {m['token_recall@5_pct']}% | {m['token_precision@5_pct']}% | {m['token_iou@5_pct']}% | "
            f"{m['mrr@5']} | {m['ndcg@5']} | {m['ils@5']} | {m['uec@5_pct']}% |"
        )

    print("\n### Table 3. Breakdown by Query Language (Language)")
    print("| Language | Recall@5 | Precision@5 | IoU@5 | MRR@5 | nDCG@5 | ILS@5 | UEC@5 |")
    print("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    for lang in ("en", "vi"):
        m = report["by_language"][lang]
        print(
            f"| **{lang.upper()}** | {m['token_recall@5_pct']}% | {m['token_precision@5_pct']}% | {m['token_iou@5_pct']}% | "
            f"{m['mrr@5']} | {m['ndcg@5']} | {m['ils@5']} | {m['uec@5_pct']}% |"
        )

    print("\n### Table 4. Latency Performance & Safety Abstention Guardrail")
    print("| Group / Metric | Mean Latency | P50 Latency | P95 Latency | Guardrail Stat |")
    print("| :--- | :---: | :---: | :---: | :---: |")
    print(
        f"| **Overall (125 queries)** | {lat_ov['mean_ms']} ms | {lat_ov['p50_ms']} ms | {lat_ov['p95_ms']} ms | "
        f"False Abstention: {gr['false_abstention_rate_pct']}% ({gr['positive_false_abstained']}) |"
    )
    for cat in ALL_CATEGORIES:
        lc = report["efficiency_latency"]["by_category"][cat]
        extra = (
            f"Abstention Recall: **{gr['abstention_recall_pct']}%** ({gr['traps_blocked']})"
            if cat == "traps_hallucination"
            else "-"
        )
        print(f"| `{cat}` | {lc['mean_ms']} ms | {lc['p50_ms']} ms | {lc['p95_ms']} ms | {extra} |")

    print(f"\n=> Saved full detailed report at: {report_path}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate fixed Retrieval pipeline across 125 stratified queries.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH, help="Path to retrieval_eval_dataset.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT_PATH, help="Path to output JSON report")
    parser.add_argument("--strategy", type=str, default="semantic", help="Chunking strategy (default: semantic)")
    parser.add_argument("--device", type=str, default=None, help="Device: cuda or cpu (auto-detected if omitted)")
    parser.add_argument("--top-k", type=int, default=5, help="Top-K cutoff for metrics (default: 5)")
    args = parser.parse_args()

    evaluate_retrieval(
        dataset_path=args.dataset,
        report_path=args.output,
        strategy=args.strategy,
        device=args.device,
        top_k=args.top_k,
    )