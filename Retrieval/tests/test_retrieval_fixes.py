"""Behavioral checks for the Retrieval/ingestion fixes. Heavy deps (chromadb, rank_bm25, langchain) are stubbed."""
import sys
import types
import unittest
from pathlib import Path

# Repo root: optional first CLI arg (used to test an exported copy), otherwise two levels up.
REPO = (Path(sys.argv.pop(1)).resolve() if len(sys.argv) > 1 and Path(sys.argv[1]).is_dir()
        else Path(__file__).resolve().parents[2])

# --- stub third-party modules not installed on this machine ---------------------------------
chromadb = types.ModuleType("chromadb")
chromadb.PersistentClient = lambda path: None
ef = types.ModuleType("chromadb.utils.embedding_functions")
ef.SentenceTransformerEmbeddingFunction = object
utils = types.ModuleType("chromadb.utils")
sys.modules.update({"chromadb": chromadb, "chromadb.utils": utils, "chromadb.utils.embedding_functions": ef})
rank_bm25 = types.ModuleType("rank_bm25")
rank_bm25.BM25Okapi = lambda corpus: None
sys.modules["rank_bm25"] = rank_bm25
for name in ("langchain_community", "langchain_community.vectorstores", "langchain_community.vectorstores.utils"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["langchain_community.vectorstores.utils"].maximal_marginal_relevance = lambda **kw: list(range(kw["k"]))

for p in (REPO / "Retrieval", REPO / "Document Ingestion and Parsing" / "Metadata", REPO / "Document Ingestion and Parsing"):
    sys.path.insert(0, str(p))

import retrieval_system as rs  # noqa: E402
from query_vectorizer import BGE_QUERY_INSTRUCTION, QueryVectorizer  # noqa: E402
from schema import RetrievalScope, ScopedRetrievalPayload  # noqa: E402
from scope_builder import ScopeBuilder  # noqa: E402

DOCS = [{"document_id": "owasp_top10", "canonical_title": "OWASP Top 10", "publisher": "OWASP"}]
VERSIONS = [
    {"document_id": "owasp_top10", "version_id": "ver_2021", "version_tag": "2021", "file_name": "top10_2021.md"},
    {"document_id": "owasp_top10", "version_id": "ver_2025", "version_tag": "2025", "file_name": "top10_2025.md"},
]


class FakeVectorizer:
    def vectorize(self, text):
        return [0.1, 0.2]


class RecordingRetriever:
    def __init__(self, log):
        self.log = log

    def retrieve_from_payload(self, payload, top_k=10):
        self.log.append(payload)
        version = payload.scope.version_ids[0] if payload.scope.version_ids else "none"
        return [{"id": f"c_{payload.task_id}_{version}", "chunk_id": f"c_{payload.task_id}_{version}",
                 "text": "t", "metadata": {"version_id": version}, "score": 1.0}]


class PassReranker:
    def rerank(self, query, candidate_chunks, top_k=None):
        return types.SimpleNamespace(insufficient_evidence=False, chunks=candidate_chunks, abstention_message=None)


class BrokenReranker:
    def rerank(self, query, candidate_chunks, top_k=None):
        raise RuntimeError("model download failed")


class PassMMR:
    def select(self, candidates, **kwargs):
        return candidates


class FakeAssembler:
    def assemble(self, chunks):
        return [types.SimpleNamespace(to_dict=lambda c=c: {"chunk_id": c["chunk_id"], "metadata": c["metadata"]}) for c in chunks]

    @staticmethod
    def format_llm_context(evidence):
        return "ctx" if evidence else "No relevant evidence was found"


def make_system(log, reranker=None):
    original = rs.load_ontology_terms
    rs.load_ontology_terms = lambda: ({}, {})
    try:
        return rs.RetrievalSystem(
            scope_builder=ScopeBuilder(documents_catalog=DOCS, versions_catalog=VERSIONS, ontology_dict={}),
            query_vectorizer=FakeVectorizer(),
            bm25_retriever=RecordingRetriever(log),
            dense_retriever=RecordingRetriever([]),
            reranker=reranker or PassReranker(),
            mmr_selector=PassMMR(),
            evidence_assembler=FakeAssembler(),
        )
    finally:
        rs.load_ontology_terms = original


def plan(intent, query, tasks=None):
    return {"intent": intent, "resolved_query": query, "need_clarify": False,
            "filter": {"version": None, "document_type": None, "source": ["owasp_top10"]},
            "entities": {"document": [], "vulnerabilities": []},
            "task": tasks if tasks is not None else [{"task_id": "t1", "intent_task": intent, "query": query,
                                                       "objective": {"type": "documents", "ids": ["owasp_top10"]},
                                                       "depends_on": None}]}


class RetrievalFixes(unittest.TestCase):
    def test_compare_branches_are_version_isolated(self):
        log = []
        out = make_system(log).retrieve(plan("COMPARE", "So sánh OWASP Top 10 2021 và 2025"))
        self.assertEqual([p.scope.version_ids for p in log], [["ver_2021"], ["ver_2025"]])
        self.assertEqual(out["evidence_status"], "FULL")

    def test_out_of_scope_never_reaches_index(self):
        log = []
        out = make_system(log).retrieve(plan("OUT_OF_SCOPE", "thời tiết hôm nay", tasks=[]))
        self.assertEqual(log, [])
        self.assertEqual(out["evidence_status"], "NONE")

    def test_clarification_intent_never_reaches_index(self):
        log = []
        out = make_system(log).retrieve(plan("CLARIFICATION", "lỗi đó", tasks=[]))
        self.assertEqual(log, [])
        self.assertEqual(out["status"], "clarification_needed")

    def test_missing_version_abstains_instead_of_mixing(self):
        log = []
        out = make_system(log).retrieve(plan("SEARCH", "OWASP Top 10 2030 có gì mới"))
        self.assertEqual(log, [])
        self.assertEqual(out["evidence_status"], "NONE")
        self.assertEqual(out["llm_context"], rs.MISSING_VERSION_MESSAGE)

    def test_protocol_numbers_are_not_versions(self):
        log = []
        make_system(log).retrieve(plan("SEARCH", "Cấu hình TLS 1.2 và OAuth 2.0 an toàn"))
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0].scope.version_ids, [])
        self.assertFalse(log[0].scope.is_relaxed)

    def test_reranker_failure_fails_closed(self):
        out = make_system([], reranker=BrokenReranker()).retrieve(plan("SEARCH", "SQL injection là gì"))
        self.assertEqual(out["evidence_status"], "NONE")
        self.assertEqual(out["llm_context"], rs.RERANKER_UNAVAILABLE_MESSAGE)

    def test_dense_never_relaxes_scope_by_default(self):
        import dense_retrieval
        captured = {}
        retriever = dense_retrieval.DenseRetriever(collection=object())
        retriever.retrieve = lambda **kw: captured.update(kw) or []
        scope = RetrievalScope(chroma_filter={"document_id": "owasp_top10"}, is_relaxed=True)
        retriever.retrieve_from_payload(ScopedRetrievalPayload("t", "SEARCH", "q", "q", [0.1], scope))
        self.assertFalse(captured["fallback_unfiltered"])


class VectorizerFixes(unittest.TestCase):
    class Echo:
        def encode(self, texts, **kw):
            VectorizerFixes.seen = list(texts)
            return [[0.0] for _ in texts]

    def test_bge_m3_gets_no_instruction(self):
        QueryVectorizer(model_name="BAAI/bge-m3", model=self.Echo()).vectorize("SQL injection")
        self.assertEqual(self.seen, ["SQL injection"])

    def test_v15_query_gets_instruction_but_passages_never(self):
        v = QueryVectorizer(model_name="BAAI/bge-base-en-v1.5", model=self.Echo())
        v.vectorize("q")
        self.assertEqual(self.seen, [f"{BGE_QUERY_INSTRUCTION}q"])
        v.vectorize_passages(["chunk text"])
        self.assertEqual(self.seen, ["chunk text"])


class ChromaPurgeFix(unittest.TestCase):
    def test_stale_versions_of_same_file_are_deleted(self):
        import vector_store

        class FakeCollection:
            deletes, upserts = [], []
            def delete(self, where):
                self.deletes.append(where)
            def upsert(self, **kw):
                self.upserts.append(kw)
            def get(self, **kw):
                return {"ids": [], "documents": [], "metadatas": []}

        col = FakeCollection()
        vector_store.prepare_and_ingest_to_chroma(
            chunks_from_chunking=[{"chunk_id": "c1", "text_content": "x", "heading_path": []}],
            doc_meta_from_diep={"document_id": "owasp_top10", "version_id": "ver_new", "title": "T", "file_format": "MARKDOWN"},
            collection=col,
            version_record={"file_name": "top10_2021.md", "version_tag": "2021"},
        )
        self.assertEqual(col.deletes[0], {"$and": [
            {"document_id": "owasp_top10"},
            {"version_id": {"$ne": "ver_new"}},
            {"$or": [{"file_name": "top10_2021.md"}, {"version_tag": "2021"}]},
        ]})
        self.assertEqual(col.deletes[1], {"version_id": "ver_new"})
        meta = col.upserts[0]["metadatas"][0]
        self.assertEqual((meta["file_name"], meta["version_tag"]), ("top10_2021.md", "2021"))


if __name__ == "__main__":
    unittest.main()
