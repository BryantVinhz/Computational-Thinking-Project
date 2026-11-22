from typing import Any

import numpy as np


def build_sample_perf_record(
    wall_ms: float,
    metadata: dict[str, Any],
    calls: list[dict[str, int]],
) -> dict[str, Any]:
    """Create a performance record of latency and token usage for a single query."""
    in_tok = sum(c["input_tokens"] for c in calls)
    out_tok = sum(c["output_tokens"] for c in calls)
    tot_tok = in_tok + out_tok

    return {
        "wall_latency_ms": round(wall_ms, 2),
        "understanding_ms": round(metadata.get("understanding_latency_s", 0.0) * 1000.0, 2),
        "retrieval_ms": round(metadata.get("retrieval_latency_s", 0.0) * 1000.0, 2),
        "synthesis_ms": round(metadata.get("synthesis_latency_s", 0.0) * 1000.0, 2),
        "verification_ms": round(metadata.get("verification_latency_s", 0.0) * 1000.0, 2),
        "llm_calls": len(calls),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": tot_tok,
    }


def summarize_system_performance(p_dict: dict[str, list[float]]) -> dict[str, Any]:
    """Aggregate Latency (Mean & P95 only) and Token Consumption."""
    wall = p_dict["wall_latency_ms"]
    return {
        "n": len(wall),
        "latency_ms": {
            "mean": round(float(np.mean(wall)), 1),
            "p95": round(float(np.percentile(wall, 95)), 1),
            "stage_breakdown": {
                "stage2_understanding_ms": {
                    "mean": round(float(np.mean(p_dict["understanding_ms"])), 1),
                    "p95": round(float(np.percentile(p_dict["understanding_ms"], 95)), 1),
                },
                "stage3_retrieval_ms": {
                    "mean": round(float(np.mean(p_dict["retrieval_ms"])), 1),
                    "p95": round(float(np.percentile(p_dict["retrieval_ms"], 95)), 1),
                },
                "stage4_synthesis_ms": {
                    "mean": round(float(np.mean(p_dict["synthesis_ms"])), 1),
                    "p95": round(float(np.percentile(p_dict["synthesis_ms"], 95)), 1),
                },
                "stage4_verification_ms": {
                    "mean": round(float(np.mean(p_dict["verification_ms"])), 1),
                    "p95": round(float(np.percentile(p_dict["verification_ms"], 95)), 1),
                },
            },
        },
        "tokens_per_query": {
            "mean_llm_calls": round(float(np.mean(p_dict["llm_calls"])), 2),
            "mean_input_tokens": round(float(np.mean(p_dict["input_tokens"])), 1),
            "mean_output_tokens": round(float(np.mean(p_dict["output_tokens"])), 1),
            "mean_total_tokens": round(float(np.mean(p_dict["total_tokens"])), 1),
        },
    }