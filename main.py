import sys, time
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR

RETRIEVAL_DIR = PROJECT_ROOT / "Retrieval"
SYNTHESIS_DIR = PROJECT_ROOT / "Response Synthesis"
STAGE_2_DIR = PROJECT_ROOT / "Query Understanding and Routing"

for p in (PROJECT_ROOT, RETRIEVAL_DIR, SYNTHESIS_DIR, STAGE_2_DIR):
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from retrieval_system import RetrievalSystem
from schema_response import FinalResponseEnvelope
from synthesizer import ResponseSynthesizer
from verifier import SEMANTIC_SUPPORT_THRESHOLD, EvidenceVerifier, MultilingualSemanticSimilarity
from traceable_formatter import TraceableFormatter
from rag_orchestrator import RAGOrchestrator
from llm import OllamaLLMClient, ColabLLMClient


class PipelineConnector:
    """Orchestrating end-to-end RAG pipeline across Stage 2 -> Stage 3 -> Stage 4"""

    def __init__(self, llm_client=None, warm_up_models=True):
        t0 = time.perf_counter()
        print("Initializing RetrievalSystem")
        self.retrieval_system = RetrievalSystem()

        if llm_client is None:
            llm_client =  OllamaLLMClient(model_name="qwen2.5:3b") # ColabLLMClient() 
        self.llm_client = llm_client

        print("Initializing Response Synthesis, Verifier & Formatter with Remote LLM")
        self.synthesizer = ResponseSynthesizer(llm_client=self.llm_client)

        similarity_scorer = self._create_shared_similarity_scorer()
        self.verifier = EvidenceVerifier(
            similarity_fn=similarity_scorer,
            support_threshold=SEMANTIC_SUPPORT_THRESHOLD,
        )

        self.formatter = TraceableFormatter()


        self.orchestrator = RAGOrchestrator(
            retrieval_system=self.retrieval_system,
            synthesizer=self.synthesizer,
            verifier=self.verifier,
            traceable_formatter=self.formatter,
        )

        self.conversation_history: list[dict[str, str]] = []
        
        if warm_up_models:
            self.warm_up()

        print(f"Pipeline ready in {time.perf_counter() - t0:.2f}s.")
        
    def _create_shared_similarity_scorer(self) -> MultilingualSemanticSimilarity:
        """Share the loaded BGE-M3 SentenceTransformer instance between Retrieval and Verifier."""
        scorer = MultilingualSemanticSimilarity()
        vectorizer = getattr(self.retrieval_system, "vectorizer", None)
        if vectorizer is not None:
            shared_model = (
                vectorizer._get_model()
                if hasattr(vectorizer, "_get_model")
                else getattr(vectorizer, "model", None)
            )
            if shared_model is not None:
                if hasattr(scorer, "_model"):
                    scorer._model = shared_model
                elif hasattr(scorer, "model"):
                    scorer.model = shared_model
        return scorer
    
    def warm_up(self) -> None:
        """Pre-load Reranker and warm up Ollama model in RAM/VRAM to avoid cold-start delay."""
        try:
            reranker = getattr(self.retrieval_system, "reranker", None)
            if reranker is not None and hasattr(reranker, "_get_model"):
                reranker._get_model()
        except Exception:
            pass

        try:
            if isinstance(self.llm_client, OllamaLLMClient):
                self.llm_client.generate(prompt="Hi", max_tokens=1)
        except Exception:
            pass
        
        
    def ask(self, query: str) -> FinalResponseEnvelope:
        print(f"\n>>> Processing query: '{query}'...")
        
        envelope = self.orchestrator.run(query, conversation_history=self.conversation_history)
        
        if envelope.status == "ok" and envelope.final_answer_markdown:
            self.conversation_history.append({"role": "user", "content": query})
            clean_answer = envelope.final_answer_markdown.split("---")[0].strip()
            self.conversation_history.append({"role": "assistant", "content": clean_answer})
            
        return envelope

    def reset_conversation(self):
        self.conversation_history.clear()
        
if __name__ == "__main__":
    try:
        local_llm = OllamaLLMClient(model_name="qwen2.5:3b")
        remote_llm = ColabLLMClient()
        pipeline = PipelineConnector(llm_client=local_llm)
    except Exception as e:
        print(f"System initialization error: {e}")
        sys.exit(1)

    print("\n" + "=" * 80)
    print(" INTELLIGENT INFORMATION SECURITY ASSISTANT (INTELLIGENT SECURITY WORKSPACE)")
    print(" Enter your question and press Enter")
    print("    Type 'exit' or 'quit' to exit")
    print("    Type 'reset' or 'clear' or 'refresh' to reset session")
    print("=" * 80)

    while True:
        try:
            user_query = input("\n[QUERY] ").strip()
            if not user_query:
                continue

            if user_query.lower() in {"exit", "quit", "q"}:
                print("Goodbye!")
                break
            
            if user_query.lower() in {"reset", "clear", "refresh"}:
                pipeline.orchestrator.reset_conversation()
                print("Session context refreshed")
                continue

            envelope = pipeline.ask(user_query)


            print("\n" + "-" * 80)
            print("[RESPONSE]: ")
            print("-" * 80)
            print(envelope.final_answer_markdown)
            print("-" * 80)

            if envelope.resolved_query and envelope.resolved_query != user_query:
                print(f"[Context Used]: Query rewritten as: {envelope.resolved_query}")
                
            # Print control metadata
            print(f">>> [Moderation] Status: {envelope.status} | Intent: {envelope.intent}")
            if envelope.verification:
                v = envelope.verification
                print(f"[Faithfulness] Status: {v.status} | Faithfulness: {v.faithfulness_score * 100:.1f}%")
            if envelope.citations:
                print(f"[Citations] Referenced {len(envelope.citations)} sources")

        except KeyboardInterrupt:
            print("\nProgram terminated")
            break
        except Exception as err:
            print(f"\nAn error occurred during processing: {err}")
