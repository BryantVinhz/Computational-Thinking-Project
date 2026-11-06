import sys
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
from verifier import EvidenceVerifier
from traceable_formatter import TraceableFormatter
from rag_orchestrator import RAGOrchestrator
from llm import ColabLLMClient


class PipelineConnector:
    """Orchestrating end-to-end RAG pipeline across Stage 2 -> Stage 3 -> Stage 4"""

    def __init__(self, llm_client=None):
        print("Initializing RetrievalSystem")
        self.retrieval_system = RetrievalSystem()

        if llm_client is None:
            llm_client = ColabLLMClient()
        self.llm_client = llm_client

        print("Initializing Response Synthesis, Verifier & Formatter with Remote LLM")
        self.synthesizer = ResponseSynthesizer(llm_client=self.llm_client)

        try:
            self.verifier = EvidenceVerifier(llm_client=self.llm_client)
        except TypeError:
            self.verifier = EvidenceVerifier()

        self.formatter = TraceableFormatter()

        self.orchestrator = RAGOrchestrator(
            retrieval_system=self.retrieval_system,
            synthesizer=self.synthesizer,
            verifier=self.verifier,
            traceable_formatter=self.formatter,
        )

    def ask(self, query: str) -> FinalResponseEnvelope:
        print(f"\n>>> Processing query: '{query}'...")
        return self.orchestrator.run(query)


if __name__ == "__main__":
    try:
        remote_llm = ColabLLMClient()
        pipeline = PipelineConnector(llm_client=remote_llm)
    except Exception as e:
        print(f"System initialization error: {e}")
        sys.exit(1)

    print("\n" + "=" * 80)
    print(" INTELLIGENT INFORMATION SECURITY ASSISTANT (INTELLIGENT SECURITY WORKSPACE)")
    print(" Enter your question and press Enter. Type 'exit' or 'quit' to exit")
    print("=" * 80)

    while True:
        try:
            user_query = input("\n[QUERY] ").strip()
            if not user_query:
                continue

            if user_query.lower() in {"exit", "quit", "q"}:
                print("Goodbye!")
                break

            envelope = pipeline.ask(user_query)

            print("\n" + "-" * 80)
            print("[RESPONE]: ")
            print("-" * 80)
            print(envelope.final_answer_markdown)
            print("-" * 80)

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
