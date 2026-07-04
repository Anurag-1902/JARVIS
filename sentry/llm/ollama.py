"""Ollama provider — talks to a local Ollama server (http://localhost:11434)."""
import json

import requests

from .base import LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(self, cfg: dict):
        self.url = cfg["llm"]["ollama_url"].rstrip("/")
        self.model = cfg["llm"]["model"]
        self.max_tokens = cfg["llm"]["max_tokens"]

    def preflight(self) -> str:
        """Verify Ollama is up and the configured model exists. Auto-selects the
        closest installed model if not — this is what previously crashed main.py
        with an HTTP 404 when the config model wasn't pulled."""
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=5)
            r.raise_for_status()
        except requests.RequestException:
            raise RuntimeError(
                f"Cannot reach Ollama at {self.url}.\n"
                "  Fix: open a second terminal and run `ollama serve`\n"
                "  (or on Mac: `brew services start ollama`)"
            )
        models = [m["name"] for m in r.json().get("models", [])]
        if not models:
            raise RuntimeError(
                "Ollama is running but has no models installed.\n"
                "  Fix: ollama pull qwen2.5:7b"
            )
        # exact match, or same family with a different tag (qwen2.5:7b vs qwen2.5:latest)
        if self.model in models:
            return ""
        base = self.model.split(":")[0]
        for m in models:
            if m.split(":")[0] == base:
                self.model = m
                return f"note: using installed model '{m}'"
        for pref in ("qwen", "llama", "mistral", "gemma"):
            for m in models:
                if pref in m and "embed" not in m:
                    old, self.model = self.model, m
                    return f"note: '{old}' isn't pulled — using installed '{m}' instead"
        self.model = models[0]
        return f"note: using installed model '{models[0]}'"

    def chat(self, system: str, messages: list[dict]) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "options": {"num_predict": self.max_tokens, "temperature": 0.4},
            "messages": [{"role": "system", "content": system}] + messages,
        }
        try:
            r = requests.post(f"{self.url}/api/chat", json=payload, timeout=180)
            if r.status_code == 404:
                raise RuntimeError(
                    f"Model '{self.model}' not found on the Ollama server.\n"
                    f"  Fix: ollama pull {self.model}   (or edit `model:` in config.yaml)"
                )
            r.raise_for_status()
        except requests.ConnectionError:
            raise RuntimeError(
                f"Cannot reach Ollama at {self.url}. Is it running? "
                "Start it with `ollama serve`, or `brew services start ollama` on Mac."
            )
        return r.json()["message"]["content"]

    def chat_stream(self, system: str, messages: list[dict]):
        payload = {
            "model": self.model,
            "stream": True,
            "options": {"num_predict": self.max_tokens, "temperature": 0.4},
            "messages": [{"role": "system", "content": system}] + messages,
        }
        try:
            with requests.post(f"{self.url}/api/chat", json=payload, stream=True, timeout=180) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    tok = chunk.get("message", {}).get("content", "")
                    if tok:
                        yield tok
                    if chunk.get("done"):
                        break
        except requests.ConnectionError:
            raise RuntimeError(
                f"Cannot reach Ollama at {self.url}. Start it with `ollama serve`."
            )

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Uses nomic-embed-text if pulled (`ollama pull nomic-embed-text`, ~274MB)."""
        try:
            r = requests.post(f"{self.url}/api/embed",
                              json={"model": "nomic-embed-text", "input": texts}, timeout=120)
            if r.status_code != 200:
                return None
            return r.json().get("embeddings")
        except requests.RequestException:
            return None

    def name(self) -> str:
        return f"ollama/{self.model}"
