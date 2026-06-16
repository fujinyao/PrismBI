from __future__ import annotations

import logging
from typing import Any

import httpx

from services.llm.adapters.base import LLMProviderAdapter

LOGGER = logging.getLogger(__name__)


class OpenAILikeAdapter(LLMProviderAdapter):
    """Adapter for OpenAI /v1/chat/completions compatible endpoints.

    Covers: OpenAI, vLLM, Together AI, DeepSeek API, GitHub Copilot, local proxies.
    """

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

        cfg = config or {}
        endpoint = str(cfg.get("endpoint") or "").rstrip("/")
        circuit_key = _llm_request_circuit_key(cfg)

        headers = {"Content-Type": "application/json"}
        api_key = cfg.get("api_key")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: dict[str, Any] = {
            "model": cfg.get("model"),
            "messages": messages,
            "temperature": cfg.get("temperature", 0.7),
            "max_tokens": cfg.get("max_tokens", 4096),
        }
        if isinstance(response_format, dict):
            payload["response_format"] = response_format
        elif response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        supported = self.get_supported_extra_params()
        extra = cfg.get("extra_params") or {}
        extra = {k: v for k, v in extra.items() if k in supported}
        payload.update(extra)
        if params:
            payload.update(params)

        request_timeout = (
            httpx.Timeout(timeout)
            if timeout is not None
            else httpx.Timeout(**_get_timeout_settings())
        )
        with httpx.Client(timeout=request_timeout) as client:
            response = _retryable_post(
                client,
                f"{endpoint}/chat/completions",
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
                    "LLM returned non-JSON response (status %d): %.200s",
                    response.status_code,
                    response.text[:200],
                )
                raise ValueError(
                    f"LLM returned non-JSON response (HTTP {response.status_code})"
                )

        return self.normalize_response(raw)
