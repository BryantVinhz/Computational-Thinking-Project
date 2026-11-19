from __future__ import annotations

import argparse
import inspect
import json
import logging
import re
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = next((p for p in [CURRENT_DIR, *CURRENT_DIR.parents] if (p / "path.py").exists()), CURRENT_DIR)
for _p in (PROJECT_ROOT, PROJECT_ROOT / "Retrieval", PROJECT_ROOT / "Document Ingestion and Parsing" / "Chunking"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:
    from path import CHUNKING_OUT_DIR, PROVENANCE_OUT_DIR, STRATEGY_FOLDER_MAP
except ImportError:
    CHUNKING_OUT_DIR = PROJECT_ROOT / "Document Ingestion and Parsing" / "output" / "Chunking"
    PROVENANCE_OUT_DIR = PROJECT_ROOT / "Document Ingestion and Parsing" / "output" / "Provenance"
    STRATEGY_FOLDER_MAP = {"fixed_size": "Fixed size", "structure_aware": "Structure aware", "semantic": "Semantic"}

try:
    from token_counter import ApproximateTokenCounter
except ImportError:
    ApproximateTokenCounter = None  # type: ignore[misc, assignment]

try:
    from query_vectorizer import QueryVectorizer
except ImportError:
    QueryVectorizer = None  # type: ignore[misc, assignment]

logger = logging.getLogger("eval_layer1")

L_MAX = 650
DEFAULT_STRATEGIES = ["fixed_size", "structure_aware", "semantic"]
ATOMIC_TYPES = {"table", "code", "record"}
TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
EXPECTED_BI = {"fixed_size": (500, 624), "structure_aware": (624, 624), "semantic": (624, 624)}


@dataclass(frozen=True)
class ChunkSpan:
    doc_id: str
    version_id: str
    chunk_id: str
    chunk_idx: int
    char_start: int
    char_end: int
    source_text: str
    indexed_text: str
    member_block_ids: tuple[str, ...]
    member_unit_spans: tuple[tuple[str, int, int], ...]
    is_non_contiguous: bool
    reported_tokens: int | None


class TokenCounter:
    def __init__(self) -> None:
        self._tc = ApproximateTokenCounter() if ApproximateTokenCounter else None
        self.name = f"{ApproximateTokenCounter.__module__}.ApproximateTokenCounter" if self._tc else "regex_TOKEN_RE"

    def count(self, text: str) -> int:
        if not text:
            return 0
        return int(self._tc.count(text)) if self._tc else len(TOKEN_RE.findall(text))


class Embedder:
    """In-memory deduplicated batch embedder wrapping repo's BAAI/bge-m3 QueryVectorizer."""

    def __init__(
        self, model_name: str, window_tokens: int, encode_fn: Callable[[list[str]], np.ndarray] | None = None
    ) -> None:
        self.model_name = model_name
        self.window_tokens = window_tokens
        self._encode_fn = encode_fn
        self._qv = self._st = self._tok = None
        self._max_len: int | None = None
        self._memo: dict[str, np.ndarray] = {}
        if encode_fn is None:
            self._setup()

    def _setup(self) -> None:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        if QueryVectorizer is not None:
            params = inspect.signature(QueryVectorizer.__init__).parameters
            kw = {k: v for k, v in (("model_name", self.model_name), ("device", device)) if k in params}
            self._qv = QueryVectorizer(**kw)
            if hasattr(self._qv, "model_name"):
                self._qv.model_name = self.model_name
            self._st = self._qv._get_model()
        else:
            from sentence_transformers import SentenceTransformer

            self._st = SentenceTransformer(self.model_name, device=device)

        if device == "cuda" and hasattr(self._st, "half"):
            self._st.half()
        self._tok = getattr(self._st, "tokenizer", None)
        st_max = getattr(self._st, "max_seq_length", None)
        tok_max = getattr(self._tok, "model_max_length", None)
        tok_max = None if (tok_max is not None and tok_max > 1_000_000) else tok_max
        if isinstance(st_max, int) and st_max < max(self.window_tokens + 256, 4096):
            target = min(max(self.window_tokens + 256, 4096), tok_max or 4096)
            if target > st_max:
                self._st.max_seq_length = st_max = target
        limits = [x for x in (st_max, tok_max) if isinstance(x, int) and x > 0]
        self._max_len = min(limits) if limits else None
        if self._max_len and self._max_len < self.window_tokens:
            logger.warning(
                "Model '%s' max_seq_length (%d) < --dcc-window-tokens (%d); truncation may occur.",
                self.model_name,
                self._max_len,
                self.window_tokens,
            )

    def embed_texts(self, texts: Sequence[str], batch_size: int = 32) -> dict[str, np.ndarray]:
        missing = list(dict.fromkeys(t for t in texts if t not in self._memo))
        if missing:
            if self._tok and self._max_len:
                for t in missing:
                    if len(self._tok(t, add_special_tokens=True, truncation=False)["input_ids"]) > self._max_len:
                        logger.warning(
                            "Input exceeds '%s' max_seq_length (%d) and will be truncated.",
                            self.model_name,
                            self._max_len,
                        )
                        break

            idx = 0
            while idx < len(missing):
                step = 8 if any(len(t) > 2000 for t in missing[idx : idx + batch_size]) else batch_size
                batch = missing[idx : idx + step]
                idx += step

                if self._encode_fn is not None:
                    arr = np.asarray(self._encode_fn(batch), dtype=np.float32)
                elif self._qv is not None:
                    import torch

                    with torch.inference_mode():
                        arr = np.asarray(self._qv.vectorize_passages(batch), dtype=np.float32)
                else:
                    import torch

                    with torch.inference_mode():
                        arr = np.asarray(
                            self._st.encode(
                                batch, batch_size=len(batch), normalize_embeddings=True, show_progress_bar=False
                            ),
                            dtype=np.float32,
                        )
                norms = np.linalg.norm(arr, axis=1, keepdims=True)
                arr = arr / np.where(norms > 0, norms, 1.0)
                for txt, vec in zip(batch, arr):
                    self._memo[txt] = vec
        return {t: self._memo[t] for t in texts}


def merge_intervals(intervals: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    valid = sorted((int(s), int(e)) for s, e in intervals if int(e) > int(s))
    if not valid:
        return []
    merged = [valid[0]]
    for s, e in valid[1:]:
        if s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def to_chunk_spans(
    strategy: str,
    doc_texts: dict[str, str],
    ver_to_doc: dict[str, str],
    block_lookup: dict[str, tuple[str, int, int, str]],  # bid -> (ver_id, block_idx, abs_start, raw_text)
    chunks_by_ver: dict[str, list[dict[str, Any]]],
    sources_by_chunk: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[ChunkSpan]], float, float, int]:
    """Map chunks onto normalized document char offsets [char_start, char_end) and validate bounds."""
    spans_by_ver: dict[str, list[ChunkSpan]] = {}
    tot_doc_chars = tot_cov_chars = tot_span_chars = non_contig = 0

    for vid, full_text in sorted(doc_texts.items()):
        doc_len = len(full_text)
        tot_doc_chars += doc_len
        ver_spans: list[ChunkSpan] = []

        for c in sorted(chunks_by_ver.get(vid, []), key=lambda x: (x.get("chunk_idx", 0), x.get("chunk_id", ""))):
            cid = c["chunk_id"]
            srcs = sources_by_chunk.get(cid, [])
            if not srcs:
                raise ValueError(f"[{strategy}] Chunk '{cid}' in '{vid}' has no chunk_sources.")

            per_block: dict[str, list[tuple[int, int]]] = defaultdict(list)
            b_order: list[str] = []
            b_indices: list[int] = []
            for s in srcs:
                bid, st, ed = s["normalized_block_id"], int(s["start_offset"]), int(s["end_offset"])
                if bid not in block_lookup:
                    raise ValueError(f"[{strategy}] Unknown block '{bid}' in chunk '{cid}'.")
                b_ver, b_idx, b_base, b_txt = block_lookup[bid]
                if b_ver != vid or st < 0 or ed > len(b_txt) or st >= ed:
                    raise ValueError(f"[{strategy}] Invalid offset [{st}, {ed}) on block '{bid}' in chunk '{cid}'.")
                abs_st, abs_ed = b_base + st, b_base + ed
                if not (0 <= abs_st < abs_ed <= doc_len):
                    raise ValueError(f"[{strategy}] Out-of-bounds span [{abs_st}, {abs_ed}) in '{vid}'.")
                if bid not in per_block:
                    b_order.append(bid)
                    b_indices.append(b_idx)
                per_block[bid].append((abs_st, abs_ed))

            unit_spans = [
                (bid, merge_intervals(per_block[bid])[0][0], merge_intervals(per_block[bid])[-1][1])
                for bid in b_order
            ]
            all_ivs = merge_intervals([iv for ivs in per_block.values() for iv in ivs])
            c_start, c_end = all_ivs[0][0], all_ivs[-1][1]
            src_text = full_text[c_start:c_end]
            if not src_text.strip():
                raise ValueError(f"[{strategy}] Empty source_text for chunk '{cid}'.")

            s_idx = sorted(set(b_indices))
            is_nc = any(s_idx[k + 1] - s_idx[k] > 1 for k in range(len(s_idx) - 1)) or any(
                bool(full_text[all_ivs[k][1] : all_ivs[k + 1][0]].strip()) for k in range(len(all_ivs) - 1)
            )
            non_contig += int(is_nc)
            ver_spans.append(
                ChunkSpan(
                    doc_id=ver_to_doc.get(vid, vid),
                    version_id=vid,
                    chunk_id=cid,
                    chunk_idx=int(c.get("chunk_idx", len(ver_spans))),
                    char_start=c_start,
                    char_end=c_end,
                    source_text=src_text,
                    indexed_text=c.get("text_content") or "",
                    member_block_ids=tuple(b_order),
                    member_unit_spans=tuple(unit_spans),
                    is_non_contiguous=is_nc,
                    reported_tokens=int(c["token_count"]) if c.get("token_count") is not None else None,
                )
            )

        ver_spans.sort(key=lambda x: (x.char_start, x.char_end, x.chunk_idx))
        spans_by_ver[vid] = ver_spans
        ivs = [(s.char_start, s.char_end) for s in ver_spans]
        tot_span_chars += sum(e - s for s, e in ivs)
        tot_cov_chars += sum(e - s for s, e in merge_intervals(ivs))

    cov = tot_cov_chars / tot_doc_chars if tot_doc_chars else 0.0
    ovl = max(0, tot_span_chars - tot_cov_chars) / tot_span_chars if tot_span_chars else 0.0
    return spans_by_ver, cov, ovl, non_contig


def load_strategy(strategy: str, prov_dir: Path, chunk_dir_root: Path, doc_filter: set[str] | None) -> dict[str, Any]:
    db_path = prov_dir / strategy / "workspace_data.db"
    chunk_dir = chunk_dir_root / STRATEGY_FOLDER_MAP.get(strategy, strategy)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    ver_to_doc = {
        r["version_id"]: r["document_id"] for r in conn.execute("SELECT version_id, document_id FROM document_versions")
    }
    blocks_all: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in conn.execute(
        "SELECT normalized_id, document_id, version_id, block_idx, block_type, normalized_text "
        "FROM normalized_blocks ORDER BY version_id, block_idx ASC"
    ):
        ver_to_doc.setdefault(r["version_id"], r["document_id"])
        blocks_all[r["version_id"]].append(dict(r))

    sel_vers = [v for v in sorted(blocks_all) if not doc_filter or v in doc_filter or ver_to_doc.get(v) in doc_filter]
    if not sel_vers:
        conn.close()
        raise ValueError(f"[{strategy}] No documents matched filter.")
    sel_set = set(sel_vers)

    doc_texts: dict[str, str] = {}
    block_lookup: dict[str, tuple[str, int, int, str]] = {}
    block_meta: dict[str, dict[str, Any]] = {}
    for vid in sel_vers:
        parts, cursor = [], 0
        for idx, b in enumerate(blocks_all[vid]):
            if idx > 0:
                parts.append("\n\n")
                cursor += 2
            txt, bid = b["normalized_text"] or "", b["normalized_id"]
            block_lookup[bid] = (vid, int(b["block_idx"]), cursor, txt)
            block_meta[bid] = {
                "version_id": vid,
                "block_type": b["block_type"],
                "char_len": len(txt),
                "tok_len": len(TOKEN_RE.findall(txt)),
            }
            parts.append(txt)
            cursor += len(txt)
        doc_texts[vid] = "".join(parts)

    block_to_chunks: dict[str, set[str]] = defaultdict(set)
    block_local_spans: dict[str, dict[str, list[tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
    sources_by_chunk: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in conn.execute(
        "SELECT chunk_id, normalized_block_id, source_order, start_offset, end_offset "
        "FROM chunk_sources ORDER BY chunk_id, source_order ASC"
    ):
        bid = r["normalized_block_id"]
        if bid in block_meta:
            cid, st, ed = r["chunk_id"], int(r["start_offset"]), int(r["end_offset"])
            block_to_chunks[bid].add(cid)
            block_local_spans[bid][cid].append((st, ed))
            sources_by_chunk[cid].append(dict(r))
    conn.close()

    chunks_by_ver: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for jf in sorted(chunk_dir.glob("*.json")):
        data = json.loads(jf.read_text(encoding="utf-8"))
        f_vid = data.get("version_id") or jf.stem.removesuffix(f"_{strategy}")
        for c in data.get("chunks", []):
            vid = c.get("version_id") or f_vid
            if vid in sel_set:
                chunks_by_ver[vid].append(c)

    spans_by_ver, cov, ovl, non_contig = to_chunk_spans(
        strategy, doc_texts, ver_to_doc, block_lookup, chunks_by_ver, sources_by_chunk
    )
    return {
        "strategy": strategy,
        "doc_texts": doc_texts,
        "ver_to_doc": ver_to_doc,
        "block_meta": block_meta,
        "block_to_chunks": block_to_chunks,
        "block_local_spans": block_local_spans,
        "spans_by_ver": spans_by_ver,
        "coverage_ratio": cov,
        "overlap_ratio": ovl,
        "nonContiguous_chunk_count": non_contig,
    }


def compute_bi_atomic(ds: dict[str, Any], is_full_corpus: bool) -> tuple[dict[str, Any], dict[str, dict[str, int]]]:
    """
    BI_atomic (project-specific variant inspired by, and different from, the paper's Block Integrity):
    fraction of eligible atomic blocks (0 < |b|_tok <= 650) with |C(b)| == 1 (logic unchanged),
    plus secondary `BI_atomic_fully_contained` for overlapping windows.
    """
    per_doc = {vid: {"num": 0, "den": 0} for vid in ds["doc_texts"]}
    intact = contained = den = 0
    for bid, m in ds["block_meta"].items():
        if m["block_type"] not in ATOMIC_TYPES or not (0 < m["tok_len"] <= L_MAX):
            continue
        den += 1
        vid = m["version_id"]
        per_doc[vid]["den"] += 1
        if len(ds["block_to_chunks"].get(bid, set())) == 1:
            intact += 1
            per_doc[vid]["num"] += 1
        if any(
            any(s <= 0 and e >= m["char_len"] for s, e in merge_intervals(ivs))
            for ivs in ds["block_local_spans"].get(bid, {}).values()
        ):
            contained += 1

    if is_full_corpus and ds["strategy"] in EXPECTED_BI and (intact, den) != EXPECTED_BI[ds["strategy"]]:
        logger.warning(
            "[%s] BI_atomic %d/%d differs from known baseline %s.",
            ds["strategy"],
            intact,
            den,
            EXPECTED_BI[ds["strategy"]],
        )
    return {
        "numerator": intact,
        "denominator": den,
        "percent": round(intact / den * 100, 4) if den else 0.0,
        "fully_contained_numerator": contained,
        "fully_contained_percent": round(contained / den * 100, 4) if den else 0.0,
    }, per_doc


def compute_icc(
    ds: dict[str, Any], emb: Embedder, tc: TokenCounter, variant: str, min_unit_tok: int
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Intrachunk Cohesion (ICC) over normalized block units clipped to chunk boundaries."""
    texts_needed: list[str] = []
    valid_units: dict[str, list[str]] = {}
    tot_chunks = tot_valid = 0

    for vid, spans in ds["spans_by_ver"].items():
        ftxt = ds["doc_texts"][vid]
        for sp in spans:
            tot_chunks += 1
            u_texts: list[str] = []
            for _, u_st, u_ed in sp.member_unit_spans:
                ist, ied = max(u_st, sp.char_start), min(u_ed, sp.char_end)
                utxt = ftxt[ist:ied] if ist < ied else ""
                if utxt.strip() and (min_unit_tok <= 0 or tc.count(utxt) >= min_unit_tok):
                    u_texts.append(utxt)
            if len(u_texts) >= 2:
                tot_valid += 1
                valid_units[sp.chunk_id] = u_texts
                c_txt = sp.source_text if variant == "source" else sp.indexed_text
                texts_needed.extend([c_txt, *u_texts])

    vecs = emb.embed_texts(texts_needed)
    per_doc: dict[str, dict[str, Any]] = {}
    doc_vals: list[float] = []
    for vid, spans in ds["spans_by_ver"].items():
        scores = []
        for sp in spans:
            if sp.chunk_id in valid_units:
                c_txt = sp.source_text if variant == "source" else sp.indexed_text
                u_mat = np.stack([vecs[u] for u in valid_units[sp.chunk_id]], axis=0)
                scores.append(float(np.mean(u_mat @ vecs[c_txt])))
        v_ratio = len(scores) / len(spans) if spans else 0.0
        if scores:
            val = max(0.0, float(np.mean(scores)))
            doc_vals.append(val)
            per_doc[vid] = {"ICC": round(val, 6), "icc_valid_chunk_ratio": round(v_ratio, 6)}
        else:
            per_doc[vid] = {"ICC": None, "icc_valid_chunk_ratio": round(v_ratio, 6)}

    n_valid = len(doc_vals)
    return {
        "mean": round(float(np.mean(doc_vals)), 6) if n_valid else None,
        "std": round(float(np.std(doc_vals, ddof=0)), 6) if n_valid else None,
        "valid_chunk_ratio": round(tot_valid / tot_chunks, 6) if tot_chunks else 0.0,
        "n_docs_valid": n_valid,
    }, per_doc


def compute_dcc(
    ds: dict[str, Any], emb: Embedder, tc: TokenCounter, variant: str, win_tokens: int, win_step: int
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Document Contextual Coherence (DCC) using non-overlapping sliding windows <= T tokens."""
    texts_needed: list[str] = []
    doc_wins: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    tot_wins = 0

    for vid, spans in ds["spans_by_ver"].items():
        ftxt = ds["doc_texts"][vid]
        k = len(spans)
        if k < 2:
            continue
        for i in range(0, k, win_step):
            if i + 1 >= k:  # Skip trailing windows that cannot hold >= 2 chunks
                continue
            w_cids = [spans[i].chunk_id]
            w_start, w_end = spans[i].char_start, spans[i].char_end
            w_txt = spans[i].source_text if variant == "source" else spans[i].indexed_text
            for j in range(i + 1, k):
                nxt = spans[j]
                t_st, t_ed = max(nxt.char_start, w_end), nxt.char_end
                new_end = max(w_end, t_ed)
                if variant == "source":
                    cand = ftxt[w_start:new_end]
                else:
                    tail = ftxt[t_st:t_ed] if t_st < t_ed else ""
                    cand = f"{w_txt}{ftxt[w_end:t_st] if t_st > w_end else ''}{tail}" if tail else w_txt
                if len(w_cids) >= 2 and tc.count(cand) > win_tokens:
                    break
                w_cids.append(nxt.chunk_id)
                w_txt, w_end = cand, new_end
                if len(w_cids) == 2 and tc.count(w_txt) > win_tokens:
                    break

            c_txts = [
                (spans[m].source_text if variant == "source" else spans[m].indexed_text)
                for m in range(i, i + len(w_cids))
            ]
            doc_wins[vid].append((w_txt, c_txts))
            texts_needed.extend([w_txt, *c_txts])
            tot_wins += 1

    vecs = emb.embed_texts(texts_needed)
    per_doc: dict[str, dict[str, Any]] = {}
    doc_vals: list[float] = []
    for vid in ds["doc_texts"]:
        w_list = doc_wins.get(vid, [])
        if not w_list:
            per_doc[vid] = {"DCC": None, "n_windows": 0}
            continue
        w_scores = [
            float(np.mean(np.stack([vecs[c] for c in c_txts], axis=0) @ vecs[w_txt]))
            for w_txt, c_txts in w_list
        ]
        val = max(0.0, float(np.mean(w_scores)))
        doc_vals.append(val)
        per_doc[vid] = {"DCC": round(val, 6), "n_windows": len(w_list)}

    n_valid = len(doc_vals)
    return {
        "mean": round(float(np.mean(doc_vals)), 6) if n_valid else None,
        "std": round(float(np.std(doc_vals, ddof=0)), 6) if n_valid else None,
        "n_docs_valid": n_valid,
        "n_windows_total": tot_wins,
    }, per_doc


def run_evaluation(
    out_path: Path,
    strategies: list[str],
    embed_model: str = "BAAI/bge-m3",
    embed_text: str = "source",
    dcc_window_tokens: int = 1500,
    dcc_window_step: int = 1,
    icc_min_unit_tokens: int = 0,
    doc_filter: set[str] | None = None,
    prov_dir: Path = PROVENANCE_OUT_DIR,
    chunk_dir_root: Path = CHUNKING_OUT_DIR,
    encode_fn: Callable[[list[str]], np.ndarray] | None = None,
) -> dict[str, Any]:
    t_all = time.perf_counter()
    tc = TokenCounter()
    emb = Embedder(embed_model, dcc_window_tokens, encode_fn=encode_fn)
    rt = {"BI_atomic": 0.0, "ICC": 0.0, "DCC": 0.0}
    strat_out: dict[str, Any] = {}
    n_docs = 0

    for strat in strategies:
        logger.info("Evaluating strategy: %s", strat)
        ds = load_strategy(strat, prov_dir, chunk_dir_root, doc_filter)
        n_docs = max(n_docs, len(ds["doc_texts"]))

        t0 = time.perf_counter()
        bi_sum, bi_doc = compute_bi_atomic(ds, doc_filter is None)
        rt["BI_atomic"] += time.perf_counter() - t0

        t0 = time.perf_counter()
        icc_sum, icc_doc = compute_icc(ds, emb, tc, embed_text, icc_min_unit_tokens)
        rt["ICC"] += time.perf_counter() - t0

        t0 = time.perf_counter()
        dcc_sum, dcc_doc = compute_dcc(ds, emb, tc, embed_text, dcc_window_tokens, dcc_window_step)
        rt["DCC"] += time.perf_counter() - t0

        all_sp = [s for v in ds["spans_by_ver"].values() for s in v]
        tok_list = [s.reported_tokens if s.reported_tokens is not None else tc.count(s.indexed_text) for s in all_sp]
        per_doc = {}
        for vid in sorted(ds["doc_texts"]):
            vs = ds["spans_by_ver"].get(vid, [])
            v_toks = [s.reported_tokens if s.reported_tokens is not None else tc.count(s.indexed_text) for s in vs]
            per_doc[vid] = {
                "doc_id": ds["ver_to_doc"].get(vid, vid),
                "version_id": vid,
                "n_chunks": len(vs),
                "avg_tokens": round(float(np.mean(v_toks)), 2) if v_toks else 0.0,
                "BI_atomic": bi_doc[vid],
                "ICC": icc_doc[vid]["ICC"],
                "icc_valid_chunk_ratio": icc_doc[vid]["icc_valid_chunk_ratio"],
                "DCC": dcc_doc[vid]["DCC"],
                "n_windows": dcc_doc[vid]["n_windows"],
            }
        strat_out[strat] = {
            "summary": {
                "n_chunks": len(all_sp),
                "avg_tokens": round(float(np.mean(tok_list)), 2) if tok_list else 0.0,
                "BI_atomic": bi_sum,
                "ICC": icc_sum,
                "DCC": dcc_sum,
                "coverage_ratio": round(ds["coverage_ratio"], 6),
                "overlap_ratio": round(ds["overlap_ratio"], 6),
                "nonContiguous_chunk_count": ds["nonContiguous_chunk_count"],
            },
            "per_document": per_doc,
        }

    rt["total"] = time.perf_counter() - t_all
    results = {
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "embed_model": embed_model,
            "tokenizer": tc.name,
            "parameters": {
                "strategies": strategies,
                "embed_text": embed_text,
                "dcc_window_tokens": dcc_window_tokens,
                "dcc_window_step": dcc_window_step,
                "icc_min_unit_tokens": icc_min_unit_tokens,
                "docs": sorted(doc_filter) if doc_filter else None,
                "L_max": L_MAX,
            },
            "window_step_source": "fallback_default_1" if dcc_window_step == 1 else f"cli_override_{dcc_window_step}",
            "n_documents": n_docs,
            "L_max": L_MAX,
        },
        "strategies": strat_out,
        "runtime_seconds": {k: round(v, 4) for k, v in rt.items()},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    for strat in strategies:
        s = strat_out[strat]["summary"]
        bi, icc, dcc = s["BI_atomic"], s["ICC"], s["DCC"]
        icc_s = f"{icc['mean']:.4f}+/-{icc['std']:.4f}" if icc["mean"] is not None else "N/A"
        dcc_s = f"{dcc['mean']:.4f}+/-{dcc['std']:.4f}" if dcc["mean"] is not None else "N/A"
        print(
            f"[{strat}] chunks={s['n_chunks']} avg_tok={s['avg_tokens']} | "
            f"BI_atomic={bi['numerator']}/{bi['denominator']} ({bi['percent']:.2f}%, fc={bi['fully_contained_percent']:.2f}%) | "
            f"ICC={icc_s} (valid={icc['valid_chunk_ratio']:.2f}, docs={icc['n_docs_valid']}) | "
            f"DCC={dcc_s} (wins={dcc['n_windows_total']}, docs={dcc['n_docs_valid']}) | "
            f"cov={s['coverage_ratio']:.4f} ovl={s['overlap_ratio']:.4f} nonContig={s['nonContiguous_chunk_count']}"
        )
    return results


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    p = argparse.ArgumentParser(description="Layer 1 – Intrinsic Chunking Evaluation (BI_atomic, ICC, DCC).")
    p.add_argument("--out", type=Path, default=Path("layer1_results.json"))
    p.add_argument("--strategies", nargs="+", default=DEFAULT_STRATEGIES)
    p.add_argument("--embed-model", default="BAAI/bge-m3")
    p.add_argument("--embed-text", choices=["source", "indexed"], default="source")
    p.add_argument("--dcc-window-tokens", type=int, default=1500)
    p.add_argument("--dcc-window-step", type=int, default=1)
    p.add_argument("--icc-min-unit-tokens", type=int, default=0)
    p.add_argument("--docs", nargs="+", default=None)
    args = p.parse_args(argv)
    run_evaluation(
        args.out,
        args.strategies,
        args.embed_model,
        args.embed_text,
        args.dcc_window_tokens,
        args.dcc_window_step,
        args.icc_min_unit_tokens,
        set(args.docs) if args.docs else None,
    )


if __name__ == "__main__":
    main()