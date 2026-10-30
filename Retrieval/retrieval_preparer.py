import logging
from typing import Any
from schema import ScopedRetrievalPayload
from scope_builder import ScopeBuilder
from query_vectorizer import QueryVectorizer

logger = logging.getLogger(__name__)



DOCUMENT_RETRIEVAL_INTENTS: set[str] = {"SEARCH", "COMPARE", "SYNTHESIS"}
RETRIEVAL_TASK_TYPES: set[str] = {"SEARCH", "COMPARE", "SYNTHESIS"}


class RetrievalPreparer:
    def __init__(self, scope_builder: ScopeBuilder | None  = None, query_vectorizer: QueryVectorizer | None = None):
        self.scope_builder = scope_builder or ScopeBuilder()
        self.vectorizer = query_vectorizer or QueryVectorizer()

    def prepare(self, input_data: dict[str, Any]) -> list[ScopedRetrievalPayload]:
        """
        Accepts a Response Envelope or Query Plan.
        Orchestrates data preparation for the Hybrid Search layer based on query intent
        """
        # Check envelope status
        if "status" in input_data and input_data.get("status") != "ok":
            logger.info("Invalid envelope status ('%s')", input_data.get("status"))
            return []
        
        
        plan = input_data.get("query_plan", input_data)
        if plan.get("need_clarify") is True:
            logger.info("Clarification required")
            return []

        intent = plan.get("intent", "").upper()
        if intent not in DOCUMENT_RETRIEVAL_INTENTS:
            logger.info("Intent '%s' requires no document retrieval (CLARIFICATION/OUT_OF_SCOPE) -> Skipping", intent)
            return []

        # Filter Invalid task
        raw_tasks = plan.get("task") or []
        target_tasks = []

        if not raw_tasks:
            q_str = plan.get("resolved_query") or plan.get("original_query") or ""
            target_tasks.append(
                {
                    "task_id": "main_task",
                    "intent_task": intent,
                    "query": q_str,
                    "task_obj": None,
                }
            )
        else:
            for t in raw_tasks:
                t_intent = t.get("intent_task", intent).upper()
                if t_intent in RETRIEVAL_TASK_TYPES:
                    target_tasks.append(
                        {
                            "task_id": t.get("task_id", "default_task"),
                            "intent_task": t_intent,
                            "query": t.get("query") or plan.get("resolved_query") or "",
                            "task_obj": t,
                        }
                    )
                else:
                    logger.info("Task '%s' has intent_task '%s' outside the supported catalog -> Skipping", t.get("task_id"), t_intent)

        if not target_tasks:
            return []

        # Batch vectorize all query
        query_texts = [item["query"] for item in target_tasks]
        vectors = self.vectorizer.vectorize_batch(query_texts)

        payloads = []
        for item, vec in zip(target_tasks, vectors):
            scope, expansions = self.scope_builder.build_scope(
                plan, task=item["task_obj"]
            )

            # Prepare BM25 keyword query: Base query + Ontology expansion terms
            expanded_bm25 = item["query"]
            if expansions:
                expanded_bm25 += " " + " ".join(expansions)

            payloads.append(
                ScopedRetrievalPayload(
                    task_id=item["task_id"],
                    intent_task=item["intent_task"],
                    original_query=item["query"],
                    query_bm25=expanded_bm25,
                    query_vector=vec,
                    scope=scope,
                )
            )

        return payloads
