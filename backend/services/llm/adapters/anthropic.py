from __future__ import annotations

import logging
from typing import Any

import httpx

from services.llm.adapters.base import LLMProviderAdapter

LOGGER = logging.getLogger(__name__)


class AnthropicAdapter(LLMProviderAdapter):
    """Adapter for Anthropic Messages API.

    Key differences from OpenAI-compatible:
    - No native response_format support (json constraint via system prompt text)
    - System message extracted from messages array to top-level 'system' field
    - Uses x-api-key header instead of Bearer
    - Different response structure (content array with type="text" blocks)
    """

    def adapt_response_format(
        self, requested: Any, tier: str, capabilities: dict
    ) -> Any:
        if isinstance(requested, dict):
            return "json"
        return requested

    def get_supported_formats(self) -> set[str]:
        return set()

    def get_supported_extra_params(self) -> set[str]:
        return {"thinking_budget_tokens"}

    def normalize_response(self, raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        parts = raw.get("content") or []
        text_parts = []
        thinking_parts = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text_parts.append(part.get("text", ""))
            elif part.get("type") == "thinking":
                thinking_parts.append(part.get("thinking", ""))
        content = "".join(text_parts)
        if not content and thinking_parts:
            LOGGER.warning(
                "Anthropic returned only thinking blocks (%d chars), no text blocks; "
                "stop_reason=%s",
                sum(len(t) for t in thinking_parts),
                raw.get("stop_reason"),
            )
        return content, raw

    def chat(
        self,
        messages: list[dict[str, Any]],
        response_format: Any = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        config: dict[str, Any] | None = None,
        retry_policy: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        from services.llm_service import _retryable_post, _llm_request_circuit_key, _get_timeout_settings

        if isinstance(response_format, dict):
            raise ValueError(
                "Anthropic provider does not support response_format schema payloads in this path."
            )

        cfg = config or {}
        endpoint = str(
            cfg.get("endpoint") or "https://api.anthropic.com"
        ).rstrip("/")
        circuit_key = _llm_request_circuit_key(cfg)

        system_parts: list[str] = []
        anthropic_messages = []
        for message in messages:
            if message["role"] == "system":
                system_parts.append(message["content"])
            else:
                anthropic_messages.append(message)
        system = "\n".join(system_parts)

        if response_format == "json":
            json_instruction = (
                "Return a valid JSON object only. "
                "Do not include markdown fences or explanatory text."
            )
            system = f"{system}\n{json_instruction}" if system else json_instruction

        extra = cfg.get("extra_params") or {}
        thinking_budget = extra.get("thinking_budget_tokens")

        headers = {
            "Content-Type": "application/json",
            "x-api-key": str(cfg.get("api_key") or ""),
            "anthropic-version": str(cfg.get("anthropic_version") or "2023-06-01"),
        }
        payload: dict[str, Any] = {
            "model": cfg.get("model"),
            "system": system,
            "messages": anthropic_messages,
            "max_tokens": cfg.get("max_tokens", 4096),
        }
        if thinking_budget:
            headers["anthropic-beta"] = "thinking-2025-01-02"
            payload["thinking"] = {"type": "enabled", "budget_tokens": int(thinking_budget)}
            payload["temperature"] = 1.0
        else:
            payload["temperature"] = cfg.get("temperature", 0.7)

        request_timeout = (
            httpx.Timeout(timeout)
            if timeout is not None
            else httpx.Timeout(**_get_timeout_settings())
        )
        with httpx.Client(timeout=request_timeout) as client:
            response = _retryable_post(
                client,
                f"{endpoint}/v1/messages",
                circuit_key=circuit_key,
                retry_policy=retry_policy,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            try:
                raw = response.json()
            except Exception:
                LOGGER.error(
                    "Anthropic returned non-JSON response (status %d): %.200s",
                    response.status_code,
                    response.text[:200],
                )
                raise ValueError(
                    f"Anthropic returned non-JSON response (HTTP {response.status_code})"
                )

        return self.normalize_response(raw)
