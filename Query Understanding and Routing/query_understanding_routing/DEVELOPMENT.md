# DEVELOPMENT & ARCHITECTURE GUIDE

This document captures architecture design, developer setup, LLM integration, and module-specific quirks for **Stage 2: Query Understanding and Routing** and the **RAG Pipeline**.

---

## 1. Architecture Overview

The system implements the **Contract v1.0** specification for the Intelligent Document Workspace:

```
[Raw User Query] + [Conversation History]
           │
           ▼
┌────────────────────────────────────────────────────────┐
│ Stage 2: Query Understanding and Routing               │
│                                                        │
│  1. Validation (validation_module.py)                  │
│     - Length, char checks, prompt injection, attacks   │
│     - Early-exit: status=rejected or clarification     │
│                                                        │
│  2. Normalization (normalization_module.py)            │
│     - Text cleaning, folding, alias mapping            │
│     - Uses ontology.yaml (5 slugs: xss, sqli, bac...)  │
│                                                        │
│  3. Query Understanding (understanding_module.py)      │
│     - Pluggable LLM (Ollama, Gemini, OpenAI, Mock)     │
│     - Fallback: deterministic semantic engine          │
│     - Intent: SEARCH, COMPARE, SYNTHESIS, CLARIFY, OOS │
│     - Context resolution & History truncation (7.1)    │
│     - Multi-task decomposition (task[])                │
│                                                        │
│  4. Routing & Envelope (routing_module.py)             │
│     - Assembles Contract v1.0 JSON envelope            │
│     - Attaches original_query, checks schema           │
└────────────────────────────────────────────────────────┘
           │
           │ (If status == 'ok')
           ▼
┌────────────────────────────────────────────────────────┐
│ Stage 3 & 4: Retrieval & Generation (rag_processor.py) │
│                                                        │
│  - Retrieval: Matches query_plan.task objectives to    │
│    document chunks (OWASP Top 10, ASVS, CVSS v4.0).    │
│  - Generation: Calls LLM model with grounded prompt    │
│    and citations to synthesize final response.         │
└────────────────────────────────────────────────────────┘
```

---

## 2. Setup Instructions

### Environment Prerequisites
- Python: `>= 3.10` (tested with Python 3.12 and Python 3.14).
- Dependencies: `PyYAML==6.0.3`, `pytest>=9.0`.

### Installation
Install the package in editable mode from the repository root:

```powershell
# Using Python Launcher on Windows:
py -m pip install -e . --no-deps

# Or with python:
python -m pip install -e . --no-deps
```

### Running Tests
Execute the comprehensive test suite (617+ tests):

```powershell
py -m pytest
```

---

## 3. LLM Model Integration in the RAG Process

In a standard RAG pipeline, the LLM model is called at two critical stages:
1. **Query Understanding (Stage 2)**: Classifying complex user intents, extracting entities/constraints, resolving coreferences against conversation history, and decomposing multi-intent questions into structured tasks.
2. **Response Generation (Stage 4)**: Grounded synthesis where the LLM reads retrieved document chunks and produces an answer with citations.

### Supported LLM Providers (`llm_client.py`)

| Provider | Client Class | Default Model | Configuration / Env Vars |
|---|---|---|---|
| **Ollama (Local)** | `OllamaLLMClient` | `qwen2.5:7b` | `LLM_PROVIDER=ollama`, `OLLAMA_HOST=http://localhost:11434` |
| **Gemini (Cloud)** | `GeminiLLMClient` | `gemini-1.5-flash` | `LLM_PROVIDER=gemini`, `GEMINI_API_KEY=<key>` |
| **OpenAI / vLLM** | `OpenAILikeLLMClient` | `gpt-4o-mini` | `LLM_PROVIDER=openai`, `OPENAI_API_KEY=<key>`, `OPENAI_BASE_URL` |
| **Mock (Offline)** | `MockLLMClient` | `mock-model` | `LLM_PROVIDER=mock` (Zero network dependency, fast unit tests) |

### How to Use the LLM in Code

#### 1. Calling Stage 2 Pipeline with an LLM
```python
from query_understanding_routing import process_query, create_llm_client

# Option A: Connect to local Ollama (e.g., Qwen 2.5)
llm = create_llm_client(provider="ollama", model_name="qwen2.5:7b")

# Option B: Connect to Gemini
# llm = create_llm_client(provider="gemini", api_key="YOUR_KEY")

envelope = process_query(
    raw_query="So sánh XSS và SQL Injection",
    conversation_history=None,
    llm_client=llm
)
print(envelope["status"])      # "ok"
print(envelope["query_plan"])    # Structured tasks, entities, and filters
```

#### 2. Running the Complete End-to-End RAG Pipeline
```python
from query_understanding_routing.rag_processor import EndToEndRAGPipeline
from query_understanding_routing.llm_client import create_llm_client

rag = EndToEndRAGPipeline(llm_client=create_llm_client("ollama"))
result = rag.process("Cách phòng chống SQL Injection theo OWASP?")

if result["status"] == "ok":
    print("Answer:", result["generation"]["answer"])
    print("Citations:", result["generation"]["citations"])
else:
    print("Handled by Stage 2:", result["envelope"]["status"])
```

#### 3. Setting a Global Default LLM Client
```python
from query_understanding_routing import set_default_llm_client, create_llm_client

set_default_llm_client(create_llm_client("ollama"))
# Subsequent calls to process_query() or understand_query() will automatically use this client
```

---

## 4. Directory & Refactoring Structure

The package structure is fully unified under `query_understanding_routing`:

```
computational-thinking-project/
├── pyproject.toml
├── DEVELOPMENT.md
├── Data/
│   ├── First_CVSS-Specification_v4.0.pdf
│   ├── OWASP_Application-Security-Verification-Standard_5.0.0.pdf
│   └── ...
├── Document Ingestion and Parsing/   (Stage 1)
│   ├── Chunking/
│   ├── Ingestion/
│   ├── Parsing/
│   └── Provenance and Traceability/
└── Query Understanding and Routing/  (Stage 2)
    ├── REFACTOR.md                   (Refactor record & documentation)
    ├── Phan_cong_Query_Understanding_Routing_v1.0_hoan_chinh.md
    ├── report.md
    ├── REPORT_MODULE.md
    ├── manual_test.py
    └── query_understanding_routing/  (Main Package)
        ├── __init__.py
        ├── config.py                 (Unified settings)
        ├── validation_module.py      (Input validation & safety)
        ├── normalization_module.py   (Query normalization & alias mapping)
        ├── understanding_module.py   (LLM & rule-based understanding)
        ├── routing_module.py         (Envelope formatting & contract check)
        ├── pipeline.py               (Orchestrator: run full pipeline)
        ├── llm_client.py             (Ollama, Gemini, OpenAI, Mock clients)
        ├── rag_processor.py          (Full RAG retrieval + generation flow)
        ├── ontology_loader.py        (YAML loader & alias validator)
        ├── prompt_templates.py       (System prompts & few-shot examples)
        ├── ontology.yaml             (5 vulnerability slugs & OWASP mapping)
        ├── paths.py                  (Cross-platform resolved paths)
        └── tests/
            ├── fixtures/
            ├── test_validation.py
            ├── test_normalization.py
            ├── test_routing.py
            ├── test_pipeline.py
            ├── test_understanding.py
            ├── test_context.py
            ├── test_clarification.py
            └── test_llm.py
```

---

## 5. Module-Specific Quirks & Gotchas

### Quirk 1: Markdown JSON Fences from Real LLMs
Real LLMs (Gemini, Qwen, GPT) frequently wrap structured outputs in markdown code fences:
````
```json
{"resolved_query": "...", "intent": "SEARCH"}
```
````
- **Symptom**: Calling `json.loads(raw_response)` fails with `JSONDecodeError`, causing unnecessary retries and eventual `llm_invalid_json`.
- **Solution**: `clean_json_codeblock()` in `understanding_module.py` and `clean_markdown_json()` in `llm_client.py` strip markdown fences before schema validation.

### Quirk 2: Strict Two-Way Equivalence (L8)
Contract v1.0 Section 0.2 enforces:
$$\text{intent} == \text{"CLARIFICATION"} \iff \text{need\_clarify} == \text{True}$$
- If `need_clarify == True`, `clarification_message` **must** be a non-empty string.
- If `need_clarify == False`, `clarification_message` **must** be `None`.
- Any mismatch causes an immediate `llm_schema_violation` error.

### Quirk 3: Strict `context_used` Evaluation (Section 2.3)
- `context_used` must be `True` **only** when conversation history is actually needed to resolve anaphoric pronouns (`"nó"`, `"lỗi này"`, `"it"`) or elliptical continuation (`"Còn về..."`).
- For independent queries (`"So sánh SQLi và XSS"`), `context_used` **must be `False`** even if conversation history was provided!

### Quirk 4: Absence of `original_query` in Understanding
- `understand_query()` must **never** return or modify `original_query`.
- `routing_module.py` is solely responsible for attaching `original_query` taken from `raw_query`.

### Quirk 5: Section 7.1 History Truncation
- Max 3 turn pairs (6 messages) and max 2,000 characters total.
- If total length exceeds 2,000 characters, oldest messages are dropped first.
- If a single message exceeds 2,000 characters, the head is truncated and the tail (most recent context) is preserved.

### Quirk 6: Lazy Package Imports
- In `__init__.py`, `process_query` and `EndToEndRAGPipeline` are lazy-loaded via `__getattr__`.
- This ensures fast CLI startup and prevents circular dependencies.
- Subprocess tests (like `test_package_import_is_lazy`) verify that importing `query_understanding_routing` does not prematurely load `pipeline`.

### Quirk 7: Subprocess Isolation on Windows
- In Windows environments, `test_package_import_is_lazy` spawns a child Python process via `subprocess.run([sys.executable, "-c", code], cwd=project_root)`.
- Always run `py -m pip install -e . --no-deps` so the Python executable knows how to resolve the package in isolated subprocesses.
