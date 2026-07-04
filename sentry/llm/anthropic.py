"""Anthropic provider — set ANTHROPIC_API_KEY in your environment."""
import os
import requests
from .base import LLMProvider


class AnthropicProvider(LLMProvider):
    def __init__(self, cfg: dict):
        self.model = cfg["llm"].get("anthropic_model", "claude-sonnet-4-6")
        self.max_tokens = cfg["llm"]["max_tokens"]
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.key:
            raise RuntimeError("ANTHROPIC_API_KEY not set. `export ANTHROPIC_API_KEY=sk-ant-...`")

    def chat(self, system: str, messages: list[dict]) -> str:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": system,
                "messages": messages,
            },
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", []))

    def chat_stream(self, system: str, messages: list[dict]):
        import json
        with requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": self.model, "max_tokens": self.max_tokens,
                  "system": system, "messages": messages, "stream": True},
            stream=True, timeout=120,
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or not line.startswith(b"data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "content_block_delta":
                    tok = ev.get("delta", {}).get("text", "")
                    if tok:
                        yield tok

    def name(self) -> str:
        return f"anthropic/{self.model}"
