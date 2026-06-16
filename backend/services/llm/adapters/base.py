from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

LOGGER = logging.getLogger(__name__)


class LLMProviderAdapter(ABC):
    """Provider adapter base class.

    Each LLM provider gets a subclass that handles provider-specific
    transport, payload format, response parsing, and capability metadata.
    """

    provider_name: str

    def __init__(self, provider_name: str = ""):
        self.provider_name = provider_name

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, Any]],
        response_format: Any = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        config: dict[str, Any] | None = None,
        retry_policy: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Call the LLM provider and return (content_text, raw_response_dict)."""

    # ── Metadata helpers ──

    def get_supported_formats(self) -> set[str]:
        """Return the set of response_format values this provider supports natively."""
        return {"json_schema", "json_object"}

    def get_supported_extra_params(self) -> set[str]:
        """Return the set of extra_params keys this provider accepts."""
        return {"seed", "top_p", "frequency_penalty", "presence_penalty", "logprobs", "top_logprobs"}

    def get_default_params(self, tier: str) -> dict[str, Any]:
        """Return provider-specific default params for a given capability tier."""
        return {}

    def adapt_response_format(
        self, requested: Any, tier: str, capabilities: dict
    ) -> Any:
        """Hook for adapter-specific response_format transformations.

        Called after the capability-level adaptation, allowing providers to
        further adjust the format (e.g. Ollama rejecting json_schema).
        Default is passthrough.
        """
        return requested

    def get_prompt_variables(
        self, model: str, tier: str, language: str | None
    ) -> dict[str, str]:
        """Return provider/model-specific prompt template variables."""
        return {}

    def normalize_response(self, raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Extract content text from a provider-specific raw response dict."""
        choices = raw.get("choices") or []
        if not choices:
            return "", raw
        content = choices[0].get("message", {}).get("content") or ""
        return content, raw
