from __future__ import annotations

import logging

from services.llm.adapters.base import LLMProviderAdapter
from services.llm.adapters.openai_like import OpenAILikeAdapter
from services.llm.adapters.ollama import OllamaAdapter
from services.llm.adapters.anthropic import AnthropicAdapter

LOGGER = logging.getLogger(__name__)

_KNOWN_OPENAI_COMPATIBLE = frozenset({
    "openai", "vllm", "deepseek", "together", "fireworks",
    "github_copilot", "groq", "custom",
})


def create_adapter(provider: str) -> LLMProviderAdapter:
    registry: dict[str, type[LLMProviderAdapter]] = {
        "ollama": OllamaAdapter,
        "anthropic": AnthropicAdapter,
    }
    cls = registry.get(provider)
    if cls is None:
        if provider and provider not in _KNOWN_OPENAI_COMPATIBLE:
            LOGGER.warning(
                "Unknown LLM provider %r; falling back to OpenAI-compatible adapter",
                provider,
            )
        cls = OpenAILikeAdapter
    return cls(provider_name=provider)


__all__ = [
    "LLMProviderAdapter",
    "OpenAILikeAdapter",
    "OllamaAdapter",
    "AnthropicAdapter",
    "create_adapter",
]
