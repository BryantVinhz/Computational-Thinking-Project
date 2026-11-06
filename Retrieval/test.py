import json
import logging
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))


from schema import ScopedRetrievalPayload
from retrieval_preparer import RetrievalPreparer
from bm25_retrieval import BM25Retriever
from dense_retrieval import DenseRetriever
from rrf import reciprocal_rank_fusion


logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")


def print_comparison_table(
    query_title: str,
    original_query: str,
    bm25_hits: list[dict],
    dense_hits: list[dict],
    rrf_hits: list[dict],
    top_n: int = 5,
):
    print("\n" + "=" * 90)
    print(f"TEST: {query_title.upper()}")
    print(f"Original Query: \"{original_query}\"")
    print("=" * 90)

    print("\n--- [1] TOP BM25 RETRIEVAL ---")
    print(f"{'Rank':<5} | {'Chunk ID':<25} | {'BM25 Score':<12} | {'Document ID':<30} | {'Heading Path'}")
    print("-" * 90)
    for hit in bm25_hits[:top_n]:
        hp = " > ".join(hit["metadata"].get("heading_path") or [])
        hp_short = (hp[:30] + "...") if len(hp) > 30 else hp
        print(f"{hit['rank']:<5} | {hit['chunk_id']:<25} | {hit['score']:<12.4f} | {hit['metadata'].get('document_id', ''):<30} | {hp_short}")

    print("\n--- [2] TOP DENSE RETRIEVAL ---")
    print(f"{'Rank':<5} | {'Chunk ID':<25} | {'Cosine Sim':<12} | {'Document ID':<30} | {'Heading Path'}")
    print("-" * 90)
    for hit in dense_hits[:top_n]:
        hp = " > ".join(hit["metadata"].get("heading_path") or [])
        hp_short = (hp[:30] + "...") if len(hp) > 30 else hp
        print(f"{hit['rank']:<5} | {hit['chunk_id']:<25} | {hit['score']:<12.4f} | {hit['metadata'].get('document_id', ''):<30} | {hp_short}")

    print("\n--- [3] TOP HYBRID - RRF ---")
    print(f"{'Rank':<5} | {'Chunk ID':<25} | {'RRF Score':<12} | {'BM25/Dense Rank':<16} | {'Source':<20} | {'Document ID'}")
    print("-" * 90)
    for hit in rrf_hits[:top_n]:
        rank_str = f"BM25:{hit.get('bm25_rank') or '-'} | Dense:{hit.get('dense_rank') or '-'}"
        print(f"{hit['rank']:<5} | {hit['chunk_id']:<25} | {hit['rrf_score']:<12.6f} | {rank_str:<16} | {hit['source']:<20} | {hit['metadata'].get('document_id', '')}")


def run_test_suite():
    dense_engine = DenseRetriever()
    bm25_engine = BM25Retriever()
    preparer = RetrievalPreparer()

    test_cases = [
        {
            "title": "Semantic Search Test (Semantic QA)",
            "envelope": {
                "status": "ok",
                "query_plan": {
                    "resolved_query": "Bản chất và khái niệm của lỗ hổng SQL Injection là gì?",
                    "intent": "SEARCH",
                    "filter": {"source": [], "version": None, "document_type": None},
                    "task": [{
                        "task_id": "test_semantic_01",
                        "intent_task": "SEARCH",
                        "query": "Bản chất và khái niệm của lỗ hổng SQL Injection là gì?",
                        "objective": {"type": "vulnerabilities", "ids": ["sqli"]}
                    }]
                }
            }
        },
        {
            "title": "Hard Filter Application Test (Hard Filter: OWASP A05 Injection)",
            "envelope": {
                "status": "ok",
                "query_plan": {
                    "resolved_query": "How to prevent Injection vulnerabilities?",
                    "intent": "SEARCH",
                    "filter": {
                        "source": ["owasp_top10_a05_injection"],
                        "version": None,
                        "document_type": None
                    },
                    "task": [{
                        "task_id": "test_filtered_03",
                        "intent_task": "SEARCH",
                        "query": "How to prevent Injection vulnerabilities?",
                        "objective": {"type": "documents", "ids": ["doc_owasp_top10_a05_injection"]}
                    }]
                }
            }
        }
    ]

    for tc in test_cases:
        payloads = preparer.prepare(tc["envelope"])
        if not payloads:
            print(f"[ERROR] Failed to create payload for: {tc['title']}")
            continue

        payload: ScopedRetrievalPayload = payloads[0]

        bm25_hits = bm25_engine.retrieve_from_payload(payload, top_k=10)
        dense_hits = dense_engine.retrieve_from_payload(payload, top_k=10)
        rrf_hits = reciprocal_rank_fusion(
            bm25_results=bm25_hits,
            dense_results=dense_hits,
            k=60,
            top_k=10
        )

        print_comparison_table(
            query_title=tc["title"],
            original_query=payload.original_query,
            bm25_hits=bm25_hits,
            dense_hits=dense_hits,
            rrf_hits=rrf_hits,
            top_n=5,
        )


if __name__ == "__main__":
    run_test_suite()
