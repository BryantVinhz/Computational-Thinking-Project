import inspect
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import torch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = next(p for p in [CURRENT_DIR, *CURRENT_DIR.parents] if (p / "path.py").is_file())

for sub in (
    CURRENT_DIR,
    PROJECT_ROOT / "Response Synthesis",
    PROJECT_ROOT / "Retrieval",
    PROJECT_ROOT / "Query Understanding and Routing",
    PROJECT_ROOT / "Document Ingestion and Parsing" / "Metadata",
    PROJECT_ROOT / "Document Ingestion and Parsing" / "Provenance and Traceability",
    PROJECT_ROOT,
):
    if sub.exists() and str(sub) not in sys.path:
        sys.path.insert(0, str(sub))

from evaluators.e2e_task_eval import AbstentionTracker, evaluate_e2e_task_sample
from evaluators.generation_eval import SemanticClaimMatcher, evaluate_generation_sample, strip_footnotes
from evaluators.system_perf_eval import build_sample_perf_record, summarize_system_performance
from query_vectorizer import QueryVectorizer
from rag_orchestrator import RAGOrchestrator
from reranker import CrossEncoderReranker
from retrieval_system import RetrievalSystem
from synthesizer import ResponseSynthesizer
from traceable_formatter import TraceableFormatter
from verifier import SEMANTIC_SUPPORT_THRESHOLD, EvidenceVerifier, MultilingualSemanticSimilarity

import argparse

DEFAULT_DATASET_PATH = CURRENT_DIR / "e2e_eval_dataset.json"
DEFAULT_REPORTS_DIR = CURRENT_DIR / "reports" / "v2_regression_150"


class LocalColabQwenClient:
    """Run Qwen3-4B directly on T4 GPU (with redundant thinking mode disabled) and count tokens accurately."""

    def __init__(self, model_name: str = "Qwen/Qwen3-4B", device: str = "cuda"):
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.model_name = model_name
        self.device = device
        print(f">>> Loading text generation model '{model_name}' onto {device}...")
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name,
            dtype=torch.float16 if device == "cuda" else torch.float32,
            device_map="auto" if device == "cuda" else None,
            attn_implementation="sdpa" if device == "cuda" else None,
        )
        if device != "cuda":
            self.model.to(device)
        self.model.eval()
        self.call_history: list[dict[str, int]] = []

    def reset_call_history(self) -> None:
        self.call_history = []

    def _format_chat(self, messages: list[dict[str, str]]) -> str:
        try:
            return self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
        except TypeError:
            return self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )

    def generate(
        self,
        prompt: Any,
        system_instruction: str | None = None,
        max_new_tokens: int = 640,
        **kwargs: Any,
    ) -> str:
        if isinstance(prompt, list):
            messages = list(prompt)
            if system_instruction and (not messages or messages[0].get("role") != "system"):
                messages.insert(0, {"role": "system", "content": system_instruction})
        else:
            messages = []
            if system_instruction:
                messages.append({"role": "system", "content": system_instruction})
            messages.append({"role": "user", "content": str(prompt)})

        text_input = self._format_chat(messages)
        inputs = self.tokenizer(
            [text_input],
            return_tensors="pt",
            truncation=True,
            max_length=6144,
        ).to(self.model.device)

        input_tokens = int(inputs["input_ids"].shape[1])

        with torch.inference_mode():
            generated_ids = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=self.tokenizer.eos_token_id,
            )

        out_ids = generated_ids[0][input_tokens:]
        output_tokens = int(out_ids.shape[0])
        raw_text = self.tokenizer.decode(out_ids, skip_special_tokens=True)
        response_text = re.sub(r"<think>[\s\S]*?</think>", "", raw_text).strip()

        self.call_history.append({
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        })
        return response_text

    def generate_reverse_questions(self, answer_text: str, lang: str, n: int = 3) -> list[str]:
        """Generate n hypothetical questions from the answer (Reverse Question Generation - RAGAS)."""
        clean_body = strip_footnotes(answer_text)[:2500]
        if not clean_body.strip():
            return []

        if lang == "vi":
            sys_msg = "Bạn là trợ lý đánh giá chất lượng câu trả lời. Chỉ xuất danh sách câu hỏi, mỗi câu 1 dòng, không đánh số."
            usr_msg = (
                f"Dựa trên đoạn câu trả lời dưới đây, hãy viết đúng {n} câu hỏi ngắn gọn bằng tiếng Việt "
                f"mà đoạn văn bản này giải đáp trực tiếp:\n\n{clean_body}"
            )
        else:
            sys_msg = "You are an evaluation assistant. Output only the questions, one per line, without numbering."
            usr_msg = (
                f"Based on the answer below, generate exactly {n} concise questions in English "
                f"that this answer directly addresses:\n\n{clean_body}"
            )

        text_input = self._format_chat([{"role": "system", "content": sys_msg}, {"role": "user", "content": usr_msg}])
        inputs = self.tokenizer([text_input], return_tensors="pt", truncation=True, max_length=3072).to(self.model.device)
        in_len = int(inputs["input_ids"].shape[1])

        with torch.inference_mode():
            gen_ids = self.model.generate(
                **inputs,
                max_new_tokens=200,
                do_sample=False,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        raw = re.sub(r"<think>[\s\S]*?</think>", "", self.tokenizer.decode(gen_ids[0][in_len:], skip_special_tokens=True)).strip()
        lines = [re.sub(r"^[\-\*\d\.\)\s]+", "", ln).strip() for ln in raw.splitlines() if ln.strip()]
        return [ln for ln in lines if len(ln) > 8][:n]


def run_e2e_evaluation(
    dataset_path: Path = DEFAULT_DATASET_PATH,
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    run_tag: str = "v2_regression_150",
) -> None:
    if not dataset_path.exists():
        raise FileNotFoundError(f"Evaluation dataset not found: {dataset_path}")

    reports_dir.mkdir(parents=True, exist_ok=True)
    gen_report_path = reports_dir / "generation_report.json"
    task_report_path = reports_dir / "task_report.json"
    perf_report_path = reports_dir / "performance_report.json"
    final_report_path = reports_dir / "final_evaluation_report.json"

    dataset: list[dict[str, Any]] = json.loads(dataset_path.read_text(encoding="utf-8"))
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f">>> Starting End-to-End & System Performance Evaluation ({len(dataset)} samples) on: {device.upper()}")

    # Initialize system components
    vec_kwargs = {"device": device} if "device" in inspect.signature(QueryVectorizer.__init__).parameters else {}
    vectorizer = QueryVectorizer(**vec_kwargs)

    rerank_kwargs: dict[str, Any] = {"threshold": 0.25, "top_k": None}
    if "device" in inspect.signature(CrossEncoderReranker.__init__).parameters:
        rerank_kwargs["device"] = device
    reranker = CrossEncoderReranker(**rerank_kwargs)

    rs_params = inspect.signature(RetrievalSystem.__init__).parameters
    rs_kwargs: dict[str, Any] = {"query_vectorizer": vectorizer, "reranker": reranker}
    if "chunking_strategy" in rs_params:
        rs_kwargs["chunking_strategy"] = "semantic"
    retrieval_sys = RetrievalSystem(**rs_kwargs)

    llm_client = LocalColabQwenClient(model_name="Qwen/Qwen3-4B", device=device)
    orchestrator = RAGOrchestrator(
        retrieval_system=retrieval_sys,
        synthesizer=ResponseSynthesizer(llm_client=llm_client),
        verifier=EvidenceVerifier(
            similarity_fn=MultilingualSemanticSimilarity(device=device),
            support_threshold=SEMANTIC_SUPPORT_THRESHOLD,
        ),
        traceable_formatter=TraceableFormatter(),
    )
    matcher = SemanticClaimMatcher(device=device)

    print(">>> Running 1 model Warm-up pass...")
    _ = orchestrator.run("What is Cross-Site Scripting (XSS)?", conversation_history=[])
    llm_client.reset_call_history()
    if device == "cuda":
        torch.cuda.synchronize()

    # SYSTEM EXECUTION & SYSTEM PERFORMANCE PROFILING
    print("\n>>> [PHASE 1] Executing end-to-end pipeline across 150 samples and recording System Performance...")
    raw_runs: list[dict[str, Any]] = []
    t_pha1_start = time.perf_counter()

    for idx, sample in enumerate(dataset, start=1):
        llm_client.reset_call_history()
        query = sample["query"]
        history = sample.get("conversation_history") or []

        if device == "cuda":
            torch.cuda.synchronize()
        t0 = time.perf_counter()

        s2_out = orchestrator.execute_stage2(query, history)
        t_s2 = time.perf_counter() - t0
        status_s2 = s2_out.get("status", "error")

        if status_s2 in {"rejected", "out_of_scope", "error"}:
            reason = s2_out.get("reason") or "Request is out of processing scope."
            envelope = orchestrator._early_exit(
                status_s2, query, s2_out, f"Cannot process ({status_s2}): {reason}",
                total_latency_s=time.perf_counter() - t0, understanding_latency_s=t_s2,
            )
        elif status_s2 == "clarification":
            msg = s2_out.get("clarification_message") or "Please clarify your question."
            envelope = orchestrator._early_exit(
                "clarification_needed", query, s2_out, msg,
                total_latency_s=time.perf_counter() - t0, understanding_latency_s=t_s2,
            )
        elif status_s2 != "ok" or "query_plan" not in s2_out:
            envelope = orchestrator._early_exit(
                "error", query, s2_out, "Invalid Stage 2 response.",
                total_latency_s=time.perf_counter() - t0, understanding_latency_s=t_s2,
            )
        else:
            q_plan = s2_out["query_plan"]
            t_s3_start = time.perf_counter()
            ret_out = retrieval_sys.retrieve(q_plan)
            t_s3 = time.perf_counter() - t_s3_start

            hist_for_syn = history if bool(q_plan.get("context_used", False)) else None
            envelope = orchestrator.run_from_retrieval(
                query=query,
                retrieval_output=ret_out,
                resolved_query=q_plan.get("resolved_query") or query,
                intent=q_plan.get("intent", "SEARCH"),
                conversation_history=hist_for_syn,
                pipeline_start=t0,
                understanding_latency_s=t_s2,
                retrieval_latency_s=t_s3,
            )

        if device == "cuda":
            torch.cuda.synchronize()
        wall_ms = (time.perf_counter() - t0) * 1000.0

        env_dict = envelope.to_dict()
        perf = build_sample_perf_record(
            wall_ms=wall_ms,
            metadata=env_dict.get("metadata") or {},
            calls=list(llm_client.call_history),
        )
        raw_runs.append({"sample": sample, "s2_out": s2_out, "envelope": env_dict, "perf": perf})

        if idx % 10 == 0 or idx == len(dataset):
            print(f"  ... Executed {idx}/{len(dataset)} samples ({time.perf_counter() - t_pha1_start:.1f}s)")

    # MODULAR METRIC SCORING (Generation + End-to-End Task + System Perf)
    print("\n>>> [PHASE 2] Scoring with specialized evaluators...")
    gen_buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    e2e_buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    perf_buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    abstention_tracker = AbstentionTracker()
    error_counter: Counter = Counter()
    sample_logs: list[dict[str, Any]] = []

    for run in raw_runs:
        sample = run["sample"]
        s2_out = run["s2_out"]
        env = run["envelope"]
        perf = run["perf"]

        cat = sample["category"]
        lang = sample["lang"]
        gen_mode = (env.get("metadata") or {}).get("generation_mode", "ABSTENTION")

        gen_res = evaluate_generation_sample(sample, env, matcher, llm_client)
        task_res = evaluate_e2e_task_sample(sample, s2_out, env, gen_res, abstention_tracker, error_counter)

        for b_key in ("overall", f"cat:{cat}", f"lang:{lang}"):
            for k, v in gen_res.items():
                if v is not None:
                    gen_buckets[b_key][k].append(float(v))

            e2e_buckets[b_key]["routing_accuracy"].append(task_res["routing_accuracy"])
            e2e_buckets[b_key]["tsr_binary"].append(task_res["tsr_binary"])
            e2e_buckets[b_key]["tsr_full_only"].append(1.0 if task_res["tsr_grade_0_2"] == 2 else 0.0)
            if task_res["version_consistent"] is not None:
                e2e_buckets[b_key]["version_accuracy"].append(1.0 if task_res["version_consistent"] else 0.0)

        for p_key in ("overall", f"cat:{cat}", f"mode:{gen_mode}"):
            for k_perf, v_perf in perf.items():
                perf_buckets[p_key][k_perf].append(float(v_perf))

        sample_logs.append({
            "eval_id": sample["eval_id"],
            "category": cat,
            "sub_type": sample.get("sub_type"),
            "lang": lang,
            "query": sample["query"],
            "expected_status": (sample.get("expected_routing") or {}).get("status", "ok"),
            "actual_status": env.get("status", "error"),
            "expected_intent": (sample.get("expected_routing") or {}).get("intent", "SEARCH"),
            "actual_intent": env.get("intent", "UNKNOWN"),
            "generation_mode": gen_mode,
            "tsr_grade_0_2": task_res["tsr_grade_0_2"],
            "tsr_binary": task_res["tsr_binary"],
            "error_taxonomy": task_res["error_taxonomy"],
            "generation_metrics": gen_res,
            "e2e_task_metrics": {
                "routing_accuracy": task_res["routing_accuracy"],
                "version_consistent": task_res["version_consistent"],
            },
            "system_performance": perf,
            "final_answer_markdown": env.get("final_answer_markdown"),
            "citations": env.get("citations"),
            "verification": env.get("verification"),
        })

    def summarize_pct(m_dict: dict[str, list[float]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, vals in m_dict.items():
            if vals:
                out[k] = round(float(np.mean(vals)) * 100.0, 2)
                out[f"{k}_n"] = len(vals)
        return out

    system_config = {
        "chunking_strategy": "semantic (L_max=650)",
        "retrieval_pipeline": "Hybrid (BM25 + bge-m3) -> RRF(k=60) -> bge-reranker-v2-m3(tau=0.25) -> MMR",
        "generator_model": llm_client.model_name,
        "verifier_model": "BAAI/bge-m3 (tau=0.50)",
        "total_samples": len(dataset),
        "run_tag": run_tag, 
        "dataset_file": dataset_path.name,
    }

    generation_report = {
        "system_config": system_config,
        "metrics_by_group": {grp: summarize_pct(m) for grp, m in gen_buckets.items()},
    }

    task_report = {
        "system_config": system_config,
        "task_and_routing": {grp: summarize_pct(m) for grp, m in e2e_buckets.items()},
        "abstention_performance": abstention_tracker.summarize(),
        "error_taxonomy_summary": dict(error_counter),
    }

    performance_report = {
        "system_config": system_config,
        "performance_by_group": {grp: summarize_system_performance(p) for grp, p in perf_buckets.items()},
    }

    final_report = {
        "system_config": system_config,
        "generation_evaluation": generation_report["metrics_by_group"],
        "end_to_end_evaluation": {
            "task_and_routing": task_report["task_and_routing"],
            "abstention_performance": task_report["abstention_performance"],
            "error_taxonomy_summary": task_report["error_taxonomy_summary"],
        },
        "system_performance": performance_report["performance_by_group"],
        "sample_logs": sample_logs,
    }

    gen_report_path.write_text(json.dumps(generation_report, ensure_ascii=False, indent=2), encoding="utf-8")
    task_report_path.write_text(json.dumps(task_report, ensure_ascii=False, indent=2), encoding="utf-8")
    perf_report_path.write_text(json.dumps(performance_report, ensure_ascii=False, indent=2), encoding="utf-8")
    final_report_path.write_text(json.dumps(final_report, ensure_ascii=False, indent=2), encoding="utf-8")

    ov_gen = generation_report["metrics_by_group"]["overall"]
    ov_e2e = task_report["task_and_routing"]["overall"]
    ov_abs = task_report["abstention_performance"]
    ov_perf = performance_report["performance_by_group"]["overall"]

    print("\n" + "=" * 92)
    print("GENERATION, END-TO-END & SYSTEM PERFORMANCE EVALUATION SUMMARY (N = 150)")
    print("=" * 92)
    print("[1] Generation Evaluation (reports/generation_report.json):")
    print(f"    - Answer Quality   : Faithfulness={ov_gen.get('faithfulness', 0)}% | Claim P/R/F1={ov_gen.get('claim_precision', 0)}% / {ov_gen.get('claim_recall', 0)}% / {ov_gen.get('claim_f1', 0)}% | AR={ov_gen.get('answer_relevancy', 0)}%")
    print(f"    - Citation Quality : Precision={ov_gen.get('citation_precision', 0)}% | Coverage={ov_gen.get('citation_coverage', 0)}% | F1={ov_gen.get('citation_f1', 0)}%")
    print("[2] End-to-End Evaluation (reports/task_report.json):")
    print(f"    - Task Completion  : TSR={ov_e2e.get('tsr_binary', 0)}% (Full={ov_e2e.get('tsr_full_only', 0)}%) | Routing Acc={ov_e2e.get('routing_accuracy', 0)}%")
    print(f"    - Abstention       : F1={ov_abs['f1_abstain_pct']}% (P={ov_abs['precision_abstain_pct']}%, R={ov_abs['recall_abstain_pct']}%) | FAR={ov_abs['false_answer_rate_pct']}% | Answerable Acc={ov_abs['answerable_query_accuracy_pct']}%")
    print(f"    - Error Taxonomy   : {dict(error_counter)}")
    print("[3] System Performance (reports/performance_report.json):")
    print(f"    - Latency          : Mean={ov_perf['latency_ms']['mean']} ms | P95={ov_perf['latency_ms']['p95']} ms")
    print(f"    - Tokens / Query   : Input={ov_perf['tokens_per_query']['mean_input_tokens']} | Output={ov_perf['tokens_per_query']['mean_output_tokens']} | Total={ov_perf['tokens_per_query']['mean_total_tokens']}")
    print("=" * 92)



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="End-to-End RAG & System Performance Evaluator")
    parser.add_argument("--dataset", type=str, default=str(DEFAULT_DATASET_PATH), help="Đường dẫn tệp dataset JSON")
    parser.add_argument("--out-dir", type=str, default=str(DEFAULT_REPORTS_DIR), help="Thư mục xuất 4 tệp báo cáo")
    parser.add_argument("--run-tag", type=str, default="v2_regression_150", help="Nhãn phiên chạy")
    args = parser.parse_args()
    run_e2e_evaluation(
        dataset_path=Path(args.dataset).resolve(),
        reports_dir=Path(args.out_dir).resolve(),
        run_tag=args.run_tag,
    )