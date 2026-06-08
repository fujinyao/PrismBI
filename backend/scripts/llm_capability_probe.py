#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import httpx


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


STATUS_SUPPORTED = "supported"
STATUS_PARTIAL = "partial"
STATUS_UNSUPPORTED = "unsupported"
STATUS_ERROR = "error"


SMALL_MAX_TOKENS = 64
TINY_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7+fVQAAAAASUVORK5CYII="
)


@dataclass
class ProbeResult:
    feature: str
    title: str
    status: str
    detail: str
    http_status: Optional[int] = None
    latency_ms: Optional[float] = None
    evidence: Optional[str] = None


class ProbeClient:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model: str,
        timeout_s: float,
        verify_tls: bool,
    ) -> None:
        self.base_endpoint = _normalize_base_endpoint(endpoint)
        self.chat_url = f"{self.base_endpoint}/chat/completions"
        self.models_url = f"{self.base_endpoint}/models"
        self.embeddings_url = f"{self.base_endpoint}/embeddings"
        self.model = model
        self.headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        self.http = httpx.Client(
            timeout=httpx.Timeout(timeout_s),
            verify=verify_tls,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.http.close()


def _normalize_base_endpoint(raw: str) -> str:
    endpoint = str(raw or "").strip().rstrip("/")
    endpoint = endpoint.removesuffix("/chat/completions")
    return endpoint


def _trim_text(value: Any, limit: int = 300) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text[:limit] + ("..." if len(text) > limit else "")


def _extract_message_text(body: dict[str, Any]) -> str:
    choices = body.get("choices") or []
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first, dict) else {}
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(part for part in parts if part).strip()
    return str(content or "").strip()


def _is_feature_unsupported(status_code: int) -> bool:
    return status_code in {400, 404, 405, 422, 501}


def _post_json(client: ProbeClient, payload: dict[str, Any], url: Optional[str] = None) -> tuple[httpx.Response, Any, str, float]:
    target = url or client.chat_url
    start = time.perf_counter()
    response = client.http.post(target, headers=client.headers, json=payload)
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    text = response.text
    try:
        body = response.json()
    except Exception:
        body = None
    return response, body, text, latency_ms


def _probe_models_list(client: ProbeClient) -> ProbeResult:
    start = time.perf_counter()
    response = client.http.get(client.models_url, headers=client.headers)
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    text = response.text
    if response.status_code != 200:
        return ProbeResult(
            feature="meta.models_list",
            title="Model discovery via /models",
            status=STATUS_UNSUPPORTED if _is_feature_unsupported(response.status_code) else STATUS_ERROR,
            detail=f"HTTP {response.status_code}",
            http_status=response.status_code,
            latency_ms=latency_ms,
            evidence=_trim_text(text),
        )
    try:
        body = response.json()
    except Exception:
        return ProbeResult(
            feature="meta.models_list",
            title="Model discovery via /models",
            status=STATUS_PARTIAL,
            detail="Endpoint returns 200 but body is not JSON",
            http_status=response.status_code,
            latency_ms=latency_ms,
            evidence=_trim_text(text),
        )
    data = body.get("data") if isinstance(body, dict) else None
    count = len(data) if isinstance(data, list) else 0
    status = STATUS_SUPPORTED if count > 0 else STATUS_PARTIAL
    detail = f"Returned {count} model entries"
    return ProbeResult(
        feature="meta.models_list",
        title="Model discovery via /models",
        status=status,
        detail=detail,
        http_status=response.status_code,
        latency_ms=latency_ms,
    )


def _probe_basic_chat(client: ProbeClient) -> ProbeResult:
    payload = {
        "model": client.model,
        "messages": [
            {"role": "system", "content": "You are a concise assistant."},
            {"role": "user", "content": "Reply with exactly: pong"},
        ],
        "temperature": 0,
        "max_tokens": SMALL_MAX_TOKENS,
    }
    response, body, text, latency_ms = _post_json(client, payload)
    if response.status_code != 200:
        return ProbeResult(
            feature="chat.basic_completion",
            title="Basic chat completion",
            status=STATUS_UNSUPPORTED if _is_feature_unsupported(response.status_code) else STATUS_ERROR,
            detail=f"HTTP {response.status_code}",
            http_status=response.status_code,
            latency_ms=latency_ms,
            evidence=_trim_text(text),
        )
    if not isinstance(body, dict):
        return ProbeResult(
            feature="chat.basic_completion",
            title="Basic chat completion",
            status=STATUS_PARTIAL,
            detail="Returned 200 but body is not JSON object",
            http_status=response.status_code,
            latency_ms=latency_ms,
            evidence=_trim_text(text),
        )
    message_text = _extract_message_text(body)
    usage = body.get("usage")
    usage_ok = isinstance(usage, dict)
    return ProbeResult(
        feature="chat.basic_completion",
        title="Basic chat completion",
        status=STATUS_SUPPORTED if message_text else STATUS_PARTIAL,
        detail=f"Assistant text length={len(message_text)}; usage field={'present' if usage_ok else 'missing'}",
        http_status=response.status_code,
        latency_ms=latency_ms,
        evidence=_trim_text(message_text or text),
    )


def _probe_system_instruction(client: ProbeClient) -> ProbeResult:
    payload = {
        "model": client.model,
        "messages": [
            {"role": "system", "content": "Always answer with exactly SYSTEM_OK and nothing else."},
            {"role": "user", "content": "Please answer USER_OK"},
        ],
        "temperature": 0,
        "max_tokens": SMALL_MAX_TOKENS,
    }
    response, body, text, latency_ms = _post_json(client, payload)
    if response.status_code != 200:
        return ProbeResult(
            feature="chat.system_prompt_adherence",
            title="System instruction handling",
            status=STATUS_UNSUPPORTED if _is_feature_unsupported(response.status_code) else STATUS_ERROR,
            detail=f"HTTP {response.status_code}",
            http_status=response.status_code,
            latency_ms=latency_ms,
            evidence=_trim_text(text),
        )
    if not isinstance(body, dict):
        return ProbeResult(
            feature="chat.system_prompt_adherence",
            title="System instruction handling",
            status=STATUS_PARTIAL,
            detail="Returned 200 but body is not JSON object",
            http_status=response.status_code,
            latency_ms=latency_ms,
        )
    message_text = _extract_message_text(body)
    obeyed = message_text.strip() == "SYSTEM_OK"
    return ProbeResult(
        feature="chat.system_prompt_adherence",
        title="System instruction handling",
        status=STATUS_SUPPORTED if obeyed else STATUS_PARTIAL,
        detail="System message fully obeyed" if obeyed else "System message not strictly obeyed",
        http_status=response.status_code,
        latency_ms=latency_ms,
        evidence=_trim_text(message_text),
    )


def _probe_streaming(client: ProbeClient) -> ProbeResult:
    payload = {
        "model": client.model,
        "messages": [{"role": "user", "content": "Reply with one short sentence."}],
        "temperature": 0,
        "max_tokens": SMALL_MAX_TOKENS,
        "stream": True,
    }
    start = time.perf_counter()
    try:
        with client.http.stream("POST", client.chat_url, headers=client.headers, json=payload) as response:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            if response.status_code != 200:
                body = response.read().decode("utf-8", errors="ignore")
                return ProbeResult(
                    feature="chat.streaming_sse",
                    title="Streaming chat completion",
                    status=STATUS_UNSUPPORTED if _is_feature_unsupported(response.status_code) else STATUS_ERROR,
                    detail=f"HTTP {response.status_code}",
                    http_status=response.status_code,
                    latency_ms=latency_ms,
                    evidence=_trim_text(body),
                )
            event_count = 0
            first_event = ""
            for line in response.iter_lines():
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="ignore")
                text_line = str(line).strip()
                if not text_line.startswith("data:"):
                    continue
                payload_text = text_line[5:].strip()
                if payload_text == "[DONE]":
                    break
                event_count += 1
                if not first_event:
                    first_event = payload_text
                if event_count >= 3:
                    break
            if event_count > 0:
                return ProbeResult(
                    feature="chat.streaming_sse",
                    title="Streaming chat completion",
                    status=STATUS_SUPPORTED,
                    detail=f"Received {event_count} stream event(s)",
                    http_status=response.status_code,
                    latency_ms=latency_ms,
                    evidence=_trim_text(first_event),
                )
            return ProbeResult(
                feature="chat.streaming_sse",
                title="Streaming chat completion",
                status=STATUS_PARTIAL,
                detail="HTTP 200 but no stream data events observed",
                http_status=response.status_code,
                latency_ms=latency_ms,
            )
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return ProbeResult(
            feature="chat.streaming_sse",
            title="Streaming chat completion",
            status=STATUS_ERROR,
            detail=f"Streaming call failed: {type(exc).__name__}",
            latency_ms=latency_ms,
            evidence=_trim_text(exc),
        )


def _probe_multi_choice(client: ProbeClient) -> ProbeResult:
    payload = {
        "model": client.model,
        "messages": [{"role": "user", "content": "Say hello"}],
        "max_tokens": SMALL_MAX_TOKENS,
        "temperature": 0.2,
        "n": 2,
    }
    response, body, text, latency_ms = _post_json(client, payload)
    if response.status_code != 200:
        return ProbeResult(
            feature="chat.multi_choice_n",
            title="Multiple choices (n parameter)",
            status=STATUS_UNSUPPORTED if _is_feature_unsupported(response.status_code) else STATUS_ERROR,
            detail=f"HTTP {response.status_code}",
            http_status=response.status_code,
            latency_ms=latency_ms,
            evidence=_trim_text(text),
        )
    choices = body.get("choices") if isinstance(body, dict) else None
    count = len(choices) if isinstance(choices, list) else 0
    return ProbeResult(
        feature="chat.multi_choice_n",
        title="Multiple choices (n parameter)",
        status=STATUS_SUPPORTED if count >= 2 else STATUS_PARTIAL,
        detail=f"Returned {count} choice(s)",
        http_status=response.status_code,
        latency_ms=latency_ms,
    )


def _probe_logprobs(client: ProbeClient) -> ProbeResult:
    payload = {
        "model": client.model,
        "messages": [{"role": "user", "content": "Reply with one token: yes"}],
        "max_tokens": 4,
        "temperature": 0,
        "logprobs": True,
        "top_logprobs": 2,
    }
    response, body, text, latency_ms = _post_json(client, payload)
    if response.status_code != 200:
        return ProbeResult(
            feature="chat.logprobs",
            title="Token log probabilities",
            status=STATUS_UNSUPPORTED if _is_feature_unsupported(response.status_code) else STATUS_ERROR,
            detail=f"HTTP {response.status_code}",
            http_status=response.status_code,
            latency_ms=latency_ms,
            evidence=_trim_text(text),
        )
    choices = body.get("choices") if isinstance(body, dict) else []
    first = choices[0] if isinstance(choices, list) and choices else {}
    has_logprobs = isinstance(first, dict) and first.get("logprobs") is not None
    return ProbeResult(
        feature="chat.logprobs",
        title="Token log probabilities",
        status=STATUS_SUPPORTED if has_logprobs else STATUS_PARTIAL,
        detail="logprobs field present" if has_logprobs else "Request succeeded but logprobs field missing",
        http_status=response.status_code,
        latency_ms=latency_ms,
    )


def _probe_seed_determinism(client: ProbeClient) -> ProbeResult:
    payload = {
        "model": client.model,
        "messages": [{"role": "user", "content": "Generate a 6-character lowercase string."}],
        "max_tokens": 8,
        "temperature": 0.8,
        "seed": 42,
    }
    response1, body1, text1, latency1 = _post_json(client, payload)
    if response1.status_code != 200:
        return ProbeResult(
            feature="chat.seed_determinism",
            title="Seed parameter determinism",
            status=STATUS_UNSUPPORTED if _is_feature_unsupported(response1.status_code) else STATUS_ERROR,
            detail=f"First call HTTP {response1.status_code}",
            http_status=response1.status_code,
            latency_ms=latency1,
            evidence=_trim_text(text1),
        )
    response2, body2, text2, latency2 = _post_json(client, payload)
    if response2.status_code != 200:
        return ProbeResult(
            feature="chat.seed_determinism",
            title="Seed parameter determinism",
            status=STATUS_PARTIAL,
            detail=f"First call succeeded; second call HTTP {response2.status_code}",
            http_status=response2.status_code,
            latency_ms=latency1 + latency2,
            evidence=_trim_text(text2),
        )
    out1 = _extract_message_text(body1 if isinstance(body1, dict) else {})
    out2 = _extract_message_text(body2 if isinstance(body2, dict) else {})
    same = bool(out1 and out2 and out1 == out2)
    return ProbeResult(
        feature="chat.seed_determinism",
        title="Seed parameter determinism",
        status=STATUS_SUPPORTED if same else STATUS_PARTIAL,
        detail="Two seeded calls matched" if same else "Seed accepted but outputs were not identical",
        http_status=response2.status_code,
        latency_ms=latency1 + latency2,
        evidence=_trim_text(f"run1={out1} | run2={out2}"),
    )


def _probe_tool_calling(client: ProbeClient) -> ProbeResult:
    payload = {
        "model": client.model,
        "messages": [{"role": "user", "content": "Call the tool to get current time in Asia/Shanghai."}],
        "max_tokens": SMALL_MAX_TOKENS,
        "temperature": 0,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_time",
                    "description": "Get current time for a timezone",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "timezone": {"type": "string"},
                        },
                        "required": ["timezone"],
                    },
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "get_time"}},
    }
    response, body, text, latency_ms = _post_json(client, payload)
    if response.status_code != 200:
        return ProbeResult(
            feature="chat.tool_calling",
            title="Function calling / tools",
            status=STATUS_UNSUPPORTED if _is_feature_unsupported(response.status_code) else STATUS_ERROR,
            detail=f"HTTP {response.status_code}",
            http_status=response.status_code,
            latency_ms=latency_ms,
            evidence=_trim_text(text),
        )
    choices = body.get("choices") if isinstance(body, dict) else []
    first = choices[0] if isinstance(choices, list) and choices else {}
    message = first.get("message") if isinstance(first, dict) else {}
    tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
    has_tool_calls = isinstance(tool_calls, list) and len(tool_calls) > 0
    return ProbeResult(
        feature="chat.tool_calling",
        title="Function calling / tools",
        status=STATUS_SUPPORTED if has_tool_calls else STATUS_PARTIAL,
        detail="tool_calls returned" if has_tool_calls else "Request succeeded but no tool_calls in response",
        http_status=response.status_code,
        latency_ms=latency_ms,
        evidence=_trim_text(tool_calls),
    )


def _probe_json_object(client: ProbeClient) -> ProbeResult:
    payload = {
        "model": client.model,
        "messages": [
            {
                "role": "user",
                "content": "Return only a JSON object with keys ping and count where ping='pong' and count=1.",
            }
        ],
        "temperature": 0,
        "max_tokens": SMALL_MAX_TOKENS,
        "response_format": {"type": "json_object"},
    }
    response, body, text, latency_ms = _post_json(client, payload)
    if response.status_code != 200:
        return ProbeResult(
            feature="chat.response_format_json_object",
            title="Strict JSON via response_format=json_object",
            status=STATUS_UNSUPPORTED if _is_feature_unsupported(response.status_code) else STATUS_ERROR,
            detail=f"HTTP {response.status_code}",
            http_status=response.status_code,
            latency_ms=latency_ms,
            evidence=_trim_text(text),
        )
    message_text = _extract_message_text(body if isinstance(body, dict) else {})
    try:
        parsed = json.loads(message_text)
    except Exception as exc:
        return ProbeResult(
            feature="chat.response_format_json_object",
            title="Strict JSON via response_format=json_object",
            status=STATUS_PARTIAL,
            detail=f"200 returned, but assistant content is not strict JSON ({type(exc).__name__})",
            http_status=response.status_code,
            latency_ms=latency_ms,
            evidence=_trim_text(message_text),
        )
    if isinstance(parsed, dict):
        return ProbeResult(
            feature="chat.response_format_json_object",
            title="Strict JSON via response_format=json_object",
            status=STATUS_SUPPORTED,
            detail="Assistant content is valid JSON object",
            http_status=response.status_code,
            latency_ms=latency_ms,
            evidence=_trim_text(parsed),
        )
    return ProbeResult(
        feature="chat.response_format_json_object",
        title="Strict JSON via response_format=json_object",
        status=STATUS_PARTIAL,
        detail="Assistant content parsed as JSON but not an object",
        http_status=response.status_code,
        latency_ms=latency_ms,
        evidence=_trim_text(parsed),
    )


def _probe_json_schema(client: ProbeClient) -> ProbeResult:
    payload = {
        "model": client.model,
        "messages": [
            {
                "role": "user",
                "content": "Return ping='pong' and count=1.",
            }
        ],
        "temperature": 0,
        "max_tokens": SMALL_MAX_TOKENS,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "probe_schema",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "ping": {"type": "string"},
                        "count": {"type": "integer"},
                    },
                    "required": ["ping", "count"],
                    "additionalProperties": False,
                },
            },
        },
    }
    response, body, text, latency_ms = _post_json(client, payload)
    if response.status_code != 200:
        return ProbeResult(
            feature="chat.response_format_json_schema",
            title="Strict JSON Schema response format",
            status=STATUS_UNSUPPORTED if _is_feature_unsupported(response.status_code) else STATUS_ERROR,
            detail=f"HTTP {response.status_code}",
            http_status=response.status_code,
            latency_ms=latency_ms,
            evidence=_trim_text(text),
        )
    message_text = _extract_message_text(body if isinstance(body, dict) else {})
    try:
        parsed = json.loads(message_text)
    except Exception as exc:
        return ProbeResult(
            feature="chat.response_format_json_schema",
            title="Strict JSON Schema response format",
            status=STATUS_PARTIAL,
            detail=f"200 returned, but assistant content is not strict JSON ({type(exc).__name__})",
            http_status=response.status_code,
            latency_ms=latency_ms,
            evidence=_trim_text(message_text),
        )
    valid = (
        isinstance(parsed, dict)
        and isinstance(parsed.get("ping"), str)
        and isinstance(parsed.get("count"), int)
        and set(parsed.keys()) == {"ping", "count"}
    )
    return ProbeResult(
        feature="chat.response_format_json_schema",
        title="Strict JSON Schema response format",
        status=STATUS_SUPPORTED if valid else STATUS_PARTIAL,
        detail="Schema-conform JSON object returned" if valid else "JSON returned but not schema-conform",
        http_status=response.status_code,
        latency_ms=latency_ms,
        evidence=_trim_text(parsed),
    )


def _probe_vision(client: ProbeClient) -> ProbeResult:
    payload = {
        "model": client.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is in this image? Reply with one word."},
                    {"type": "image_url", "image_url": {"url": TINY_PNG_DATA_URL}},
                ],
            }
        ],
        "max_tokens": SMALL_MAX_TOKENS,
        "temperature": 0,
    }
    response, body, text, latency_ms = _post_json(client, payload)
    if response.status_code != 200:
        return ProbeResult(
            feature="chat.vision_image_url",
            title="Vision input (image_url)",
            status=STATUS_UNSUPPORTED if _is_feature_unsupported(response.status_code) else STATUS_ERROR,
            detail=f"HTTP {response.status_code}",
            http_status=response.status_code,
            latency_ms=latency_ms,
            evidence=_trim_text(text),
        )
    message_text = _extract_message_text(body if isinstance(body, dict) else {})
    return ProbeResult(
        feature="chat.vision_image_url",
        title="Vision input (image_url)",
        status=STATUS_SUPPORTED if message_text else STATUS_PARTIAL,
        detail="Vision request accepted" if message_text else "Vision request accepted but empty text output",
        http_status=response.status_code,
        latency_ms=latency_ms,
        evidence=_trim_text(message_text),
    )


def _probe_embeddings(client: ProbeClient, embedding_model: str) -> ProbeResult:
    payload = {
        "model": embedding_model,
        "input": "capability probe",
    }
    response, body, text, latency_ms = _post_json(client, payload, url=client.embeddings_url)
    if response.status_code != 200:
        return ProbeResult(
            feature="embeddings.basic",
            title="Embeddings endpoint",
            status=STATUS_UNSUPPORTED if _is_feature_unsupported(response.status_code) else STATUS_ERROR,
            detail=f"HTTP {response.status_code}",
            http_status=response.status_code,
            latency_ms=latency_ms,
            evidence=_trim_text(text),
        )
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, list) and data:
        first = data[0] if isinstance(data[0], dict) else {}
        vector = first.get("embedding") if isinstance(first, dict) else None
        if isinstance(vector, list) and vector:
            return ProbeResult(
                feature="embeddings.basic",
                title="Embeddings endpoint",
                status=STATUS_SUPPORTED,
                detail=f"Embedding vector length={len(vector)}",
                http_status=response.status_code,
                latency_ms=latency_ms,
            )
    return ProbeResult(
        feature="embeddings.basic",
        title="Embeddings endpoint",
        status=STATUS_PARTIAL,
        detail="Endpoint returned 200 but embedding vector not found",
        http_status=response.status_code,
        latency_ms=latency_ms,
        evidence=_trim_text(body),
    )


def _load_config_from_settings() -> dict[str, str]:
    try:
        from services.llm_service import get_llm_config

        cfg = get_llm_config()
        return {
            "endpoint": str(cfg.get("endpoint") or "").strip(),
            "api_key": str(cfg.get("api_key") or "").strip(),
            "model": str(cfg.get("model") or "").strip(),
            "provider": str(cfg.get("provider") or "").strip(),
        }
    except Exception:
        return {"endpoint": "", "api_key": "", "model": "", "provider": ""}


def _resolve_runtime_config(args: argparse.Namespace) -> dict[str, str]:
    settings = _load_config_from_settings() if not args.no_settings else {}
    endpoint = (
        args.endpoint
        or settings.get("endpoint")
        or os.getenv("LLM_ENDPOINT")
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("OPENAI_API_BASE")
        or ""
    )
    api_key = (
        args.api_key
        or settings.get("api_key")
        or os.getenv("LLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    )
    model = args.model or settings.get("model") or os.getenv("LLM_MODEL") or ""
    provider = settings.get("provider") or os.getenv("LLM_PROVIDER") or ""
    return {
        "endpoint": str(endpoint or "").strip(),
        "api_key": str(api_key or "").strip(),
        "model": str(model or "").strip(),
        "provider": str(provider or "").strip(),
    }


def _summary(results: list[ProbeResult]) -> dict[str, Any]:
    counts = {
        STATUS_SUPPORTED: 0,
        STATUS_PARTIAL: 0,
        STATUS_UNSUPPORTED: 0,
        STATUS_ERROR: 0,
    }
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1

    by_feature = {result.feature: result for result in results}
    json_object = by_feature.get("chat.response_format_json_object")
    json_schema = by_feature.get("chat.response_format_json_schema")
    strict_json_supported = False
    strict_json_mode = "none"
    strict_json_detail = "Strict JSON not confirmed"

    if json_object and json_object.status == STATUS_SUPPORTED:
        strict_json_supported = True
        strict_json_mode = "json_object"
        strict_json_detail = "response_format=json_object produced strict JSON"
    elif json_schema and json_schema.status == STATUS_SUPPORTED:
        strict_json_supported = True
        strict_json_mode = "json_schema"
        strict_json_detail = "response_format=json_schema produced schema-conform strict JSON"
    elif (json_object and json_object.status == STATUS_PARTIAL) or (json_schema and json_schema.status == STATUS_PARTIAL):
        strict_json_mode = "partial"
        strict_json_detail = "JSON mode accepted or partially working, but strict JSON was not consistently confirmed"

    return {
        "counts": counts,
        "strict_json": {
            "supported": strict_json_supported,
            "mode": strict_json_mode,
            "detail": strict_json_detail,
            "json_object_status": json_object.status if json_object else "not_run",
            "json_schema_status": json_schema.status if json_schema else "not_run",
        },
    }


def _print_report(endpoint: str, model: str, provider: str, results: list[ProbeResult]) -> None:
    summary = _summary(results)
    print("LLM Capability Probe Report")
    print("===========================")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"Endpoint:  {endpoint}")
    print(f"Provider:  {provider or 'unknown'}")
    print(f"Model:     {model}")
    print("")
    print("Capability Matrix")
    for result in results:
        print(f"- [{result.status.upper():11}] {result.feature}: {result.detail}")
        if result.http_status is not None:
            print(f"    HTTP={result.http_status}, latency_ms={result.latency_ms}")
        if result.evidence:
            print(f"    evidence={_trim_text(result.evidence, limit=220)}")
    print("")
    counts = summary["counts"]
    print("Summary")
    print(f"- supported:   {counts.get(STATUS_SUPPORTED, 0)}")
    print(f"- partial:     {counts.get(STATUS_PARTIAL, 0)}")
    print(f"- unsupported: {counts.get(STATUS_UNSUPPORTED, 0)}")
    print(f"- error:       {counts.get(STATUS_ERROR, 0)}")
    strict_json = summary["strict_json"]
    print("")
    print("Strict JSON Verdict")
    print(f"- supported: {strict_json['supported']}")
    print(f"- mode:      {strict_json['mode']}")
    print(f"- detail:    {strict_json['detail']}")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe LLM service capabilities and strict JSON support.",
    )
    parser.add_argument("--endpoint", help="Base endpoint, e.g. https://host/v1")
    parser.add_argument("--api-key", help="API key (defaults to settings/env)")
    parser.add_argument("--model", help="Chat model to probe (e.g. qwen3.5-35b-a3b)")
    parser.add_argument("--embedding-model", help="Embedding model (defaults to --model)")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification")
    parser.add_argument("--no-settings", action="store_true", help="Do not load default config from backend settings")
    parser.add_argument("--skip-vision", action="store_true", help="Skip vision/image capability probe")
    parser.add_argument("--skip-embeddings", action="store_true", help="Skip embeddings endpoint probe")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON report")
    parser.add_argument("--output", type=Path, help="Optional file to write JSON report")
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    runtime = _resolve_runtime_config(args)
    endpoint = runtime["endpoint"]
    model = runtime["model"]
    api_key = runtime["api_key"]
    provider = runtime.get("provider") or ""

    if not endpoint:
        raise SystemExit("Missing endpoint. Pass --endpoint or configure LLM settings.")
    if not model:
        raise SystemExit("Missing model. Pass --model or configure LLM settings.")

    embedding_model = args.embedding_model or model
    verify_tls = not args.insecure

    client = ProbeClient(
        endpoint=endpoint,
        api_key=api_key,
        model=model,
        timeout_s=args.timeout,
        verify_tls=verify_tls,
    )

    probes: list[Callable[[ProbeClient], ProbeResult]] = [
        _probe_models_list,
        _probe_basic_chat,
        _probe_system_instruction,
        _probe_streaming,
        _probe_multi_choice,
        _probe_logprobs,
        _probe_seed_determinism,
        _probe_tool_calling,
        _probe_json_object,
        _probe_json_schema,
    ]

    if not args.skip_vision:
        probes.append(_probe_vision)

    results: list[ProbeResult] = []
    try:
        for probe in probes:
            try:
                results.append(probe(client))
            except Exception as exc:
                results.append(
                    ProbeResult(
                        feature=getattr(probe, "__name__", "unknown_probe"),
                        title="Internal probe execution",
                        status=STATUS_ERROR,
                        detail=f"Probe raised {type(exc).__name__}",
                        evidence=_trim_text(exc),
                    )
                )

        if not args.skip_embeddings:
            try:
                results.append(_probe_embeddings(client, embedding_model))
            except Exception as exc:
                results.append(
                    ProbeResult(
                        feature="embeddings.basic",
                        title="Embeddings endpoint",
                        status=STATUS_ERROR,
                        detail=f"Probe raised {type(exc).__name__}",
                        evidence=_trim_text(exc),
                    )
                )
    finally:
        client.close()

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "endpoint": client.base_endpoint,
        "provider": provider,
        "model": model,
        "embedding_model": embedding_model,
        "results": [asdict(result) for result in results],
        "summary": _summary(results),
    }

    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(client.base_endpoint, model, provider, results)

    strict_json_supported = report["summary"]["strict_json"]["supported"]
    return 0 if strict_json_supported else 2


if __name__ == "__main__":
    raise SystemExit(main())
