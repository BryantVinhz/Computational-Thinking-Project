# Pipeline Integration (Contract v1.0)

Orchestrates Validation → Normalization → Understanding → Routing.

## Public API

```python
from query_understanding_routing import process_query

def process_query(raw_query,                      # no type constraint
                  conversation_history=None,      # list[dict] or None
                  *, llm_client=None,            # forwarded to understand_query
                  ontology=None,                 # dict or None
                  document_ids=None) -> dict:    # set[str] or None
    """Return exactly one response envelope (status: ok, rejected, clarification, out_of_scope, error)."""
```

All four parameters after `raw_query` are optional. Non-list history is treated as `None`.

## Envelope Examples

Status | Fixture
---|---
`ok` | [envelope_ok.json](tests/fixtures/envelope_ok.json)
`rejected` | [envelope_rejected.json](tests/fixtures/envelope_rejected.json)
`clarification` | [envelope_clarification.json](tests/fixtures/envelope_clarification.json)
`out_of_scope` | [envelope_out_of_scope.json](tests/fixtures/envelope_out_of_scope.json)
`error` | [envelope_error.json](tests/fixtures/envelope_error.json)

## Setup

Both packages live under `Query Understanding and Routing/` and keep their top-level import names. Install them once in editable mode from the repository root:

```bash
python -m pip install -e .
```

## Running Tests

Run from the repository root:

```bash
# Unit tests (stage functions stubbed via monkeypatch)
python -m pytest "Query Understanding and Routing/query_understanding_routing/tests/test_pipeline.py" -k "not Integration"

# Full suite including integration tests (real Understanding and Ontology)
REQUIRE_INTEGRATION=1 python -m pytest

# Integration tests only
REQUIRE_INTEGRATION=1 python -m pytest -k Integration
```

PowerShell equivalent of the full suite:

```powershell
$env:REQUIRE_INTEGRATION="1"; python -m pytest -q -rs; Remove-Item Env:REQUIRE_INTEGRATION
```

Do not leave `REQUIRE_INTEGRATION` set when dependencies cannot be imported: integration tests then fail instead of skipping.

## Known Limitations

1. **Rule-based Understanding**: This phase uses Long's deterministic engine. LLM-dependent cases are pending.
2. **Multi-turn attacks**: Validation runs once per turn. Multi-turn attack patterns are not detected (v1.0 known gap; test is `xfail`).
3. **`llm_client` parameter**: Pipeline extension outside Contract v1.0, forwarded as a keyword to `understand_query`. No live-LLM tests yet.
4. **Configuration**: Unified in `query_understanding_routing/config.py`. Legacy `stage_2` shims have been retired.
5. **Ontology cache**: The default ontology is loaded once per process. A failed load is not cached; a caller-supplied ontology skips loading.
6. **Caller ontology**: must be a non-empty dict. `None` selects the default; anything else returns `internal_error`.
