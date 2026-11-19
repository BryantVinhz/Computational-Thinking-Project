"""
_stage4_baseline_benchmark.py
Stage 4 — Pre-optimisation baseline performance benchmark (Phase 2C).

Measures synthesis latency, lexical verification latency, and fake-semantic
verification behaviour (cache hits, encode call counts) WITHOUT any real LLM
or SentenceTransformer calls and WITHOUT modifying any production file.

Run from the repository root:
    python -I "Response Synthesis/_stage4_baseline_benchmark.py"
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup — add Stage 4 directory so relative imports resolve as absolute
# ---------------------------------------------------------------------------
STAGE4_DIR = Path(__file__).resolve().parent
if str(STAGE4_DIR) not in sys.path:
    sys.path.insert(0, str(STAGE4_DIR))

# Import isolation: verifier.py and prompt_templates.py both call
# sys.path.insert(0, QUR_DIR) at module-level, pushing the Query Understanding &
# Routing package directory ahead of STAGE4_DIR before synthesizer.py's fallback
# `from prompt_templates import ...` runs.  Importing Stage 4's prompt_templates
# here — while STAGE4_DIR is still at position 0 — locks it into sys.modules so
# every subsequent bare `from prompt_templates import ...` in the production
# modules hits the cached Stage 4 entry and never re-searches sys.path.
import prompt_templates as _stage4_pt  # noqa: F401 — side-effect: cache correct module
del _stage4_pt

# ---------------------------------------------------------------------------
# Benchmark configuration
# ---------------------------------------------------------------------------
WARMUP_ITERS = 3
MEASURE_ITERS = 20


def _p95(data: list[float]) -> float:
    """Return the 95th percentile of *data* (nearest-rank method)."""
    s = sorted(data)
    idx = max(0, int(0.95 * len(s)) - 1)
    return s[idx]


# ---------------------------------------------------------------------------
# Shared fixtures — representative bilingual claim/evidence pairs
# ---------------------------------------------------------------------------

def _make_evidence(tag: str, chunk_id: str, text: str) -> dict[str, Any]:
    return {
        "evidence_id": tag,
        "chunk_id": chunk_id,
        "text_content": text,
        "score": 0.9,
        "metadata": {},
        "provenance": {
            "chunk_id": chunk_id,
            "document_id": "doc_bench_01",
            "version_id": "v1.0",
            "file_name": "sqli_bench.pdf",
            "file_format": "PDF",
            "canonical_title": "OWASP SQL Injection Cheat Sheet",
            "sources": [],
        },
    }


BENCH_EVIDENCE = [
    _make_evidence(
        "[1]", "bench_c1",
        "Parameterised queries prevent SQL Injection attacks effectively. "
        "Truy vấn tham số hóa ngăn chặn tấn công SQL Injection hiệu quả. "
        "Attackers inject malicious SQL code into input data to manipulate databases.",
    ),
    _make_evidence(
        "[2]", "bench_c2",
        "Input validation is an additional defence layer. "
        "Kiểm tra dữ liệu đầu vào là lớp phòng thủ bổ sung. "
        "Database accounts should be granted minimum necessary privileges.",
    ),
]

# A STRICT_RAG draft that cites both evidence items — deterministic content
BENCH_DRAFT_TEXT = (
    "### Kết quả phân tích bảo mật\n\n"
    "Vấn đề bảo mật nêu trên được định nghĩa và ghi nhận trong hệ thống tiêu chuẩn [1]. "
    "Cơ chế khai thác bắt nguồn từ việc thiếu kiểm soát chặt chẽ đối với các luồng dữ liệu đầu vào [1].\n\n"
    "### Biện pháp phòng chống và khắc phục\n\n"
    "Để khắc phục triệt để, hệ thống bắt buộc phải áp dụng nguyên tắc kiểm tra và chuẩn hóa dữ liệu nghiêm ngặt [2]. "
    "Đồng thời, cần cấu hình phân quyền tối thiểu và tuân thủ các chỉ dẫn kỹ thuật an toàn [2]."
)

# Retrieval output dict that drives ResponseSynthesizer
BENCH_RETRIEVAL_OUTPUT = {
    "status": "ok",
    "evidence_status": "FULL",
    "intent": "SEARCH",
    "llm_context": (
        "[1] Parameterised queries prevent SQL Injection attacks effectively.\n"
        "[2] Input validation is an additional defence layer."
    ),
    "total_evidence": 2,
    "evidence_list": BENCH_EVIDENCE,
}


# ---------------------------------------------------------------------------
# A. Synthesis latency
# ---------------------------------------------------------------------------

def bench_synthesis() -> dict[str, Any]:
    from synthesizer import DefaultDeterministicLLM, ResponseSynthesizer

    llm = DefaultDeterministicLLM()
    synth = ResponseSynthesizer(llm_client=llm)
    query = "Làm thế nào để ngăn chặn SQL Injection?"

    # Warm-up
    for _ in range(WARMUP_ITERS):
        synth.synthesize(query, BENCH_RETRIEVAL_OUTPUT, intent="SEARCH")

    latencies_ms: list[float] = []
    for _ in range(MEASURE_ITERS):
        draft = synth.synthesize(query, BENCH_RETRIEVAL_OUTPUT, intent="SEARCH")
        # Prefer the latency recorded in generation_metadata when present
        meta_latency = draft.generation_metadata.get("latency_s")
        if meta_latency is not None:
            latencies_ms.append(float(meta_latency) * 1000.0)
        else:
            # Fallback: outer wall-clock (should not be needed given current code)
            t0 = time.perf_counter()
            synth.synthesize(query, BENCH_RETRIEVAL_OUTPUT, intent="SEARCH")
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)

    return {
        "median_ms": statistics.median(latencies_ms),
        "p95_ms": _p95(latencies_ms),
        "source": "generation_metadata[latency_s]",
    }


# ---------------------------------------------------------------------------
# B. Lexical verification latency
# ---------------------------------------------------------------------------

def bench_lexical_verification() -> dict[str, Any]:
    from schema_response import SynthesizedDraftSchema
    from verifier import EvidenceVerifier, lexical_support_score

    verifier = EvidenceVerifier(similarity_fn=lexical_support_score)
    draft = SynthesizedDraftSchema(
        draft_answer=BENCH_DRAFT_TEXT,
        intent="SEARCH",
        generation_mode="STRICT_RAG",
    )

    # Warm-up
    for _ in range(WARMUP_ITERS):
        verifier.verify(draft, BENCH_EVIDENCE)

    latencies_ms: list[float] = []
    for _ in range(MEASURE_ITERS):
        t0 = time.perf_counter()
        verifier.verify(draft, BENCH_EVIDENCE)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)

    return {
        "median_ms": statistics.median(latencies_ms),
        "p95_ms": _p95(latencies_ms),
    }


# ---------------------------------------------------------------------------
# C. Fake-semantic verification (CountingModel)
# ---------------------------------------------------------------------------

class CountingModel:
    """
    Drop-in replacement for a SentenceTransformer passed to
    MultilingualSemanticSimilarity(model=...).

    Returns deterministic, L2-normalised, nonzero unit vectors so that cosine
    similarity is always a valid float in [0, 1].  Uses NumPy when available,
    falling back to a pure-Python list whose dot-product equals 1.0.

    Tracks:
        encode_calls  — number of times encode() was invoked.
        encoded_texts — total number of text strings submitted across all calls.
    """

    def __init__(self) -> None:
        self.encode_calls: int = 0
        self.encoded_texts: int = 0
        self._dim = 4  # small enough to stay dependency-free

    def encode(
        self,
        texts: list[str],
        normalize_embeddings: bool = True,
        show_progress_bar: bool = False,
    ) -> Any:
        self.encode_calls += 1
        self.encoded_texts += len(texts)

        try:
            import numpy as np
            # Return identical unit vectors — cosine = 1.0 for every pair.
            vec = np.ones(self._dim, dtype=float) / (self._dim ** 0.5)
            return np.tile(vec, (len(texts), 1))
        except ImportError:
            # Pure-Python fallback: list of lists
            vec = [1.0 / (self._dim ** 0.5)] * self._dim
            return [vec[:] for _ in texts]

    def reset(self) -> None:
        self.encode_calls = 0
        self.encoded_texts = 0


def bench_fake_semantic() -> dict[str, Any]:
    from schema_response import SynthesizedDraftSchema
    from verifier import EvidenceVerifier, MultilingualSemanticSimilarity

    # Use a draft with Vietnamese claims so the vi-detection path activates
    # (EvidenceVerifier._best_match checks c_lang == "vi" and self.semantic_fn)
    # We pass the similarity_fn as the semantic fn via the public similarity_fn param
    # so the verifier uses CountingModel for all claim->evidence scoring.
    counting_model = CountingModel()
    sim = MultilingualSemanticSimilarity(model=counting_model)

    draft = SynthesizedDraftSchema(
        draft_answer=BENCH_DRAFT_TEXT,
        intent="SEARCH",
        generation_mode="STRICT_RAG",
    )

    # EvidenceVerifier uses similarity_fn for English claims; for the fake-semantic
    # measurement we set similarity_fn=sim so every _best_match call goes through
    # CountingModel.encode regardless of language detection outcome.
    verifier = EvidenceVerifier(similarity_fn=sim)

    # --- Cold-cache run ---
    counting_model.reset()
    sim._cache.clear()
    t0 = time.perf_counter()
    verifier.verify(draft, BENCH_EVIDENCE)
    cold_latency_ms = (time.perf_counter() - t0) * 1000.0
    cold_encode_calls = counting_model.encode_calls
    cold_encoded_texts = counting_model.encoded_texts

    # --- Warm-cache run (same texts, cache already populated) ---
    counting_model.reset()
    t0 = time.perf_counter()
    verifier.verify(draft, BENCH_EVIDENCE)
    warm_latency_ms = (time.perf_counter() - t0) * 1000.0
    warm_encode_calls = counting_model.encode_calls
    warm_encoded_texts = counting_model.encoded_texts

    # --- Cache reuse across a third run ---
    counting_model.reset()
    t0 = time.perf_counter()
    verifier.verify(draft, BENCH_EVIDENCE)
    third_latency_ms = (time.perf_counter() - t0) * 1000.0
    third_encode_calls = counting_model.encode_calls
    third_encoded_texts = counting_model.encoded_texts

    return {
        "cold_latency_ms": cold_latency_ms,
        "cold_encode_calls": cold_encode_calls,
        "cold_encoded_texts": cold_encoded_texts,
        "warm_latency_ms": warm_latency_ms,
        "warm_encode_calls": warm_encode_calls,
        "warm_encoded_texts": warm_encoded_texts,
        "third_latency_ms": third_latency_ms,
        "third_encode_calls": third_encode_calls,
        "third_encoded_texts": third_encoded_texts,
        "cache_reuse_confirmed": (warm_encode_calls < cold_encode_calls
                                  or warm_encoded_texts < cold_encoded_texts),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _count_claims_and_evidence() -> tuple[int, int]:
    from verifier import extract_claims
    return len(extract_claims(BENCH_DRAFT_TEXT)), len(BENCH_EVIDENCE)


def main() -> None:
    print("=" * 68)
    print("Stage 4 Baseline Benchmark  (pre-optimisation, Phase 2C)")
    print("=" * 68)

    n_claims, n_evidence = _count_claims_and_evidence()
    print(f"\nFixtures")
    print(f"  Claims extracted from bench draft : {n_claims}")
    print(f"  Evidence items                    : {n_evidence}")
    print(f"  Warm-up iterations                : {WARMUP_ITERS}")
    print(f"  Measured iterations               : {MEASURE_ITERS}")

    # A. Synthesis
    print("\n--- A. Synthesis latency (DefaultDeterministicLLM) ---")
    try:
        s = bench_synthesis()
        print(f"  Latency source  : {s['source']}")
        print(f"  Median          : {s['median_ms']:.3f} ms")
        print(f"  p95             : {s['p95_ms']:.3f} ms")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # B. Lexical verification
    print("\n--- B. Lexical verification latency (EvidenceVerifier) ---")
    try:
        lv = bench_lexical_verification()
        print(f"  Median          : {lv['median_ms']:.3f} ms")
        print(f"  p95             : {lv['p95_ms']:.3f} ms")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # C. Fake-semantic
    print("\n--- C. Fake-semantic verification (CountingModel) ---")
    try:
        fs = bench_fake_semantic()
        print(f"  Cold-cache latency      : {fs['cold_latency_ms']:.3f} ms")
        print(f"  Cold encode() calls     : {fs['cold_encode_calls']}")
        print(f"  Cold encoded text count : {fs['cold_encoded_texts']}")
        print(f"  Warm-cache latency      : {fs['warm_latency_ms']:.3f} ms")
        print(f"  Warm encode() calls     : {fs['warm_encode_calls']}")
        print(f"  Warm encoded text count : {fs['warm_encoded_texts']}")
        print(f"  3rd-run encode() calls  : {fs['third_encode_calls']}")
        print(f"  3rd-run encoded texts   : {fs['third_encoded_texts']}")
        reuse = "YES" if fs["cache_reuse_confirmed"] else "NO (verify cache logic)"
        print(f"  Cache reuse confirmed   : {reuse}")
        print()
        print("  NOTE: CountingModel.encode() returns deterministic unit vectors.")
        print("  Latency above reflects pure Python/NumPy overhead, NOT")
        print("  the real BAAI/bge-m3 inference cost (~50-500 ms per batch).")
        print("  Real model performance is NOT measured in this benchmark.")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    print("\n--- Measurement limitations ---")
    print("  * Synthesis latency is sourced from generation_metadata['latency_s']")
    print("    which is set by ResponseSynthesizer using time.perf_counter().")
    print("    It includes prompt-build time but excludes result post-processing.")
    print("  * Lexical verification is wall-clock (time.perf_counter()) and")
    print("    includes claim extraction on every call.")
    print("  * Fake-semantic encode() counts reflect MultilingualSemanticSimilarity")
    print("    cache behaviour with the current _embed() implementation.")
    print("  * No network, model download, or file I/O occurs during this run.")
    print("  * Results are for CURRENT, UNMODIFIED Stage 4 code (3 known test")
    print("    failures are NOT fixed and do NOT affect these measurements).")
    print("=" * 68)


if __name__ == "__main__":
    main()
