"""LLM provider interface. Every provider takes messages, returns text.

Tool use is handled at the prompt level (JSON protocol) so it works with
ANY model — including small local ones that lack native function calling.
"""
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def chat(self, system: str, messages: list[dict]) -> str:
        """messages: [{"role": "user"|"assistant", "content": str}, ...] -> reply text"""
        ...

    def chat_stream(self, system: str, messages: list[dict]):
        """Yield reply tokens as they arrive. Default: fake-stream the full reply."""
        yield self.chat(system, messages)

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Return embedding vectors, or None if this provider can't embed."""
        return None

    def name(self) -> str:
        return self.__class__.__name__


def get_provider(cfg: dict) -> LLMProvider:
    provider = cfg["llm"]["provider"].lower()
    if provider == "ollama":
        from .ollama import OllamaProvider
        return OllamaProvider(cfg)
    if provider == "anthropic":
        from .anthropic import AnthropicProvider
        return AnthropicProvider(cfg)
    if provider == "echo":  # testing only
        from .echo import EchoProvider
        return EchoProvider(cfg)
    raise ValueError(f"Unknown LLM provider: {provider}")
