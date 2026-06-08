from __future__ import annotations

import httpx
import pytest

import services.llm_service as llm_service
from services.llm_service import LLMCircuitOpenError, LLMService


def _anthropic_config() -> dict[str, object]:
    return {
        "provider": "anthropic",
        "api_key": "test-key",
        "model": "claude-3-5-sonnet-latest",
        "endpoint": "https://api.anthropic.com",
        "max_tokens": 128,
        "temperature": 0,
        "extra_params": {},
    }


def test_anthropic_chat_rejects_structured_response_format_schema(monkeypatch):
    service = LLMService(config=_anthropic_config())

    monkeypatch.setattr(
        service,
        "_anthropic_chat",
        lambda _messages, response_format=None: ("{}", {"response_format": response_format}),
    )

    with pytest.raises(ValueError, match="response_format"):
        service.chat(
            [{"role": "user", "content": "hello"}],
            response_format={"type": "json_schema"},
        )


def test_anthropic_chat_accepts_json_mode(monkeypatch):
    service = LLMService(config=_anthropic_config())
    captured: dict[str, object] = {"response_format": None}

    def _fake_anthropic(_messages, response_format=None):
        captured["response_format"] = response_format
        return "{\"ok\":true}", {"ok": True}

    monkeypatch.setattr(service, "_anthropic_chat", _fake_anthropic)

    result = service.chat(
        [{"role": "user", "content": "hello"}],
        response_format="json",
    )

    assert result["configured"] is True
    assert captured["response_format"] == "json"


class _StatusCodeClient:
    def __init__(self, statuses: list[int]):
        self._statuses = list(statuses)
        self.calls = 0

    def post(self, url: str, **_kwargs):
        self.calls += 1
        index = min(self.calls - 1, len(self._statuses) - 1)
        status_code = int(self._statuses[index])
        request = httpx.Request("POST", url)
        return httpx.Response(
            status_code=status_code,
            request=request,
            json={"status": status_code},
        )


def test_retryable_post_opens_circuit_and_skips_retries_while_open(monkeypatch):
    llm_service.clear_llm_http_circuit_state()
    monkeypatch.setattr(llm_service.time, "sleep", lambda _delay: None)
    monotonic = {"value": 1000.0}
    monkeypatch.setattr(llm_service.time, "monotonic", lambda: monotonic["value"])

    client = _StatusCodeClient([503])
    url = "https://api.openai.com/v1/chat/completions"
    circuit_key = "openai:https://api.openai.com/v1:gpt-4o"
    retry_policy = {
        "max_retries": 2,
        "retry_base_delay_s": 0.0,
        "retry_max_delay_s": 0.1,
        "circuit_enabled": True,
        "circuit_failure_threshold": 2,
        "circuit_open_seconds": 30.0,
    }

    with pytest.raises(httpx.HTTPStatusError):
        llm_service._retryable_post(client, url, circuit_key=circuit_key, retry_policy=retry_policy, json={"x": 1})
    with pytest.raises(httpx.HTTPStatusError):
        llm_service._retryable_post(client, url, circuit_key=circuit_key, retry_policy=retry_policy, json={"x": 1})

    calls_before_fast_fail = client.calls
    with pytest.raises(LLMCircuitOpenError):
        llm_service._retryable_post(client, url, circuit_key=circuit_key, retry_policy=retry_policy, json={"x": 1})

    assert calls_before_fast_fail == 4
    assert client.calls == calls_before_fast_fail
    llm_service.clear_llm_http_circuit_state()


def test_retryable_post_allows_recovery_after_cooldown(monkeypatch):
    llm_service.clear_llm_http_circuit_state()
    monkeypatch.setattr(llm_service.time, "sleep", lambda _delay: None)
    monotonic = {"value": 2000.0}
    monkeypatch.setattr(llm_service.time, "monotonic", lambda: monotonic["value"])

    client = _StatusCodeClient([503, 200])
    url = "https://api.openai.com/v1/chat/completions"
    circuit_key = "openai:https://api.openai.com/v1:gpt-4o"
    retry_policy = {
        "max_retries": 1,
        "retry_base_delay_s": 0.0,
        "retry_max_delay_s": 0.1,
        "circuit_enabled": True,
        "circuit_failure_threshold": 1,
        "circuit_open_seconds": 10.0,
    }

    with pytest.raises(httpx.HTTPStatusError):
        llm_service._retryable_post(client, url, circuit_key=circuit_key, retry_policy=retry_policy, json={"x": 1})
    with pytest.raises(LLMCircuitOpenError):
        llm_service._retryable_post(client, url, circuit_key=circuit_key, retry_policy=retry_policy, json={"x": 1})

    monotonic["value"] = 2011.0
    response = llm_service._retryable_post(client, url, circuit_key=circuit_key, retry_policy=retry_policy, json={"x": 1})

    assert response.status_code == 200
    assert client.calls == 2
    assert circuit_key not in llm_service._LLM_HTTP_CIRCUIT_STATE_BY_KEY
    llm_service.clear_llm_http_circuit_state()


def test_llm_http_circuit_snapshot_reports_open_and_closed_keys(monkeypatch):
    llm_service.clear_llm_http_circuit_state()
    monotonic = {"value": 500.0}
    monkeypatch.setattr(llm_service.time, "monotonic", lambda: monotonic["value"])

    llm_service._LLM_HTTP_CIRCUIT_STATE_BY_KEY["open-key"] = {
        "consecutive_failures": 0,
        "open_until": 515.0,
    }
    llm_service._LLM_HTTP_CIRCUIT_STATE_BY_KEY["closed-key"] = {
        "consecutive_failures": 2,
        "open_until": 0.0,
    }

    snapshot = llm_service.get_llm_http_circuit_snapshot()

    assert snapshot["total_keys"] == 2
    assert snapshot["open_keys"] == 1
    assert snapshot["keys"]["open-key"]["state"] == "open"
    assert snapshot["keys"]["closed-key"]["state"] == "closed"
    assert snapshot["keys"]["closed-key"]["consecutive_failures"] == 2
    llm_service.clear_llm_http_circuit_state()
