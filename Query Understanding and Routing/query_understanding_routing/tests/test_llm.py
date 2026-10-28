"""
Unit Tests for LLM Client Subsystem, Markdown Codeblock Cleaning & RAG Processor.
"""

import json
import pytest

from query_understanding_routing.llm_client import (
    BaseLLMClient,
    MockLLMClient,
    OllamaLLMClient,
    GeminiLLMClient,
    OpenAILikeLLMClient,
    clean_markdown_json,
    create_llm_client,
)
from query_understanding_routing.prompt_templates import (
    SYSTEM_INSTRUCTION_QUERY_UNDERSTANDING,
    build_understanding_prompt,
)
from query_understanding_routing.rag_processor import (
    EndToEndRAGPipeline,
    RAGGenerator,
)
from query_understanding_routing.understanding_module import (
    understand_query,
    set_default_llm_client,
    get_default_llm_client,
)
from query_understanding_routing.pipeline import process_query


class TestMarkdownCleaning:
    def test_clean_json_codeblock_with_json_tag(self):
        raw = "```json\n{\"resolved_query\": \"XSS\", \"intent\": \"SEARCH\"}\n```"
        cleaned = clean_markdown_json(raw)
        assert cleaned == "{\"resolved_query\": \"XSS\", \"intent\": \"SEARCH\"}"
        assert json.loads(cleaned)["intent"] == "SEARCH"

    def test_clean_json_codeblock_without_tag(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        assert clean_markdown_json(raw) == "{\"key\": \"value\"}"

    def test_clean_plain_json(self):
        raw = "{\"key\": \"value\"}"
        assert clean_markdown_json(raw) == "{\"key\": \"value\"}"


class TestMockLLMClient:
    def test_mock_client_default_response(self):
        client = MockLLMClient()
        res = understand_query("Cross-Site Scripting là gì?", llm_client=client)
        assert res["intent"] == "SEARCH"
        assert res["context_used"] is False
        assert client.call_count == 1

    def test_mock_client_canned_json_with_markdown(self):
        canned = "```json\n" + json.dumps({
            "resolved_query": "SQL Injection",
            "intent": "SEARCH",
            "confidence": 0.95,
            "context_used": False,
            "entities": {"document": [], "vulnerabilities": ["sqli"]},
            "filter": {"version": None, "document_type": None, "source": []},
            "task": [
                {
                    "task_id": "task_001",
                    "intent_task": "SEARCH",
                    "query": "SQL Injection",
                    "objective": {"type": "vulnerabilities", "ids": ["sqli"]},
                    "depends_on": None,
                }
            ],
            "need_clarify": False,
            "clarification_message": None,
        }) + "\n```"
        client = MockLLMClient(canned_response=canned)
        res = understand_query("SQL Injection là gì?", llm_client=client)
        assert res["intent"] == "SEARCH"
        assert res["entities"]["vulnerabilities"] == ["sqli"]

    def test_mock_client_error_injection(self):
        client = MockLLMClient(error_to_raise=TimeoutError("Request timed out"))
        res = understand_query("XSS là gì?", llm_client=client)
        assert res == {"error_code": "llm_unavailable"}


class TestFactoryAndEnvConfig:
    def test_create_mock_client(self):
        client = create_llm_client("mock")
        assert isinstance(client, MockLLMClient)

    def test_create_ollama_client(self):
        client = create_llm_client("ollama", model_name="qwen2.5:7b")
        assert isinstance(client, OllamaLLMClient)
        assert client.model_name == "qwen2.5:7b"

    def test_create_gemini_client(self):
        client = create_llm_client("gemini", api_key="dummy_key", model_name="gemini-1.5-flash")
        assert isinstance(client, GeminiLLMClient)
        assert client.model_name == "gemini-1.5-flash"

    def test_create_openai_client(self):
        client = create_llm_client("openai", api_key="dummy_key", model_name="gpt-4o-mini")
        assert isinstance(client, OpenAILikeLLMClient)
        assert client.model_name == "gpt-4o-mini"


class TestRAGProcessor:
    def test_end_to_end_rag_pipeline_search_ok(self):
        rag = EndToEndRAGPipeline(llm_client=MockLLMClient())
        result = rag.process("XSS là gì?")
        assert result["status"] == "ok"
        assert "envelope" in result
        assert result["envelope"]["status"] == "ok"
        assert len(result["retrieved_chunks"]) >= 1
        assert "answer" in result["generation"]
        assert len(result["generation"]["citations"]) >= 1

    def test_end_to_end_rag_pipeline_rejected_query(self):
        rag = EndToEndRAGPipeline(llm_client=MockLLMClient())
        result = rag.process("   ")
        assert result["status"] == "rejected"
        assert result["generation"] is None

    def test_end_to_end_rag_pipeline_clarification(self):
        rag = EndToEndRAGPipeline(llm_client=MockLLMClient())
        result = rag.process("Các lỗ hổng nguy hiểm")
        assert result["status"] == "clarification"
        assert result["generation"] is None
