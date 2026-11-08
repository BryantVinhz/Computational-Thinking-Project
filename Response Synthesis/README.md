# Stage 4: Response Synthesis, Evidence Verification & Traceable Response

Directory containing source code and technical documentation for the final stage of the RAG system in the **Computational Thinking Project**.

---

## 1. Role Assignment

* **Nguyen (Response Synthesis & Orchestration):**
  * `schema_response.py`: Standardize Data Contract (v1.0), add `evidence_status` and `generation_mode` for Hybrid Mode.
  * `prompt_templates.py`: Build prompt templates for `SEARCH`, `COMPARE`, `SYNTHESIS` intents, with `STRICT_RAG` and `HYBRID_MODE` directives.
  * `synthesizer.py`: Call LLM, generate answer draft (`SynthesizedDraftSchema`) with inline citations `[1]`, `[2]`.
  * `rag_orchestrator.py`: Connect the complete flow from Query Understanding -> Retrieval -> Stage 4.
  * `test_synthesis_orchestration.py`: Automated test suite for the entire Synthesis & Orchestration layer.
* **Bao (Evidence Verification & Traceability):**
  * `verifier.py`: Extract claims, verify against `AssembledEvidence`, compute `faithfulness_score` (`VerificationReportSchema`).
  * `traceable_formatter.py`: Extract `[1]`, `[2]` tags, parse location information (`TraceableCitationSchema`), and generate Footnotes/References section at the end of the text.

---

## 2. Interaction Protocol (Contract Overview)

All data structures are defined and standardized at: [`schema_response.py`](file:///D:/hcmus_iv/Tư%20duy%20tính%20toán/Project/Computational-Thinking-Project/Response%20Synthesis/schema_response.py)

```
[Retrieval Output: AssembledEvidence + evidence_status]
                          │
                          ▼
                 [Synthesizer (Nguyen)]
             (Strict RAG / Hybrid / Abstention)
                          │
                          ▼
                SynthesizedDraftSchema
                          │
                          ▼
          ┌───────────────┴───────────────┐
          ▼                               ▼
    [Verifier (Bao)]    [TraceableFormatter (Bao)]
          │                               │
          ▼                               ▼
VerificationReportSchema  list[TraceableCitationSchema]
          └───────────────┬───────────────┘
                          │
                          ▼
                 FinalResponseEnvelope
```

---

## 3. Synthesis Modes

The Synthesis layer receives `evidence_status` from Retrieval (`RetrievalSystem.retrieve`):
1. **`STRICT_RAG` (`evidence_status == "FULL"`):**
   - Sufficient evidence (2 or more chunks, no missing sub-tasks).
   - Model is restricted to facts in the provided context, requiring `[1]`, `[2]` citations for each claim.
2. **`HYBRID` (`evidence_status == "PARTIAL"`):**
   - Partial evidence (fewer than 2 chunks or sub-tasks missing evidence).
   - Model grounds supported facts with `[1]`, `[2]`, and supplements missing domain knowledge clearly annotated under `General Knowledge Advisory`.
3. **`ABSTENTION` (`evidence_status == "NONE"` or `status == "clarification_needed"`):**
   - Activates safety abstention policy or requests clarification to eliminate hallucinations.

---

## 4. Import and Usage

```python
import sys
from pathlib import Path

STAGE_4_DIR = Path(__file__).resolve().parent
if str(STAGE_4_DIR) not in sys.path:
    sys.path.insert(0, str(STAGE_4_DIR))

from schema_response import FinalResponseEnvelope, SynthesizedDraftSchema
from synthesizer import ResponseSynthesizer
from rag_orchestrator import RAGOrchestrator
from verifier import EvidenceVerifier
from traceable_formatter import TraceableFormatter

# 1. Initialize Synthesizer, Verifier, Formatter & Orchestrator
synthesizer = ResponseSynthesizer()
orchestrator = RAGOrchestrator(
    synthesizer=synthesizer,
    verifier=EvidenceVerifier(),
    traceable_formatter=TraceableFormatter(),
)

# 2. Run from Retrieval output
envelope: FinalResponseEnvelope = orchestrator.run_from_retrieval(
    query="How to prevent SQL Injection?",
    retrieval_output=retrieval_output,
)

print(envelope.final_answer_markdown)
print(envelope.verification.status, envelope.verification.faithfulness_score)
```

---

## 5. Evidence Verification & Traceability (Bao)

**`EvidenceVerifier.verify(draft, evidence_list) -> VerificationReportSchema`**
1. Split `draft_answer` into atomic claims by sentence / line (skipping headings, notes `(...)`, table headers, code blocks).
2. Claims with valid tags are checked **only** against their cited evidence; uncited claims are scanned across all evidence (SUPPORTED with missing-tag warning).
3. `SUPPORTED` when support score >= `support_threshold` (default 0.5); `CONTRADICTED` when matched content has opposing negation polarity; otherwise `UNVERIFIED`.
4. `faithfulness_score = supported / grounded_claims`. In `HYBRID` mode, claims under `General Knowledge Advisory` are listed but excluded from the score.
5. Status: `PASSED` >= 0.8, `WARNING` >= 0.5, `FAILED` < 0.5, `SKIPPED` on abstention.

Default scoring uses lexical coverage (unigram + bigram), running offline. For cross-lingual verification, pass a semantic similarity function:

```python
verifier = EvidenceVerifier(similarity_fn=lambda claim, passage: cosine(embed(claim), embed(passage)))
```

**`TraceableFormatter.format_citations(draft, evidence_list) -> (markdown, citations)`**
- Map `[n]` by `evidence_id` from Retrieval; only list tags that actually appear in the answer.
- `location_summary` generated from Provenance locators: `Page 3-4` (PDF), `Lines 12-25` (MD/TXT), `Rows 4-6` (CSV), `JSON $.path` (JSON); `locator["spans"]` preserves all blocks + offsets for audit.
- Tags without matching evidence are flagged with ⚠️ in References instead of being dropped.

Run tests: `python -m unittest test_verification_traceability test_synthesis_orchestration`
