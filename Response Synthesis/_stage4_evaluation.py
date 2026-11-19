"""
_stage4_evaluation.py
Stage 4 — R9C acceptance evaluation harness (local Ollama only).

Runs selected tests from Evaluation.json end-to-end through the real pipeline
(Stage 2 -> Stage 3 -> Stage 4) using the existing PipelineConnector, and writes
an auditable artefact per test:

    <out>/<TEST_ID>.envelope.json   exact FinalResponseEnvelope JSON (unmodified)
    <out>/<TEST_ID>.record.json     derived summary + automated checks
    <out>/run_manifest.json         what ran, on what model, with what outcome

Guarantees:
  * Nothing runs unless test IDs are named explicitly (--tests/--all). The
    default invocation is a read-only preflight that never touches the model.
  * Only the local Ollama endpoint from llm.py is used. ColabLLMClient and any
    remote API are refused.
  * Hugging Face downloads are disabled by default (HF_HUB_OFFLINE=1) so a
    missing model fails loudly instead of being fetched.
  * Databases are never rebuilt; missing artefacts are reported as preflight
    failures before any test runs.
  * Ground truth is copied verbatim from Evaluation.json. This script invents
    no expected evidence, scores or answers.

Run from the repository root:
    python -I "Response Synthesis/_stage4_evaluation.py"                     # preflight only
    python -I "Response Synthesis/_stage4_evaluation.py" --tests TEST_02,TEST_05
    python -I "Response Synthesis/_stage4_evaluation.py" --all
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent          # "Response Synthesis"
PROJECT_ROOT = SCRIPT_DIR.parent

for _p in (PROJECT_ROOT, SCRIPT_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# verifier.py and the Query Understanding package both ship a module named
# prompt_templates, and importing Stage 4 modules pushes QUR_DIR to sys.path[0].
# Importing Stage 4's copy here — while SCRIPT_DIR is still first — caches it in
# sys.modules so every later bare `from prompt_templates import ...` resolves to
# the Stage 4 module.
import prompt_templates as _stage4_prompt_templates  # noqa: F401
del _stage4_prompt_templates

DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_EVALUATION = PROJECT_ROOT / "Evaluation.json"
DEFAULT_OUT_DIR = PROJECT_ROOT / "evaluations" / "r9c"

VECTOR_DB = PROJECT_ROOT / "Document Ingestion and Parsing" / "output" / "Vector DB"
PROVENANCE = PROJECT_ROOT / "Document Ingestion and Parsing" / "output" / "Provenance"


# ---------------------------------------------------------------------------
# Argument handling
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Stage 4 R9C evaluation harness (local Ollama, explicit selection only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sel = p.add_mutually_exclusive_group()
    sel.add_argument("--tests", help="Comma-separated test IDs, e.g. TEST_02,TEST_05.")
    sel.add_argument("--all", action="store_true", help="Run every entry in Evaluation.json.")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"Ollama model name (default: {DEFAULT_MODEL}).")
    p.add_argument("--evaluation-json", type=Path, default=DEFAULT_EVALUATION)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR,
                   help=f"Output directory (default: {DEFAULT_OUT_DIR}).")
    p.add_argument("--chunking-strategy", default="semantic")
    p.add_argument("--no-warmup", action="store_true",
                   help="Skip PipelineConnector.warm_up() (avoids the 1-token probe call).")
    p.add_argument("--allow-model-download", action="store_true",
                   help="Permit Hugging Face downloads. Off by default; off is the correct setting "
                        "for a reproducible evaluation.")
    p.add_argument("--dry-run", action="store_true",
                   help="Preflight only. Implied when no test selection is given.")
    return p.parse_args(argv)


def selected_ids(args: argparse.Namespace, entries: dict[str, dict[str, Any]]) -> list[str]:
    if args.all:
        return list(entries)
    if not args.tests:
        return []
    ids = [t.strip() for t in args.tests.split(",") if t.strip()]
    unknown = [t for t in ids if t not in entries]
    if unknown:
        raise SystemExit(f"Unknown test ID(s): {', '.join(unknown)}. Available: {', '.join(entries)}")
    return ids


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def load_evaluation(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries: dict[str, dict[str, Any]] = {}
    for item in data:
        tid = item.get("test_id")
        if not tid:
            raise SystemExit("Evaluation.json contains an entry without test_id")
        entries[tid] = item
    return entries


def _ollama_models(host: str) -> tuple[list[str], str | None]:
    """Return installed model names, or ([], error). Never loads a model."""
    try:
        with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # connection refused, timeout, bad JSON...
        return [], f"{type(exc).__name__}: {exc}"
    return [m.get("name", "") for m in payload.get("models", [])], None


def preflight(
    *,
    eval_path: Path,
    ids: list[str],
    model: str,
    chunking_strategy: str,
) -> list[str]:
    """Return a list of blocking problems. Empty list means ready."""
    problems: list[str] = []

    if not eval_path.exists():
        problems.append(f"Evaluation file not found: {eval_path}")
        return problems  # nothing further can be checked meaningfully

    # Retrieval artefacts — never rebuilt by this script.
    chroma = VECTOR_DB / chunking_strategy.lower().replace("-", "_") / "chroma_security_db"
    prov_db = PROVENANCE / chunking_strategy.lower().replace("-", "_") / "workspace_data.db"
    if not chroma.exists():
        problems.append(f"Vector DB missing for strategy '{chunking_strategy}': {chroma}")
    if not prov_db.exists():
        problems.append(f"Provenance DB missing for strategy '{chunking_strategy}': {prov_db}")

    # Local inference endpoint.
    from llm import OLLAMA_HOST

    models, err = _ollama_models(OLLAMA_HOST)
    if err is not None:
        problems.append(f"Ollama unreachable at {OLLAMA_HOST} ({err}). Start 'ollama serve'.")
    else:
        installed = {m for m in models}
        installed |= {m.split(":")[0] for m in models}
        if model not in installed:
            problems.append(
                f"Model '{model}' is not installed on the Ollama host. "
                f"Installed: {', '.join(sorted(installed)) or '(none)'}. "
                "This harness will not download it."
            )

    if not ids:
        problems.append("No tests selected. Pass --tests <ids> or --all to run the model.")
    return problems


# ---------------------------------------------------------------------------
# Record assembly
# ---------------------------------------------------------------------------

def _t(meta: dict[str, Any], key: str) -> float | None:
    value = meta.get(key)
    return round(float(value), 4) if isinstance(value, (int, float)) else None


def _basename(path_like: str) -> str:
    return PurePosixPath(str(path_like).replace("\\", "/")).name.lower()


def build_record(
    test: dict[str, Any],
    envelope_dict: dict[str, Any] | None,
    wall_clock_s: float,
    error: str | None = None,
) -> dict[str, Any]:
    """Derive the auditable summary from an envelope dict. Adds no judgement."""
    gt = test.get("ground_truth", {}) or {}
    record: dict[str, Any] = {
        "test_id": test.get("test_id"),
        "scenario": test.get("scenario"),
        "query": test.get("query") or test.get("main_query"),
        "run_status": "ok" if envelope_dict is not None else "pipeline_run_error",
        "wall_clock_s": round(wall_clock_s, 4),
        "error": error,
    }

    if envelope_dict is None:
        record.update(_empty_metrics())
        record["manual_review"] = _manual_review_block()
        record["ground_truth"] = gt
        return record

    meta = envelope_dict.get("metadata") or {}
    verification = envelope_dict.get("verification") or {}
    draft = envelope_dict.get("raw_draft") or {}
    citations = envelope_dict.get("citations") or []

    cited_tags = list(draft.get("cited_evidence_ids") or [])
    citation_tags = [c.get("tag") for c in citations if c.get("tag")]
    dangling = sorted(set(cited_tags) - set(citation_tags))

    record.update({
        "status": envelope_dict.get("status"),
        "intent": envelope_dict.get("intent"),
        "resolved_query": envelope_dict.get("resolved_query"),
        "evidence_status": meta.get("evidence_status") or draft.get("evidence_status"),
        "generation_mode": meta.get("generation_mode") or draft.get("generation_mode"),
        "is_abstained": draft.get("is_abstained"),
        "abstention_reason": draft.get("abstention_reason"),
        "faithfulness_score": verification.get("faithfulness_score"),
        "verification_status": verification.get("status"),
        "verification_gate": meta.get("verification_gate"),
        "claims": {
            "total": verification.get("total_claims"),
            "supported": verification.get("supported_claims"),
            "contradicted": verification.get("contradicted_claims"),
            "unverified": verification.get("unverified_claims"),
        },
        "citations": {
            "count": len(citations),
            "tags": citation_tags,
            "cited_tags_in_draft": cited_tags,
            "dangling_draft_tags": dangling,
        },
        "citation_warnings": list(verification.get("warnings") or []),
        "latency_s": {
            "total": _t(meta, "total_latency_s"),
            "stage2_understanding": _t(meta, "understanding_latency_s"),
            "stage3_retrieval": _t(meta, "retrieval_latency_s"),
            "stage4_synthesis": _t(meta, "synthesis_latency_s"),
            "stage4_verification": _t(meta, "verification_latency_s"),
            "wall_clock": round(wall_clock_s, 4),
        },
        "automated_checks": {
            # Objective comparisons against stated ground truth only.
            "intent_matches_expected": (
                None if "expected_intent" not in gt
                else envelope_dict.get("intent") == gt["expected_intent"]
            ),
            "evidence_status_matches_expected": (
                None if "expected_evidence_status" not in gt
                else (meta.get("evidence_status") or draft.get("evidence_status")) == gt["expected_evidence_status"]
            ),
            "target_documents_retrieved": _target_doc_hits(gt, citations),
            "dangling_citation_tags": dangling,
            "answer_blocked_by_gate": meta.get("verification_gate") == "blocked",
        },
        "manual_review": _manual_review_block(),
        "ground_truth": gt,
    })
    return record


def _target_doc_hits(gt: dict[str, Any], citations: list[dict[str, Any]]) -> dict[str, Any] | None:
    targets = gt.get("target_documents") or []
    if not targets:
        return None
    cited_names = {_basename(c.get("file_name", "")) for c in citations if c.get("file_name")}
    cited_names |= {_basename(c.get("document_id", "")) for c in citations if c.get("document_id")}
    hit = [t for t in targets if _basename(t) in cited_names]
    return {
        "expected": len(targets),
        "cited": len(hit),
        "hit": sorted(_basename(t) for t in hit),
        "missed": sorted(_basename(t) for t in targets if _basename(t) not in cited_names),
    }


def _empty_metrics() -> dict[str, Any]:
    return {
        "status": None, "intent": None, "resolved_query": None,
        "evidence_status": None, "generation_mode": None,
        "is_abstained": None, "abstention_reason": None,
        "faithfulness_score": None, "verification_status": None, "verification_gate": None,
        "claims": {"total": None, "supported": None, "contradicted": None, "unverified": None},
        "citations": {"count": None, "tags": [], "cited_tags_in_draft": [], "dangling_draft_tags": []},
        "citation_warnings": [],
        "latency_s": {"total": None, "stage2_understanding": None, "stage3_retrieval": None,
                      "stage4_synthesis": None, "stage4_verification": None, "wall_clock": None},
        "automated_checks": None,
    }


def _manual_review_block() -> dict[str, Any]:
    """Deliberately empty. Filled by a human reading the envelope, never by this script."""
    return {
        "grounded_accuracy": None,
        "citation_correctness": None,
        "answer_completeness_vs_ground_truth": None,
        "notes": None,
        "reviewer": None,
    }


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def build_pipeline(model: str, chunking_strategy: str, warm_up: bool):
    from main import PipelineConnector
    from llm import OllamaLLMClient

    llm_client = OllamaLLMClient(model_name=model)
    if not isinstance(llm_client, OllamaLLMClient):
        raise RuntimeError("Evaluation harness must use the local OllamaLLMClient only.")
    return PipelineConnector(
        llm_client=llm_client,
        warm_up_models=warm_up,
        chunking_strategy=chunking_strategy,
    )


def run_one(pipeline, query: str) -> tuple[dict[str, Any] | None, float, str | None]:
    t0 = time.perf_counter()
    try:
        envelope = pipeline.ask(query)
        return envelope.to_dict(), time.perf_counter() - t0, None
    except Exception:
        return None, time.perf_counter() - t0, traceback.format_exc(limit=6)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.allow_model_download:
        # Fail instead of fetching weights mid-evaluation.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    entries = load_evaluation(args.evaluation_json)
    try:
        ids = selected_ids(args, entries)
    except SystemExit as exc:
        print(exc)
        return 2

    dry_run = args.dry_run or not ids
    problems = preflight(
        eval_path=args.evaluation_json,
        ids=ids,
        model=args.model,
        chunking_strategy=args.chunking_strategy,
    )

    print("=" * 72)
    print("Stage 4 — R9C evaluation harness")
    print("=" * 72)
    print(f"  Evaluation : {args.evaluation_json}")
    print(f"  Model      : {args.model} (local Ollama only)")
    print(f"  Strategy   : {args.chunking_strategy}")
    print(f"  Tests      : {', '.join(ids) if ids else '(none selected)'}")
    print(f"  Output     : {args.out}")
    print(f"  HF offline : {os.environ.get('HF_HUB_OFFLINE')}")

    if problems:
        print("\nPreflight problems:")
        for p in problems:
            print(f"  - {p}")
        if dry_run:
            print("\nNothing was run.")
        write_json(args.out / "run_manifest.json", {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": args.model,
            "chunking_strategy": args.chunking_strategy,
            "selected_tests": ids,
            "outcome": "preflight_failed",
            "problems": problems,
        })
        return 1

    if dry_run:
        print("\nPreflight OK. Re-run with --tests <ids> or --all to invoke the model.")
        write_json(args.out / "run_manifest.json", {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": args.model,
            "chunking_strategy": args.chunking_strategy,
            "selected_tests": ids,
            "outcome": "dry_run_preflight_ok",
            "problems": [],
        })
        return 0

    # ---- real run: pipeline construction is the expensive, failure-prone step ----
    print("\nInitialising pipeline ...")
    try:
        pipeline = build_pipeline(args.model, args.chunking_strategy, warm_up=not args.no_warmup)
    except Exception:
        err = traceback.format_exc(limit=8)
        print("\nPipeline initialisation FAILED:\n" + err)
        write_json(args.out / "run_manifest.json", {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": args.model,
            "chunking_strategy": args.chunking_strategy,
            "selected_tests": ids,
            "outcome": "pipeline_init_error",
            "error": err,
        })
        for tid in ids:
            write_json(args.out / f"{tid}.record.json", build_record(entries[tid], None, 0.0, err))
        return 1

    results: list[dict[str, Any]] = []
    for tid in ids:
        test = entries[tid]
        print(f"\n--- {tid} [{test.get('scenario')}] ---")

        # Conversation state must not leak between independent tests.
        pipeline.reset_conversation()

        first_error: str | None = None
        if test.get("query_first"):
            print(f"  (context turn) {test['query_first']}")
            first_env, first_wall, first_err = run_one(pipeline, test["query_first"])
            if first_env is not None:
                write_json(args.out / f"{tid}.context.envelope.json", first_env)
                write_json(args.out / f"{tid}.context.record.json",
                           build_record(test, first_env, first_wall))
            else:
                first_error = first_err
                write_json(args.out / f"{tid}.context.record.json",
                           build_record(test, None, first_wall, first_err))

        query = test.get("query") or test.get("main_query") or ""
        print(f"  query: {query[:90]}{'...' if len(query) > 90 else ''}")
        envelope_dict, wall, err = run_one(pipeline, query)

        if envelope_dict is not None:
            # Exact envelope, unmodified — the audit artefact.
            write_json(args.out / f"{tid}.envelope.json", envelope_dict)
        record = build_record(test, envelope_dict, wall, err)
        if first_error and err is None:
            record["context_turn_error"] = first_error
        write_json(args.out / f"{tid}.record.json", record)
        results.append(record)

        print(f"  status={record['status']} intent={record['intent']} "
              f"evidence={record['evidence_status']} gate={record['verification_gate']} "
              f"faithfulness={record['faithfulness_score']} ({record['wall_clock_s']}s)")
        if err:
            print(f"  ERROR:\n{err}")

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "test_ids": ids,
        "count": len(results),
        "errors": sum(r["run_status"] != "ok" for r in results),
        "records": [
            {
                "test_id": r["test_id"],
                "run_status": r["run_status"],
                "status": r["status"],
                "intent": r["intent"],
                "evidence_status": r["evidence_status"],
                "verification_gate": r["verification_gate"],
                "faithfulness_score": r["faithfulness_score"],
                "wall_clock_s": r["wall_clock_s"],
            }
            for r in results
        ],
    }
    write_json(args.out / "summary.json", summary)
    write_json(args.out / "run_manifest.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "chunking_strategy": args.chunking_strategy,
        "selected_tests": ids,
        "outcome": "completed",
        "problems": [],
    })

    print(f"\nWrote {len(results)} record(s) to {args.out}")
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
