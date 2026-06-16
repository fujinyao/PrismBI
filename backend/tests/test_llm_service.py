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


def test_anthropic_chat_downgrades_dict_response_format(monkeypatch):
    import services.llm_service as llm_service_mod
    llm_service_mod._LLM_RESPONSE_CACHE.clear()
    from services.llm.adapters.anthropic import AnthropicAdapter

    class FakeAnthropicAdapter(AnthropicAdapter):
        def __init__(self):
            self.received_rf = None

        def chat(self, messages, response_format=None, **kwargs):
            self.received_rf = response_format
            return ("{\"ok\": true}", {"ok": True})

    llm_caps = {
        "sql_quality": {"sql_accuracy_tier": "high", "sql_safety_compliant": False},
        "structured_output": {"supports_json_schema": False, "supports_json_object": True},
        "repair": {"repair_capability": False},
    }

    service = LLMService(config=_anthropic_config())
    adapter = FakeAnthropicAdapter()
    monkeypatch.setattr(service, "_get_adapter", lambda: adapter)
    monkeypatch.setattr(service, "_get_capabilities", lambda: llm_caps)

    result = service.chat(
        [{"role": "user", "content": "hello"}],
        response_format={"type": "json_schema"},
    )

    assert result["configured"] is True
    assert adapter.received_rf == "json"


def test_anthropic_chat_passes_through_json_mode(monkeypatch):
    from services.llm.adapters.anthropic import AnthropicAdapter
    import services.llm_service as llm_service_mod

    llm_service_mod._LLM_RESPONSE_CACHE.clear()

    class FakeAnthropicAdapter(AnthropicAdapter):
        def __init__(self):
            self.received_rf = None

        def chat(self, messages, response_format=None, **kwargs):
            self.received_rf = response_format
            return ("{\"ok\": true}", {"ok": True})

    llm_caps = {
        "sql_quality": {"sql_accuracy_tier": "high", "sql_safety_compliant": False},
        "structured_output": {"supports_json_schema": True, "supports_json_object": True},
        "repair": {"repair_capability": False},
    }

    service = LLMService(config=_anthropic_config())
    adapter = FakeAnthropicAdapter()
    monkeypatch.setattr(service, "_get_adapter", lambda: adapter)
    monkeypatch.setattr(service, "_get_capabilities", lambda: llm_caps)

    result = service.chat(
        [{"role": "user", "content": "hello"}],
        response_format="json",
    )

    assert result["configured"] is True
    assert adapter.received_rf == "json"


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


def test_normalize_retry_policy_overrides_max_retries_without_losing_other_settings(monkeypatch):
    monkeypatch.setattr(
        llm_service,
        "_load_llm_http_resilience_settings",
        lambda force_refresh=False: {
            "max_retries": 3,
            "retry_base_delay_s": 0.25,
            "retry_max_delay_s": 1.5,
            "circuit_enabled": True,
            "circuit_failure_threshold": 4,
            "circuit_open_seconds": 22.0,
        },
    )

    policy = llm_service._normalize_retry_policy({"max_retries": 1})

    assert policy["max_retries"] == 1
    assert policy["retry_base_delay_s"] == 0.25
    assert policy["retry_max_delay_s"] == 1.5
    assert policy["circuit_enabled"] is True
    assert policy["circuit_failure_threshold"] == 4
    assert policy["circuit_open_seconds"] == 22.0


def test_llm_service_chat_passes_retry_policy_to_adapter(monkeypatch):
    class FakeAdapter:
        def __init__(self):
            self.received_retry_policy = None

        def chat(self, messages, response_format=None, params=None, timeout=None, config=None, retry_policy=None):
            self.received_retry_policy = retry_policy
            return "ok", {"ok": True}

    adapter = FakeAdapter()
    service = LLMService(config={
        "provider": "openai",
        "api_key": "test-key",
        "model": "gpt-4o-mini",
        "endpoint": "https://api.openai.com/v1",
        "max_tokens": 128,
        "temperature": 0,
        "extra_params": {},
        "_probe_mode": True,
    })
    monkeypatch.setattr(service, "_get_adapter", lambda: adapter)

    result = service.chat(
        [{"role": "user", "content": "hello"}],
        retry_policy={"max_retries": 1},
    )

    assert result["configured"] is True
    assert adapter.received_retry_policy == {"max_retries": 1}


def test_llm_service_adapt_params_preserves_user_overrides(monkeypatch):
    class FakeAdapter:
        def __init__(self):
            self.received_config = None

        def adapt_response_format(self, requested, tier, capabilities):
            return requested

        def get_default_params(self, tier):
            return {
                "temperature": 0.9,
                "max_tokens": 1024,
                "extra_params": {"frequency_penalty": 0.1},
                "seed": 11,
            }

        def chat(self, messages, response_format=None, params=None, timeout=None, config=None, retry_policy=None):
            self.received_config = dict(config or {})
            return "ok", {"ok": True}

    adapter = FakeAdapter()
    service = LLMService(config={
        "provider": "openai",
        "api_key": "test-key",
        "model": "gpt-4o-mini",
        "endpoint": "https://api.openai.com/v1",
        "max_tokens": 512,
        "temperature": 0.6,
        "extra_params": {
            "top_p": 0.2,
            "frequency_penalty": 0.4,
        },
    })
    monkeypatch.setattr(
        service,
        "_get_capabilities",
        lambda: {
            "sql_quality": {"sql_accuracy_tier": "low", "sql_safety_compliant": False},
            "structured_output": {"supports_json_object": False, "json_mode_reliable": False},
            "repair": {"repair_capability": False},
        },
    )
    monkeypatch.setattr(service, "_get_adapter", lambda: adapter)

    result = service.chat([{"role": "user", "content": "hello"}], response_format="json")

    assert result["configured"] is True
    assert adapter.received_config is not None
    assert adapter.received_config["temperature"] == 0.6
    assert adapter.received_config["max_tokens"] == 512
    assert adapter.received_config["extra_params"]["top_p"] == 0.2
    assert adapter.received_config["extra_params"]["frequency_penalty"] == 0.4
    assert adapter.received_config["extra_params"]["seed"] == 11
    assert "repeat_penalty" not in adapter.received_config["extra_params"]


def test_llm_service_adapt_params_applies_adapter_defaults_when_user_unset(monkeypatch):
    class FakeAdapter:
        def __init__(self):
            self.received_config = None

        def adapt_response_format(self, requested, tier, capabilities):
            return requested

        def get_default_params(self, tier):
            return {
                "temperature": 0.45,
                "max_tokens": 2048,
                "repeat_penalty": 1.05,
                "extra_params": {"seed": 7},
            }

        def chat(self, messages, response_format=None, params=None, timeout=None, config=None, retry_policy=None):
            self.received_config = dict(config or {})
            return "ok", {"ok": True}

    adapter = FakeAdapter()
    service = LLMService(config={
        "provider": "openai",
        "api_key": "test-key",
        "model": "gpt-4o-mini",
        "endpoint": "https://api.openai.com/v1",
        "max_tokens": None,
        "temperature": None,
        "extra_params": {"top_k": 25},
    })
    monkeypatch.setattr(
        service,
        "_get_capabilities",
        lambda: {
            "sql_quality": {"sql_accuracy_tier": "medium", "sql_safety_compliant": False},
            "structured_output": {"supports_json_object": True, "json_mode_reliable": True},
            "repair": {"repair_capability": False},
        },
    )
    monkeypatch.setattr(service, "_get_adapter", lambda: adapter)

    result = service.chat([{"role": "user", "content": "hello"}], response_format="json")

    assert result["configured"] is True
    assert adapter.received_config is not None
    assert adapter.received_config["temperature"] == 0.45
    assert adapter.received_config["max_tokens"] == 2048
    assert adapter.received_config["extra_params"]["seed"] == 7
    assert adapter.received_config["extra_params"]["repeat_penalty"] == 1.05
    assert adapter.received_config["extra_params"]["top_k"] == 25


def test_llm_service_cache_returns_copy_to_prevent_caller_mutation(monkeypatch):
    class FakeAdapter:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, response_format=None, params=None, timeout=None, config=None, retry_policy=None):
            self.calls += 1
            return "original", {"call": self.calls}

    adapter = FakeAdapter()
    service = LLMService(config={
        "provider": "openai",
        "api_key": "test-key",
        "model": "gpt-4o-mini",
        "endpoint": "https://api.openai.com/v1",
        "max_tokens": 128,
        "temperature": 0.1,
        "extra_params": {},
        "_probe_mode": True,
    })
    monkeypatch.setattr(service, "_get_adapter", lambda: adapter)

    messages = [{"role": "user", "content": "hello"}]
    first = service.chat(messages, response_format="json")
    first["content"] = "mutated"
    if isinstance(first.get("raw"), dict):
        first["raw"]["call"] = 999

    second = service.chat(messages, response_format="json")

    assert adapter.calls == 1
    assert second["content"] == "original"
    assert isinstance(second.get("raw"), dict)
    assert second["raw"]["call"] == 1
