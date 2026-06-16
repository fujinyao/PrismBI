from __future__ import annotations

import logging
from typing import Any, Optional

from services.llm.adapters.base import LLMProviderAdapter
from services.llm.adapters.openai_like import OpenAILikeAdapter

LOGGER = logging.getLogger(__name__)


class OllamaAdapter(OpenAILikeAdapter):
    """Adapter for Ollama local deployments.

    Key differences from standard OpenAI-compatible:
    - Does NOT support json_schema (returns 400 from API)
    - Supports json_object via grammar constraints (but some models return empty content)
    - Accepts extra params: repeat_penalty, top_k, mirostat, num_ctx, seed
    - Has tier-specific default params (repeat_penalty for weak/medium models)
    """

    def get_supported_formats(self) -> set[str]:
        return {"json_object"}

    def get_supported_extra_params(self) -> set[str]:
        return {"repeat_penalty", "top_k", "mirostat", "num_ctx", "seed"}

    def get_default_params(self, tier: str) -> dict[str, Any]:
        base = super().get_default_params(tier)
        ollama_defaults = {
            "strong": {},
            "medium": {"repeat_penalty": 1.05},
            "weak": {"repeat_penalty": 1.1, "top_k": 40},
        }
        base.update(ollama_defaults.get(tier, {}))
        return base
