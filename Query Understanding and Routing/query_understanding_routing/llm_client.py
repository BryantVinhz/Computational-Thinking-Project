"""
LLM Client Architecture for Stage 2 (Query Understanding) & End-to-End RAG.

Provides pluggable, robust LLM connectors:
- BaseLLMClient: Interface contract supporting call(...) and generate(...)
- OllamaLLMClient: Local models (Qwen, Llama, DeepSeek) via Ollama API
- GeminiLLMClient: Google Gemini models (Gemini 1.5 Flash / Pro, Gemini 2.0)
- OpenAILikeLLMClient: OpenAI, vLLM, LM Studio, Groq, LocalAI
- MockLLMClient: Deterministic testing and offline execution
- create_llm_client: Factory resolving environment variables
"""

from __future__ import annotations

import abc
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Union

try:
    from query_understanding_routing.config import (
        LLM_MAX_RETRIES,
        LLM_TEMPERATURE,
        LLM_TIMEOUT_S,
        OLLAMA_HOST,
        OPENAI_BASE_URL,
    )
    from query_understanding_routing.prompt_templates import (
        SYSTEM_INSTRUCTION_QUERY_UNDERSTANDING,
        build_understanding_prompt,
    )
except ImportError:
    from config import (
        LLM_MAX_RETRIES,
        LLM_TEMPERATURE,
        LLM_TIMEOUT_S,
        OLLAMA_HOST,
        OPENAI_BASE_URL,
    )
    from prompt_templates import (
        SYSTEM_INSTRUCTION_QUERY_UNDERSTANDING,
        build_understanding_prompt,
    )

logger = logging.getLogger(__name__)


def clean_markdown_json(raw_text: str) -> str:
    """Strip markdown code block fences (e.g., ```json ... ```) often emitted by LLMs."""
    if not isinstance(raw_text, str):
        return ""
    text = raw_text.strip()
    if text.startswith("```"):
        # Match ```json or ``` at beginning, and ``` at end
        pattern = r"^```(?:json)?\s*\n?(.*?)\n?```$"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Fallback line-by-line stripping
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


class BaseLLMClient(abc.ABC):
    """Abstract base class for LLM clients used in Stage 2 & RAG Generation."""

    def __init__(
        self,
        model_name: str,
        temperature: float = LLM_TEMPERATURE,
        default_timeout: float = LLM_TIMEOUT_S,
    ):
        self.model_name = model_name
        self.temperature = temperature
        self.default_timeout = default_timeout

    @abc.abstractmethod
    def call(
        self,
        normalized_query: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> Union[str, Dict[str, Any]]:
        """
        Execute Query Understanding request and return raw JSON string or parsed dict.

        Must raise:
            TimeoutError: if request exceeds timeout.
            ConnectionError: if network/endpoint is unreachable.
        """
        pass

    @abc.abstractmethod
    def generate(
        self,
        prompt: Union[str, List[Dict[str, str]]],
        system_instruction: Optional[str] = None,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> str:
        """General text generation for RAG answer synthesis and grounded responses."""
        pass


class MockLLMClient(BaseLLMClient):
    """
    Mock LLM client for tests, benchmarks, and offline execution.
    Can return static responses, call dynamic handlers, or inject specific errors.
    """

    def __init__(
        self,
        model_name: str = "mock-model",
        canned_response: Optional[Union[str, Dict[str, Any]]] = None,
        error_to_raise: Optional[Exception] = None,
        handler: Optional[Callable[..., Any]] = None,
    ):
        super().__init__(model_name=model_name)
        self.canned_response = canned_response
        self.error_to_raise = error_to_raise
        self.handler = handler
        self.call_count = 0
        self.last_prompt: Any = None

    def call(
        self,
        normalized_query: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> Union[str, Dict[str, Any]]:
        self.call_count += 1
        self.last_prompt = {
            "normalized_query": normalized_query,
            "conversation_history": conversation_history,
        }

        if self.error_to_raise is not None:
            raise self.error_to_raise

        if self.handler is not None:
            return self.handler(normalized_query, conversation_history)

        if self.canned_response is not None:
            return self.canned_response

        # Default mock response conforming to Contract
        return {
            "resolved_query": normalized_query,
            "intent": "SEARCH",
            "confidence": 0.95,
            "context_used": False,
            "entities": {"document": [], "vulnerabilities": ["xss"]},
            "filter": {"version": None, "document_type": None, "source": []},
            "task": [
                {
                    "task_id": "task_001",
                    "intent_task": "SEARCH",
                    "query": normalized_query,
                    "objective": {"type": "vulnerabilities", "ids": ["xss"]},
                    "depends_on": None,
                }
            ],
            "need_clarify": False,
            "clarification_message": None,
        }

    def generate(
        self,
        prompt: Union[str, List[Dict[str, str]]],
        system_instruction: Optional[str] = None,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> str:
        self.call_count += 1
        if self.error_to_raise is not None:
            raise self.error_to_raise
        return "Đây là câu trả lời mẫu từ Mock LLM dựa trên tài liệu được cung cấp."


class OllamaLLMClient(BaseLLMClient):
    """
    Local LLM runner using Ollama REST API (Qwen, Llama 3, DeepSeek, etc.).
    Requires Ollama running (default: http://localhost:11434).
    Zero external pip dependencies: uses standard urllib.
    """

    def __init__(
        self,
        model_name: str = "qwen2.5:7b",
        host: str = OLLAMA_HOST,
        temperature: float = LLM_TEMPERATURE,
        default_timeout: float = LLM_TIMEOUT_S,
    ):
        super().__init__(model_name, temperature, default_timeout)
        self.host = host.rstrip("/")

    def call(
        self,
        normalized_query: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> str:
        messages = build_understanding_prompt(
            normalized_query=normalized_query,
            conversation_history=conversation_history,
        )
        effective_timeout = timeout or self.default_timeout

        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": self.temperature,
            },
        }

        url = f"{self.host}/api/chat"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data.get("message", {}).get("content", "")
                return clean_markdown_json(content)
        except urllib.error.URLError as e:
            if isinstance(e.reason, (TimeoutError,)):
                raise TimeoutError(f"Ollama request timed out after {effective_timeout}s: {e}")
            raise ConnectionError(f"Failed to connect to Ollama at {self.host}: {e}")
        except TimeoutError as e:
            raise TimeoutError(f"Ollama request timed out: {e}")
        except Exception as e:
            raise ConnectionError(f"Ollama execution error: {e}")

    def generate(
        self,
        prompt: Union[str, List[Dict[str, str]]],
        system_instruction: Optional[str] = None,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> str:
        effective_timeout = timeout or self.default_timeout
        messages: List[Dict[str, str]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})

        if isinstance(prompt, str):
            messages.append({"role": "user", "content": prompt})
        elif isinstance(prompt, list):
            messages.extend(prompt)

        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
            "options": {"temperature": self.temperature},
        }

        url = f"{self.host}/api/chat"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("message", {}).get("content", "")
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):
                raise TimeoutError(f"Ollama generate timed out: {e}")
            raise ConnectionError(f"Ollama connection error: {e}")


class GeminiLLMClient(BaseLLMClient):
    """
    Google Gemini Client using standard REST API (no heavy SDK dependency).
    Supports gemini-1.5-flash, gemini-1.5-pro, gemini-2.0-flash, etc.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-1.5-flash",
        temperature: float = LLM_TEMPERATURE,
        default_timeout: float = LLM_TIMEOUT_S,
    ):
        super().__init__(model_name, temperature, default_timeout)
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        if not self.api_key:
            logger.warning("GeminiLLMClient initialized without GEMINI_API_KEY")

    def _convert_messages_to_gemini(
        self, messages: List[Dict[str, str]]
    ) -> tuple[Optional[str], List[Dict[str, Any]]]:
        system_instruction = None
        contents: List[Dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system_instruction = content
            else:
                gemini_role = "model" if role == "assistant" else "user"
                contents.append({
                    "role": gemini_role,
                    "parts": [{"text": content}]
                })
        return system_instruction, contents

    def call(
        self,
        normalized_query: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> str:
        if not self.api_key:
            raise ConnectionError("GEMINI_API_KEY is not set.")

        messages = build_understanding_prompt(
            normalized_query=normalized_query,
            conversation_history=conversation_history,
        )
        system_instr, contents = self._convert_messages_to_gemini(messages)
        effective_timeout = timeout or self.default_timeout

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": self.temperature,
                "responseMimeType": "application/json",
            },
        }
        if system_instr:
            payload["systemInstruction"] = {"parts": [{"text": system_instr}]}

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_name}:generateContent?key={self.api_key}"
        )

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                candidates = data.get("candidates", [])
                if not candidates:
                    raise ValueError("No candidates returned from Gemini API")
                text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                return clean_markdown_json(text)
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):
                raise TimeoutError(f"Gemini API timed out after {effective_timeout}s: {e}")
            raise ConnectionError(f"Gemini API connection error: {e}")
        except TimeoutError as e:
            raise TimeoutError(f"Gemini API timed out: {e}")

    def generate(
        self,
        prompt: Union[str, List[Dict[str, str]]],
        system_instruction: Optional[str] = None,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> str:
        if not self.api_key:
            raise ConnectionError("GEMINI_API_KEY is not set.")

        effective_timeout = timeout or self.default_timeout
        contents: List[Dict[str, Any]] = []

        if isinstance(prompt, str):
            contents.append({"role": "user", "parts": [{"text": prompt}]})
        elif isinstance(prompt, list):
            _, contents = self._convert_messages_to_gemini(prompt)

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": self.temperature},
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_name}:generateContent?key={self.api_key}"
        )
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                candidates = data.get("candidates", [])
                if not candidates:
                    return ""
                return candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):
                raise TimeoutError(f"Gemini generate timed out: {e}")
            raise ConnectionError(f"Gemini connection error: {e}")


class OpenAILikeLLMClient(BaseLLMClient):
    """
    OpenAI-compatible LLM Client.
    Works with OpenAI API, vLLM, LM Studio, Groq, FastChat, and Ollama's /v1 endpoint.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = OPENAI_BASE_URL,
        model_name: str = "gpt-4o-mini",
        temperature: float = LLM_TEMPERATURE,
        default_timeout: float = LLM_TIMEOUT_S,
    ):
        super().__init__(model_name, temperature, default_timeout)
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = base_url.rstrip("/")

    def call(
        self,
        normalized_query: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> str:
        messages = build_understanding_prompt(
            normalized_query=normalized_query,
            conversation_history=conversation_history,
        )
        effective_timeout = timeout or self.default_timeout

        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                choices = data.get("choices", [])
                if not choices:
                    raise ValueError("No choices returned from OpenAI-compatible API")
                content = choices[0].get("message", {}).get("content", "")
                return clean_markdown_json(content)
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):
                raise TimeoutError(f"OpenAI request timed out after {effective_timeout}s: {e}")
            raise ConnectionError(f"OpenAI connection error at {self.base_url}: {e}")
        except TimeoutError as e:
            raise TimeoutError(f"OpenAI request timed out: {e}")

    def generate(
        self,
        prompt: Union[str, List[Dict[str, str]]],
        system_instruction: Optional[str] = None,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> str:
        effective_timeout = timeout or self.default_timeout
        messages: List[Dict[str, str]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})

        if isinstance(prompt, str):
            messages.append({"role": "user", "content": prompt})
        elif isinstance(prompt, list):
            messages.extend(prompt)

        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
        }

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                choices = data.get("choices", [])
                return choices[0].get("message", {}).get("content", "") if choices else ""
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):
                raise TimeoutError(f"OpenAI generate timed out: {e}")
            raise ConnectionError(f"OpenAI connection error: {e}")


def create_llm_client(
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
    **kwargs: Any,
) -> BaseLLMClient:
    """
    Factory creating configured LLM client instance.
    Inspects provider or LLM_PROVIDER / GEMINI_API_KEY / OPENAI_API_KEY environment variables.
    """
    chosen_provider = (
        provider
        or os.getenv("LLM_PROVIDER", "").strip().lower()
        or ("gemini" if os.getenv("GEMINI_API_KEY") else ("openai" if os.getenv("OPENAI_API_KEY") else "ollama"))
    )

    if chosen_provider == "gemini":
        model = model_name or os.getenv("LLM_MODEL", "gemini-1.5-flash")
        return GeminiLLMClient(model_name=model, **kwargs)

    if chosen_provider in {"openai", "vllm", "lmstudio"}:
        model = model_name or os.getenv("LLM_MODEL", "gpt-4o-mini")
        return OpenAILikeLLMClient(model_name=model, **kwargs)

    if chosen_provider == "ollama":
        model = model_name or os.getenv("LLM_MODEL", "qwen2.5:7b")
        return OllamaLLMClient(model_name=model, **kwargs)

    if chosen_provider == "mock":
        model = model_name or "mock-model"
        return MockLLMClient(model_name=model, **kwargs)

    raise ValueError(f"Unsupported LLM provider: {chosen_provider}. Supported: ollama, gemini, openai, mock")


# Default client reference
LLMClient = BaseLLMClient
