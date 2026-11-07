import logging
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from schema import ScopedRetrievalPayload
from query_vectorizer import QueryVectorizer
from scope_builder import ScopeBuilder
from bm25_retrieval import BM25Retriever
from dense_retrieval import DenseRetriever
from rrf import reciprocal_rank_fusion
from MMR import LangChainMMRSelector
from reranker import CrossEncoderReranker
from evidence_package import EvidenceAssembler
from ontology_utils import load_ontology_terms

logger = logging.getLogger(__name__)

RETRIEVAL_INTENTS = {"SEARCH", "COMPARE", "SYNTHESIS"}
MISSING_VERSION_MESSAGE = "Kho tài liệu không có phiên bản được yêu cầu trong câu hỏi."
RERANKER_UNAVAILABLE_MESSAGE = "Không thể xác minh độ liên quan của tài liệu (reranker lỗi)."


class RetrievalSystem:
    """
    Complete Facade Service:
    - Supports Single-hop and Multi-hop retrieval (depends_on=True)
    - Dynamic parameter allocation:
        + SEARCH: k=5
        + COMPARE: k=3 per branch
        + SYNTHESIS: k=8 to 10 (broader coverage across multiple documents for synthesis)
    - Pipeline: Input -> Preparer -> (BM25 + Dense) -> RRF -> Cross-Encoder -> MMR -> Evidence Assembler
    """

    def __init__(
        self,
        scope_builder: ScopeBuilder | None = None,
        query_vectorizer: QueryVectorizer | None = None,
        bm25_retriever: BM25Retriever | None = None,
        dense_retriever: DenseRetriever | None = None,
        reranker: CrossEncoderReranker | None = None,
        mmr_selector: LangChainMMRSelector | None = None,
        evidence_assembler: EvidenceAssembler | None = None,
    ):
        terms, self.ontology_labels = load_ontology_terms()
        self.scope_builder = scope_builder or ScopeBuilder(ontology_dict=terms)
        self.vectorizer = query_vectorizer or QueryVectorizer()
        self.dense = dense_retriever or DenseRetriever()
        self.bm25 = bm25_retriever or BM25Retriever()
        self.reranker = reranker or CrossEncoderReranker(threshold=0.25, top_k=None)
        self.mmr = mmr_selector or LangChainMMRSelector(vectorizer=self.vectorizer)
        self.assembler = evidence_assembler or EvidenceAssembler()

    @staticmethod
    def _get_intent_params(intent: str, task_intent: str | None = None) -> tuple[int, float]:
        """
        Configure MMR k and lambda parameters according to task classification:
        - SEARCH: k = 3, lambda = 0.65
        - COMPARE: k = 3 (per target branch), lambda = 0.60
        - SYNTHESIS: k = 8 - 10, lambda = 0.50 (increase diversity/coverage)
        """

        target_intent = (task_intent or intent).upper()
        if target_intent == "COMPARE":
            return 3, 0.60
        elif target_intent == "SYNTHESIS":
            return 9, 0.50  
        else:
            return 3, 0.65  # SEARCH 

    def _execute_search_pipeline(self, payload: ScopedRetrievalPayload, target_k: int, lambda_val: float) -> tuple[list[dict[str, Any]], bool, str | None]:
        """Execute search pipeline (Sparse + Dense -> RRF -> Rerank -> MMR)"""
        # Expanded initial retrieval top-K (3-5x target k to allow room for RRF and MMR selection)
        candidate_pool_size = max(15, target_k * 4)

        # Run BM25 and Dense retrievals in parallel
        bm25_hits = self.bm25.retrieve_from_payload(payload=payload, top_k=candidate_pool_size)
        dense_hits = self.dense.retrieve_from_payload(payload=payload, top_k=candidate_pool_size)

        # Merge rankings by Reciprocal Rank Fusion (RRF)
        fused = reciprocal_rank_fusion(
            bm25_results=bm25_hits,
            dense_results=dense_hits,
            k=60,
            top_k=candidate_pool_size,
        )

        if not fused:
            return [], False, None

        # The cross-encoder threshold is the abstention gate. If it cannot run, fail closed:
        # unvetted RRF candidates must never reach the LLM as FULL evidence.
        if self.reranker is None or not hasattr(self.reranker, "rerank"):
            logger.error("Task '%s': reranker unavailable, abstaining", payload.task_id)
            return [], True, RERANKER_UNAVAILABLE_MESSAGE
        rerank_query = payload.query_bm25 if payload.query_bm25 else payload.original_query
        try:
            rerank_result = self.reranker.rerank(
                query=rerank_query,
                candidate_chunks=fused,
                top_k=candidate_pool_size,
            )
        except Exception:
            logger.exception("Task '%s': reranker failed, abstaining", payload.task_id)
            return [], True, RERANKER_UNAVAILABLE_MESSAGE

        if rerank_result.insufficient_evidence:
            logger.warning(
                "Task '%s' triggered Abstention Policy: 0 chunks met confidence threshold",
                payload.task_id,
            )
            return [], True, rerank_result.abstention_message

        # Diversify and deduplicate through MMR
        selected = self.mmr.select(
            candidates=rerank_result.chunks,
            query_vector=payload.query_vector,
            original_query=payload.original_query,
            top_k=target_k,
            lambda_param=lambda_val,
        )
        return selected, False, None

    @staticmethod
    def _extract_context_summary(chunks: list[dict[str, Any]], max_chars: int = 300) -> str:
        """
        Extract keywords and key content from preceding task results 
        to enrich the query for downstream dependent tasks
        """
        if not chunks:
            return ""
        top_chunk = chunks[0]
        text = top_chunk.get("text") or top_chunk.get("text_content") or ""
        headings = " ".join(top_chunk.get("metadata", {}).get("heading_path") or [])
        summary = f"{headings} {text}".strip()
        return summary[:max_chars]

    def retrieve(self, plan: dict[str, Any]) -> dict[str, Any]:
        """
        Accept Query Plan, resolve dependency order, and execute retrieval
        """
        plan = plan.get("query_plan", plan)
        global_intent = str(plan.get("intent") or "SEARCH").upper()

        if plan.get("need_clarify") is True or global_intent == "CLARIFICATION":
            return self._empty_response(
                status="clarification_needed",
                intent=global_intent,
                llm_context=plan.get("clarification_message") or "Please clarify your question",
            )

        # Only retrieval intents may reach the index. Anything else (OUT_OF_SCOPE, unknown) must not
        # degrade into an unconstrained corpus-wide SEARCH.
        if global_intent not in RETRIEVAL_INTENTS:
            logger.warning("Refusing retrieval for non-retrieval intent '%s'", global_intent)
            return self._empty_response(
                status="ok",
                intent=global_intent,
                llm_context="Yêu cầu nằm ngoài phạm vi truy xuất tài liệu.",
            )

        raw_tasks = plan.get("task") or []

        if plan.get("need_clarify") is True or global_intent == "CLARIFICATION":
            return self._empty_response(
                status="clarification_needed",
                intent=global_intent,
                llm_context=plan.get("clarification_message") or "Please clarify your question",
            )

        # Only retrieval intents may reach the index. Anything else (OUT_OF_SCOPE, unknown) must not
        # degrade into an unconstrained corpus-wide SEARCH.
        if global_intent not in RETRIEVAL_INTENTS:
            logger.warning("Refusing retrieval for non-retrieval intent '%s'", global_intent)
            return self._empty_response(
                status="ok",
                intent=global_intent,
                llm_context="Yêu cầu nằm ngoài phạm vi truy xuất tài liệu.",
            )

        raw_tasks = plan.get("task") or []
        if not raw_tasks:
            raw_tasks = [{
                "task_id": "main_task",
                "intent_task": global_intent,
                "query": plan.get("resolved_query") or plan.get("original_query") or "",
                "depends_on": None,
            }]

        # Single-task COMPARE across versions: split into one branch per version. Each branch carries
        # its own explicit "version" so ScopeBuilder binds exactly that version, not all of them.
        if global_intent == "COMPARE" and len(raw_tasks) == 1:
            main_task = raw_tasks[0]
            query_text = main_task.get("query") or plan.get("resolved_query") or plan.get("original_query", "")
            detected_vers = self.scope_builder.requested_versions(query_text)
            if len(detected_vers) >= 2:
                logger.info("Multi-branch Retrieval over versions %s", detected_vers)
                raw_tasks = [
                    {
                        "task_id": f"{main_task.get('task_id', 'task_compare')}_branch_{v_tag}",
                        "intent_task": "COMPARE",
                        "query": query_text,
                        "version": v_tag,
                        "depends_on": None,
                        "objective": main_task.get("objective", {}),
                    }
                    for v_tag in detected_vers
                ]

        task_results: dict[str, list[dict[str, Any]]] = {}
        all_selected_chunks: list[dict[str, Any]] = []
        task_insufficient_flags: list[bool] = []
        last_abstention_msg: str | None = None

        # Iterate and execute each task sequentially
        for task in raw_tasks:
            task_id = task.get("task_id", f"task_{len(task_results)}")
            task_intent = task.get("intent_task", global_intent)
            base_query = task.get("query") or plan.get("resolved_query") or ""
            depends_on = task.get("depends_on")

            enriched_query = base_query
            # MULTI-HOP DEPENDENCY RESOLUTION
            if depends_on:
                parent_context = ""
                if isinstance(depends_on, str) and depends_on in task_results:
                    parent_context = self._extract_context_summary(task_results[depends_on])
                elif task_results:
                    last_task_id = list(task_results.keys())[-1]
                    parent_context = self._extract_context_summary(task_results[last_task_id])
                if parent_context:
                    logger.info("Multi-hop Task '%s' inheriting context from preceding task", task_id)
                    enriched_query = f"{base_query} Context: {parent_context}"

            # Build dynamic Scope and Vector for the current task
            scope, expansions = self.scope_builder.build_scope(plan, task=task)

            # The user asked for a version the knowledge base does not hold: abstain for this task
            # instead of answering from another version.
            if scope.is_relaxed:
                logger.warning("Task '%s': requested version not in knowledge base, abstaining", task_id)
                task_insufficient_flags.append(True)
                last_abstention_msg = MISSING_VERSION_MESSAGE
                task_results[task_id] = []
                continue

            query_bm25 = enriched_query
            if expansions:
                query_bm25 += " " + " ".join(expansions)

            payload = ScopedRetrievalPayload(
                task_id=task_id,
                intent_task=task_intent,
                original_query=enriched_query,
                query_bm25=query_bm25,
                query_vector=self.vectorizer.vectorize(enriched_query),
                scope=scope,
            )

            # Determine exact k and lambda based on task intent
            target_k, lambda_val = self._get_intent_params(global_intent, task_intent)
            logger.info(
                "Executing Task '%s' | Intent: %s | Target k=%d | Lambda=%.2f",
                task_id, task_intent, target_k, lambda_val
            )

            selected_chunks, is_insufficient, abstention_msg = self._execute_search_pipeline(
                payload=payload, target_k=target_k, lambda_val=lambda_val
            )

            task_insufficient_flags.append(is_insufficient)
            if is_insufficient:
                last_abstention_msg = abstention_msg

            task_results[task_id] = selected_chunks
            all_selected_chunks.extend(selected_chunks)

        # Deduplicate chunks across different tasks
        seen_ids = set()
        unique_chunks: list[dict[str, Any]] = []
        for ch in all_selected_chunks:
            c_id = ch.get("chunk_id") or ch.get("id")
            if c_id not in seen_ids:
                seen_ids.add(c_id)
                unique_chunks.append(ch)

        # Package Provenance and generate Citations
        assembled_evidence = self.assembler.assemble(unique_chunks)
        llm_context = self.assembler.format_llm_context(assembled_evidence)

        # Determine evidence state for the generation layer to route hybrid mode
        total_found = len(assembled_evidence)
        if total_found == 0:
            evidence_status = "NONE"
            llm_context = last_abstention_msg or "No relevant data was found in the knowledge base for this question"
        elif any(task_insufficient_flags) or total_found < 2:
            evidence_status = "PARTIAL" # Partial evidence -> Enable Hybrid Mode
        else:
            evidence_status = "FULL"

        evidence_dict_list = [ev.to_dict() for ev in assembled_evidence]
        ev_by_id = {ev["chunk_id"]: ev for ev in evidence_dict_list}

        response = {
            "status": "ok",
            "evidence_status": evidence_status,
            "intent": global_intent,
            "total_evidence": total_found,
            "evidence_by_task": {
                t_id: [ev_by_id[c["chunk_id"]] for c in c_list if c.get("chunk_id") in ev_by_id]
                for t_id, c_list in task_results.items()
            },
            "evidence_list": evidence_dict_list,
            "llm_context": llm_context,
        }
        return response

    @staticmethod
    def _empty_response(status: str, intent: str, llm_context: str) -> dict[str, Any]:
        return {
            "status": status,
            "evidence_status": "NONE",
            "intent": intent,
            "total_evidence": 0,
            "evidence_by_task": {},
            "evidence_list": [],
            "llm_context": llm_context,
        }
