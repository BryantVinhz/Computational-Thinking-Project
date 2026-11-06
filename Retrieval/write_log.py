import json
from pathlib import Path
from dataclasses import asdict
from retrieval_preparer import RetrievalPreparer

preparer = RetrievalPreparer()

sample_plan = {
    "schema_version": "1.0",
    "status": "ok",
    "original_query": "Cho tôi khái niệm về Broken Access Control",
    "query_plan": {
        "original_query": "Cho tôi khái niệm về Broken Access Control",
        "resolved_query": "Khái niệm Broken Access Control là gì?",
        "intent": "SEARCH",
        "confidence": 0.95,
        "context_used": False,
        "entities": {
            "document": [],
            "vulnerabilities": ["abac"]
        },
        "filter": {
            "version": None,
            "document_type": None,
            "source": []
        },
        "task": [
            {
                "task_id": "task_001",
                "intent_task": "SEARCH",
                "query": "Khái niệm Broken Access Control là gì?",
                "objective": {
                    "type": "vulnerabilities",
                    "ids": ["broken-access-control"]
                },
                "depends_on": None
            }
        ],
        "need_clarify": False,
        "clarification_message": None
    }
}

payloads = preparer.prepare(sample_plan)
output_path = Path(__file__).resolve().parent / "retrieval_payload_debug.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump([asdict(p) for p in payloads], f, ensure_ascii=False, indent=2)

print(f"Wrote successfully {len(payloads)} payload into retrieval_payload_debug.json")