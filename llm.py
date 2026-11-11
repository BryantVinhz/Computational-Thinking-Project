import os
import requests
from typing import Any

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
COLAB_URL = "https://frequency-operational-modify-receivers.trycloudflare.com"
class ColabLLMClient:
    """client calls the LLM running on Google Colab via FastAPI"""
    model_name: str = "Qwen2.5-3B-Instruct-colab-remote"

    def __init__(self, endpoint_url: str = COLAB_URL):
        self.api_url = f"{endpoint_url.rstrip('/')}/generate"

    def generate(self, prompt: Any, system_instruction: Any = None, **kwargs: Any) -> str:
        sys_inst = None
        if isinstance(system_instruction, str):
            sys_inst = system_instruction
        elif isinstance(system_instruction, dict):
            sys_inst = system_instruction.get("vi") or system_instruction.get("en")
            
        payload = {
            "prompt": prompt,
            "system_instruction": system_instruction,
            "max_tokens": kwargs.get("max_tokens", 798),
            "temperature": kwargs.get("temperature", 0.2),
        }
        try:
            resp = requests.post(self.api_url, json=payload, timeout=900)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise RuntimeError(f"Colab API Connection Error ({self.api_url}): {e}") from e

        text = data.get("response") if isinstance(data, dict) else None
        if not isinstance(text, str) or not text.strip():
            # An empty completion must not become an empty "ok" answer; the synthesizer abstains on errors.
            raise RuntimeError(f"Colab API returned an empty completion ({self.api_url})")
        return text


class OllamaLLMClient:
    """Client calls the local model via Ollama API (localhost)"""
    
    def __init__(self, model_name: str = "qwen2.5:3b", host: str = OLLAMA_HOST):
        self.model_name = model_name
        self.api_url = f"{host.rstrip('/')}/api/chat"

    def generate(self, prompt: Any, system_instruction: Any = None, **kwargs: Any) -> str:
        # Normalize format messages for Ollama
        messages = []
        if isinstance(system_instruction, str) and system_instruction.strip():
            messages.append({"role": "system", "content": system_instruction})

        if isinstance(prompt, list):
            messages.extend(prompt)
        else:
            messages.append({"role": "user", "content": str(prompt)})

        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": kwargs.get("temperature", 0.2),
                "num_predict": kwargs.get("max_tokens", 798),
                "num_ctx": 4096,
            },
        }

        try:
            resp = requests.post(self.api_url, json=payload, timeout=900)
            resp.raise_for_status()
            data = resp.json()
            
            response_text = data.get("message", {}).get("content", "")
            if not response_text.strip():
                raise RuntimeError("Ollama returned an empty response")
            return response_text
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                f"Failed to connect to Ollama at {self.api_url}. "
                "Please make sure 'ollama serve' is running or the Ollama app is open!"
            )
        except Exception as e:
            raise RuntimeError(f"Ollama API Error: {e}") from e
        
