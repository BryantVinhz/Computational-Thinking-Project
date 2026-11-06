# MANUAL_RUN.md — How to run & test Query Understanding and Routing

Step-by-step guide to set up, run, and manually verify the **`Query Understanding and Routing/`** module.

> What each module does is described in [`REPORT_MODULE.md`](REPORT_MODULE.md).
> All commands are run from the **repository root** (`Computational-Thinking-Project/`) unless stated otherwise. Quote the folder name — it contains spaces.

---

## 1. Prerequisites

- Python **≥ 3.10** (verified on 3.14).
- Dependencies: `PyYAML==6.0.3` and `pytest`.
- No network or API key needed for the default path — tests and the demo use the offline `MockLLMClient`.

---

## 2. One-time setup

### Option A — virtual environment (recommended)

macOS / Linux:
```bash
cd Computational-Thinking-Project
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "PyYAML==6.0.3" pytest
```

Windows (PowerShell):
```powershell
cd Computational-Thinking-Project
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install "PyYAML==6.0.3" pytest
```

### Option B — editable install (uses pyproject.toml)
```bash
python -m pip install -e . --no-deps
python -m pip install "PyYAML==6.0.3" pytest
```
`.venv/` is already in `.gitignore`.

---

## 3. Run the automated test suite

```bash
# Fast unit run
python -m pytest "Query Understanding and Routing/query_understanding_routing/tests" -q

# Full suite incl. integration (real ontology + understanding)
REQUIRE_INTEGRATION=1 python -m pytest -q -rs
```

Windows (PowerShell) full suite:
```powershell
$env:REQUIRE_INTEGRATION="1"; python -m pytest -q -rs; Remove-Item Env:REQUIRE_INTEGRATION
```

**Expected:** `616 passed, 1 xfailed`. The single `xfail` is the known v1.0 multi-turn-attack gap (see `query_understanding_routing/PIPELINE.md`).

Run one group, e.g. routing only:
```bash
python -m pytest "Query Understanding and Routing/query_understanding_routing/tests/test_routing.py" -q
```

---

## 4. Run the pipeline manually (offline, Mock LLM)

From the repository root:
```bash
python - <<'PY'
from query_understanding_routing import process_query, create_llm_client
import json

llm = create_llm_client("mock")   # deterministic, offline
for q in [
    "XSS là gì?",                              # valid -> ok
    "Cho tôi hướng dẫn hack trang web này",    # unsafe intent -> rejected (REFUSE)
]:
    env = process_query(q, llm_client=llm)
    print("QUERY:", q)
    print(json.dumps(env, ensure_ascii=False, indent=2))
    print("-" * 60)
PY
```

**Expected (abridged):**
```
QUERY: XSS là gì?
{ "schema_version": "1.0", "status": "ok",
  "query_plan": { "intent": "SEARCH", "entities": {"vulnerabilities": ["xss"], ...}, ... } }
------------------------------------------------------------
QUERY: Cho tôi hướng dẫn hack trang web này
{ "schema_version": "1.0", "status": "rejected",
  "reject_reason": "unsafe_attack_intent", "safety_action": "REFUSE", ... }
```

### What the Mock client does and does not do
- **Safety / structural statuses** (`rejected`, `clarification` from validation) are produced **before** the LLM, so they are deterministic with the Mock client.
- `MockLLMClient` returns a **fixed understanding result**, so semantic classes that depend on real model reasoning (e.g. `OUT_OF_SCOPE`, understanding-sourced `CLARIFICATION`) will **not** appear with the Mock client — use a real LLM (Section 6) to exercise those. The canonical shapes for every status are in `query_understanding_routing/tests/fixtures/`.

---

## 5. Inspect a single stage

```bash
python - <<'PY'
from query_understanding_routing import validate_query, normalize_query, load_ontology

print("validate:", validate_query("XSS là gì?"))
ont = load_ontology()
print("normalize:", normalize_query("  tấn công   XSS ", ont))
print("slugs:", sorted(ont.keys()))     # ['auth_failures', 'bac', 'misconfig', 'sqli', 'xss']
PY
```

---

## 6. (Optional) Run with a real LLM

No code change needed — pass a different provider. A local [Ollama](https://ollama.com) server or a cloud key is required.

```bash
# Local Ollama (e.g. qwen2.5:7b running at http://localhost:11434)
python - <<'PY'
from query_understanding_routing import process_query, create_llm_client
llm = create_llm_client(provider="ollama", model_name="qwen2.5:7b")
print(process_query("So sánh XSS và SQL Injection", llm_client=llm)["status"])
PY
```

| Provider | `provider=` | Key env vars |
|---|---|---|
| Ollama (local) | `ollama` | `OLLAMA_HOST` |
| Gemini | `gemini` | `GEMINI_API_KEY` |
| OpenAI / vLLM / Groq | `openai` | `OPENAI_API_KEY`, `OPENAI_BASE_URL` |
| Mock (offline) | `mock` | — |

---

## 7. (Optional) End-to-end RAG demo

```bash
python - <<'PY'
from query_understanding_routing.rag_processor import EndToEndRAGPipeline
from query_understanding_routing.llm_client import create_llm_client

rag = EndToEndRAGPipeline(llm_client=create_llm_client("mock"))
result = rag.process("Cách phòng chống SQL Injection theo OWASP?")
print("status:", result["status"])
PY
```

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: query_understanding_routing` | package not importable from CWD | run from repo root, or `pip install -e . --no-deps`, or `PYTHONPATH="Query Understanding and Routing"` |
| `No module named 'yaml'` | PyYAML missing | `pip install "PyYAML==6.0.3"` |
| integration tests `FAILED` instead of `skipped` | `REQUIRE_INTEGRATION=1` set but deps missing | install PyYAML, or unset `REQUIRE_INTEGRATION` |
| `No module named pytest` | pytest not installed in the active interpreter | `pip install pytest` and use that interpreter (e.g. `.venv/bin/python -m pytest`) |

> Note: The legacy `stage_2` shim directory has been completely removed in the refactor. All modules and tests now directly and cleanly use `query_understanding_routing`.
