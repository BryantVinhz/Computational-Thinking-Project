# REPORT_MODULE.md — Query Understanding and Routing

Explains what lives inside the **`Query Understanding and Routing/`** folder, what each module does, how data flows, and the refactor applied on branch `stage2`.

> Companion doc: see [`MANUAL_RUN.md`](MANUAL_RUN.md) for step-by-step setup, run, and test commands.
> Deeper design notes already exist in [`query_understanding_routing/DEVELOPMENT.md`](query_understanding_routing/DEVELOPMENT.md) and [`query_understanding_routing/PIPELINE.md`](query_understanding_routing/PIPELINE.md); this file is the module-level map.

---

## 1. Purpose

This folder is **Stage 2** of the Intelligent Document Workspace. It turns a raw user query (plus optional conversation history) into exactly **one Contract v1.0 response envelope** that downstream retrieval/generation (Stage 3/4) can act on. It:

1. **Validates** the query (length, characters, spam, prompt-injection, unsafe-attack intent).
2. **Normalizes** text (Unicode/whitespace cleanup, then ontology alias → canonical mapping).
3. **Understands** it via a pluggable LLM (intent, entities, filters, multi-task decomposition, context resolution), with a deterministic fallback.
4. **Routes** all of the above into a single JSON envelope.

A thin **RAG processor** (`rag_processor.py`) chains Stage 2 into Stage 3 (retrieval) + Stage 4 (grounded generation) for end-to-end demos.

---

## 2. Folder layout

```
Query Understanding and Routing/
├── REPORT_MODULE.md                 ← this file (what each module does)
├── MANUAL_RUN.md                    ← how to run & test manually
├── PROMPT.md                        ← original task brief
├── MANUAL_TESTING_GUIDE.md          ← pre-existing manual test notes
├── manual_test.py                   ← pre-existing ad-hoc manual script
├── report.md / Phan_cong_*.md       ← pre-existing Vietnamese reports
│
├── query_understanding_routing/     ← MAIN package (the improved sources)
│   ├── __init__.py                  ← public API surface + lazy imports
│   ├── config.py                    ← all thresholds, intents, error codes, messages, regexes
│   ├── validation_module.py         ← structural + safety validation
│   ├── normalization_module.py      ← text cleanup + alias→canonical mapping
│   ├── understanding_module.py      ← LLM + rule-based understanding engine
│   ├── routing_module.py            ← assembles the Contract v1.0 envelope  (routing sources)
│   ├── pipeline.py                  ← orchestrator: validate → normalize → understand → route
│   ├── llm_client.py                ← Ollama / Gemini / OpenAI-like / Mock clients + factory
│   ├── rag_processor.py             ← Stage 2 → Stage 3 retrieval → Stage 4 generation
│   ├── ontology_loader.py           ← loads & validates ontology.yaml
│   ├── ontology.yaml                ← 5 vulnerability slugs + OWASP mapping (single source of truth)
│   ├── prompt_templates.py          ← system instruction + few-shot + prompt builders
│   ├── paths.py                     ← cross-platform resolved paths
│   └── tests/                       ← full pytest suite + JSON fixtures
```

---

## 3. What each module does

| Module | Responsibility | Key public functions |
|---|---|---|
| `config.py` | Single place for every constant: query-length bounds, history limits, LLM settings, valid intents / task intents / LLM error codes, standard slugs, user-facing messages, and precompiled safety regex sources. | constants only |
| `validation_module.py` | First gate. Rejects empty/too-short/too-long/spam/invalid-char/invalid-type queries; detects prompt injection and unsafe attack intent; decides `safety_action` ∈ `ALLOW` / `CLARIFY` / `REFUSE`. | `validate_query(raw_query) -> ValidationResult` |
| `normalization_module.py` | Between validation and the LLM. Phase A: newline/zero-width/Unicode (NFC)/whitespace cleanup. Phase B: map ontology aliases to canonical vocabulary. Can yield an empty string (pipeline handles it). | `normalize_query(raw, ontology) -> {"normalized_query": ...}` |
| `understanding_module.py` | The brain. Classifies intent (`SEARCH`/`COMPARE`/`SYNTHESIS`/`CLARIFICATION`/`OUT_OF_SCOPE`), extracts entities & filters, decomposes into `task[]`, resolves context from history (strict `context_used`), truncates history (§7.1), enforces the two-way `CLARIFICATION ⇔ need_clarify` rule. Never emits `original_query`. | `understand_query(...)`, `QueryUnderstandingEngine`, `truncate_conversation_history`, `get_default_llm_client` |
| `routing_module.py` | **Routing sources.** Combines the validation + understanding results into one envelope, attaches `original_query`, sets `status`, and schema-checks the output. | `route(raw_query, validation_result, understanding_result=None, ontology=None, document_ids=None)`, `SCHEMA_VERSION` |
| `pipeline.py` | Orchestrates the four stages with caching + error isolation; always returns exactly one envelope. | `process_query(raw_query, conversation_history=None, *, llm_client=None, ontology=None, document_ids=None)` |
| `llm_client.py` | Pluggable LLM connectors with retry/markdown-fence cleanup; `MockLLMClient` gives deterministic offline behaviour used by the tests. | `create_llm_client(provider=...)`, `MockLLMClient`, `OllamaLLMClient`, `GeminiLLMClient`, `OpenAILikeLLMClient` |
| `ontology_loader.py` | Loads `ontology.yaml`, enforces exactly 5 slugs, required fields, no cross-entry alias collisions, forbids ambiguous alias `bac`. | `load_ontology(path=None)`, `DEFAULT_ONTOLOGY_PATH` |
| `rag_processor.py` | End-to-end demo: runs Stage 2, then retrieves matching knowledge chunks, then generates a grounded, cited answer. | `EndToEndRAGPipeline`, `RAGGenerator` |
| `prompt_templates.py` | System instruction, few-shot examples, and the prompt builder for LLM understanding. | `build_understanding_prompt(...)`, `SYSTEM_INSTRUCTION_QUERY_UNDERSTANDING` |
| `paths.py` / `path.py` | Resolve `DATA_DIR`, ontology path, package/stage/project roots with `.resolve()` so imports work from root, from the folder, or from `tests/`. | path constants |

---

## 4. Data flow (pipeline)

```
raw_query (+ conversation_history)
        │
        ▼
validate_query ──► not valid / CLARIFY ─► route(...) ─► envelope (rejected | clarification)
        │ valid
        ▼
load_ontology (cached) ── fails ─► route(..., internal_error) ─► envelope (error)
        │
        ▼
normalize_query ── empty result ─► route(..., empty_query) ─► envelope (rejected)
        │
        ▼
understand_query (LLM or fallback) ── exception/invalid ─► internal_error
        │
        ▼
route(raw, validation, understanding, ontology, document_ids)
        │
        ▼
ONE Contract v1.0 envelope  (status: ok | rejected | clarification | out_of_scope | error)
```

`process_query` wraps this in timing + a last-resort `error` envelope so a crash never escapes as an exception.

---

## 5. Output contract (Contract v1.0)

Every call returns a single envelope. `schema_version` is always `"1.0"`.

- **ok** → carries `query_plan` (`resolved_query`, `intent`, `confidence`, `context_used`, `entities`, `filter`, `task[]`, `need_clarify`, `clarification_message`).
- **rejected** → `reject_reason`, `reason`, `safety_action` (e.g. unsafe intent → `REFUSE`).
- **clarification** → `source`, `clarification_message`.
- **out_of_scope** → fixed out-of-scope message.
- **error** → `error_code` (`internal_error`, `llm_unavailable`, `llm_invalid_json`, `llm_schema_violation`), `reason`.

Canonical examples live in [`query_understanding_routing/tests/fixtures/`](query_understanding_routing/tests/fixtures/) (`envelope_ok.json`, `envelope_rejected.json`, `envelope_clarification.json`, `envelope_out_of_scope.json`, `envelope_error.json`).

### Vocabulary (from `config.py` / `ontology.yaml`)
- **Standard slugs (5):** `xss`, `sqli`, `bac`, `auth_failures`, `misconfig` (each mapped to an OWASP id, e.g. `xss → A05:2025`).
- **Overall intents:** `SEARCH`, `COMPARE`, `SYNTHESIS`, `CLARIFICATION`, `OUT_OF_SCOPE`.
- **Task intents:** `SEARCH`, `COMPARE`, `SYNTHESIS`.

---

## 6. Refactor: Complete Elimination of Legacy `stage_2` & Redundancies

Following the architectural cleanup:
- **Complete removal of `stage_2/`:** The entire legacy `stage_2/` shim subpackage has been removed. All imports across the codebase and test suite now point directly and uniformly to `query_understanding_routing`.
- **Elimination of redundant alias files:** Removed `path.py` (which was an exact duplicate alias of `paths.py`).
- **Cleaned build artifacts:** Removed stale `query_understanding_routing.egg-info/` directory.
- **Config & Ontology alignment:** Cleaned `pyproject.toml` to remove `stage_2` entries. Corrected `ontology.yaml` schema fields (`owasp` string mappings, canonical aliases for `xss`, `sqli`, `misconfig`) ensuring 100% test pass rate.
- **Detailed Log:** Recorded in `Query Understanding and Routing/REFACTOR.md`.

### Verification
Full suite passes: **616 passed, 1 xfailed** (0 failures).

---

## 7. Known limitations (carried from `PIPELINE.md`)
- Rule-based understanding covers the deterministic path; live-LLM understanding is exercised via `MockLLMClient` in tests (no live-LLM tests).
- Validation runs once per turn; multi-turn attack chains are a known v1.0 gap (`xfail`).
