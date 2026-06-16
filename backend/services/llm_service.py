from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import random
import threading
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Optional

import httpx

from db import get_connection, connection_lock
from services.crypto_service import decrypt_json, is_encrypted_value
from services.prompt_templates import DEFAULT_SYSTEM_PROMPT

if TYPE_CHECKING:
    from services.llm.adapters.base import LLMProviderAdapter

LOGGER = logging.getLogger(__name__)

DEFAULT_ENDPOINT_WHITELIST = [
    "https://api.openai.com",
    "https://api.anthropic.com",
    "https://api.githubcopilot.com",
    "https://zen.opencode.ai",
    "http://localhost",
    "http://127.0.0.1",
    "http://0.0.0.0",
    "http://10.",
    "http://172.",
    "http://192.168.",
    "http://host.docker.internal",
]

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0
_RETRY_MAX_DELAY = 10.0
_CIRCUIT_ENABLED = True
_CIRCUIT_FAILURE_THRESHOLD = 3
_CIRCUIT_OPEN_SECONDS = 60.0
_LLM_HTTP_CIRCUIT_LOCK = threading.Lock()
_LLM_HTTP_CIRCUIT_STATE_BY_KEY: dict[str, dict[str, float | int]] = {}
_LLM_HTTP_POLICY_CACHE_LOCK = threading.Lock()
_LLM_HTTP_POLICY_CACHE = {
    "loaded_at": 0.0,
    "policy": None,
}
_LLM_HTTP_POLICY_CACHE_TTL_SECONDS = 5.0

# LLM response LRU cache: identical prompts return cached result
# Capacity 8 entries, TTL 60s — reduces redundant calls for retries, repair, and decompose-merge
_LLM_RESPONSE_CACHE: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_LLM_RESPONSE_CACHE_MAX_SIZE = 8
_LLM_RESPONSE_CACHE_TTL_SECONDS = 60.0
_LLM_RESPONSE_CACHE_LOCK = threading.Lock()


def _llm_cache_key(config: dict[str, Any], messages: list[dict[str, Any]], response_format: Any) -> str:
    raw = json.dumps(
        {
            "provider": config.get("provider"),
            "endpoint": config.get("endpoint"),
            "model": config.get("model"),
            "temperature": config.get("temperature"),
            "max_tokens": config.get("max_tokens"),
            "extra_params": config.get("extra_params"),
            "messages": messages,
            "response_format": response_format,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _llm_cache_get(key: str) -> dict[str, Any] | None:
    now = time.monotonic()
    with _LLM_RESPONSE_CACHE_LOCK:
        entry = _LLM_RESPONSE_CACHE.get(key)
        if entry is None:
            return None
        ts, result = entry
        if now - ts > _LLM_RESPONSE_CACHE_TTL_SECONDS:
            _LLM_RESPONSE_CACHE.pop(key, None)
            return None
        _LLM_RESPONSE_CACHE.move_to_end(key)
        return copy.deepcopy(result)


def _llm_cache_set(key: str, result: dict[str, Any]) -> None:
    now = time.monotonic()
    with _LLM_RESPONSE_CACHE_LOCK:
        if key in _LLM_RESPONSE_CACHE:
            _LLM_RESPONSE_CACHE.move_to_end(key)
        _LLM_RESPONSE_CACHE[key] = (now, copy.deepcopy(result))
        while len(_LLM_RESPONSE_CACHE) > _LLM_RESPONSE_CACHE_MAX_SIZE:
            _LLM_RESPONSE_CACHE.popitem(last=False)


class LLMCircuitOpenError(httpx.HTTPError):
    pass


def _llm_http_circuit_key(url: str, circuit_key: str | None = None) -> str:
    explicit = str(circuit_key or "").strip()
    if explicit:
        return explicit
    return str(url or "").strip().lower()


def _llm_request_circuit_key(config: dict[str, Any]) -> str:
    provider = str(config.get("provider") or "unknown").strip().lower()
    endpoint = str(config.get("endpoint") or "").strip().rstrip("/").lower()
    model = str(config.get("model") or "").strip().lower()
    return f"{provider}:{endpoint}:{model}"


def _coerce_int_setting(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(minimum, min(maximum, parsed))


def _coerce_float_setting(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        parsed = float(default)
    return max(minimum, min(maximum, parsed))


def _coerce_bool_setting(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return bool(default)


def _load_llm_http_resilience_settings(force_refresh: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    with _LLM_HTTP_POLICY_CACHE_LOCK:
        cached = _LLM_HTTP_POLICY_CACHE.get("policy")
        cached_at = float(_LLM_HTTP_POLICY_CACHE.get("loaded_at") or 0.0)
        if (
            not force_refresh
            and isinstance(cached, dict)
            and (now - cached_at) <= _LLM_HTTP_POLICY_CACHE_TTL_SECONDS
        ):
            return dict(cached)

    defaults = {
        "max_retries": int(_MAX_RETRIES),
        "retry_base_delay_s": float(_RETRY_BASE_DELAY),
        "retry_max_delay_s": float(_RETRY_MAX_DELAY),
        "circuit_enabled": bool(_CIRCUIT_ENABLED),
        "circuit_failure_threshold": int(_CIRCUIT_FAILURE_THRESHOLD),
        "circuit_open_seconds": float(_CIRCUIT_OPEN_SECONDS),
    }

    try:
        with connection_lock():
            con = get_connection()
            rows = con.execute(
                """
                SELECT key, value
                FROM metadata.settings
                WHERE key IN (
                    'llm_max_retries',
                    'llm_retry_base_delay_s',
                    'llm_retry_max_delay_s',
                    'llm_http_circuit_enabled',
                    'llm_http_circuit_failure_threshold',
                    'llm_http_circuit_open_seconds'
                )
                """
            ).fetchall()
        values = {row[0]: _json_value(row[1], row[1]) for row in rows}
    except Exception:
        values = {}

    policy = {
        "max_retries": _coerce_int_setting(values.get("llm_max_retries"), defaults["max_retries"], 1, 10),
        "retry_base_delay_s": _coerce_float_setting(
            values.get("llm_retry_base_delay_s"),
            defaults["retry_base_delay_s"],
            0.0,
            60.0,
        ),
        "retry_max_delay_s": _coerce_float_setting(
            values.get("llm_retry_max_delay_s"),
            defaults["retry_max_delay_s"],
            0.1,
            300.0,
        ),
        "circuit_enabled": _coerce_bool_setting(
            values.get("llm_http_circuit_enabled"),
            defaults["circuit_enabled"],
        ),
        "circuit_failure_threshold": _coerce_int_setting(
            values.get("llm_http_circuit_failure_threshold"),
            defaults["circuit_failure_threshold"],
            1,
            100,
        ),
        "circuit_open_seconds": _coerce_float_setting(
            values.get("llm_http_circuit_open_seconds"),
            defaults["circuit_open_seconds"],
            1.0,
            3600.0,
        ),
    }
    if policy["retry_max_delay_s"] < policy["retry_base_delay_s"]:
        policy["retry_max_delay_s"] = policy["retry_base_delay_s"]

    with _LLM_HTTP_POLICY_CACHE_LOCK:
        _LLM_HTTP_POLICY_CACHE["loaded_at"] = now
        _LLM_HTTP_POLICY_CACHE["policy"] = dict(policy)
    return policy


def _normalize_retry_policy(retry_policy: dict[str, Any] | None) -> dict[str, Any]:
    base_policy = _load_llm_http_resilience_settings()
    if isinstance(retry_policy, dict):
        source = dict(base_policy)
        source.update(dict(retry_policy))
    else:
        source = base_policy
    normalized = {
        "max_retries": _coerce_int_setting(source.get("max_retries"), _MAX_RETRIES, 1, 10),
        "retry_base_delay_s": _coerce_float_setting(source.get("retry_base_delay_s"), _RETRY_BASE_DELAY, 0.0, 60.0),
        "retry_max_delay_s": _coerce_float_setting(source.get("retry_max_delay_s"), _RETRY_MAX_DELAY, 0.1, 300.0),
        "circuit_enabled": _coerce_bool_setting(source.get("circuit_enabled"), _CIRCUIT_ENABLED),
        "circuit_failure_threshold": _coerce_int_setting(
            source.get("circuit_failure_threshold"),
            _CIRCUIT_FAILURE_THRESHOLD,
            1,
            100,
        ),
        "circuit_open_seconds": _coerce_float_setting(
            source.get("circuit_open_seconds"),
            _CIRCUIT_OPEN_SECONDS,
            1.0,
            3600.0,
        ),
    }
    if normalized["retry_max_delay_s"] < normalized["retry_base_delay_s"]:
        normalized["retry_max_delay_s"] = normalized["retry_base_delay_s"]
    return normalized


def refresh_llm_http_resilience_settings(force_refresh: bool = True) -> dict[str, Any]:
    loaded = _load_llm_http_resilience_settings(force_refresh=force_refresh)
    return _normalize_retry_policy(loaded)


def get_llm_http_resilience_policy_snapshot() -> dict[str, Any]:
    return refresh_llm_http_resilience_settings(force_refresh=False)


def clear_llm_http_circuit_state(circuit_key: str | None = None) -> None:
    with _LLM_HTTP_CIRCUIT_LOCK:
        if circuit_key is None:
            _LLM_HTTP_CIRCUIT_STATE_BY_KEY.clear()
            return
        _LLM_HTTP_CIRCUIT_STATE_BY_KEY.pop(str(circuit_key), None)


def get_llm_http_circuit_snapshot() -> dict[str, Any]:
    now = time.monotonic()
    with _LLM_HTTP_CIRCUIT_LOCK:
        details: dict[str, dict[str, Any]] = {}
        open_keys = 0
        for key in sorted(_LLM_HTTP_CIRCUIT_STATE_BY_KEY.keys()):
            state = _LLM_HTTP_CIRCUIT_STATE_BY_KEY.get(key) or {}
            open_until = float(state.get("open_until") or 0.0)
            consecutive_failures = int(state.get("consecutive_failures") or 0)
            remaining_open_seconds = max(0.0, round(open_until - now, 3))
            is_open = remaining_open_seconds > 0
            if is_open:
                open_keys += 1
            details[key] = {
                "state": "open" if is_open else "closed",
                "remaining_open_seconds": remaining_open_seconds,
                "consecutive_failures": consecutive_failures,
            }
    return {
        "total_keys": len(details),
        "open_keys": int(open_keys),
        "keys": details,
    }


def _llm_http_circuit_allow_request(circuit_key: str, *, circuit_enabled: bool) -> None:
    if not circuit_enabled:
        with _LLM_HTTP_CIRCUIT_LOCK:
            _LLM_HTTP_CIRCUIT_STATE_BY_KEY.pop(circuit_key, None)
        return
    now = time.monotonic()
    with _LLM_HTTP_CIRCUIT_LOCK:
        state = _LLM_HTTP_CIRCUIT_STATE_BY_KEY.get(circuit_key)
        if not state:
            return
        open_until = float(state.get("open_until") or 0.0)
        if open_until > now:
            remaining = round(open_until - now, 2)
            raise LLMCircuitOpenError(
                f"LLM HTTP circuit is open for '{circuit_key}' (retry suppressed for {remaining}s)"
            )
        if open_until > 0:
            state["open_until"] = 0.0
            state["consecutive_failures"] = 0


def _record_llm_http_success(circuit_key: str) -> None:
    with _LLM_HTTP_CIRCUIT_LOCK:
        _LLM_HTTP_CIRCUIT_STATE_BY_KEY.pop(circuit_key, None)


def _record_llm_http_failure(
    circuit_key: str,
    *,
    threshold: int,
    open_seconds: float,
    circuit_enabled: bool,
    exc: Exception | None = None,
) -> None:
    if not circuit_enabled:
        return
    threshold = max(1, int(threshold))
    open_seconds = max(1.0, float(open_seconds))
    now = time.monotonic()
    opened = False
    with _LLM_HTTP_CIRCUIT_LOCK:
        state = _LLM_HTTP_CIRCUIT_STATE_BY_KEY.setdefault(
            circuit_key,
            {"consecutive_failures": 0, "open_until": 0.0},
        )
        open_until = float(state.get("open_until") or 0.0)
        if open_until > now:
            return
        failures = int(state.get("consecutive_failures") or 0) + 1
        if failures >= threshold:
            state["consecutive_failures"] = 0
            state["open_until"] = now + open_seconds
            opened = True
        else:
            state["consecutive_failures"] = failures
    if opened:
        LOGGER.warning(
            "Opening LLM HTTP circuit for key=%s after %d consecutive failure(s); suppressing retries for %.1fs (%s)",
            circuit_key,
            threshold,
            open_seconds,
            type(exc).__name__ if exc is not None else "unknown_error",
        )


def _get_endpoint_whitelist() -> tuple[bool, list[str]]:
    enabled = True
    try:
        with connection_lock():
            con = get_connection()
            enabled_row = con.execute("SELECT value FROM metadata.settings WHERE key = 'llm_endpoint_whitelist_enabled'").fetchone()
            wl_row = con.execute("SELECT value FROM metadata.settings WHERE key = 'llm_endpoint_whitelist'").fetchone()
        if enabled_row:
            val = enabled_row[0]
            if isinstance(val, str):
                val = val.strip('"')
            if str(val).lower() in ("false", "0"):
                return (False, [])
        if wl_row:
            import json as _json
            val = wl_row[0]
            if isinstance(val, str):
                val = _json.loads(val)
            if isinstance(val, list):
                return (True, val)
        return (True, list(DEFAULT_ENDPOINT_WHITELIST))
    except Exception:
        LOGGER.exception("Failed to load endpoint whitelist from database; defaulting to enabled with defaults")
        return (True, list(DEFAULT_ENDPOINT_WHITELIST))


def _validate_llm_endpoint(endpoint: str, extra_allowed: list[str] | None = None) -> str:
    if not endpoint:
        return endpoint
    ep = endpoint.rstrip("/")
    enabled, whitelist = _get_endpoint_whitelist()
    if not enabled:
        return ep
    if not whitelist:
        raise ValueError("LLM endpoint whitelist is enabled but empty — no endpoints are allowed. Add prefixes to the whitelist or disable it.")
    allowed = whitelist + (extra_allowed or [])
    if ep.startswith(tuple(allowed)):
        return ep
    raise ValueError(f"LLM endpoint not allowed: {endpoint}. Add it to the endpoint whitelist in Settings > LLM, or disable the whitelist.")


DEFAULT_ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "github_copilot": "https://api.githubcopilot.com",
    "opencode_zen": "https://opencode.ai/zen/v1",
    "maxkb": "http://localhost:8080/v1",
    "ollama": "http://localhost:11434/v1",
    "vllm": "http://localhost:8000/v1",
    "custom": "",
}

DEFAULT_MODELS = {
    "openai": "gpt-4o",
    "anthropic": "claude-3-5-sonnet-latest",
    "github_copilot": "gpt-4o-copilot",
    "opencode_zen": "zen-1",
    "maxkb": "maxkb",
    "ollama": "llama3.1",
    "vllm": "qwen2.5",
    "custom": "",
}


def _json_value(value: Any, fallback: Any = None) -> Any:
    if is_encrypted_value(value):
        return decrypt_json(value, fallback)
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _get_timeout_settings() -> dict[str, float]:
    try:
        with connection_lock():
            con = get_connection()
            rows = con.execute("SELECT key, value FROM metadata.settings WHERE key LIKE 'timeout_%'").fetchall()
            vals = {r[0]: _json_value(r[1], r[1]) for r in rows}
    except Exception:
        vals = {}
    return {
        "connect": float(vals.get("timeout_llm_connect_s") or 10),
        "read": float(vals.get("timeout_llm_read_s") or 120),
        "write": float(vals.get("timeout_llm_write_s") or 10),
        "pool": float(vals.get("timeout_llm_pool_s") or 10),
    }


def get_llm_config() -> dict[str, Any]:
    with connection_lock():
        con = get_connection()
        rows = con.execute("SELECT key, value FROM metadata.settings WHERE key LIKE 'llm_%'").fetchall()
        data = {row[0]: _json_value(row[1]) for row in rows}
    provider = str(data.get("llm_provider") or "openai")
    if data.get("llm_endpoint"):
        endpoint = data["llm_endpoint"]
    elif provider == "ollama" and os.environ.get("OLLAMA_HOST"):
        ollama_host = os.environ["OLLAMA_HOST"].rstrip("/")
        endpoint = ollama_host + "/v1" if not ollama_host.endswith("/v1") else ollama_host
    else:
        endpoint = DEFAULT_ENDPOINTS.get(provider, "")
    try:
        endpoint = _validate_llm_endpoint(str(endpoint))
    except ValueError as e:
        LOGGER.error("LLM endpoint %r rejected by validation: %s; refusing to fall back to default for %s", str(endpoint), e, provider)
        raise
    return {
        "provider": provider,
        "api_key": data.get("llm_api_key") or data.get("llm_key") or "",
        "model": data.get("llm_model") or DEFAULT_MODELS.get(provider, ""),
        "endpoint": endpoint,
        "max_tokens": int(data.get("llm_max_tokens") or 4096),
        "temperature": float(data.get("llm_temperature") or 0.7),
        "extra_params": _json_value(data.get("llm_extra_params"), {}) or {},
        "system_prompt": data.get("llm_system_prompt") or DEFAULT_SYSTEM_PROMPT,
    }


def _retryable_post(
    client: httpx.Client,
    url: str,
    *,
    circuit_key: str | None = None,
    retry_policy: dict[str, Any] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    policy = _normalize_retry_policy(retry_policy)
    max_retries = int(policy["max_retries"])
    base_delay = float(policy["retry_base_delay_s"])
    max_delay = float(policy["retry_max_delay_s"])
    circuit_enabled = bool(policy["circuit_enabled"])
    circuit_failure_threshold = int(policy["circuit_failure_threshold"])
    circuit_open_seconds = float(policy["circuit_open_seconds"])

    normalized_circuit_key = _llm_http_circuit_key(url, circuit_key)
    _llm_http_circuit_allow_request(normalized_circuit_key, circuit_enabled=circuit_enabled)
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = client.post(url, **kwargs)
            if response.status_code not in _RETRYABLE_STATUS_CODES:
                _record_llm_http_success(normalized_circuit_key)
                return response
            last_exc = httpx.HTTPStatusError(
                f"LLM returned {response.status_code}",
                request=response.request,
                response=response,
            )
            LOGGER.warning("LLM request attempt %d/%d failed with status %d", attempt + 1, max_retries, response.status_code)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            last_exc = exc
            LOGGER.warning("LLM request attempt %d/%d failed with connection error: %s", attempt + 1, max_retries, exc)
        if attempt < max_retries - 1:
            delay = min(base_delay * (2 ** attempt), max_delay)
            delay = delay * (0.5 + random.random())
            time.sleep(delay)
    if last_exc is not None:
        _record_llm_http_failure(
            normalized_circuit_key,
            threshold=circuit_failure_threshold,
            open_seconds=circuit_open_seconds,
            circuit_enabled=circuit_enabled,
            exc=last_exc,
        )
        raise last_exc
    exhausted = httpx.HTTPError("All LLM retry attempts exhausted")
    _record_llm_http_failure(
        normalized_circuit_key,
        threshold=circuit_failure_threshold,
        open_seconds=circuit_open_seconds,
        circuit_enabled=circuit_enabled,
        exc=exhausted,
    )
    raise exhausted


class LLMService:
    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.config = config or get_llm_config()
        self._config_snapshot = copy.deepcopy(self.config)
        self._adapter: Optional[LLMProviderAdapter] = None

    def is_configured(self) -> bool:
        provider = self.config.get("provider")
        if provider == "ollama":
            return bool(self.config.get("endpoint"))
        return bool(self.config.get("endpoint") and (self.config.get("api_key") or provider in {"ollama", "vllm", "custom"}))

    def _get_adapter(self) -> LLMProviderAdapter:
        if self._adapter is None:
            from services.llm.adapters import create_adapter
            self._adapter = create_adapter(self.config.get("provider", ""))
        return self._adapter

    def chat(
        self,
        messages: list[dict[str, Any]],
        response_format: Optional[Any] = None,
        timeout: Optional[float] = None,
        retry_policy: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        start = time.perf_counter()
        if not self.is_configured():
            return {
                "content": "LLM provider is not configured. Please configure it in Settings > LLM.",
                "raw": None,
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
                "configured": False,
            }
        if not self.config.get("_probe_mode"):
            capabilities = self._get_capabilities()
            response_format = self._adapt_response_format(response_format, capabilities)
            self._adapt_params(capabilities)
        cache_key = _llm_cache_key(self.config, messages, response_format)
        cached = _llm_cache_get(cache_key)
        if cached is not None:
            return cached
        adapter = self._get_adapter()
        content, raw = adapter.chat(
            messages,
            response_format=response_format,
            timeout=timeout,
            config=self.config,
            retry_policy=retry_policy,
        )
        result = {
            "content": content,
            "raw": raw,
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            "configured": True,
        }
        if content:
            _llm_cache_set(cache_key, result)
        return result

    def _get_capabilities(self) -> dict:
        from services.sql_routing.llm_capability import get_model_capabilities
        return get_model_capabilities(
            self.config.get("provider", ""),
            self.config.get("endpoint", ""),
            self.config.get("model", ""),
        )

    def _adapt_response_format(self, response_format: Optional[Any], capabilities: dict | None = None) -> Optional[Any]:
        from services.sql_routing.llm_capability import _adapt_response_format as _adapt_rf, _capabilities_to_tier
        if capabilities is None:
            capabilities = self._get_capabilities()
        tier = _capabilities_to_tier(capabilities)
        adapted = _adapt_rf(response_format, capabilities)
        adapter = self._get_adapter()
        return adapter.adapt_response_format(adapted, tier, capabilities)

    def _adapt_params(self, capabilities: dict | None = None) -> None:
        from services.sql_routing.llm_capability import _capabilities_to_tier, _get_tier_params
        if capabilities is None:
            capabilities = self._get_capabilities()
        tier = _capabilities_to_tier(capabilities)
        params = _get_tier_params(tier)
        adapter = self._get_adapter()
        adapter_defaults = dict(adapter.get_default_params(tier) or {})
        user_temperature = self._config_snapshot.get("temperature")
        user_max_tokens = self._config_snapshot.get("max_tokens")
        user_extra_params = dict(self._config_snapshot.get("extra_params", {}) or {})

        tier_temperature = params.get("temperature", 0.7)
        tier_max_tokens = params.get("max_tokens", 4096)

        if user_temperature is not None:
            self.config["temperature"] = user_temperature
        elif "temperature" in adapter_defaults:
            self.config["temperature"] = adapter_defaults["temperature"]
        else:
            self.config["temperature"] = tier_temperature

        if user_max_tokens is not None:
            effective_max_tokens = user_max_tokens
        elif "max_tokens" in adapter_defaults:
            effective_max_tokens = adapter_defaults.get("max_tokens")
        else:
            effective_max_tokens = tier_max_tokens

        try:
            parsed_max_tokens = int(effective_max_tokens)
            if parsed_max_tokens <= 0:
                raise ValueError("max_tokens must be positive")
        except Exception:
            parsed_max_tokens = int(tier_max_tokens or 4096)
            if parsed_max_tokens <= 0:
                parsed_max_tokens = 4096
        self.config["max_tokens"] = parsed_max_tokens

        merged_extra_params: dict[str, Any] = {}
        merged_extra_params.update(dict(params.get("extra_params", {}) or {}))

        adapter_extra_params = dict(adapter_defaults.get("extra_params", {}) or {})
        for key, value in adapter_defaults.items():
            if key in {"temperature", "max_tokens", "extra_params"}:
                continue
            adapter_extra_params[str(key)] = value
        merged_extra_params.update(adapter_extra_params)
        merged_extra_params.update(user_extra_params)
        self.config["extra_params"] = merged_extra_params


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        code_lines = []
        started = False
        for line in lines:
            stripped_line = line.strip()
            if stripped_line.startswith("```"):
                if not started:
                    started = True
                else:
                    break
                continue
            if started:
                code_lines.append(line)
        stripped = "\n".join(code_lines).strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    if start < 0:
        return json.loads(stripped)
    depth = 0
    in_str = False
    i = start
    while i < len(stripped):
        ch = stripped[i]
        if ch == '"':
            backslashes = 0
            j = i - 1
            while j >= 0 and stripped[j] == '\\':
                backslashes += 1
                j -= 1
            if backslashes % 2 == 0:
                in_str = not in_str
        elif not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(stripped[start : i + 1])
        i += 1
    return json.loads(stripped[start:])
