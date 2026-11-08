import requests
from typing import Any

COLAB_URL = "https://vision-baths-companion-speaker.trycloudflare.com"
class ColabLLMClient:
    """client calls the LLM running on Google Colab via FastAPI"""
    model_name: str = "Qwen2.5-3B-Instruct-colab-remote"

    def __init__(self, endpoint_url: str = COLAB_URL):
        self.api_url = f"{endpoint_url.rstrip('/')}/generate"

    def generate(self, prompt: Any, system_instruction: str | None = None, **kwargs: Any) -> str:
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
