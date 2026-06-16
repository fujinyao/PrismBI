from __future__ import annotations

import hashlib
import importlib
import json
import logging
import math
import os
import re
import socket
import threading
import time
import unicodedata
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import lru_cache
from typing import Any, Callable, Optional

import duckdb

import httpx
import sqlglot
from sqlglot import exp

from db import connection_lock, get_connection
from services.crypto_service import decrypt_json, is_encrypted_value
from services.llm_service import LLMCircuitOpenError, LLMService, parse_json_object
from services.prompt_templates import (
    DEFAULT_PROJECT_PROMPT,
    DEFAULT_SYSTEM_PROMPT,
    common_prompt_variables,
    localized_contract,
    render_prompt_template,
)
from services.security_policy_service import apply_cls_to_rows, plan_secured_sql
from services.sql_routing.audit import emit_sql_route_event
from services.sql_routing.candidate_guard import CandidateGuard
from services.sql_routing.contracts import AskInput, MetadataHitContext
from services.sql_routing.datasource_registry import (
    apply_limit_for_datasource,
    dialect_for_datasource,
    normalize_datasource_type,
    resolve_datasource_definition,
)
from services.sql_routing.execution_router import ExecutionRouter
from services.sql_routing.execution_pipeline import ExecutionPipeline
from services.sql_routing.generation_pipeline import GenerationPipeline
from services.sql_routing.generation_router import GenerationRouter
from services.sql_routing.llm_capability import get_strict_json_capability
from services.sql_routing.prompt_profiles import PromptProfileRouter

from services.ask_execution import (
    _acquire_pooled_connection,
    _clear_external_connection_pool,
    _close_connection_quietly,
    _external_pool_key,
    _is_generic_connection_healthy,
    _is_mysql_connection_healthy,
    _is_postgres_connection_healthy,
    _release_pooled_connection,
)

from services.ask_config import (
    BACKEND_DIR,
    PROJECT_DATA_DIR,
    MAX_SQL_ROWS,
    DEFAULT_PREVIEW_ROW_LIMIT,
    MIN_PREVIEW_ROW_LIMIT,
    MAX_PREVIEW_ROW_LIMIT,
    MIN_EXECUTION_ROW_LIMIT,
    MAX_EXECUTION_ROW_LIMIT,
    MAX_SOURCE_MATERIALIZATION_ROWS,
    ROUTER_CONFIG,
)


def _formatted_sql_contract(dialect_hint: str, language: Optional[str] = None) -> str:
    return render_prompt_template(localized_contract("sql_response", language), {"dialect_hint": dialect_hint})

def _guidance_prompt(language: Optional[str] = None) -> str:
    lang = _normalize_language(language)
    guidance = _sql_msg("guidance_prompt", language)
    if not _in_chinese(language) and lang != "en":
        guidance += f"\n\n{_language_instruction(language)}"
    return guidance
LOGGER = logging.getLogger(__name__)

# Empty content circuit breaker for Ollama json_object mode
# Track consecutive empty-content responses per LLM circuit key
_EMPTY_CONTENT_BREAKER_LOCK = threading.Lock()
_EMPTY_CONTENT_BREAKER_STATE: dict[str, dict[str, float | int]] = {}
_EMPTY_CONTENT_BREAKER_THRESHOLD = 3
_EMPTY_CONTENT_BREAKER_RESET_SECONDS = 300.0


def _json_empty_content_circuit_key(llm: LLMService) -> str:
    cfg = getattr(llm, "config", {}) or {}
    return f"{cfg.get('provider','?')}:{cfg.get('endpoint','?')}:{cfg.get('model','?')}"


def _json_empty_content_circuit_allowed(key: str) -> bool:
    now = time.monotonic()
    with _EMPTY_CONTENT_BREAKER_LOCK:
        state = _EMPTY_CONTENT_BREAKER_STATE.get(key)
        if not state:
            return True
        disabled_until = float(state.get("disabled_until") or 0.0)
        if disabled_until > now:
            return False
        if disabled_until > 0:
            state["disabled_until"] = 0.0
            state["consecutive_empty"] = 0
        return True


def _record_json_empty_content(key: str) -> None:
    with _EMPTY_CONTENT_BREAKER_LOCK:
        state = _EMPTY_CONTENT_BREAKER_STATE.setdefault(
            key, {"consecutive_empty": 0, "disabled_until": 0.0}
        )
        disabled_until = float(state.get("disabled_until") or 0.0)
        if disabled_until > time.monotonic():
            return
        consec = int(state.get("consecutive_empty") or 0) + 1
        if consec >= _EMPTY_CONTENT_BREAKER_THRESHOLD:
            state["consecutive_empty"] = 0
            state["disabled_until"] = time.monotonic() + _EMPTY_CONTENT_BREAKER_RESET_SECONDS
            LOGGER.warning(
                "Empty content circuit breaker tripped for key=%s after %d consecutive empty responses; "
                "disabling json response_format for %.1fs",
                key, _EMPTY_CONTENT_BREAKER_THRESHOLD, _EMPTY_CONTENT_BREAKER_RESET_SECONDS,
            )
        else:
            state["consecutive_empty"] = consec


def _reset_json_empty_content_breaker(key: str) -> None:
    with _EMPTY_CONTENT_BREAKER_LOCK:
        _EMPTY_CONTENT_BREAKER_STATE.pop(key, None)


class AskCancelledError(ValueError):
    pass


GENERAL_CHAT_RE = re.compile(
    r"^\s*(hi|hello|hey|你好|您好|你是谁|你是誰|who are you|what are you|介绍一下你自己|介紹一下你自己|你[能会]做|你[能会]帮|what can you|what do you|help me|帮我|你能|你会|可以帮我|介绍一下|功能介绍|怎么用|如何使用)[\s\S]{0,25}$",
    re.IGNORECASE,
)
LANGUAGE_NAMES = {
    "en": "English",
    "zh": "Chinese",
    "zh-cn": "Simplified Chinese",
    "zh_tw": "Traditional Chinese",
    "zh-tw": "Traditional Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "pt-br": "Brazilian Portuguese",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
    "th": "Thai",
    "vi": "Vietnamese",
    "nl": "Dutch",
    "sv": "Swedish",
    "pl": "Polish",
    "tr": "Turkish",
    "id": "Indonesian",
    "ms": "Malay",
    "cs": "Czech",
    "ro": "Romanian",
    "uk": "Ukrainian",
    "el": "Greek",
    "he": "Hebrew",
}
_EN_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CJK_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+")
_HAS_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ALIAS_SCOPE_ISSUE_RE = re.compile(
    r"^\s*(?P<table>[A-Za-z_][\w$]*)\.(?P<column>[A-Za-z_][\w$]*)\s*"
    r"\(table/alias not visible in current SELECT scope; available:\s*(?P<available>[^)]+)\)\s*$",
    re.IGNORECASE,
)
_UNQUALIFIED_ALIAS_SCOPE_ISSUE_RE = re.compile(
    r"^\s*(?P<column>[A-Za-z_][\w$]*)\s*"
    r"\(not visible in current SELECT scope; ambiguous unqualified column, candidates:\s*(?P<candidates>[^)]+)\)\s*$",
    re.IGNORECASE,
)
_HALLUCINATED_COLUMN_ISSUE_RE = re.compile(
    r"^\s*(?P<table>[A-Za-z_][\w$]*)\.(?P<column>[A-Za-z_][\w$]*)\s*"
    r"\(not found in any model\)\s*$",
    re.IGNORECASE,
)
_HALLUCINATED_QUANTITY_TOKENS = frozenset(
    {
        "quantity",
        "qty",
        "item_quantity",
        "item_qty",
        "order_quantity",
        "order_qty",
    }
)
_CLAUSE_SPLIT_RE = re.compile(
    r"(?:[。！？!?；;]|\s+(?:and then|and|then|also|plus|but|while)\s+|\s*(?:以及|并且|而且|同时|另外|然后|并|但|但是|且)\s*)",
    re.IGNORECASE,
)
_DATA_ROUTE_INDICATORS = (
    "count", "total", "sum", "average", "avg", "max", "min", "top", "bottom",
    "rank", "ranking", "compare", "comparison", "trend", "performance",
    "how many", "how much", "which", "where", "by city", "by region",
    "by country", "by product", "by category", "by customer", "by month",
    "by year", "by week", "by quarter", "breakdown", "distribution",
    "percentage", "proportion", "ratio", "增长", "下降", "排名", "比较", "对比",
    "趋势", "表现", "分布", "占比", "比例", "多少", "哪些", "城市", "地区",
    "产品", "类别", "客户", "月", "年", "季度", "最好", "最差", "最高", "最低",
    "销售量", "销售额", "订单量", "收入", "利润", "数量",
)
_DECOMPOSE_SQL_PLACEHOLDER_RE = re.compile(
    r"(?:\[(?:schema|table|column|database)\]|<(?:schema|table|column|database)>|\byour_(?:schema|table|column)\b|\bexample_(?:schema|table|column)\b)",
    re.IGNORECASE,
)

_PROMPT_PROFILE_ROUTER: PromptProfileRouter | None = None
_GENERATION_ROUTER: GenerationRouter | None = None
_EXECUTION_ROUTER: ExecutionRouter | None = None


def _get_profile_router() -> PromptProfileRouter:
    global _PROMPT_PROFILE_ROUTER
    if _PROMPT_PROFILE_ROUTER is None:
        _PROMPT_PROFILE_ROUTER = PromptProfileRouter()
    return _PROMPT_PROFILE_ROUTER


def _get_generation_router() -> GenerationRouter:
    global _GENERATION_ROUTER
    if _GENERATION_ROUTER is None:
        _GENERATION_ROUTER = GenerationRouter(config_getter=lambda: ROUTER_CONFIG)
    return _GENERATION_ROUTER


def _get_execution_router() -> ExecutionRouter:
    global _EXECUTION_ROUTER
    if _EXECUTION_ROUTER is None:
        _EXECUTION_ROUTER = ExecutionRouter()
    return _EXECUTION_ROUTER


def set_profile_router(router: PromptProfileRouter | None) -> None:
    global _PROMPT_PROFILE_ROUTER
    _PROMPT_PROFILE_ROUTER = router


def set_generation_router(router: GenerationRouter | None) -> None:
    global _GENERATION_ROUTER
    _GENERATION_ROUTER = router


def set_execution_router(router: ExecutionRouter | None) -> None:
    global _EXECUTION_ROUTER
    _EXECUTION_ROUTER = router


def _is_sql_route_v2_enabled(project_id: int | None) -> bool:
    if not ROUTER_CONFIG.get("sql_route_v2_enabled", True):
        return False
    allowlist = ROUTER_CONFIG.get("sql_route_allowlist_projects") or []
    normalized_allowlist: set[int] = set()
    for item in allowlist:
        try:
            normalized_allowlist.add(int(item))
        except Exception:
            continue
    if not normalized_allowlist:
        return True
    if project_id is None:
        return False
    return int(project_id) in normalized_allowlist


def _strict_json_capability() -> dict[str, Any]:
    if not ROUTER_CONFIG.get("sql_route_strict_json_probe_enabled", True):
        return {"supported": False, "mode": "none", "detail": "Strict JSON probe disabled by router config."}
    capability = get_strict_json_capability()
    mode = str(capability.get("mode") or "none").strip().lower()
    if mode not in {"json_object", "json_schema", "partial", "none"}:
        mode = "partial"
    return {
        "supported": bool(capability.get("supported")),
        "mode": mode,
        "detail": str(capability.get("detail") or ""),
    }


def _current_llm_model_tier(llm: Optional[LLMService] = None) -> tuple[str | None, dict[str, Any]]:
    service = llm if isinstance(llm, LLMService) else LLMService()
    config = getattr(service, "config", None)
    if not isinstance(config, dict):
        return None, {}
    provider = str(config.get("provider") or "")
    endpoint = str(config.get("endpoint") or "")
    model = str(config.get("model") or "")
    if not provider or not model:
        return None, {}
    try:
        from services.sql_routing.llm_capability import (
            _capabilities_to_tier,
            get_model_capabilities,
        )

        caps = get_model_capabilities(provider, endpoint, model)
        tier = _capabilities_to_tier(caps)
        return (str(tier) if tier else None), (caps if isinstance(caps, dict) else {})
    except Exception:
        LOGGER.debug("Failed to resolve model tier for prompt profile selection", exc_info=True)
        return None, {}


def _prompt_profile_selection(stage: str, *, strict_json_mode: str, model_tier: str | None = None) -> Any:
    return _get_profile_router().select(
        stage,
        strict_json_mode=strict_json_mode,
        profile_id=str(ROUTER_CONFIG.get("sql_route_profile_id") or "prismbi.default"),
        profile_version=str(ROUTER_CONFIG.get("sql_route_profile_version") or "v2"),
        model_tier=model_tier,
    )


def _sql_gen_error_result(
    summary: str,
    reasoning: str,
    sql_engine: str,
    *,
    retrieved_tables: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "sql": None,
        "summary": summary,
        "reasoning": reasoning,
        "retrieved_tables": retrieved_tables if retrieved_tables is not None else [],
        "configured": True,
        "sql_engine": sql_engine,
    }


def _emit_route_event(event_type: str, payload: dict[str, Any], project_id: int | None = None) -> None:
    _record_route_dimension_metric(event_type, payload, project_id)
    emit_sql_route_event(
        event_type,
        payload,
        project_id=project_id,
        persist=bool(ROUTER_CONFIG.get("sql_route_event_persist_enabled", True)),
    )


def _looks_like_response_format_error(exc: Exception) -> bool:
    exc_name = type(exc).__name__.lower()
    name_markers = ("badrequest", "validationerror", "valueerror")
    if any(m in exc_name for m in name_markers):
        return True
    lowered = str(exc).strip().lower() if exc else ""
    if not lowered:
        return False
    markers = (
        "response_format",
        "json_schema",
        "json schema",
        "json_object",
        "json object",
        "unsupported",
        "not support",
        "invalid_request",
        "invalid request",
        "unrecognized",
        "unknown field",
    )
    return any(marker in lowered for marker in markers)


def _contains_sql_placeholder_markers(sql: str) -> bool:
    text = str(sql or "")
    if not text:
        return False
    return bool(_DECOMPOSE_SQL_PLACEHOLDER_RE.search(text))


def _default_llm_retry_policy_for_stage(stage: str) -> dict[str, Any] | None:
    normalized_stage = str(stage or "").strip().lower()
    if normalized_stage.startswith("sql_generation") or normalized_stage == "sql_repair":
        # Allow 1 transport retry to handle transient timeouts without multiplicative blowup.
        return {"max_retries": 2}
    return None


def _llm_chat_with_response_format_fallback(
    llm: LLMService,
    messages: list[dict[str, Any]],
    *,
    response_format: Any,
    stage: str,
    timeout: Optional[float] = None,
    retry_policy: Optional[dict[str, Any]] = None,
    cancel_check: Optional[Callable[[], None]] = None,
) -> dict[str, Any]:
    _t0 = time.monotonic()
    effective_retry_policy = (
        dict(retry_policy)
        if isinstance(retry_policy, dict)
        else _default_llm_retry_policy_for_stage(stage)
    )
    breaker_key = _json_empty_content_circuit_key(llm)
    if not isinstance(response_format, dict):
        effective_rf = response_format
        if response_format == "json" and not _json_empty_content_circuit_allowed(breaker_key):
            LOGGER.warning(
                "Empty content circuit breaker active for key=%s at stage=%s; skipping json response_format",
                breaker_key, stage,
            )
            effective_rf = None
        if cancel_check:
            cancel_check()
        try:
            result = llm.chat(
                messages,
                response_format=effective_rf,
                timeout=timeout,
                retry_policy=effective_retry_policy,
            )
        except Exception as exc:
            if _looks_like_response_format_error(exc) and effective_rf == "json":
                safe_error = _sanitize_error_message(exc)
                LOGGER.warning(
                    "JSON response_format rejected at stage=%s (%s); retrying without format",
                    stage, safe_error,
                )
                if cancel_check:
                    cancel_check()
                result = llm.chat(
                    messages,
                    response_format=None,
                    timeout=timeout,
                    retry_policy=effective_retry_policy,
                )
                _log_llm_call(stage, _t0, result, None)
                return result
            _log_llm_call(stage, _t0, None, exc)
            raise
        _log_llm_call(stage, _t0, result, None)
        if not _llm_content_text(result).strip() and effective_rf == "json":
            raw = result.get("raw") if isinstance(result, dict) else None
            LOGGER.warning(
                "Empty content with json response_format at stage=%s; retrying without format. "
                "raw_keys=%s, stop_reason=%s",
                stage,
                list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__,
                raw.get("stop_reason") if isinstance(raw, dict) else None,
            )
            _record_json_empty_content(breaker_key)
            if cancel_check:
                cancel_check()
            result = llm.chat(
                messages,
                response_format=None,
                timeout=timeout,
                retry_policy=effective_retry_policy,
            )
            _log_llm_call(stage, _t0, result, None)
        else:
            _reset_json_empty_content_breaker(breaker_key)
        return result
    try:
        if cancel_check:
            cancel_check()
        result = llm.chat(
            messages,
            response_format=response_format,
            timeout=timeout,
            retry_policy=effective_retry_policy,
        )
        _log_llm_call(stage, _t0, result, None)
        _reset_json_empty_content_breaker(breaker_key)
        return result
    except Exception as exc:
        if not _looks_like_response_format_error(exc):
            _log_llm_call(stage, _t0, None, exc)
            raise
        safe_error = _sanitize_error_message(exc)
        LOGGER.warning(
            "Structured response_format rejected at stage=%s (%s); retrying with json mode",
            stage,
            safe_error,
        )
        if not _json_empty_content_circuit_allowed(breaker_key):
            LOGGER.warning(
                "Empty content circuit breaker active for key=%s at stage=%s; skipping json fallback",
                breaker_key, stage,
            )
            if cancel_check:
                cancel_check()
            result = llm.chat(
                messages,
                response_format=None,
                timeout=timeout,
                retry_policy=effective_retry_policy,
            )
            _log_llm_call(stage, _t0, result, None)
            return result
        if cancel_check:
            cancel_check()
        result = llm.chat(
            messages,
            response_format="json",
            timeout=timeout,
            retry_policy=effective_retry_policy,
        )
        _log_llm_call(stage, _t0, result, None)
        if not _llm_content_text(result).strip():
            raw = result.get("raw") if isinstance(result, dict) else None
            LOGGER.warning(
                "Empty content with json response_format at stage=%s (dict fallback path); retrying without format. "
                "raw_keys=%s, stop_reason=%s",
                stage,
                list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__,
                raw.get("stop_reason") if isinstance(raw, dict) else None,
            )
            _record_json_empty_content(breaker_key)
            if cancel_check:
                cancel_check()
            result = llm.chat(
                messages,
                response_format=None,
                timeout=timeout,
                retry_policy=effective_retry_policy,
            )
            _log_llm_call(stage, _t0, result, None)
        else:
            _reset_json_empty_content_breaker(breaker_key)
        return result


def _log_llm_call(stage: str, start: float, result: Any, error: Optional[BaseException]) -> None:
    latency = (time.monotonic() - start) * 1000
    tokens_in = 0
    tokens_out = 0
    if isinstance(result, dict):
        usage = result.get("usage") or {}
        if isinstance(usage, dict):
            tokens_in = int(usage.get("prompt_tokens") or 0)
            tokens_out = int(usage.get("completion_tokens") or 0)
    status = "error" if error else "ok"
    extra = {
        "llm_call_stage": stage,
        "latency_ms": round(latency, 1),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "status": status,
    }
    if error:
        extra["error"] = _sanitize_error_message(error)
    LOGGER.info("llm_call stage=%s latency=%.0fms tokens=%d+%d status=%s", stage, latency, tokens_in, tokens_out, status, extra=extra)


def _llm_content_text(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("content") or "")
    return str(result or "")


def _contains_cjk(text: str) -> bool:
    return bool(_HAS_CJK_RE.search(text or ""))


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _normalize_match_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = text.replace("_", " ").replace("-", " ").replace("/", " ")
    text = _strip_accents(text).lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


_FULLWIDTH_SQL_TRANSLATION = str.maketrans(
    {
        "，": ",",
        "、": ",",
        "；": ";",
        "（": "(",
        "）": ")",
        "。": ".",
        "：": ":",
        "＝": "=",
        "＜": "<",
        "＞": ">",
        "！": "!",
        "？": "?",
    }
)


def _normalize_sql_text(value: Any) -> str:
    sql = str(value or "")
    if not sql:
        return ""
    normalized: list[str] = []
    in_single = False
    in_double = False
    in_backtick = False
    in_bracket = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_single:
            normalized.append(ch)
            if ch == "'":
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    normalized.append(sql[i + 1])
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if in_double:
            normalized.append(ch)
            if ch == '"':
                if i + 1 < len(sql) and sql[i + 1] == '"':
                    normalized.append(sql[i + 1])
                    i += 2
                    continue
                in_double = False
            i += 1
            continue
        if in_backtick:
            normalized.append(ch)
            if ch == "`":
                if i + 1 < len(sql) and sql[i + 1] == "`":
                    normalized.append(sql[i + 1])
                    i += 2
                    continue
                in_backtick = False
            i += 1
            continue
        if in_bracket:
            normalized.append(ch)
            if ch == "]":
                in_bracket = False
            i += 1
            continue

        if ch == "'":
            in_single = True
            normalized.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            normalized.append(ch)
            i += 1
            continue
        if ch == "`":
            in_backtick = True
            normalized.append(ch)
            i += 1
            continue
        if ch == "[":
            in_bracket = True
            normalized.append(ch)
            i += 1
            continue

        normalized_ch = unicodedata.normalize("NFKC", ch).translate(_FULLWIDTH_SQL_TRANSLATION)
        normalized.append(normalized_ch)
        i += 1
    normalized_sql = "".join(normalized)
    if "\u3000" in normalized_sql:
        normalized_sql = normalized_sql.replace("\u3000", " ")
    return normalized_sql


def _normalize_sql_candidate(value: Any) -> str:
    sql = _normalize_sql_text(str(value or "").strip())
    if sql.endswith(";"):
        sql = sql[:-1].strip()
    return sql


_SQL_FENCE_RE = re.compile(
    r'```(?:sql)?\s*\n(.*?)```',
    re.DOTALL | re.IGNORECASE,
)
_SQL_BARE_RE = re.compile(
    r'((?:WITH\b|SELECT\b)[\s\S]*?)(?:;|\Z)',
    re.IGNORECASE,
)


def _extract_sql_from_raw_text(text: str) -> str:
    source = str(text or "").strip()
    if not source:
        return ""
    fence_match = _SQL_FENCE_RE.search(source)
    if fence_match:
        candidate = fence_match.group(1).strip()
        if candidate:
            return _normalize_sql_candidate(candidate)
    bare_match = _SQL_BARE_RE.search(source)
    if bare_match:
        candidate = bare_match.group(1).strip()
        if candidate and len(candidate) > 10:
            return _normalize_sql_candidate(candidate)
    return ""


def _build_sql_json_reask_prompt(previous_content: str) -> str:
    snippet = str(previous_content or "").strip()
    if len(snippet) > 2000:
        snippet = snippet[:2000]
    return (
        "Your previous response was not valid JSON for SQL generation.\n"
        "Rewrite it as EXACTLY one JSON object with keys: sql, summary, reasoning.\n"
        "Rules:\n"
        "- sql must be one read-only SELECT/WITH query string.\n"
        "- summary must be concise.\n"
        "- reasoning must be concise.\n"
        "- Do not output markdown fences.\n"
        "- Do not output extra prose outside JSON.\n"
        "Previous response:\n"
        f"{snippet}"
    )


_DOTTED_ALIAS_TOKEN_RE = re.compile(
    r"(?i)\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b"
)


def _rewrite_dotted_alias_tokens(sql: str) -> str:
    normalized_sql = _normalize_sql_candidate(sql)
    if not normalized_sql:
        return normalized_sql
    rewritten = _DOTTED_ALIAS_TOKEN_RE.sub(
        lambda match: f"AS {match.group(1)}_{match.group(2)}",
        normalized_sql,
    )
    return _normalize_sql_candidate(rewritten)


def _prune_unreferenced_ctes(sql: str) -> str:
    normalized_sql = _normalize_sql_candidate(sql)
    if not normalized_sql:
        return normalized_sql
    try:
        parsed = sqlglot.parse_one(_normalize_sql_text(normalized_sql), read="duckdb")
    except Exception:
        return normalized_sql

    with_clause = parsed.args.get("with_")
    if not isinstance(with_clause, exp.With):
        return normalized_sql
    cte_expressions = list(with_clause.expressions or [])
    if not cte_expressions or any(not isinstance(item, exp.CTE) for item in cte_expressions):
        return normalized_sql

    cte_items: list[tuple[str, exp.CTE]] = []
    for cte_item in cte_expressions:
        cte_name = str(cte_item.alias_or_name or "").strip().lower()
        if not cte_name:
            return normalized_sql
        cte_items.append((cte_name, cte_item))
    cte_name_set = {cte_name for cte_name, _ in cte_items}

    main_query = parsed.copy()
    main_query.set("with_", None)
    required_cte_names = {
        str(table_ref.name or "").strip().lower()
        for table_ref in main_query.find_all(exp.Table)
        if str(table_ref.name or "").strip().lower() in cte_name_set
    }

    changed = True
    while changed:
        changed = False
        for cte_name, cte_item in cte_items:
            if cte_name not in required_cte_names:
                continue
            for table_ref in cte_item.this.find_all(exp.Table):
                ref_name = str(table_ref.name or "").strip().lower()
                if ref_name in cte_name_set and ref_name not in required_cte_names:
                    required_cte_names.add(ref_name)
                    changed = True

    if len(required_cte_names) == len(cte_items):
        return normalized_sql

    kept_ctes = [cte_item for cte_name, cte_item in cte_items if cte_name in required_cte_names]
    if kept_ctes:
        with_clause.set("expressions", kept_ctes)
        parsed.set("with_", with_clause)
    else:
        parsed.set("with_", None)
    return _normalize_sql_candidate(parsed.sql(dialect="duckdb"))


def _local_sql_repair_preflight(
    failed_sql: str,
    error: str,
    project_id: int,
) -> tuple[str | None, list[str]]:
    candidate_sql = _normalize_sql_candidate(failed_sql)
    if not candidate_sql:
        return None, []

    error_text = str(error or "").strip().lower()
    stages: list[str] = []
    syntax_markers = (
        "parser error",
        "syntax error",
        "unexpected token",
        "failed to parse",
        "near \".\"",
    )
    orphan_markers = (
        "cte(s) defined but never referenced",
        "orphaned cte",
        "orphan cte",
        "never referenced",
    )

    if any(marker in error_text for marker in syntax_markers):
        rewritten_alias_sql = _rewrite_dotted_alias_tokens(candidate_sql)
        if rewritten_alias_sql != candidate_sql:
            candidate_sql = rewritten_alias_sql
            stages.append("alias_dot")
        auto_completed_sql = _auto_complete_single_cte_main_select(candidate_sql)
        if auto_completed_sql != candidate_sql:
            candidate_sql = auto_completed_sql
            stages.append("single_cte")

    if any(marker in error_text for marker in orphan_markers):
        pruned_sql = _prune_unreferenced_ctes(candidate_sql)
        if pruned_sql != candidate_sql:
            candidate_sql = pruned_sql
            stages.append("orphan_cte")

    if not stages:
        return None, []

    syntax_issues = _validate_sql_syntax_for_project(candidate_sql, project_id)
    orphan_issues = _validate_no_orphaned_cte(candidate_sql)
    if syntax_issues or orphan_issues:
        return None, []
    return candidate_sql, stages


def _rewrite_bracket_identifiers_for_duckdb(sql: str) -> str:
    text = str(sql or "")
    if "[" not in text or "]" not in text:
        return text
    normalized: list[str] = []
    in_single = False
    in_double = False
    in_backtick = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_single:
            normalized.append(ch)
            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    normalized.append(text[i + 1])
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if in_double:
            normalized.append(ch)
            if ch == '"':
                if i + 1 < len(text) and text[i + 1] == '"':
                    normalized.append(text[i + 1])
                    i += 2
                    continue
                in_double = False
            i += 1
            continue
        if in_backtick:
            normalized.append(ch)
            if ch == "`":
                if i + 1 < len(text) and text[i + 1] == "`":
                    normalized.append(text[i + 1])
                    i += 2
                    continue
                in_backtick = False
            i += 1
            continue

        if ch == "'":
            in_single = True
            normalized.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            normalized.append(ch)
            i += 1
            continue
        if ch == "`":
            in_backtick = True
            normalized.append(ch)
            i += 1
            continue
        if ch == "[":
            end = i + 1
            while end < len(text) and text[end] != "]":
                end += 1
            if end < len(text):
                token = text[i + 1 : end]
                if token.strip():
                    escaped_token = token.replace('"', '""')
                    normalized.append(f'"{escaped_token}"')
                    i = end + 1
                    continue
            normalized.append(ch)
            i += 1
            continue

        normalized.append(ch)
        i += 1
    return "".join(normalized)


def _truncate_question(question: Any, max_length: int = 4000) -> str:
    q = str(question or "")[:max_length]
    return q

def _sanitize_list(items: Any, max_per_item: int = 200, max_items: int = 3) -> list[str]:
    if not items:
        return []
    return [str(x)[:max_per_item] for x in list(items)[-max_items:]]

def _sanitize_error_message(value: Any, max_length: int = 320) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown error"
    redacted = re.sub(
        r"(?i)(password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key|client[_-]?secret|private[_-]?key)\s*(=|:)\s*[^,;\s]+",
        r"\1\2[REDACTED]",
        text,
    )
    redacted = re.sub(
        r"(?i)([?&](?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key|client[_-]?secret)=)[^&\s]+",
        r"\1[REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)(://[^:/\s]+:)[^@/\s]+@",
        r"\1[REDACTED]@",
        redacted,
    )
    redacted = re.sub(
        r"(?i)\bbearer\s+[a-z0-9._~+/=-]+",
        "Bearer [REDACTED]",
        redacted,
    )
    if len(redacted) > max_length:
        redacted = redacted[:max_length].rstrip() + "..."
    return redacted


def _extract_duplicate_alias_name(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    patterns = (
        r"alias\s+already\s+used:\s*([A-Za-z_][\w$]*)",
        r"duplicate\s+alias\s+[\"`]?([A-Za-z_][\w$]*)[\"`]?")
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return str(match.group(1) or "")
    return ""


def _sql_log_snippet(sql: Any, max_length: int = 260) -> str:
    normalized = _normalize_sql_text(sql)
    one_line = re.sub(r"\s+", " ", str(normalized or "").strip())
    if not one_line:
        return ""
    if len(one_line) > max_length:
        return one_line[:max_length].rstrip() + "..."
    return one_line


def _identifier_markers(value: Any) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    cleaned = re.sub(r"[`\"\[\]]", "", text).strip()
    if not cleaned:
        return set()
    variants = {
        cleaned.lower(),
        cleaned.replace(" ", "_").replace("-", "_").lower(),
        _normalize_match_text(cleaned),
    }
    if "." in cleaned:
        tail = cleaned.split(".")[-1].strip()
        if tail:
            variants.add(tail.lower())
            variants.add(tail.replace(" ", "_").replace("-", "_").lower())
            variants.add(_normalize_match_text(tail))
    return {item for item in variants if item}


def _collect_aliases(*values: Any) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()
    for value in values:
        raw = str(value or "").strip()
        if not raw:
            continue
        candidates = {raw.lower(), _normalize_match_text(raw)}
        for candidate in candidates:
            normalized = (candidate or "").strip().lower()
            if len(normalized) < 2:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            aliases.append(normalized)
    return aliases


_COLUMN_NAME_ALIAS_HINTS: dict[str, tuple[str, ...]] = {
    "product_id": ("产品",),
    "product_category_name": ("产品", "产品类别", "产品类目"),
    "product_category_name_english": ("产品", "产品类别", "产品类目"),
    "customer_city": ("城市", "客户城市"),
    "seller_city": ("城市", "卖家城市"),
    "dept_name": ("部门", "部门名称"),
    "department_name": ("部门", "部门名称"),
    "department": ("部门",),
    "title": ("岗位", "职位", "工作岗位", "职称"),
    "job_title": ("岗位", "职位", "工作岗位", "职称"),
    "position": ("岗位", "职位"),
    "role": ("岗位", "角色"),
    "city": ("城市",),
}

_COLUMN_TOKEN_ALIAS_HINTS: dict[str, tuple[str, ...]] = {
    "product": ("产品",),
    "category": ("类别", "类目"),
    "city": ("城市",),
    "state": ("州", "省份"),
    "country": ("国家",),
    "region": ("地区",),
    "department": ("部门",),
    "dept": ("部门",),
    "title": ("岗位", "职位", "工作岗位", "职称"),
    "job": ("岗位", "职位", "工作岗位"),
    "position": ("岗位", "职位"),
    "role": ("岗位", "角色"),
    "customer": ("客户",),
    "seller": ("卖家",),
    "employee": ("员工", "雇员"),
    "order": ("订单",),
    "sales": ("销售", "销售额"),
    "revenue": ("收入", "销售额"),
    "amount": ("金额",),
    "price": ("价格",),
    "count": ("数量",),
    "quantity": ("数量", "销量"),
}


def _fallback_column_aliases(column_name: Any) -> list[str]:
    normalized = _normalize_match_text(column_name)
    if not normalized:
        return []
    compact = normalized.replace(" ", "_")
    tokens = [token for token in re.split(r"[\s_]+", compact) if token]
    fallback_values: list[str] = []

    for item in _COLUMN_NAME_ALIAS_HINTS.get(compact, ()):
        fallback_values.append(item)

    for token in tokens:
        for item in _COLUMN_TOKEN_ALIAS_HINTS.get(token, ()):
            fallback_values.append(item)

    token_set = set(tokens)
    if {"product", "category"}.issubset(token_set):
        fallback_values.extend(["产品类别", "产品类目", "产品"])
    if "city" in token_set:
        if "customer" in token_set:
            fallback_values.append("客户城市")
        if "seller" in token_set:
            fallback_values.append("卖家城市")
        fallback_values.append("城市")
    if "state" in token_set and "seller" in token_set:
        fallback_values.append("卖家州")
    if "order" in token_set and "count" in token_set:
        fallback_values.append("订单量")
    if "dept" in token_set or "department" in token_set:
        fallback_values.extend(["部门", "部门名称"])
    if "title" in token_set or "job" in token_set or "position" in token_set:
        fallback_values.extend(["岗位", "职位", "工作岗位"])
    if {"job", "title"}.issubset(token_set):
        fallback_values.append("工作岗位")
    if "role" in token_set:
        fallback_values.extend(["岗位", "角色"])

    deduped: list[str] = []
    seen: set[str] = set()
    for value in fallback_values:
        normalized_value = str(value or "").strip()
        if len(normalized_value) < 2:
            continue
        key = normalized_value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized_value)
    return deduped


def _column_alias_strings(column: dict[str, Any]) -> list[str]:
    explicit_aliases = [item for item in (column.get("aliases") or []) if isinstance(item, str)]
    fallback_aliases = _fallback_column_aliases(column.get("name"))
    return _collect_aliases(
        column.get("name"),
        column.get("display_name") or column.get("displayName"),
        column.get("description"),
        *explicit_aliases,
        *fallback_aliases,
    )


def _english_token_variants(token: str) -> set[str]:
    variants = {token}
    if token.endswith("ies") and len(token) > 4:
        variants.add(token[:-3] + "y")
    if token.endswith("es") and len(token) > 4:
        variants.add(token[:-2])
    if token.endswith("s") and len(token) > 3:
        variants.add(token[:-1])
    return variants





@lru_cache(maxsize=4096)
def _tokenize_cached(text: str) -> tuple[str, ...]:
    raw_text = str(text or "")
    normalized_text = _normalize_match_text(raw_text)
    if not raw_text and not normalized_text:
        return tuple()

    tokens: set[str] = set()
    for token in _EN_TOKEN_RE.findall(normalized_text):
        if len(token) <= 1:
            continue
        tokens.update(_english_token_variants(token))
    for token in _CJK_TOKEN_RE.findall(raw_text):
        if len(token) <= 1:
            continue
        tokens.add(token)

    filtered = {
        token
        for token in tokens
        if token and (len(token) > 1 or _contains_cjk(token))
    }
    return tuple(sorted(filtered))


def _safe_json_loads(value: Any, fallback: Any):
    if is_encrypted_value(value):
        decoded = decrypt_json(value, fallback)
        return decoded if decoded is not None else fallback
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, bool):
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return fallback
    return fallback


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=str)


def _import_optional(module_name: str):
    try:
        return importlib.import_module(module_name)
    except Exception:
        return None


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "require", "required"}


def _language_name(language: Optional[str]) -> str:
    if not language:
        return ""
    normalized = str(language).strip().lower().replace("_", "-")
    return LANGUAGE_NAMES.get(normalized) or str(language).strip()


def _language_instruction(language: Optional[str]) -> str:
    name = _language_name(language)
    return f"Respond in {name}." if name else "Respect the user's language when possible."


def _normalize_language(language: Optional[str] = None, question: str = "") -> str:
    normalized = str(language or "").strip().lower().replace("_", "-") if language else ""
    if normalized and normalized in LANGUAGE_NAMES:
        return normalized
    if normalized:
        base = normalized.split("-")[0]
        if base in LANGUAGE_NAMES:
            return base
    if _contains_cjk(question or ""):
        return "zh"
    return "en"


def _llm_not_configured_error(language: Optional[str] = None) -> dict[str, Any]:
    return {"content": _sql_msg("llm_not_configured", language), "configured": False, "latency_ms": None}


_SQL_MSG: dict[str, tuple[str, str]] = {
    "gen_context_failed": ("生成 SQL 的上下文不可用。", 'Failed to prepare SQL generation context.'),
    "context_unavailable": ("生成上下文不可用。", 'Generation context is unavailable.'),
    "llm_no_valid_sql": ("LLM 在多次尝试后未返回有效的 SQL。", 'LLM did not return valid SQL after retries.'),
    "repaired_orphan_cte": ("修复 SQL：删除了孤立的 CTE。", 'Repaired SQL: removed orphaned CTE.'),
    "repaired_orphan_cte_best": ("已修复 SQL（孤立 CTE，尽力而为）。", 'Repaired SQL after orphan CTE (best effort).'),
    "repaired_syntax": ("已修复 SQL（语法验证）。", 'Repaired SQL after syntax validation.'),
    "auto_corrected_column_refs": ("SQL 自动修正了列引用。", 'SQL auto-corrected column references.'),
    "auto_corrected_alias_scope": ("SQL 自动修正了别名作用域引用。", 'SQL auto-corrected alias scope references.'),
    "auto_corrected_metric_cols": ("SQL 自动修正了未解析的指标列。", 'SQL auto-corrected unresolved metric columns from available schema columns.'),
    "auto_corrected_validation": ("多次验证失败后 SQL 自动修正。", 'SQL auto-corrected after repeated validation failures.'),
    "validation_circuit_open": ("SQL 验证反复失败，修复断路器已断开。", 'SQL validation failed repeatedly; repair circuit opened.'),
    "repaired_column_validation": ("已修复 SQL（列验证后）。", 'Repaired SQL after column validation.'),
    "repaired_column_best": ("已修复 SQL（列验证，尽力而为）。", 'Repaired SQL after column validation (best effort).'),
    "circuit_breaker_open": ("LLM 服务暂时不可用（断路器断开）。", 'LLM service is temporarily unavailable (circuit breaker open).'),
    "llm_response_not_parsed": ("LLM 响应在多次尝试后无法解析为有效 JSON。", 'LLM response could not be parsed as valid JSON after retries.'),
    "failed_generate_sql": ("多次尝试后未能生成有效 SQL。", 'Failed to generate valid SQL after retries.'),
    "repaired_sql": ("已为你的问题修复 SQL。", 'Repaired SQL for your question.'),
    "fix_sql_rules": ("修复 SQL 以正确执行。请遵循以下规则：\n", "Fix the SQL to execute correctly. Follow these rules:\n"),
    "repair_failed": ("修复失败", "Repair failed"),
    "llm_call_error": ("LLM 调用错误。", 'LLM call error.'),
    "failed_generate_sql_short": ("生成 SQL 失败。", 'Failed to generate SQL.'),
    "returned_query_results": ("查询已完成。", 'Returned query results.'),
    "answer_helpful_assistant": ("以有帮助的助手身份回答。不要返回 SQL、JSON、代码围栏或查询计划。", "Answer as a helpful assistant. Do not return SQL, JSON, code fences, or query plans."),
    "no_project_metadata_match": ("没有匹配到问题的项目模型、字段或关系元数据。", "No project model, field, or relation metadata matched the question."),
    "routing_skipped_llm_unconfigured": ("匹配到项目元数据；由于 LLM 未配置，跳过了 LLM 路由。", "Matched project metadata; LLM routing was skipped because the provider is not configured."),
    "llm_syntax_errors_after_retries": ("LLM 在多次重试后返回了包含语法错误的 SQL。", "LLM returned SQL with syntax errors after retries."),
    "supplemental_llm_not_configured": ("LLM 未配置，无法生成补充回答。", 'LLM provider is not configured for supplemental answer.'),
    "llm_analyzer_fallback": ("LLM 分析器跳过或不可用；使用简单回退。", "LLM analyzer skipped or unavailable; using simple fallback."),
    "route_no_metadata_match": ("没有匹配到问题的项目模型、字段或关系元数据。", "No project model, field, or relation metadata matched the question."),
    "route_llm_not_configured": ("匹配到项目元数据；由于 LLM 未配置，跳过了 LLM 路由。", "Matched project metadata; LLM routing was skipped because the provider is not configured."),
    "route_classification_failed": ("路由分类失败（{}）；因项目元数据已匹配，默认走 SQL 路径。", "Route classification failed ({}); defaulted to SQL path because project metadata was matched."),
    "route_default_answer_path": ("匹配到项目元数据，已路由到回答路径。", "Matched project metadata and routed the answer path."),
    "route_clause_separation": ("子句级路由分离了 SQL 相关子句和普通子句。", "Clause-level routing separated SQL-focused and general clauses."),
    # _basic_result_summary
    "result_summary_returned": ("查询已完成，返回", "Query completed and returned"),
    "result_summary_no_columns": ("查询已完成，但没有返回可展示的列。", "Query completed, but no displayable columns were returned."),
    "result_summary_no_rows": ("查询已完成，但结果为空。", "Query completed, but the result is empty."),
    "result_summary_warning": ("提示", "Warning"),
    "result_summary_rows": ("行结果", "rows"),
    "result_summary_first": ("前", "First"),
    "result_summary_rows_short": ("行", "rows"),
    # _generic_result_answer
    "generic_takeaway": ("### 结论", "### Takeaway"),
    "generic_preview_fmt": ("预览结果包含 **{}** 行、**{}** 个字段。", "The preview contains **{}** rows and **{}** columns."),
    "generic_key_metrics": ("### 关键指标", "### Key Metrics"),
    "generic_metrics_header": ("| 字段 | 合计 | 平均 | 最大值 |", "| Field | Total | Average | Max |"),
    "generic_data_insights": ("### 数据洞察", "### Data Insights"),
    "generic_all_rows": ("### 全部结果", "### All Rows"),
    "generic_preview": ("### 代表性预览", "### Representative Preview"),
    "generic_truncated_fmt": ("每行仅展示前 {} 个字段，其余字段可在 Result 视图查看。", "Each row shows only the first {} columns; the rest are available in the Result view."),
    "generic_warning_label": ("提示:", "Warning:"),
    "generic_subq_note_fmt": ("**注意**: 原问题包含 {} 个子问题，以上数据可能仅覆盖部分子问题。", "**Note**: The original question has {} sub-questions; the above data may only cover some of them."),
    "generic_result_hint": ("更多数据明细和可视化信息可在 Result 数据视图和图表中查看。", "More row-level details and visual information are available in the Result data view and charts."),
    # _generate_data_insights
    "insight_wide_spread_fmt": ("**{}** 的最大值 ({}) 约是最小值 ({}) 的 **{}** 倍，差异显著。", "**{}** has a wide spread: max ({}) is **{}**x the min ({})."),
    "insight_top_group_fmt": ("**{}** 的 {} 合计 ({}) 占总量的 **{:.1f}%**。", "**{}** accounts for **{:.1f}%** of total {} ({})."),
    "insight_gap_fmt": ("{} 中，**{}** ({}) 与 **{}** ({}) 的 {} 差异达 **{}** 倍。", "The gap between **{}** ({}) and **{}** ({}) in {} is **{}**x."),
    "insight_correlation_fmt": ("**{}** 与 **{}** 呈强正相关 (r={:.2f})，一方升高时另一方也倾向于升高。", "**{}** and **{}** are strongly correlated (r={:.2f}) — when one rises, the other tends to rise as well."),
    # ask_question intentReasoning
    "intent_empty_project": ("仅在无项目上下文中使用系统和用户提示进行回答。", "Answered with system and user prompts only because no non-empty project context is active."),
    "intent_general_chat": ("识别为项目内普通对话，未尝试 SQL 执行。", "Classified as project-scoped general chat; no SQL execution was attempted."),
    "intent_no_metadata": ("未找到匹配的项目元数据，未使用 SQL 回答。", "No matching project metadata was found; answered without SQL."),
    "intent_sql_failed_llm_fallback": ("匹配到项目元数据，但 SQL 生成失败，由 LLM 回答。", "Matched project metadata but SQL generation failed; answered with LLM."),
    "intent_sql_failed": ("匹配到项目元数据，但 SQL 生成失败。", "Matched project metadata but SQL generation failed."),
    "intent_sql_ok": ("匹配到项目元数据，已将数据部分路由到 SQL。", "Matched project metadata and routed the data-backed part to SQL."),
    "intent_exec_attempt": ("匹配到项目元数据，已尝试 SQL 执行。", "Matched project metadata and attempted SQL execution."),
    "intent_metadata_to_sql_failed": ("元数据到 SQL 失败，使用项目上下文作为普通 LLM 响应回答。", "Metadata-to-SQL failed; answered with project context as a normal LLM response."),
    # ask_question description
    "desc_empty_project": ("PrismBI 仅在无项目上下文中使用系统和用户提示。", "PrismBI used the system prompt and user prompt only because no non-empty project context is active."),
    "desc_no_metadata": ("没有匹配到此问题的项目元数据，PrismBI 使用 LLM 回答，跳过了 SQL 生成。", "No project metadata matched this question, so PrismBI answered with the LLM and skipped SQL generation."),
    "desc_sql_failed_retries": ("SQL 生成多次尝试后失败；PrismBI 回退到通用 LLM 回答。", "SQL generation failed after retries; PrismBI fell back to a general LLM answer."),
    "desc_sql_failed_retries_short": ("SQL 生成多次尝试后失败。", "SQL generation failed after retries."),
    "desc_sql_exec_ok": ("匹配元数据后通过 SQL 执行回答；未匹配部分由 LLM 在组织最终答案前完成。", "Matched metadata was answered through SQL execution; unmatched question text was completed by the LLM before composing the final answer."),
    "desc_sql_full_ok": ("匹配元数据，生成 SQL，应用安全策略，执行查询，并总结结果。", "Matched metadata, generated SQL, applied security policy planning, executed the query, and summarized the result."),
    "desc_sql_exec_failed": ("SQL 生成成功但执行失败。响应保留在 SQL 视图中，用户可调整后重新运行。", "SQL generation succeeded but execution failed. The response remains in SQL view so the user can adjust and rerun it."),
    "desc_sql_or_exec_failed": ("SQL 生成或执行失败，PrismBI 回退到项目上下文 LLM 回答。", "SQL generation or execution failed, then PrismBI fell back to a project-context LLM answer."),
    # error messages
    "err_unable_generate_sql": ("无法生成有效的 SQL 查询，请尝试重新描述您的问题。", "Unable to generate a valid SQL query. Please try rephrasing your question."),
    "err_sql_exec_failed": ("SQL 执行失败，请在 SQL 视图中调整后重新运行。", "SQL execution failed. Adjust it in SQL view and run it again."),
    # transpilation
    "transpile_unavailable_fmt": ("SQL 转换为 {} 不可用（未安装 sqlglot），使用 DuckDB SQL 作为回退。", "SQL transpilation to {} is unavailable because sqlglot is not installed; using DuckDB SQL as fallback."),
    "transpile_failed_fmt": ("SQL 转换为 {} 失败；查询可能包含不兼容语法。错误: {}", "SQL transpilation to {} failed; query may contain incompatible syntax. Error: {}"),
    "transpile_empty_fmt": ("SQL 转换为 {} 无输出，使用 DuckDB SQL 作为回退。", "SQL transpilation to {} produced no output; using DuckDB SQL as fallback."),
    # ask_decompose
    "decompose_gen_sql": ("已为你的问题生成 SQL。", "Generated SQL for your question."),
    "decompose_single_subq": ("子问题已通过分解合并直接生成。", "Single sub-question generated directly via decompose-merge."),
    "decompose_merged_sql": ("已为你的复合问题合并 SQL。", "Merged SQL for your compound question."),
    "decompose_merge_fallback_fmt": ("合并 JSON 解析失败（{}）；从纯文本回退提取 SQL。", "Merge JSON parse failed ({}); extracted SQL from plain-text fallback."),
    "decompose_merged_count_fmt": ("已将 {} 个子查询合并到一个 SQL 中。", "Merged {} sub-queries into one SQL."),
    "decompose_merged_auto_correct": ("已为你的复合问题合并 SQL（自动修正列）。", "Merged SQL for your compound question (auto-corrected columns)."),
    # fallback ask capabilities
    "fallback_ask_capabilities": ("如果用户询问你能做什么，请描述上面列出的项目功能。", "If the user asks what you can do, describe the project capabilities listed above."),
    # llm_not_configured_error
    "llm_not_configured": ("LLM 服务未配置。请在「设置 > LLM」中进行配置。", "LLM provider is not configured. Please configure it in Settings > LLM."),
    # retrieve_detail / execution_detail
    "retrieve_detail_fmt": ("命中表: {}; 知识命中: {} 条指令, {} 条 SQL 示例", "Matched tables: {}; Knowledge hits: {} instructions, {} SQL pairs."),
    "execution_detail_fmt": ("返回 {} 行, 耗时 {} ms。", "Returned {} rows, took {} ms."),
    # _fallback_general_chat_text
    "fallback_chat_project_scoped": (
        "我是 PrismBI 助手。"
        "我可以回答通用问题，也可以基于当前项目数据帮你生成 SQL、执行查询并解释结果。"
        "你可以继续问我：例如\u201c按城市统计销售额\u201d。",
        "I am PrismBI assistant. I can answer general questions and also help with SQL generation, "
        "query execution, and result explanations for the current project data.",
    ),
    "fallback_chat_general": (
        "我是 PrismBI 助手。"
        "我可以进行通用问答；在你选择项目并连接数据后，"
        "还可以帮你生成 SQL、执行查询并解释结果。",
        "I am PrismBI assistant. I can answer general questions, and once a project with data is selected "
        "I can generate SQL, run queries, and explain results.",
    ),
    # _project_general_chat instruction
    "project_chat_instruction": (
        "作为一个有帮助的助手回答问题。不要返回 SQL、JSON、代码围栏或查询计划。"
        "如果用户问你能做什么，描述上面列出的项目能力。"
        "如果用户询问未匹配到元数据的实时项目数据，说明哪些数据可用并建议一个具体问题。"
        "使用上面的对话历史来理解上下文和追问。",
        "Answer as a helpful assistant. Do not return SQL, JSON, code fences, or query plans. "
        "If the user asks what you can do, describe the project capabilities listed above. "
        "If the user asks about real-time project data that does not match metadata, "
        "explain what data is available and suggest a specific question. "
        "Use the conversation history above to understand context and follow-ups.",
    ),
    # _render_project_general_context
    "project_context_prefix": ("项目", "Project"),
    "project_desc_prefix": ("描述", "Description"),
    "project_context_nosql": (
        "此项目上下文仅用于理解业务领域。不要在普通对话回复中生成 SQL、JSON、代码围栏或查询计划。",
        "This project context is for understanding the business domain only. "
        "Do not generate SQL, JSON, code fences, or query plans in general conversation replies.",
    ),
    "project_context_metadata": ("可用元数据上下文", "Available metadata context"),
    # _build_project_capabilities
    "capabilities_intro": (
        "PrismBI 可以用自然语言回答数据问题，并自动生成 SQL 查询。",
        "PrismBI can answer data questions in natural language and automatically generate SQL queries.",
    ),
    "capabilities_ask": (
        "您可以询问趋势、排名、比较、总计、平均值以及项目数据中的任何指标。",
        "You can ask about trends, rankings, comparisons, totals, averages, and any metrics in the project data.",
    ),
    "capabilities_results": (
        "结果以摘要回答、数据表格和交互式图表的形式呈现。",
        "Results are presented as summary answers, data tables, and interactive charts.",
    ),
    "capabilities_models_fmt": ("此项目有 {} 个语义模型连接到其数据源。", "This project has {} semantic models connected to its data sources."),
    "capabilities_datasources_fmt": ("此项目连接到 {} 个数据源。", "This project connects to {} data sources."),
    # _non_metadata_completion
    "non_metadata_instruction": (
        "仅回答用户请求中未匹配项目元数据的那部分。不要捏造实时项目数据或查询结果。"
        "如果用户的问题需要未匹配到元数据的实时项目数据，说明项目中有哪些可用数据并建议一个他们可以问的具体问题。"
        "用用户问题的同一语言回答。",
        "Answer only the part of the user's request that did not match project metadata. "
        "Do not fabricate real-time project data or query results. "
        "If the user's question requires real-time project data that did not match metadata, "
        "explain what data is available in the project and suggest a specific question they can ask. "
        "Answer in the same language as the user's question.",
    ),
    # _auto_thread_title / _syntax_failure_result
    "new_session": ("新会话", "New Session"),
    "syntax_failure_summary": ("LLM 在多次重试后返回了有语法错误的 SQL。", "LLM returned SQL with syntax errors after retries."),
    # progress callbacks
    "progress_organizing": (
        "正在组织查询计划…",
        "Organizing query plan…",
    ),
    # _emit_thread_progress
    "thread_progress_decompose_disabled": (
        "分解合并暂时禁用（先前失败），直接生成 SQL。",
        "Decompose-merge temporarily disabled (previous failure); generating directly.",
    ),
    "thread_progress_decompose_failed": (
        "分解合并失败，切换到直接生成 SQL。",
        "Decompose-merge failed, switching to direct generation.",
    ),
    # _guidance_prompt
    "guidance_prompt": (
        "用户的问题没有匹配到可用的项目元数据。你的任务是有帮助地引导用户：\n"
        "\n"
        "1) 简要说明该项目中有哪些数据主题和模型可用。\n"
        "   可用模型概览：\n{model_summary}\n"
        "\n"
        "2) 给出 2-3 个用户可以问的示例问题，使用实际的模型名和列名。\n"
        "   建议问题：\n{suggested_questions}\n"
        "\n"
        "3) 如果用户的措辞接近可用数据，展示命名映射关系。\n"
        "\n"
        "用友好、自然的方式组织回答——不要让它看起来像错误消息。",
        "The user's question did not match available project metadata. "
        "Your task is to guide them helpfully:\n"
        "1) Briefly explain what data topics and models are available in this project.\n"
        "   Available models overview:\n{model_summary}\n"
        "2) Suggest 2-3 example questions they could ask, using actual model and column names.\n"
        "   Suggested questions:\n{suggested_questions}\n"
        "3) If the user's wording is close to available data, show the naming mapping.\n"
        "Format your response helpfully and naturally — not as an error message.",
    ),
    # _project_general_chat / _fallback_answer_after_sql_failure labels
    "project_context_label": ("项目上下文：", "Project context: "),
    "capabilities_label": ("能力：", "Capabilities: "),
    "available_models_label": ("\n可用的项目模型：\n", "\nAvailable project models:\n"),
    "example_questions_label": ("你可以问的示例问题：\n", "Example questions you can ask:\n"),
    # few-shot header in ask_observability
    "few_shot_header": ("\n示例：\n", "\nExamples:\n"),
}


def _sql_msg(key: str, language: Optional[str] = None) -> str:
    cn, en = _SQL_MSG.get(key, ("", ""))
    return cn if _in_chinese(language) else en


def _build_sql_system_message(system_suffix: str, dialect_hint: str, language: Optional[str], *, extra_instructions: Optional[str] = None) -> str:
    parts = [
        f"{_render_system_prompt()}{system_suffix}\n\n{_formatted_sql_contract(dialect_hint, language)}",
    ]
    if extra_instructions:
        parts.append(extra_instructions)
    parts.append(_language_instruction(language))
    return "\n".join(parts)


def _build_retry_user_message(base_msg: str, errors: list[str], attempt: int, max_attempts: int) -> str:
    if not errors or attempt < 1:
        return base_msg
    error_text = "\n".join(f"- {e}" for e in errors)
    return f"{base_msg}\n\nPrevious attempt {attempt}/{max_attempts} issues:\n{error_text}\nFix these issues to produce valid SQL:" if error_text else base_msg


def _in_chinese(language: Optional[str]) -> bool:
    return _normalize_language(language).startswith("zh")


def _step_title(key: str, language: Optional[str] = None) -> str:
    titles: dict[str, tuple[str, str]] = {
        "understand": ("理解问题", "Understand Question"),
        "retrieve": ("检索语义模型", "Retrieve Semantic Model"),
        "organize": ("组织查询思路", "Organize Query Plan"),
        "execute": ("执行查询", "Execute Query"),
        "answer": ("生成回答", "Generate Answer"),
    }
    cn, en = titles.get(key, (key, key))
    return cn if _in_chinese(language) else en


def _step_detail_text(key: str, language: Optional[str] = None) -> str:
    details: dict[str, tuple[str, str]] = {
        "no_metadata_hit": ("没有命中可用于 SQL 的项目模型、字段或关系。", "No project models, fields, or relationships matched for SQL generation."),
        "empty_project_or_no_project": ("识别为无项目或空项目上下文的普通对话。", "Identified as a general conversation without project context."),
        "in_project_general": ("识别为项目内普通对话。", "Identified as a general conversation within the project."),
        "analyzing_question": ("正在分析用户问题。", "Analyzing the user's question."),
        "semantic_linking": ("正在将问题术语映射到元数据。", "Mapping question terms to metadata."),
        "routing_question": ("正在判断是否需要 SQL 查询。", "Determining if SQL query is needed."),
        "generating_sql": ("正在生成 SQL 查询。", "Generating SQL query."),
        "repairing_sql": ("正在修复 SQL 错误。", "Repairing SQL errors."),
        "executing_sql": ("正在执行 SQL 查询。", "Executing SQL query."),
        "generating_answer": ("正在生成最终回答。", "Generating final answer."),
    }
    cn, en = details.get(key, (key, key))
    return cn if _in_chinese(language) else en


def _retrieve_detail(generated: dict, route: dict, language: Optional[str] = None) -> str:
    tables = ', '.join(generated.get('retrieved_tables', [])) or 'none'
    instructions_count = len((route.get('knowledge_hits') or {}).get('instructions') or [])
    sql_pairs_count = len((route.get('knowledge_hits') or {}).get('sql_pairs') or [])
    return _sql_msg("retrieve_detail_fmt", language).format(tables, instructions_count, sql_pairs_count)


def _execution_detail(query_result: dict, language: Optional[str] = None) -> str:
    rows = query_result.get('total_rows', 0)
    ms = query_result.get('execution_time_ms', 0)
    return _sql_msg("execution_detail_fmt", language).format(rows, ms)


_ALLOWED_ID_TABLES = frozenset({
    "metadata.threads",
    "metadata.thread_responses",
    "metadata.sql_generation_failures",
})


def _max_id(con, table: str) -> int:
    if table not in _ALLOWED_ID_TABLES:
        raise ValueError(f"Unknown table for ID generation: {table}")
    con.execute("INSERT INTO metadata.id_sequences VALUES (?, COALESCE((SELECT MAX(id) FROM %s), 0)) ON CONFLICT DO NOTHING" % table, [table])
    existing = con.execute("SELECT next_id FROM metadata.id_sequences WHERE table_name = ?", [table]).fetchone()
    if existing and existing[0] <= 1:
        max_existing = con.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0]
        if max_existing > 0:
            con.execute("UPDATE metadata.id_sequences SET next_id = ? WHERE table_name = ?", [max_existing, table])
    return con.execute("UPDATE metadata.id_sequences SET next_id = next_id + 1 WHERE table_name = ? RETURNING next_id", [table]).fetchone()[0]


_thread_progress: threading.local = threading.local()


def _set_thread_progress(cb: Any) -> None:
    _thread_progress.cb = cb


def _clear_thread_progress() -> None:
    _thread_progress.cb = None


def _emit_thread_progress(step_key: str, detail: str) -> None:
    cb = getattr(_thread_progress, 'cb', None)
    if cb is not None:
        cb(step_key, detail)


def get_user_default_project_id(user_id: int) -> Optional[int]:
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT default_project_id FROM metadata.users WHERE id = ?",
            [user_id],
        ).fetchone()
        if row and row[0] is not None:
            return int(row[0])
        row = con.execute("SELECT id FROM metadata.projects WHERE is_current = true ORDER BY id LIMIT 1").fetchone()
        return int(row[0]) if row else None


def _settings_map() -> dict[str, Any]:
    with connection_lock():
        con = get_connection()
        rows = con.execute("SELECT key, value FROM metadata.settings").fetchall()
        return {row[0]: _safe_json_loads(row[1], row[1]) for row in rows}


def _project_meta(project_id: int) -> Optional[dict[str, Any]]:
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT id, name, display_name, description, prompt, type, language, sample_dataset FROM metadata.projects WHERE id = ?",
            [project_id],
        ).fetchone()
        if not row:
            return None
        datasource_count = con.execute(
            "SELECT COUNT(*) FROM metadata.project_datasources WHERE project_id = ?",
            [project_id],
        ).fetchone()[0]
        model_count = con.execute(
            "SELECT COUNT(*) FROM metadata.models WHERE project_id = ?",
            [project_id],
        ).fetchone()[0]
        return {
            "id": row[0],
            "name": row[1],
            "display_name": row[2] or row[1],
            "description": row[3] or "",
            "prompt": row[4] or DEFAULT_PROJECT_PROMPT,
            "type": row[5] or "",
            "language": row[6] or "EN",
            "sample_dataset": row[7] or "",
            "datasource_count": int(datasource_count or 0),
            "model_count": int(model_count or 0),
        }


def _project_has_context(project_id: int) -> bool:
    meta = _project_meta(project_id)
    return bool(meta and (meta["datasource_count"] > 0 or meta["model_count"] > 0))


def _render_system_prompt(settings: Optional[dict[str, Any]] = None) -> str:
    data = settings or _settings_map()
    template = data.get("llm_system_prompt") or DEFAULT_SYSTEM_PROMPT
    def _truncate(v: Any, max_len: int = 200) -> Any:
        if isinstance(v, str) and len(v) > max_len:
            return v[:max_len]
        return v

    variables = common_prompt_variables({
        "app_name": _truncate(data.get("app_name") or "PrismBI"),
        "language": _truncate(data.get("language") or "en", 50),
        "timezone": _truncate(data.get("timezone") or "UTC", 50),
        "date_format": _truncate(data.get("date_format") or "YYYY-MM-DD", 50),
        "llm_provider": _truncate(data.get("llm_provider") or ""),
        "llm_model": _truncate(data.get("llm_model") or ""),
        "llm_endpoint": _truncate(data.get("llm_endpoint") or ""),
    })
    return render_prompt_template(str(template), variables) or DEFAULT_SYSTEM_PROMPT


def _render_project_prompt(project_id: int, semantic_context: str, sql_examples: str = "") -> str:
    meta = _project_meta(project_id) or {}
    variables = common_prompt_variables({
        "project_id": meta.get("id") or project_id,
        "name": meta.get("name") or "",
        "display_name": meta.get("display_name") or meta.get("name") or "",
        "description": meta.get("description") or "",
        "type": meta.get("type") or "",
        "language": meta.get("language") or "EN",
        "sample_dataset": meta.get("sample_dataset") or "",
        "datasource_count": meta.get("datasource_count") or 0,
        "model_count": meta.get("model_count") or 0,
        "semantic_model": semantic_context,
        "sql_examples": sql_examples,
    })
    return render_prompt_template(str(meta.get("prompt") or DEFAULT_PROJECT_PROMPT), variables) or render_prompt_template(DEFAULT_PROJECT_PROMPT, variables)


def _render_project_general_context(project_id: int, semantic_context: str, language: Optional[str] = None) -> str:
    meta = _project_meta(project_id) or {}
    parts = [
        f"{_sql_msg('project_context_prefix', language)}: {meta.get('display_name') or meta.get('name') or project_id}",
        f"{_sql_msg('project_desc_prefix', language)}: {meta.get('description') or ''}",
        _sql_msg("project_context_nosql", language),
    ]
    if semantic_context:
        parts.append(f"{_sql_msg('project_context_metadata', language)}:\n{semantic_context}")
    return "\n\n".join(parts)


def _build_project_capabilities(project_id: int, language: Optional[str] = None) -> str:
    meta = _project_meta(project_id) or {}
    model_count = int(meta.get("model_count") or 0)
    datasource_count = int(meta.get("datasource_count") or 0)
    capabilities = [
        _sql_msg("capabilities_intro", language),
        _sql_msg("capabilities_ask", language),
        _sql_msg("capabilities_results", language),
    ]
    if model_count > 0:
        capabilities.append(_sql_msg("capabilities_models_fmt", language).format(model_count))
    if datasource_count > 0:
        capabilities.append(_sql_msg("capabilities_datasources_fmt", language).format(datasource_count))
    return " ".join(capabilities)


def _build_metadata_summary(project_id: int) -> dict:
    all_models = _models_for_project(project_id)
    relations = _relations_for_project(project_id)
    max_models = ROUTER_CONFIG.get("metadata_summary_max_models", 10)
    models = all_models[:max_models] if len(all_models) > max_models else all_models
    max_suggested = ROUTER_CONFIG.get("max_suggested_questions", 5)
    summary_lines = []
    suggested_questions = []
    for model in models:
        cols = model.get("columns", [])
        dname = model.get("display_name") or model["name"]
        metric_cols = [c["name"] for c in cols if c.get("type", "").upper() in {"INTEGER", "BIGINT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC"}]
        dim_cols = [c["name"] for c in cols if c.get("type", "").upper() in {"VARCHAR", "TEXT", "DATE", "TIMESTAMP"}]
        summary_lines.append(f"- {dname}: {len(cols)} columns")
        if metric_cols and dim_cols:
            suggested_questions.append(
                f"Query {dname} {metric_cols[0]} grouped by {dim_cols[0]}"
            )
        elif metric_cols:
            suggested_questions.append(f"Count {metric_cols[0]} for {dname}")
    return {
        "summary": "\n".join(summary_lines),
        "suggested_questions": suggested_questions[:max_suggested],
        "models_count": len(all_models),
        "relations_count": len(relations),
    }


_analysis_cache: OrderedDict[str, tuple[dict, float]] = OrderedDict()
_analysis_cache_max = 128
_CACHE_TTL_SECONDS = 300.0
_analysis_cache_lock = threading.Lock()
_analysis_cache_computing: set[str] = set()
_runtime_settings_lock = threading.Lock()
_runtime_settings_loaded = False
_runtime_settings_last_refresh: float = 0.0
from services.ask_config import (
    _RUNTIME_SETTINGS_CACHE_TTL,
    _RUNTIME_ASK_DEFAULTS,
    _CONNECTION_TIMEOUTS,
)
_runtime_settings_snapshot: dict[str, Any] = {}


def _coerce_int_setting(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(int(minimum), min(int(maximum), parsed))


def _coerce_float_setting(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return max(float(minimum), min(float(maximum), parsed))


def _coerce_string_setting(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text or str(default)


def _coerce_int_list_setting(value: Any) -> list[int]:
    parsed_items: list[Any]
    if isinstance(value, list):
        parsed_items = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            maybe_json = json.loads(text)
            parsed_items = maybe_json if isinstance(maybe_json, list) else [part.strip() for part in text.split(",") if part.strip()]
        except Exception:
            parsed_items = [part.strip() for part in text.split(",") if part.strip()]
    else:
        return []
    result: list[int] = []
    seen: set[int] = set()
    for item in parsed_items:
        try:
            value_int = int(item)
        except (TypeError, ValueError):
            continue
        if value_int <= 0 or value_int in seen:
            continue
        seen.add(value_int)
        result.append(value_int)
    return result


def _propagate_runtime_settings_to_obs(
    max_sql_rows: int,
    default_preview: int,
    min_preview: int,
    max_preview: int,
    max_source_rows: int,
    analysis_cache_max: int,
    cache_ttl: float,
) -> None:
    import services.ask_observability as _obs
    _obs.MAX_SQL_ROWS = max_sql_rows
    _obs.DEFAULT_PREVIEW_ROW_LIMIT = default_preview
    _obs.MIN_PREVIEW_ROW_LIMIT = min_preview
    _obs.MAX_PREVIEW_ROW_LIMIT = max_preview
    _obs.MAX_SOURCE_MATERIALIZATION_ROWS = max_source_rows
    _obs._analysis_cache_max = analysis_cache_max
    _obs._CACHE_TTL_SECONDS = cache_ttl


def refresh_runtime_router_settings(force: bool = False) -> dict[str, Any]:
    global MAX_SQL_ROWS
    global DEFAULT_PREVIEW_ROW_LIMIT
    global MIN_PREVIEW_ROW_LIMIT
    global MAX_PREVIEW_ROW_LIMIT
    global MAX_SOURCE_MATERIALIZATION_ROWS
    global _analysis_cache_max
    global _CACHE_TTL_SECONDS
    global _runtime_settings_loaded
    global _runtime_settings_last_refresh
    global _runtime_settings_snapshot

    with _runtime_settings_lock:
        if _runtime_settings_loaded and not force and time.monotonic() - _runtime_settings_last_refresh < _RUNTIME_SETTINGS_CACHE_TTL:
            return dict(_runtime_settings_snapshot)

    settings = _settings_map()

    max_sql_rows = _coerce_int_setting(
        settings.get("ask_max_sql_rows"),
        _RUNTIME_ASK_DEFAULTS["max_sql_rows"],
        1,
        100000,
    )
    min_preview = _coerce_int_setting(
        settings.get("ask_min_preview_row_limit"),
        _RUNTIME_ASK_DEFAULTS["min_preview_row_limit"],
        1,
        100000,
    )
    max_preview = _coerce_int_setting(
        settings.get("ask_max_preview_row_limit"),
        _RUNTIME_ASK_DEFAULTS["max_preview_row_limit"],
        min_preview,
        100000,
    )
    if max_preview < min_preview:
        max_preview = min_preview
    default_preview = _coerce_int_setting(
        settings.get("ask_default_preview_row_limit"),
        _RUNTIME_ASK_DEFAULTS["default_preview_row_limit"],
        min_preview,
        max_preview,
    )
    max_source_materialization_rows = _coerce_int_setting(
        settings.get("ask_max_source_materialization_rows"),
        _RUNTIME_ASK_DEFAULTS["max_source_materialization_rows"],
        100,
        200000,
    )
    analysis_cache_max = _coerce_int_setting(
        settings.get("ask_analysis_cache_max"),
        _RUNTIME_ASK_DEFAULTS["analysis_cache_max"],
        16,
        10000,
    )
    analysis_cache_ttl = _coerce_float_setting(
        settings.get("ask_analysis_cache_ttl_s"),
        _RUNTIME_ASK_DEFAULTS["analysis_cache_ttl_s"],
        10.0,
        86400.0,
    )

    updated_router = dict(ROUTER_CONFIG)
    integer_router_settings: dict[str, tuple[int, int, int]] = {
        "router_tier1_max_retries": (updated_router.get("tier1_max_retries", 1), 1, 10),
        "router_tier2_max_retries": (updated_router.get("tier2_max_retries", 2), 1, 10),
        "router_tier3_max_retries": (updated_router.get("tier3_max_retries", 3), 1, 10),
        "router_adaptive_strategy_consensus_risk_threshold": (
            updated_router.get("adaptive_strategy_consensus_risk_threshold", 4),
            1,
            20,
        ),
        "router_adaptive_strategy_decompose_risk_threshold": (
            updated_router.get("adaptive_strategy_decompose_risk_threshold", 7),
            1,
            20,
        ),
        "router_adaptive_strategy_min_subquestions_for_decompose": (
            updated_router.get("adaptive_strategy_min_subquestions_for_decompose", 2),
            1,
            10,
        ),
        "router_tier1_max_columns_per_model": (updated_router.get("tier1_max_columns_per_model", 12), 1, 500),
        "router_tier2_max_columns_per_model": (updated_router.get("tier2_max_columns_per_model", 15), 1, 500),
        "router_tier3_max_columns_per_model": (updated_router.get("tier3_max_columns_per_model", 20), 1, 500),
        "router_max_sub_questions": (updated_router.get("max_sub_questions", 3), 1, 20),
        "router_max_suggested_questions": (updated_router.get("max_suggested_questions", 5), 1, 20),
        "router_metadata_summary_max_models": (updated_router.get("metadata_summary_max_models", 10), 1, 200),
        "router_cross_source_max_workers": (updated_router.get("cross_source_max_workers", 4), 1, 32),
        "router_decompose_merge_failure_threshold": (updated_router.get("decompose_merge_failure_threshold", 1), 1, 20),
        "router_duckdb_did_you_mean_max_retries": (updated_router.get("duckdb_did_you_mean_max_retries", 1), 0, 5),
        "router_external_connection_pool_max_per_key": (updated_router.get("external_connection_pool_max_per_key", 4), 1, 64),
        "router_execution_metrics_log_every": (updated_router.get("execution_metrics_log_every", 25), 1, 2000),
        "router_execution_metrics_max_samples": (updated_router.get("execution_metrics_max_samples", 400), 50, 10000),
        "router_route_observability_window_seconds": (updated_router.get("route_observability_window_seconds", 1800), 300, 86400),
        "router_route_observability_max_events_per_project": (
            updated_router.get("route_observability_max_events_per_project", 20000),
            1000,
            200000,
        ),
        "router_route_observability_persist_event_delta": (
            updated_router.get("route_observability_persist_event_delta", 20),
            1,
            10000,
        ),
        "router_route_observability_strategy_trend_max_points": (
            updated_router.get("route_observability_strategy_trend_max_points", 24),
            6,
            240,
        ),
        "router_route_observability_strategy_trend_persist_decision_delta": (
            updated_router.get("route_observability_strategy_trend_persist_decision_delta", 5),
            1,
            10000,
        ),
        "router_route_alert_repair_timeout_short_circuit_min_warning_events": (
            updated_router.get("route_alert_repair_timeout_short_circuit_min_warning_events", 6),
            1,
            10000,
        ),
        "router_route_alert_repair_timeout_short_circuit_min_critical_events": (
            updated_router.get("route_alert_repair_timeout_short_circuit_min_critical_events", 12),
            1,
            10000,
        ),
        "router_route_alert_repair_budget_low_short_circuit_min_warning_events": (
            updated_router.get("route_alert_repair_budget_low_short_circuit_min_warning_events", 6),
            1,
            10000,
        ),
        "router_route_alert_repair_budget_low_short_circuit_min_critical_events": (
            updated_router.get("route_alert_repair_budget_low_short_circuit_min_critical_events", 12),
            1,
            10000,
        ),
        "router_route_alert_json_reask_min_warning_decisions": (
            updated_router.get("route_alert_json_reask_min_warning_decisions", 10),
            1,
            10000,
        ),
        "router_route_alert_json_reask_min_critical_decisions": (
            updated_router.get("route_alert_json_reask_min_critical_decisions", 20),
            1,
            10000,
        ),
        "router_route_alert_decompose_cancelled_min_warning_events": (
            updated_router.get("route_alert_decompose_cancelled_min_warning_events", 6),
            1,
            10000,
        ),
        "router_route_alert_decompose_cancelled_min_critical_events": (
            updated_router.get("route_alert_decompose_cancelled_min_critical_events", 12),
            1,
            10000,
        ),
    }
    float_router_settings: dict[str, tuple[float, float, float]] = {
        "router_decompose_merge_disable_seconds": (updated_router.get("decompose_merge_disable_seconds", 3600), 30.0, 86400.0),
        "router_decompose_merge_stage_budget_s": (updated_router.get("decompose_merge_stage_budget_s", 60.0), 5.0, 600.0),
        "router_sql_generation_total_budget_s": (updated_router.get("sql_generation_total_budget_s", 300.0), 10.0, 900.0),
        "router_sql_repair_timeout_cap_s": (updated_router.get("sql_repair_timeout_cap_s", 20.0), 2.0, 120.0),
        "router_sql_repair_skip_if_remaining_budget_below_s": (
            updated_router.get("sql_repair_skip_if_remaining_budget_below_s", 8.0),
            0.5,
            60.0,
        ),
        "router_sql_generation_timeout_cap_s": (updated_router.get("sql_generation_timeout_cap_s", 120.0), 1.0, 300.0),
        "router_sql_generation_timeout_min_s": (updated_router.get("sql_generation_timeout_min_s", 1.0), 0.1, 60.0),
        "router_json_reask_timeout_cap_s": (updated_router.get("json_reask_timeout_cap_s", 20.0), 0.5, 120.0),
        "router_json_reask_timeout_min_s": (updated_router.get("json_reask_timeout_min_s", 0.5), 0.1, 30.0),
        "router_llm_sub_query_timeout_s": (updated_router.get("llm_sub_query_timeout_s", 90.0), 1.0, 300.0),
        "router_llm_merge_timeout_s": (updated_router.get("llm_merge_timeout_s", 120.0), 1.0, 600.0),
        "router_external_connection_pool_idle_seconds": (updated_router.get("external_connection_pool_idle_seconds", 300), 30.0, 86400.0),
        "timeout_request_ms": (updated_router.get("request_timeout_ms", 120000), 1000.0, 1800000.0),
        "timeout_llm_connect_s": (updated_router.get("llm_connect_timeout_s", 30), 1.0, 3600.0),
        "timeout_llm_read_s": (updated_router.get("llm_read_timeout_s", 120), 1.0, 3600.0),
        "timeout_llm_write_s": (updated_router.get("llm_write_timeout_s", 30), 1.0, 3600.0),
        "timeout_llm_pool_s": (updated_router.get("llm_pool_timeout_s", 30), 1.0, 3600.0),
        "timeout_db_connect_s": (updated_router.get("db_connect_timeout_s", 10), 1.0, 3600.0),
        "timeout_model_list_s": (updated_router.get("model_list_timeout_s", 30), 1.0, 3600.0),
        "router_execution_metrics_log_interval_seconds": (updated_router.get("execution_metrics_log_interval_seconds", 180), 10.0, 86400.0),
        "router_route_observability_persist_interval_seconds": (
            updated_router.get("route_observability_persist_interval_seconds", 30),
            1.0,
            3600.0,
        ),
        "router_route_observability_strategy_trend_persist_interval_seconds": (
            updated_router.get("route_observability_strategy_trend_persist_interval_seconds", 60),
            1.0,
            3600.0,
        ),
        "router_route_alert_repair_timeout_short_circuit_warning_rate": (
            updated_router.get("route_alert_repair_timeout_short_circuit_warning_rate", 0.25),
            0.01,
            1.0,
        ),
        "router_route_alert_repair_timeout_short_circuit_critical_rate": (
            updated_router.get("route_alert_repair_timeout_short_circuit_critical_rate", 0.45),
            0.01,
            1.0,
        ),
        "router_route_alert_repair_budget_low_short_circuit_warning_rate": (
            updated_router.get("route_alert_repair_budget_low_short_circuit_warning_rate", 0.20),
            0.01,
            1.0,
        ),
        "router_route_alert_repair_budget_low_short_circuit_critical_rate": (
            updated_router.get("route_alert_repair_budget_low_short_circuit_critical_rate", 0.35),
            0.01,
            1.0,
        ),
        "router_route_alert_json_reask_warning_rate": (
            updated_router.get("route_alert_json_reask_warning_rate", 0.20),
            0.01,
            1.0,
        ),
        "router_route_alert_json_reask_critical_rate": (
            updated_router.get("route_alert_json_reask_critical_rate", 0.40),
            0.01,
            1.0,
        ),
        "router_route_alert_decompose_cancelled_warning_rate": (
            updated_router.get("route_alert_decompose_cancelled_warning_rate", 0.15),
            0.01,
            1.0,
        ),
        "router_route_alert_decompose_cancelled_critical_rate": (
            updated_router.get("route_alert_decompose_cancelled_critical_rate", 0.30),
            0.01,
            1.0,
        ),
    }
    bool_router_settings: dict[str, str] = {
        "router_adaptive_strategy_enabled": "adaptive_strategy_enabled",
        "router_guidance_llm_available": "guidance_llm_available",
        "router_schema_pruning_enabled": "schema_pruning_enabled",
        "router_decompose_merge_enabled": "decompose_merge_enabled",
        "router_decompose_merge_circuit_enabled": "decompose_merge_circuit_enabled",
        "router_duckdb_did_you_mean_fix_enabled": "duckdb_did_you_mean_fix_enabled",
        "router_duckdb_did_you_mean_allow_internal_tables": "duckdb_did_you_mean_allow_internal_tables",
        "router_external_connection_pool_enabled": "external_connection_pool_enabled",
        "router_route_observability_persist_enabled": "route_observability_persist_enabled",
        "router_sql_route_v2_enabled": "sql_route_v2_enabled",
        "router_sql_route_shadow_mode": "sql_route_shadow_mode",
        "router_sql_route_event_persist_enabled": "sql_route_event_persist_enabled",
        "router_sql_route_strict_json_probe_enabled": "sql_route_strict_json_probe_enabled",
        "router_model_ref_case_sensitive": "model_ref_case_sensitive",
        "router_sql_repair_local_preflight_enabled": "sql_repair_local_preflight_enabled",
    }

    int_router_mapping = {
        "router_tier1_max_retries": "tier1_max_retries",
        "router_tier2_max_retries": "tier2_max_retries",
        "router_tier3_max_retries": "tier3_max_retries",
        "router_adaptive_strategy_consensus_risk_threshold": "adaptive_strategy_consensus_risk_threshold",
        "router_adaptive_strategy_decompose_risk_threshold": "adaptive_strategy_decompose_risk_threshold",
        "router_adaptive_strategy_min_subquestions_for_decompose": "adaptive_strategy_min_subquestions_for_decompose",
        "router_tier1_max_columns_per_model": "tier1_max_columns_per_model",
        "router_tier2_max_columns_per_model": "tier2_max_columns_per_model",
        "router_tier3_max_columns_per_model": "tier3_max_columns_per_model",
        "router_max_sub_questions": "max_sub_questions",
        "router_max_suggested_questions": "max_suggested_questions",
        "router_metadata_summary_max_models": "metadata_summary_max_models",
        "router_cross_source_max_workers": "cross_source_max_workers",
        "router_decompose_merge_failure_threshold": "decompose_merge_failure_threshold",
        "router_duckdb_did_you_mean_max_retries": "duckdb_did_you_mean_max_retries",
        "router_external_connection_pool_max_per_key": "external_connection_pool_max_per_key",
        "router_execution_metrics_log_every": "execution_metrics_log_every",
        "router_execution_metrics_max_samples": "execution_metrics_max_samples",
        "router_route_observability_window_seconds": "route_observability_window_seconds",
        "router_route_observability_max_events_per_project": "route_observability_max_events_per_project",
        "router_route_observability_persist_event_delta": "route_observability_persist_event_delta",
        "router_route_observability_strategy_trend_max_points": "route_observability_strategy_trend_max_points",
        "router_route_observability_strategy_trend_persist_decision_delta": "route_observability_strategy_trend_persist_decision_delta",
        "router_route_alert_repair_timeout_short_circuit_min_warning_events": "route_alert_repair_timeout_short_circuit_min_warning_events",
        "router_route_alert_repair_timeout_short_circuit_min_critical_events": "route_alert_repair_timeout_short_circuit_min_critical_events",
        "router_route_alert_repair_budget_low_short_circuit_min_warning_events": "route_alert_repair_budget_low_short_circuit_min_warning_events",
        "router_route_alert_repair_budget_low_short_circuit_min_critical_events": "route_alert_repair_budget_low_short_circuit_min_critical_events",
        "router_route_alert_json_reask_min_warning_decisions": "route_alert_json_reask_min_warning_decisions",
        "router_route_alert_json_reask_min_critical_decisions": "route_alert_json_reask_min_critical_decisions",
        "router_route_alert_decompose_cancelled_min_warning_events": "route_alert_decompose_cancelled_min_warning_events",
        "router_route_alert_decompose_cancelled_min_critical_events": "route_alert_decompose_cancelled_min_critical_events",
    }
    float_router_mapping = {
        "router_decompose_merge_disable_seconds": "decompose_merge_disable_seconds",
        "router_decompose_merge_stage_budget_s": "decompose_merge_stage_budget_s",
        "router_sql_generation_total_budget_s": "sql_generation_total_budget_s",
        "router_sql_repair_timeout_cap_s": "sql_repair_timeout_cap_s",
        "router_sql_repair_skip_if_remaining_budget_below_s": "sql_repair_skip_if_remaining_budget_below_s",
        "router_external_connection_pool_idle_seconds": "external_connection_pool_idle_seconds",
        "router_execution_metrics_log_interval_seconds": "execution_metrics_log_interval_seconds",
        "router_route_observability_persist_interval_seconds": "route_observability_persist_interval_seconds",
        "router_route_observability_strategy_trend_persist_interval_seconds": "route_observability_strategy_trend_persist_interval_seconds",
        "router_route_alert_repair_timeout_short_circuit_warning_rate": "route_alert_repair_timeout_short_circuit_warning_rate",
        "router_route_alert_repair_timeout_short_circuit_critical_rate": "route_alert_repair_timeout_short_circuit_critical_rate",
        "router_route_alert_repair_budget_low_short_circuit_warning_rate": "route_alert_repair_budget_low_short_circuit_warning_rate",
        "router_route_alert_repair_budget_low_short_circuit_critical_rate": "route_alert_repair_budget_low_short_circuit_critical_rate",
        "router_route_alert_json_reask_warning_rate": "route_alert_json_reask_warning_rate",
        "router_route_alert_json_reask_critical_rate": "route_alert_json_reask_critical_rate",
        "router_route_alert_decompose_cancelled_warning_rate": "route_alert_decompose_cancelled_warning_rate",
        "router_route_alert_decompose_cancelled_critical_rate": "route_alert_decompose_cancelled_critical_rate",
        "router_sql_generation_timeout_cap_s": "sql_generation_timeout_cap_s",
        "router_sql_generation_timeout_min_s": "sql_generation_timeout_min_s",
        "router_json_reask_timeout_cap_s": "json_reask_timeout_cap_s",
        "router_json_reask_timeout_min_s": "json_reask_timeout_min_s",
        "router_llm_sub_query_timeout_s": "llm_sub_query_timeout_s",
        "router_llm_merge_timeout_s": "llm_merge_timeout_s",
        "timeout_request_ms": "request_timeout_ms",
        "timeout_llm_connect_s": "llm_connect_timeout_s",
        "timeout_llm_read_s": "llm_read_timeout_s",
        "timeout_llm_write_s": "llm_write_timeout_s",
        "timeout_llm_pool_s": "llm_pool_timeout_s",
        "timeout_db_connect_s": "db_connect_timeout_s",
        "timeout_model_list_s": "model_list_timeout_s",
    }

    for setting_key, (default_value, minimum, maximum) in integer_router_settings.items():
        if setting_key in settings:
            config_key = int_router_mapping[setting_key]
            updated_router[config_key] = _coerce_int_setting(settings.get(setting_key), int(default_value), minimum, maximum)

    for setting_key, (default_value, minimum, maximum) in float_router_settings.items():
        if setting_key in settings:
            config_key = float_router_mapping[setting_key]
            updated_router[config_key] = _coerce_float_setting(settings.get(setting_key), float(default_value), minimum, maximum)

    for setting_key, config_key in bool_router_settings.items():
        if setting_key in settings:
            updated_router[config_key] = _normalize_bool(settings.get(setting_key))

    adaptive_consensus_threshold = _coerce_int_setting(
        updated_router.get("adaptive_strategy_consensus_risk_threshold"),
        4,
        1,
        20,
    )
    adaptive_decompose_threshold = _coerce_int_setting(
        updated_router.get("adaptive_strategy_decompose_risk_threshold"),
        7,
        1,
        20,
    )
    if adaptive_decompose_threshold < adaptive_consensus_threshold:
        adaptive_decompose_threshold = adaptive_consensus_threshold
    updated_router["adaptive_strategy_consensus_risk_threshold"] = adaptive_consensus_threshold
    updated_router["adaptive_strategy_decompose_risk_threshold"] = adaptive_decompose_threshold
    updated_router["adaptive_strategy_min_subquestions_for_decompose"] = _coerce_int_setting(
        updated_router.get("adaptive_strategy_min_subquestions_for_decompose"),
        2,
        1,
        10,
    )

    sql_gen_cap = _coerce_float_setting(updated_router.get("sql_generation_timeout_cap_s"), 60.0, 1.0, 300.0)
    json_reask_cap = _coerce_float_setting(updated_router.get("json_reask_timeout_cap_s"), 20.0, 0.5, 120.0)
    total_budget = updated_router.get("sql_generation_total_budget_s", 300.0)

    updated_router["sql_generation_timeout_min_s"] = max(0.5, sql_gen_cap * 0.017)
    updated_router["json_reask_timeout_min_s"] = max(0.1, json_reask_cap * 0.025)
    sql_repair_cap = max(5.0, sql_gen_cap * 0.33)
    updated_router["sql_repair_timeout_cap_s"] = sql_repair_cap
    updated_router["sql_repair_skip_if_remaining_budget_below_s"] = max(2.0, sql_repair_cap * 0.4)
    updated_router["decompose_merge_stage_budget_s"] = max(10.0, total_budget * 0.5)

    repair_timeout_warning_rate = _coerce_float_setting(
        updated_router.get("route_alert_repair_timeout_short_circuit_warning_rate"),
        0.25,
        0.01,
        1.0,
    )
    repair_timeout_critical_rate = _coerce_float_setting(
        updated_router.get("route_alert_repair_timeout_short_circuit_critical_rate"),
        max(repair_timeout_warning_rate, 0.45),
        0.01,
        1.0,
    )
    if repair_timeout_critical_rate < repair_timeout_warning_rate:
        repair_timeout_critical_rate = repair_timeout_warning_rate
    repair_timeout_min_warning_events = _coerce_int_setting(
        updated_router.get("route_alert_repair_timeout_short_circuit_min_warning_events"),
        6,
        1,
        10000,
    )
    repair_timeout_min_critical_events = _coerce_int_setting(
        updated_router.get("route_alert_repair_timeout_short_circuit_min_critical_events"),
        max(repair_timeout_min_warning_events, 12),
        1,
        10000,
    )
    if repair_timeout_min_critical_events < repair_timeout_min_warning_events:
        repair_timeout_min_critical_events = repair_timeout_min_warning_events

    repair_budget_warning_rate = _coerce_float_setting(
        updated_router.get("route_alert_repair_budget_low_short_circuit_warning_rate"),
        0.20,
        0.01,
        1.0,
    )
    repair_budget_critical_rate = _coerce_float_setting(
        updated_router.get("route_alert_repair_budget_low_short_circuit_critical_rate"),
        max(repair_budget_warning_rate, 0.35),
        0.01,
        1.0,
    )
    if repair_budget_critical_rate < repair_budget_warning_rate:
        repair_budget_critical_rate = repair_budget_warning_rate
    repair_budget_min_warning_events = _coerce_int_setting(
        updated_router.get("route_alert_repair_budget_low_short_circuit_min_warning_events"),
        6,
        1,
        10000,
    )
    repair_budget_min_critical_events = _coerce_int_setting(
        updated_router.get("route_alert_repair_budget_low_short_circuit_min_critical_events"),
        max(repair_budget_min_warning_events, 12),
        1,
        10000,
    )
    if repair_budget_min_critical_events < repair_budget_min_warning_events:
        repair_budget_min_critical_events = repair_budget_min_warning_events

    updated_router["route_alert_repair_timeout_short_circuit_warning_rate"] = repair_timeout_warning_rate
    updated_router["route_alert_repair_timeout_short_circuit_critical_rate"] = repair_timeout_critical_rate
    updated_router["route_alert_repair_timeout_short_circuit_min_warning_events"] = repair_timeout_min_warning_events
    updated_router["route_alert_repair_timeout_short_circuit_min_critical_events"] = repair_timeout_min_critical_events
    updated_router["route_alert_repair_budget_low_short_circuit_warning_rate"] = repair_budget_warning_rate
    updated_router["route_alert_repair_budget_low_short_circuit_critical_rate"] = repair_budget_critical_rate
    updated_router["route_alert_repair_budget_low_short_circuit_min_warning_events"] = repair_budget_min_warning_events
    updated_router["route_alert_repair_budget_low_short_circuit_min_critical_events"] = repair_budget_min_critical_events

    json_reask_warning_rate = _coerce_float_setting(
        updated_router.get("route_alert_json_reask_warning_rate"),
        0.20,
        0.01,
        1.0,
    )
    json_reask_critical_rate = _coerce_float_setting(
        updated_router.get("route_alert_json_reask_critical_rate"),
        max(json_reask_warning_rate, 0.40),
        0.01,
        1.0,
    )
    if json_reask_critical_rate < json_reask_warning_rate:
        json_reask_critical_rate = json_reask_warning_rate
    json_reask_min_warning_decisions = _coerce_int_setting(
        updated_router.get("route_alert_json_reask_min_warning_decisions"),
        10,
        1,
        10000,
    )
    json_reask_min_critical_decisions = _coerce_int_setting(
        updated_router.get("route_alert_json_reask_min_critical_decisions"),
        max(json_reask_min_warning_decisions, 20),
        1,
        10000,
    )
    if json_reask_min_critical_decisions < json_reask_min_warning_decisions:
        json_reask_min_critical_decisions = json_reask_min_warning_decisions

    decompose_cancelled_warning_rate = _coerce_float_setting(
        updated_router.get("route_alert_decompose_cancelled_warning_rate"),
        0.15,
        0.01,
        1.0,
    )
    decompose_cancelled_critical_rate = _coerce_float_setting(
        updated_router.get("route_alert_decompose_cancelled_critical_rate"),
        max(decompose_cancelled_warning_rate, 0.30),
        0.01,
        1.0,
    )
    if decompose_cancelled_critical_rate < decompose_cancelled_warning_rate:
        decompose_cancelled_critical_rate = decompose_cancelled_warning_rate
    decompose_cancelled_min_warning_events = _coerce_int_setting(
        updated_router.get("route_alert_decompose_cancelled_min_warning_events"),
        6,
        1,
        10000,
    )
    decompose_cancelled_min_critical_events = _coerce_int_setting(
        updated_router.get("route_alert_decompose_cancelled_min_critical_events"),
        max(decompose_cancelled_min_warning_events, 12),
        1,
        10000,
    )
    if decompose_cancelled_min_critical_events < decompose_cancelled_min_warning_events:
        decompose_cancelled_min_critical_events = decompose_cancelled_min_warning_events

    updated_router["route_alert_json_reask_warning_rate"] = json_reask_warning_rate
    updated_router["route_alert_json_reask_critical_rate"] = json_reask_critical_rate
    updated_router["route_alert_json_reask_min_warning_decisions"] = json_reask_min_warning_decisions
    updated_router["route_alert_json_reask_min_critical_decisions"] = json_reask_min_critical_decisions
    updated_router["route_alert_decompose_cancelled_warning_rate"] = decompose_cancelled_warning_rate
    updated_router["route_alert_decompose_cancelled_critical_rate"] = decompose_cancelled_critical_rate
    updated_router["route_alert_decompose_cancelled_min_warning_events"] = decompose_cancelled_min_warning_events
    updated_router["route_alert_decompose_cancelled_min_critical_events"] = decompose_cancelled_min_critical_events

    if "router_sql_route_allowlist_projects" in settings:
        updated_router["sql_route_allowlist_projects"] = _coerce_int_list_setting(settings.get("router_sql_route_allowlist_projects"))
    if "router_sql_route_profile_id" in settings:
        updated_router["sql_route_profile_id"] = _coerce_string_setting(
            settings.get("router_sql_route_profile_id"),
            str(updated_router.get("sql_route_profile_id") or "prismbi.default"),
        )
    if "router_sql_route_profile_version" in settings:
        updated_router["sql_route_profile_version"] = _coerce_string_setting(
            settings.get("router_sql_route_profile_version"),
            str(updated_router.get("sql_route_profile_version") or "v2"),
        )

    previous_pool_enabled = bool(ROUTER_CONFIG.get("external_connection_pool_enabled", True))
    next_pool_enabled = bool(updated_router.get("external_connection_pool_enabled", True))

    snapshot: dict[str, Any]
    with _runtime_settings_lock:
        if _runtime_settings_loaded and not force:
            return dict(_runtime_settings_snapshot)

        MAX_SQL_ROWS = int(max_sql_rows)
        MIN_PREVIEW_ROW_LIMIT = int(min_preview)
        MAX_PREVIEW_ROW_LIMIT = int(max_preview)
        DEFAULT_PREVIEW_ROW_LIMIT = int(default_preview)
        MAX_SOURCE_MATERIALIZATION_ROWS = int(max_source_materialization_rows)
        _analysis_cache_max = int(analysis_cache_max)
        _CACHE_TTL_SECONDS = float(analysis_cache_ttl)

        # Propagate runtime settings to ask_observability (whose functions
        # resolve these names via static bindings from __globals__).
        _propagate_runtime_settings_to_obs(
            MAX_SQL_ROWS, DEFAULT_PREVIEW_ROW_LIMIT,
            MIN_PREVIEW_ROW_LIMIT, MAX_PREVIEW_ROW_LIMIT,
            MAX_SOURCE_MATERIALIZATION_ROWS,
            _analysis_cache_max, _CACHE_TTL_SECONDS,
        )

        ROUTER_CONFIG.update(updated_router)

        with _analysis_cache_lock:
            if len(_analysis_cache) > _analysis_cache_max:
                for key in list(_analysis_cache.keys())[: len(_analysis_cache) - _analysis_cache_max]:
                    _analysis_cache.pop(key, None)

        snapshot = {
            "MAX_SQL_ROWS": MAX_SQL_ROWS,
            "DEFAULT_PREVIEW_ROW_LIMIT": DEFAULT_PREVIEW_ROW_LIMIT,
            "MIN_PREVIEW_ROW_LIMIT": MIN_PREVIEW_ROW_LIMIT,
            "MAX_PREVIEW_ROW_LIMIT": MAX_PREVIEW_ROW_LIMIT,
            "MAX_SOURCE_MATERIALIZATION_ROWS": MAX_SOURCE_MATERIALIZATION_ROWS,
            "analysis_cache_max": _analysis_cache_max,
            "analysis_cache_ttl_s": _CACHE_TTL_SECONDS,
            "router_config": dict(ROUTER_CONFIG),
        }
        _runtime_settings_snapshot = snapshot
        _runtime_settings_loaded = True
        _runtime_settings_last_refresh = time.monotonic()

    if previous_pool_enabled and not next_pool_enabled:
        _clear_external_connection_pool()
    return dict(snapshot)


# ── Observable subsystem ────────────────────────────────────────────
# Functions are imported lazily at the end of this module via
# _bind_observability() to avoid circular imports.

def _format_sql_clause_focus_hint(analysis: Optional[dict[str, Any]]) -> str:
    if not isinstance(analysis, dict):
        return ""
    metadata_part = str(analysis.get("metadata_question_part") or "").strip()
    non_metadata_part = str(analysis.get("non_metadata_question_part") or "").strip()
    lines: list[str] = []
    if metadata_part:
        lines.append(f"SQL-related question part: {metadata_part}")
    if non_metadata_part:
        lines.append(f"Non-SQL question part (do NOT treat as SQL requirement): {non_metadata_part}")
    clause_prompt = _format_clause_routing_for_prompt(analysis.get("clause_routing"))
    if clause_prompt:
        lines.append("Clause routing details:")
        lines.extend(clause_prompt.split("\n"))
    if not lines:
        return ""
    return "\nClause routing context:\n" + "\n".join(lines)


def _generate_sql(
    question: str,
    project_id: int,
    previous_questions: Optional[list[str]] = None,
    semantic_context: Optional[str] = None,
    retrieved_tables: Optional[list[str]] = None,
    semantic_hits: Optional[dict[str, Any]] = None,
    language: Optional[str] = None,
    knowledge_context: Optional[str] = None,
    analysis: Optional[dict] = None,
    cancel_check: Optional[Callable[[], None]] = None,
) -> dict[str, Any]:
    if cancel_check:
        cancel_check()
    raw_analysis = analysis if isinstance(analysis, dict) else {}
    generation_pipeline = _generation_pipeline()
    prepared, early_result = generation_pipeline.prepare_context(
        question=question,
        project_id=project_id,
        semantic_context=semantic_context,
        retrieved_tables=retrieved_tables,
        semantic_hits=semantic_hits,
        knowledge_context=knowledge_context,
        analysis=analysis,
        router_config=ROUTER_CONFIG,
    )
    if early_result is not None:
        return early_result
    if prepared is None:
        return _sql_gen_error_result(
            _sql_msg("gen_context_failed", language),
            _sql_msg("context_unavailable", language),
            "llm_fallback_prepare_error",
        )

    emit_route_events = _is_sql_route_v2_enabled(project_id) or bool(ROUTER_CONFIG.get("sql_route_shadow_mode", False))

    analysis = prepared.analysis
    semantic_context = prepared.semantic_context
    retrieved_tables = prepared.retrieved_tables
    semantic_hits = prepared.semantic_hits
    resolved = prepared.resolved
    schema_link_plan = prepared.schema_link_plan
    sql_plan = prepared.sql_plan
    strategy = prepared.strategy
    engine_label = prepared.engine_label
    response_format = prepared.response_format
    system_suffix = prepared.system_suffix

    total_budget_raw = ROUTER_CONFIG.get("sql_generation_total_budget_s", 300.0)
    try:
        total_budget_s = float(total_budget_raw)
    except Exception:
        total_budget_s = 120.0
    if not math.isfinite(total_budget_s):
        total_budget_s = 120.0
    total_budget_s = max(0.0, total_budget_s)
    generation_started_at = time.monotonic()
    generation_deadline = generation_started_at + total_budget_s

    def _remaining_generation_budget_s() -> float:
        if total_budget_s <= 0.0:
            return 0.0
        return max(0.0, generation_deadline - time.monotonic())

    def _budget_exceeded_error_result(*, reason: str) -> dict[str, Any]:
        detail = (
            f"SQL generation exceeded total budget ({total_budget_s:.1f}s) at stage={reason}."
        )
        if emit_route_events:
            _emit_route_event(
                "sql_generation_fallback",
                {
                    "from_engine": engine_label,
                    "to_engine": "none",
                    "reason": "total_budget_exceeded",
                    "stage": reason,
                    "budget_seconds": total_budget_s,
                },
                project_id=project_id,
            )
        return _sql_gen_error_result(
            _sql_msg("failed_generate_sql", language),
            detail,
            "llm_fallback_budget_exceeded",
            retrieved_tables=retrieved_tables,
        )

    def _attempt_timeout_from_budget(*, cap_s: float = 60.0, min_s: float = 1.0) -> float | None:
        remaining = _remaining_generation_budget_s()
        if remaining < min_s:
            return None
        return max(min_s, min(float(cap_s), remaining))

    had_compound_fallback = False
    # Decompose & Merge for compound questions
    normalized_sub_questions = _normalize_analysis_string_list((analysis or {}).get("sub_questions"))
    decompose_stage_budget_raw = ROUTER_CONFIG.get("decompose_merge_stage_budget_s", 60.0)
    try:
        decompose_stage_budget_s = float(decompose_stage_budget_raw)
    except Exception:
        decompose_stage_budget_s = 60.0
    if not math.isfinite(decompose_stage_budget_s):
        decompose_stage_budget_s = 60.0
    decompose_stage_budget_s = max(0.0, decompose_stage_budget_s)
    decompose_merge_enabled = bool(ROUTER_CONFIG.get("decompose_merge_enabled", True))
    if engine_label == "decompose_merge" and not decompose_merge_enabled:
        target_engine = "fewshot_cot"
        if len(normalized_sub_questions) <= 1 and len((analysis or {}).get("dimensions") or []) <= 1:
            target_engine = "direct_llm"
        LOGGER.info(
            "Decompose-merge disabled via router settings; using %s generation",
            target_engine,
        )
        if emit_route_events:
            _emit_route_event(
                "sql_generation_fallback",
                {
                    "from_engine": "decompose_merge",
                    "to_engine": target_engine,
                    "reason": "decompose_disabled",
                    "sub_question_count": len(normalized_sub_questions),
                },
                project_id=project_id,
            )
        engine_label = target_engine
        had_compound_fallback = target_engine == "direct_llm"
    if engine_label == "decompose_merge" and normalized_sub_questions:
        if len(normalized_sub_questions) == 1:
            LOGGER.info(
                "Decompose-merge skipped for single sub-question; using fewshot_cot generation",
            )
            if emit_route_events:
                _emit_route_event(
                    "sql_generation_fallback",
                    {
                        "from_engine": "decompose_merge",
                        "to_engine": "fewshot_cot",
                        "reason": "single_sub_question_bypass",
                        "sub_question_count": len(normalized_sub_questions),
                    },
                    project_id=project_id,
                )
            engine_label = "fewshot_cot"
        elif _is_decompose_merge_temporarily_disabled(project_id):
            LOGGER.warning(
                "Decompose-merge temporarily disabled for project_id=%d; using direct generation",
                project_id,
            )
            _emit_thread_progress("organize", _sql_msg("thread_progress_decompose_disabled", language))
            if emit_route_events:
                _emit_route_event(
                    "sql_generation_fallback",
                    {
                        "from_engine": "decompose_merge",
                        "to_engine": "direct_llm",
                        "reason": "circuit_open",
                        "sub_question_count": len(normalized_sub_questions),
                    },
                    project_id=project_id,
                )
            engine_label = "direct_llm"
            had_compound_fallback = True
        else:
            remaining_before_decompose = _remaining_generation_budget_s()
            effective_decompose_budget_s = min(decompose_stage_budget_s, remaining_before_decompose)
            if effective_decompose_budget_s <= 0.0:
                LOGGER.warning(
                    "Skipping decompose-merge: generation budget exhausted (remaining=%.2fs, decompose_budget=%.2fs)",
                    remaining_before_decompose,
                    decompose_stage_budget_s,
                )
                if emit_route_events:
                    _emit_route_event(
                        "sql_generation_fallback",
                        {
                            "from_engine": "decompose_merge",
                            "to_engine": "direct_llm",
                            "reason": "budget_exceeded",
                            "sub_question_count": len(normalized_sub_questions),
                            "generation_budget_seconds": total_budget_s,
                            "decompose_stage_budget_seconds": decompose_stage_budget_s,
                        },
                        project_id=project_id,
                    )
                _record_decompose_merge_failure(project_id, "budget_exceeded")
                _emit_thread_progress("organize", _sql_msg("thread_progress_decompose_failed", language))
                engine_label = "direct_llm"
                had_compound_fallback = True
                decompose_failure_meta = {"reason": "budget_exceeded"}
            else:
                decompose_failure_meta = {}
            if engine_label == "decompose_merge":
                decompose_started_at = time.perf_counter()
                dm_result = _decompose_merge_sql(
                    question,
                    project_id,
                    analysis,
                    semantic_context,
                    retrieved_tables,
                    semantic_hits,
                    previous_questions,
                    language,
                    knowledge_context,
                    resolved,
                    schema_link_plan=schema_link_plan,
                    cancel_check=cancel_check,
                    failure_meta=decompose_failure_meta,
                    stage_budget_s=effective_decompose_budget_s,
                )
                decompose_elapsed_ms = (time.perf_counter() - decompose_started_at) * 1000.0
                if emit_route_events:
                    _emit_route_event(
                        "sql_generation_decompose_stage",
                        {
                            "sub_question_count": len(normalized_sub_questions),
                            "elapsed_ms": round(float(decompose_elapsed_ms), 3),
                            "stage_budget_seconds": effective_decompose_budget_s,
                            "remaining_total_budget_seconds": round(_remaining_generation_budget_s(), 3),
                            "status": "success" if dm_result is not None else "fallback",
                            "reason": str(decompose_failure_meta.get("reason") or "ok"),
                        },
                        project_id=project_id,
                    )
                if dm_result is not None:
                    _record_decompose_merge_success(project_id)
                    return dm_result
                dm_reason = str(decompose_failure_meta.get("reason") or "returned_none")
                dm_reason_counts = decompose_failure_meta.get("reason_counts")
                if isinstance(dm_reason_counts, dict) and dm_reason_counts:
                    LOGGER.warning(
                        "Decompose-merge returned None (reason=%s, reason_counts=%s); falling back to direct generation with compound hint",
                        dm_reason,
                        dm_reason_counts,
                    )
                else:
                    LOGGER.warning(
                        "Decompose-merge returned None (reason=%s); falling back to direct generation with compound hint",
                        dm_reason,
                    )
                if emit_route_events:
                    _emit_route_event(
                        "sql_generation_fallback",
                        {
                            "from_engine": "decompose_merge",
                            "to_engine": "direct_llm",
                            "reason": dm_reason,
                            "reason_counts": dm_reason_counts if isinstance(dm_reason_counts, dict) else {},
                            "sub_question_count": len(normalized_sub_questions),
                        },
                        project_id=project_id,
                    )
                _record_decompose_merge_failure(project_id, dm_reason)
                _emit_thread_progress("organize", _sql_msg("thread_progress_decompose_failed", language))
                engine_label = "direct_llm"
                had_compound_fallback = True
    hit_models = semantic_hits.get("models", [])
    guard = _candidate_guard()
    dimension_mapping = ""
    if resolved and any(resolved.get(k) for k in ("dimensions_resolved", "metrics_resolved", "entities_resolved")):
        dimension_mapping = _format_dimension_mapping(analysis or {}, resolved)
    clause_focus_hint = _format_sql_clause_focus_hint(raw_analysis)
    schema_link_hint = _format_schema_linking_hint(schema_link_plan)
    owner_lock_hint = _format_owner_lock_constraints_hint(resolved, schema_link_plan)
    sql_plan_hint = _format_sql_plan_hint(sql_plan)
    fallback_constraints_hint = ""
    if had_compound_fallback:
        fallback_constraints_hint = _format_direct_fallback_sql_constraints_hint(
            hit_models,
            resolved,
            schema_link_plan,
        )
    system = _build_sql_system_message(system_suffix, _dialect_hint_for_project(project_id), language)
    use_examples = strategy.get("use_examples", True)
    sql_examples = _extract_sql_examples_from_knowledge(knowledge_context or "") if use_examples else ""
    project_prompt = _render_project_prompt(project_id, semantic_context, sql_examples)
    strategy_hint = ""
    if engine_label == "decompose_merge" or had_compound_fallback:
        strategy_hint = (
            "\nThis question contains multiple sub-questions. Generate a SINGLE SQL query that answers ALL parts. "
            "Use one flat query with all necessary joins and a GROUP BY covering all dimensions. "
            "If using CTEs, syntax must be: WITH name AS (SELECT ...) and the final SELECT must reference the CTE."
        )
    elif engine_label == "fewshot_cot":
        if sql_examples and use_examples:
            strategy_hint = (
                "\nThis question spans multiple dimensions. Reference the verified SQL examples below as patterns. "
                "Write a SQL with GROUP BY covering ALL dimensions. "
                "Think step by step: identify tables, join conditions, GROUP BY columns, then write the query."
            )
        else:
            strategy_hint = (
                "\nThis question spans multiple dimensions. Write a SQL with GROUP BY covering ALL dimensions. "
                "Think step by step: identify tables, join conditions, GROUP BY columns, then write the query."
            )
    _safe_prev = _sanitize_list(previous_questions, max_per_item=200, max_items=3)
    user = (
        f"Previous questions: {_safe_prev}\n"
        f"Question: {_truncate_question(question)}{strategy_hint}{clause_focus_hint}{dimension_mapping}{schema_link_hint}{owner_lock_hint}{sql_plan_hint}{fallback_constraints_hint}"
    )
    llm = LLMService()
    if not llm.is_configured():
        result = _sql_gen_error_result(
            _llm_not_configured_error(language).get("content", ""),
            None,
            "llm_fallback",
            retrieved_tables=retrieved_tables,
        )
        result["configured"] = False
        return result
    max_retries = int(prepared.max_retries)
    last_errors = []
    last_repair_result = None
    last_unknown_issue_bucket = ""
    unknown_issue_bucket_streak = 0
    circuitable_issue_bucket_streak = 0
    unknown_issue_bucket_circuit_threshold = int(ROUTER_CONFIG.get("unknown_issue_bucket_circuit_threshold", 3))
    circuitable_issue_buckets = {
        "alias_scope_leak",
        "wrong_alias_owner",
        "ambiguous_owner",
        "hallucinated_column",
        "cte_projection_missing",
    }

    def _should_retry_generation(attempt_index: int, reason: str) -> bool:
        if attempt_index >= max_retries - 1:
            return False
        normalized_reason = str(reason or "").strip().lower()
        dynamic_cap = max_retries
        if normalized_reason in {"json_parse", "syntax", "group_by", "empty_response", "empty_sql", "transport"}:
            dynamic_cap = min(dynamic_cap, 2)
        elif normalized_reason in {"bad_columns", "orphan_cte"}:
            dynamic_cap = min(dynamic_cap, 3)
        return attempt_index < max(dynamic_cap - 1, 0)

    if total_budget_s <= 0.0:
        return _budget_exceeded_error_result(reason="start")

    for attempt in range(max_retries):
        if cancel_check:
            cancel_check()
        if _remaining_generation_budget_s() <= 0.0:
            return _budget_exceeded_error_result(reason=f"attempt_{attempt+1}_start")
        active_engine_label = engine_label
        try:
            user_parts = [f"Project prompt:\n{project_prompt}", user]
            if last_errors:
                _safe_errors = [_sanitize_error_message(e, 160) for e in last_errors]
                error_feedback = "\nPrevious attempt errors:\n" + "\n".join(_safe_errors)
                user_parts.append(f"Fix these errors:{error_feedback}")
            sql_gen_cap = _coerce_float_setting(ROUTER_CONFIG.get("sql_generation_timeout_cap_s"), 120.0, 1.0, 300.0)
            sql_gen_min = _coerce_float_setting(ROUTER_CONFIG.get("sql_generation_timeout_min_s"), 1.0, 0.1, 60.0)
            llm_timeout_s = _attempt_timeout_from_budget(cap_s=sql_gen_cap, min_s=sql_gen_min)
            if llm_timeout_s is None:
                return _budget_exceeded_error_result(reason=f"attempt_{attempt+1}_llm_call")
            context_messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": "\n\n".join(user_parts)},
            ]
            result = _llm_chat_with_response_format_fallback(
                llm,
                context_messages,
                response_format=response_format,
                stage="sql_generation",
                timeout=llm_timeout_s,
                cancel_check=cancel_check,
            )
            raw_content = _llm_content_text(result)
            if not raw_content.strip():
                LOGGER.warning(
                    "LLM returned empty SQL generation payload (attempt %d/%d); retrying with local semantic context",
                    attempt + 1,
                    max_retries,
                )
                if emit_route_events:
                    _emit_route_event(
                        "sql_generation_retry",
                        {
                            "reason": "empty_llm_content",
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "generation_engine": engine_label,
                        },
                        project_id=project_id,
                    )
                local_context_hint = ", ".join(retrieved_tables[:6]) or "no matched tables"
                last_errors.append(
                    f"Attempt {attempt+1}: empty response; regenerate using only local matched metadata tables ({local_context_hint})"
                )
                if _should_retry_generation(attempt, "empty_response"):
                    continue
                return {
                    "sql": None,
                    "summary": _sql_msg("llm_no_valid_sql", language),
                    "reasoning": "; ".join(last_errors),
                    "retrieved_tables": retrieved_tables,
                    "configured": True,
                    "sql_engine": f"{engine_label}_failed",
                }
            try:
                parsed = parse_json_object(raw_content)
            except (json.JSONDecodeError, ValueError) as parse_exc:
                LOGGER.warning(
                    "SQL generation JSON parse failed (attempt %d/%d): %s; raw_content[:500]=%.500s",
                    attempt + 1, max_retries, parse_exc, raw_content,
                )
                parsed = None
                fallback_sql = _extract_sql_from_raw_text(raw_content)
                parse_error_detail = str(parse_exc)
                json_reask_attempted = False
                if fallback_sql:
                    json_reask_cap = _coerce_float_setting(ROUTER_CONFIG.get("json_reask_timeout_cap_s"), 20.0, 0.5, 120.0)
                    json_reask_min = _coerce_float_setting(ROUTER_CONFIG.get("json_reask_timeout_min_s"), 0.5, 0.1, 30.0)
                    json_reask_timeout_s = _attempt_timeout_from_budget(cap_s=json_reask_cap, min_s=json_reask_min)
                    if json_reask_timeout_s is not None:
                        json_reask_attempted = True
                        if emit_route_events:
                            _emit_route_event(
                                "sql_generation_retry",
                                {
                                    "reason": "json_reask",
                                    "attempt": attempt + 1,
                                    "max_retries": max_retries,
                                    "generation_engine": engine_label,
                                },
                                project_id=project_id,
                            )
                        reask_raw_content = ""
                        try:
                            reask_result = _llm_chat_with_response_format_fallback(
                                llm,
                                [
                                    {"role": "system", "content": system},
                                    {"role": "user", "content": _build_sql_json_reask_prompt(raw_content)},
                                ],
                                response_format=response_format,
                                stage="sql_generation_reformat",
                                timeout=json_reask_timeout_s,
                                retry_policy={"max_retries": 1},
                                cancel_check=cancel_check,
                            )
                            reask_raw_content = _llm_content_text(reask_result)
                            parsed = parse_json_object(reask_raw_content)
                            raw_content = reask_raw_content
                            fallback_sql = ""
                        except (json.JSONDecodeError, ValueError) as reask_parse_exc:
                            parse_error_detail = f"{parse_error_detail}; reask={reask_parse_exc}"
                            retry_fallback_sql = _extract_sql_from_raw_text(reask_raw_content)
                            if retry_fallback_sql:
                                fallback_sql = retry_fallback_sql
                            LOGGER.warning(
                                "SQL generation strict JSON re-ask parse failed (attempt %d/%d): %s",
                                attempt + 1,
                                max_retries,
                                reask_parse_exc,
                            )
                        except Exception as reask_exc:
                            parse_error_detail = f"{parse_error_detail}; reask_call={reask_exc}"
                            LOGGER.warning(
                                "SQL generation strict JSON re-ask call failed (attempt %d/%d): %s",
                                attempt + 1,
                                max_retries,
                                reask_exc,
                            )
                if fallback_sql:
                    if json_reask_attempted and _remaining_generation_budget_s() > 0.0:
                        repair = _repair_sql(
                            question,
                            fallback_sql,
                            (
                                "LLM returned non-JSON SQL payload after strict JSON re-ask. "
                                f"Parse details: {parse_error_detail}"
                            ),
                            project_id,
                            semantic_context,
                            language,
                            hit_models=hit_models,
                            analysis=analysis,
                            schema_link_plan=schema_link_plan,
                            cancel_check=cancel_check,
                            timeout_cap_s=_remaining_generation_budget_s(),
                        )
                        repaired_sql = _normalize_sql_candidate(repair.get("sql"))
                        if repaired_sql:
                            parsed = {
                                "sql": repaired_sql,
                                "summary": repair.get("summary") or "Repaired SQL from non-JSON response",
                                "reasoning": repair.get("reasoning")
                                or "Applied strict JSON re-ask and SQL repair fallback.",
                            }
                            active_engine_label = f"{engine_label}_repair"
                        else:
                            LOGGER.info(
                                "SQL generation strict JSON re-ask + repair did not produce SQL; using extracted fallback SQL"
                            )
                            parsed = {
                                "sql": fallback_sql,
                                "summary": "Extracted from non-JSON response",
                                "reasoning": "",
                            }
                    else:
                        LOGGER.info("Extracted SQL from raw text via fallback regex")
                        parsed = {"sql": fallback_sql, "summary": "Extracted from non-JSON response", "reasoning": ""}
                if not isinstance(parsed, dict):
                    LOGGER.warning(
                        "JSON decode error at generation attempt %d/%d: %s; raw[:200]=%r",
                        attempt + 1, max_retries, parse_exc, raw_content[:200],
                    )
                    last_errors.append(
                        f"Attempt {attempt+1}: JSONDecodeError ({parse_exc})"
                    )
                    if _should_retry_generation(attempt, "json_parse"):
                        continue
                    return _sql_gen_error_result(
                        _sql_msg("llm_response_not_parsed", language),
                        "; ".join(last_errors),
                        f"{engine_label}_failed",
                        retrieved_tables=retrieved_tables,
                    )
            sql = _normalize_sql_candidate(parsed.get("sql"))
            if not sql:
                last_errors.append(f"Attempt {attempt+1}: LLM returned empty SQL")
                if _should_retry_generation(attempt, "empty_sql"):
                    continue
                return {
                    "sql": None,
                    "summary": _sql_msg("llm_no_valid_sql", language),
                    "reasoning": "; ".join(last_errors),
                    "retrieved_tables": retrieved_tables,
                    "configured": True,
                    "sql_engine": f"{engine_label}_failed",
                }
            orphan_cte_issues = _validate_no_orphaned_cte(sql)
            orphan_syntax_issue = any(
                str(issue or "").lower().startswith("cte sql syntax is invalid:")
                for issue in orphan_cte_issues
            )
            if orphan_cte_issues and not orphan_syntax_issue:
                LOGGER.warning("Orphaned CTE detected: %s — attempting repair", orphan_cte_issues)
                if _remaining_generation_budget_s() <= 0.0:
                    return _budget_exceeded_error_result(reason="orphan_cte_repair")
                repair = _repair_sql(
                    question,
                    sql,
                    "; ".join(orphan_cte_issues),
                    project_id,
                    semantic_context,
                    language,
                    hit_models=hit_models,
                    analysis=analysis,
                    schema_link_plan=schema_link_plan,
                    cancel_check=cancel_check,
                    timeout_cap_s=_remaining_generation_budget_s(),
                )
                if repair.get("sql") and not _validate_no_orphaned_cte(repair["sql"]):
                    LOGGER.info(
                        "Orphan CTE repair resolved candidate (attempt %d/%d); skipping regeneration retry",
                        attempt + 1,
                        max_retries,
                    )
                    if emit_route_events:
                        _emit_route_event(
                            "sql_repair_short_circuit",
                            {
                                "reason": "orphan_cte",
                                "attempt": attempt + 1,
                                "max_retries": max_retries,
                                "generation_engine": engine_label,
                            },
                            project_id=project_id,
                        )
                    sql = repair["sql"]
                    parsed = {
                        "sql": sql,
                        "summary": repair.get("summary") or parsed.get("summary") or _sql_msg("repaired_orphan_cte", language),
                        "reasoning": repair.get("reasoning") or parsed.get("reasoning"),
                    }
                    active_engine_label = f"{engine_label}_repair"
                else:
                    last_repair_result = {
                        "sql": repair.get("sql"),
                        "summary": repair.get("summary") or _sql_msg("repaired_orphan_cte_best", language),
                        "reasoning": repair.get("reasoning"),
                        "retrieved_tables": retrieved_tables,
                        "configured": True,
                        "sql_engine": f"{engine_label}_repair",
                    }
                    last_errors.append(f"Attempt {attempt+1}: orphan CTE ({orphan_cte_issues[0] if orphan_cte_issues else 'unknown'})")
                    if _should_retry_generation(attempt, "orphan_cte"):
                        continue
                    return last_repair_result
            inspected = guard.inspect(
                sql,
                dimensions=(analysis or {}).get("dimensions") or [],
                hit_models=hit_models,
                resolved=resolved,
                project_id=project_id,
            )
            syntax_issues = list(inspected.syntax_issues)
            if syntax_issues:
                auto_cte_sql = _auto_complete_single_cte_main_select(sql)
                if auto_cte_sql != sql:
                    auto_cte_inspected = guard.inspect(
                        auto_cte_sql,
                        dimensions=(analysis or {}).get("dimensions") or [],
                        hit_models=hit_models,
                        resolved=resolved,
                        project_id=project_id,
                    )
                    if not auto_cte_inspected.syntax_issues:
                        LOGGER.info(
                            "Auto-completed single-CTE SQL with terminal SELECT (attempt %d/%d)",
                            attempt + 1,
                            max_retries,
                        )
                        sql = auto_cte_sql
                        parsed = {
                            "sql": sql,
                            "summary": parsed.get("summary") or "Auto-completed CTE SQL with final SELECT.",
                            "reasoning": parsed.get("reasoning"),
                        }
                        active_engine_label = f"{engine_label}_repair"
                        inspected = auto_cte_inspected
                        syntax_issues = []
            if syntax_issues:
                LOGGER.warning("SQL syntax issues detected before execution (attempt %d/%d): %s", attempt + 1, max_retries, syntax_issues)
                _record_sql_generation_failure(
                    project_id=project_id,
                    question=question,
                    failed_sql=sql,
                    error_text=f"Syntax issues: {syntax_issues}",
                    stage="validate_sql_syntax",
                    sql_engine=engine_label,
                    attempt=attempt + 1,
                    schema_link_snapshot=schema_link_plan,
                    sql_plan_snapshot=sql_plan,
                )
                if _remaining_generation_budget_s() <= 0.0:
                    return _budget_exceeded_error_result(reason="syntax_repair")
                repair = _repair_sql(
                    question,
                    sql,
                    "SQL syntax issues: "
                    + "; ".join(str(item) for item in syntax_issues[:4])
                    + ". Return exactly one valid read-only SELECT/WITH query. "
                    + "If using a CTE, append a final SELECT that references the CTE.",
                    project_id,
                    semantic_context,
                    language,
                    hit_models=hit_models,
                    analysis=analysis,
                    schema_link_plan=schema_link_plan,
                    cancel_check=cancel_check,
                    timeout_cap_s=_remaining_generation_budget_s(),
                )
                repaired_sql = _normalize_sql_candidate(repair.get("sql"))
                if repaired_sql:
                    repaired_inspected = guard.inspect(
                        repaired_sql,
                        dimensions=(analysis or {}).get("dimensions") or [],
                        hit_models=hit_models,
                        resolved=resolved,
                        project_id=project_id,
                    )
                    if not repaired_inspected.syntax_issues:
                        sql = repaired_sql
                        parsed = {
                            "sql": sql,
                            "summary": repair.get("summary") or parsed.get("summary") or _sql_msg("repaired_syntax", language),
                            "reasoning": repair.get("reasoning") or parsed.get("reasoning"),
                        }
                        active_engine_label = f"{engine_label}_repair"
                        inspected = repaired_inspected
                    else:
                        last_errors.append(
                            f"Attempt {attempt+1}: syntax repair failed ({repaired_inspected.syntax_issues[0]})"
                        )
                        if _should_retry_generation(attempt, "syntax"):
                            continue
                        return _syntax_failure_result(engine_label, last_errors, retrieved_tables, language)
                else:
                    last_errors.append(f"Attempt {attempt+1}: syntax repair returned empty SQL")
                    if _should_retry_generation(attempt, "syntax"):
                        continue
                    return _syntax_failure_result(engine_label, last_errors, retrieved_tables, language)
            bad_columns = None if inspected.columns_inconclusive else list(inspected.bad_columns)
            if bad_columns:
                issue_buckets = _summarize_unknown_column_issues(bad_columns)
                dominant_issue_bucket = _dominant_issue_bucket(issue_buckets)
                current_circuitable_buckets = {
                    str(bucket or "")
                    for bucket, count in (issue_buckets or {}).items()
                    if str(bucket or "") in circuitable_issue_buckets and int(count or 0) > 0
                }
                if dominant_issue_bucket:
                    if dominant_issue_bucket == last_unknown_issue_bucket:
                        unknown_issue_bucket_streak += 1
                    else:
                        last_unknown_issue_bucket = dominant_issue_bucket
                        unknown_issue_bucket_streak = 1
                else:
                    last_unknown_issue_bucket = ""
                    unknown_issue_bucket_streak = 0
                if current_circuitable_buckets:
                    circuitable_issue_bucket_streak += 1
                else:
                    circuitable_issue_bucket_streak = 0
                LOGGER.warning("SQL references unknown columns %s — attempting rehint then repair", bad_columns)
                LOGGER.warning("Unknown column issue buckets: %s", issue_buckets)
                if emit_route_events:
                    _emit_route_event(
                        "sql_validation_issue",
                        {
                            "stage": "validate_sql_columns",
                            "issue_buckets": issue_buckets,
                            "generation_engine": engine_label,
                            "attempt": attempt + 1,
                        },
                        project_id=project_id,
                    )
                _record_sql_generation_failure(
                    project_id=project_id,
                    question=question,
                    failed_sql=sql,
                    error_text=f"Unknown columns: {bad_columns}",
                    stage="validate_sql_columns",
                    sql_engine=engine_label,
                    attempt=attempt + 1,
                    issue_buckets=issue_buckets,
                    schema_link_snapshot=schema_link_plan,
                        sql_plan_snapshot=sql_plan,
                    )
                rehinted = _apply_owner_selector_rules(
                    sql,
                    hit_models,
                    bad_columns=bad_columns,
                    schema_link_plan=schema_link_plan,
                )
                local_rewrite_stages: list[str] = []
                if rehinted != sql:
                    local_rewrite_stages.append("owner_selector")
                if issue_buckets.get("hallucinated_column"):
                    hallucinated_rehinted = _apply_hallucinated_column_rewrite_rules(
                        rehinted,
                        bad_columns,
                        hit_models=hit_models,
                    )
                    if hallucinated_rehinted != rehinted:
                        rehinted = hallucinated_rehinted
                        local_rewrite_stages.append("hallucinated_column")
                if issue_buckets.get("alias_scope_leak"):
                    alias_scope_rehinted = _apply_alias_scope_rewrite_rules(
                        rehinted,
                        bad_columns,
                        hit_models=hit_models,
                        schema_link_plan=schema_link_plan,
                    )
                    if alias_scope_rehinted != rehinted:
                        rehinted = alias_scope_rehinted
                        local_rewrite_stages.append("alias_scope")
                rehinted_validated = _validate_sql_columns(rehinted, hit_models) if rehinted != sql else bad_columns
                if rehinted != sql and rehinted_validated is not None and not rehinted_validated:
                    if local_rewrite_stages:
                        LOGGER.info(
                            "Local unknown-column rewrites resolved references before LLM repair (stages=%s)",
                            local_rewrite_stages,
                        )
                    else:
                        LOGGER.info("Rehinted SQL resolved column references: %s -> %s", bad_columns[:3], rehinted_validated)
                    sql = rehinted
                    parsed = {"sql": sql, "summary": parsed.get("summary"), "reasoning": parsed.get("reasoning")}
                    bad_columns = rehinted_validated
                    if not bad_columns:
                        sql, group_issues = _enforce_group_by_constraints(
                            sql,
                            (analysis or {}).get("dimensions") or [],
                            hit_models=hit_models,
                            resolved=resolved,
                        )
                        parsed = {"sql": sql, "summary": parsed.get("summary"), "reasoning": parsed.get("reasoning")}
                        if not group_issues:
                            fixed_sql = _fix_type_mismatch_multiply(sql, hit_models)
                            if fixed_sql != sql:
                                sql = fixed_sql
                                parsed = {"sql": sql, "summary": parsed.get("summary"), "reasoning": parsed.get("reasoning")}
                            sql, agg_issues = _auto_repair_aggregation_issues(
                                sql,
                                dimensions=(analysis or {}).get("dimensions") or [],
                                hit_models=hit_models,
                                resolved=resolved,
                            )
                            parsed = {"sql": sql, "summary": parsed.get("summary"), "reasoning": parsed.get("reasoning")}
                            if agg_issues:
                                LOGGER.warning("SQL aggregation issues after rehint-repair: %s", agg_issues)
                            local_summary = _sql_msg("auto_corrected_column_refs", language)
                            if local_rewrite_stages == ["alias_scope"]:
                                local_summary = _sql_msg("auto_corrected_alias_scope", language)
                            elif "hallucinated_column" in local_rewrite_stages:
                                local_summary = _sql_msg("auto_corrected_metric_cols", language)
                            return {
                                "sql": sql,
                                "summary": parsed.get("summary") or local_summary,
                                "reasoning": parsed.get("reasoning"),
                                "retrieved_tables": retrieved_tables,
                                "configured": True,
                                "sql_engine": f"{engine_label}_rehint",
                            }
                dominant_bucket_repeated = (
                    bool(dominant_issue_bucket)
                    and dominant_issue_bucket in circuitable_issue_buckets
                    and unknown_issue_bucket_streak >= unknown_issue_bucket_circuit_threshold
                )
                circuitable_bucket_streak_open = (
                    bool(current_circuitable_buckets)
                    and circuitable_issue_bucket_streak >= unknown_issue_bucket_circuit_threshold
                )
                bucket_circuit_open = dominant_bucket_repeated or circuitable_bucket_streak_open
                circuit_bucket_label = (
                    dominant_issue_bucket
                    if dominant_issue_bucket in current_circuitable_buckets
                    else (sorted(current_circuitable_buckets)[0] if current_circuitable_buckets else dominant_issue_bucket)
                )
                if bucket_circuit_open:
                    LOGGER.warning(
                        "Unknown column issue bucket '%s' repeated %d time(s) (circuitable streak=%d); opening local repair circuit",
                        circuit_bucket_label,
                        unknown_issue_bucket_streak if dominant_bucket_repeated else circuitable_issue_bucket_streak,
                        circuitable_issue_bucket_streak,
                    )
                    local_candidate = _apply_owner_selector_rules(
                        sql,
                        hit_models,
                        bad_columns=bad_columns,
                        schema_link_plan=schema_link_plan,
                    )
                    local_candidate = _apply_hallucinated_column_rewrite_rules(
                        local_candidate,
                        bad_columns,
                        hit_models=hit_models,
                    )
                    local_candidate = _apply_alias_scope_rewrite_rules(
                        local_candidate,
                        bad_columns,
                        hit_models=hit_models,
                        schema_link_plan=schema_link_plan,
                    )
                    local_candidate, local_group_issues = _enforce_group_by_constraints(
                        local_candidate,
                        (analysis or {}).get("dimensions") or [],
                        hit_models=hit_models,
                        resolved=resolved,
                    )
                    local_validation = _validate_sql_columns(local_candidate, hit_models)
                    if (local_validation is None or not local_validation) and not local_group_issues:
                        LOGGER.info(
                            "Unknown column repair circuit resolved SQL locally (bucket=%s)",
                            circuit_bucket_label,
                        )
                        if emit_route_events:
                            _emit_route_event(
                                "sql_repair_short_circuit",
                                {
                                    "reason": "bucket_circuit_local_fix",
                                    "issue_bucket": circuit_bucket_label,
                                    "attempt": attempt + 1,
                                    "max_retries": max_retries,
                                    "generation_engine": engine_label,
                                    "dominant_issue_bucket": dominant_issue_bucket,
                                    "issue_bucket_streak": unknown_issue_bucket_streak,
                                    "circuitable_issue_bucket_streak": circuitable_issue_bucket_streak,
                                },
                                project_id=project_id,
                            )
                        fixed_sql = _fix_type_mismatch_multiply(local_candidate, hit_models)
                        if fixed_sql != local_candidate:
                            local_candidate = fixed_sql
                        local_candidate, agg_issues = _auto_repair_aggregation_issues(
                            local_candidate,
                            dimensions=(analysis or {}).get("dimensions") or [],
                            hit_models=hit_models,
                            resolved=resolved,
                        )
                        if agg_issues:
                            LOGGER.warning("SQL aggregation issues after local circuit fix: %s", agg_issues)
                        reasoning = parsed.get("reasoning") or ""
                        if agg_issues:
                            warn = ", ".join(str(item) for item in agg_issues)
                            reasoning = f"{reasoning} [WARN: Aggregation issues: {warn}]" if reasoning else f"Aggregation issues: {warn}"
                        return {
                            "sql": local_candidate,
                            "summary": parsed.get("summary") or _sql_msg("auto_corrected_validation", language),
                            "reasoning": reasoning,
                            "retrieved_tables": retrieved_tables,
                            "configured": True,
                            "sql_engine": f"{engine_label}_rehint",
                        }

                    if emit_route_events:
                        _emit_route_event(
                            "sql_repair_short_circuit",
                            {
                                "reason": "repeated_issue_bucket",
                                "issue_bucket": circuit_bucket_label,
                                "attempt": attempt + 1,
                                "max_retries": max_retries,
                                "generation_engine": engine_label,
                                "dominant_issue_bucket": dominant_issue_bucket,
                                "issue_bucket_streak": unknown_issue_bucket_streak,
                                "circuitable_issue_bucket_streak": circuitable_issue_bucket_streak,
                            },
                            project_id=project_id,
                        )
                    circuit_reasoning = (
                        f"Stopped LLM repair retries after repeated unknown-column bucket '{circuit_bucket_label}'"
                    )
                    if bad_columns:
                        circuit_reasoning += f" (example: {bad_columns[0]})."
                    if local_group_issues:
                        circuit_reasoning += f" GROUP BY issues: {'; '.join(local_group_issues)}."
                    return {
                        "sql": None,
                        "summary": _sql_msg("validation_circuit_open", language),
                        "reasoning": circuit_reasoning,
                        "retrieved_tables": retrieved_tables,
                        "configured": True,
                        "sql_engine": f"{engine_label}_validation_circuit_open",
                    }
                if _remaining_generation_budget_s() <= 0.0:
                    return _budget_exceeded_error_result(reason="column_repair")
                repair = _repair_sql(
                    question,
                    sql,
                    f"Unknown columns: {bad_columns}",
                    project_id,
                    semantic_context,
                    language,
                    hit_models=hit_models,
                    analysis=analysis,
                    schema_link_plan=schema_link_plan,
                    cancel_check=cancel_check,
                    timeout_cap_s=_remaining_generation_budget_s(),
                )
                repair_validated = _validate_sql_columns(repair["sql"], hit_models) if repair.get("sql") and repair["sql"].strip() else None
                if repair.get("sql") and repair_validated:
                    repaired_by_rules = _apply_owner_selector_rules(
                        repair.get("sql") or "",
                        hit_models,
                        bad_columns=repair_validated,
                        schema_link_plan=schema_link_plan,
                    )
                    repaired_by_rules = _apply_hallucinated_column_rewrite_rules(
                        repaired_by_rules,
                        repair_validated,
                        hit_models=hit_models,
                    )
                    repaired_by_rules = _apply_alias_scope_rewrite_rules(
                        repaired_by_rules,
                        repair_validated,
                        hit_models=hit_models,
                        schema_link_plan=schema_link_plan,
                    )
                    if repaired_by_rules != repair.get("sql"):
                        repaired_by_rules_validated = _validate_sql_columns(repaired_by_rules, hit_models)
                        if repaired_by_rules_validated is not None and not repaired_by_rules_validated:
                            repair["sql"] = repaired_by_rules
                            repair_validated = repaired_by_rules_validated
                if repair.get("sql") and (repair_validated is not None and not repair_validated):
                    _record_sql_generation_failure(
                        project_id=project_id,
                        question=question,
                        failed_sql=sql,
                        error_text=f"Unknown columns: {bad_columns}",
                        stage="repair_sql_columns",
                        sql_engine=f"{engine_label}_repair",
                        attempt=attempt + 1,
                        issue_buckets=issue_buckets,
                        repaired_sql=repair.get("sql"),
                        resolved=True,
                        schema_link_snapshot=schema_link_plan,
                        sql_plan_snapshot=sql_plan,
                    )
                    LOGGER.info(
                        "Column repair resolved unknown references (attempt %d/%d); skipping regeneration retry",
                        attempt + 1,
                        max_retries,
                    )
                    if emit_route_events:
                        _emit_route_event(
                            "sql_repair_short_circuit",
                            {
                                "reason": "column_validation",
                                "attempt": attempt + 1,
                                "max_retries": max_retries,
                                "generation_engine": engine_label,
                            },
                            project_id=project_id,
                        )
                    sql = repair["sql"]
                    parsed = {
                        "sql": sql,
                        "summary": repair.get("summary") or parsed.get("summary") or _sql_msg("repaired_column_validation", language),
                        "reasoning": repair.get("reasoning") or parsed.get("reasoning"),
                    }
                    active_engine_label = f"{engine_label}_repair"
                else:
                    last_repair_result = {
                        "sql": repair.get("sql"),
                        "summary": repair.get("summary") or _sql_msg("repaired_column_best", language),
                        "reasoning": repair.get("reasoning"),
                        "retrieved_tables": retrieved_tables,
                        "configured": True,
                        "sql_engine": f"{engine_label}_repair",
                    }
                    last_errors.append(
                        f"Attempt {attempt+1}: bad columns ({bad_columns[0] if bad_columns else 'unknown'}) buckets={issue_buckets}"
                    )
                    if _should_retry_generation(attempt, "bad_columns"):
                        continue
                    if repair.get("sql") and repair_validated is None:
                        LOGGER.warning("Column validation inconclusive for repaired SQL — using as-is")
                        return {
                            "sql": repair["sql"],
                            "summary": repair.get("summary") or _sql_msg("repaired_column_best", language),
                            "reasoning": repair.get("reasoning"),
                            "retrieved_tables": retrieved_tables,
                            "configured": True,
                            "sql_engine": f"{engine_label}_repair",
                        }
                    return last_repair_result
            sql, group_issues = _enforce_group_by_constraints(
                sql,
                (analysis or {}).get("dimensions") or [],
                hit_models=hit_models,
                resolved=resolved,
            )
            parsed = {"sql": sql, "summary": parsed.get("summary"), "reasoning": parsed.get("reasoning")}
            if group_issues:
                candidate_sql = sql
                auto_group_sql = _apply_group_by_completion_rules(candidate_sql)
                if auto_group_sql != candidate_sql:
                    auto_columns = _validate_sql_columns(auto_group_sql, hit_models)
                    auto_group_issues = _validate_sql_group_by(
                        auto_group_sql,
                        (analysis or {}).get("dimensions") or [],
                        hit_models=hit_models,
                        resolved=resolved,
                    )
                    if (auto_columns is None or not auto_columns) and not auto_group_issues:
                        LOGGER.info("Auto-completed GROUP BY with local rules; skipping LLM repair")
                        sql = auto_group_sql
                        parsed = {
                            "sql": sql,
                            "summary": parsed.get("summary"),
                            "reasoning": parsed.get("reasoning"),
                        }
                        group_issues = []
                    else:
                        candidate_sql = auto_group_sql
                if group_issues and resolved and resolved.get("dimensions_resolved"):
                    resolved_group_sql = _apply_resolved_dimension_group_by_rules(
                        candidate_sql,
                        resolved,
                        hit_models=hit_models,
                    )
                    if resolved_group_sql != candidate_sql:
                        resolved_columns = _validate_sql_columns(resolved_group_sql, hit_models)
                        resolved_group_issues = _validate_sql_group_by(
                            resolved_group_sql,
                            (analysis or {}).get("dimensions") or [],
                            hit_models=hit_models,
                            resolved=resolved,
                        )
                        if (resolved_columns is None or not resolved_columns) and not resolved_group_issues:
                            LOGGER.info("Auto-completed GROUP BY using resolved dimensions; skipping LLM repair")
                            sql = resolved_group_sql
                            parsed = {
                                "sql": sql,
                                "summary": parsed.get("summary"),
                                "reasoning": parsed.get("reasoning"),
                            }
                            group_issues = []
                if group_issues:
                    LOGGER.warning("SQL GROUP BY may be incomplete (dimensions: %s): %s", (analysis or {}).get("dimensions"), group_issues)
                    _record_sql_generation_failure(
                        project_id=project_id,
                        question=question,
                        failed_sql=sql,
                        error_text=f"GROUP BY issues: {group_issues}",
                        stage="validate_group_by",
                        sql_engine=engine_label,
                        attempt=attempt + 1,
                        schema_link_snapshot=schema_link_plan,
                        sql_plan_snapshot=sql_plan,
                    )
                    missing_hint = "; ".join(group_issues)
                    agg_hint_issues = _validate_sql_aggregation(sql)
                    agg_hint = ""
                    if agg_hint_issues:
                        agg_hint = (
                            " Aggregation consistency issues: "
                            + "; ".join(agg_hint_issues[:6])
                            + ". Ensure every non-aggregated SELECT column is present in GROUP BY."
                        )
                    resolved_hint = ""
                    if resolved and resolved.get("dimensions_resolved"):
                        dim_lines = []
                        for entry in resolved["dimensions_resolved"]:
                            dim_lines.append(f"{entry['column']} (model: {entry['model']})")
                        resolved_hint = " Resolved dimension columns: " + ", ".join(dim_lines)
                    if _remaining_generation_budget_s() <= 0.0:
                        return _budget_exceeded_error_result(reason="group_by_repair")
                    repair = _repair_sql(
                        question,
                        sql,
                        f"GROUP BY is incomplete. {missing_hint}.{agg_hint} Add all missing dimension columns to both GROUP BY and SELECT.{resolved_hint}",
                        project_id,
                        semantic_context,
                        language,
                        hit_models=hit_models,
                        analysis=analysis,
                        schema_link_plan=schema_link_plan,
                        cancel_check=cancel_check,
                        timeout_cap_s=_remaining_generation_budget_s(),
                    )
                    if repair.get("sql") and repair["sql"].strip():
                        repair["sql"], repaired_group = _enforce_group_by_constraints(
                            repair["sql"],
                            (analysis or {}).get("dimensions") or [],
                            hit_models=hit_models,
                            resolved=resolved,
                        )
                        repaired_columns = _validate_sql_columns(repair["sql"], hit_models)
                        if (repaired_columns is not None and not repaired_columns) and not repaired_group:
                            _record_sql_generation_failure(
                                project_id=project_id,
                                question=question,
                                failed_sql=sql,
                                error_text=f"GROUP BY issues: {group_issues}",
                                stage="repair_group_by",
                                sql_engine=f"{engine_label}_repair",
                                attempt=attempt + 1,
                                repaired_sql=repair.get("sql"),
                                resolved=True,
                                schema_link_snapshot=schema_link_plan,
                                sql_plan_snapshot=sql_plan,
                            )
                            sql = repair["sql"]
                            parsed = {"sql": sql, "summary": repair.get("summary") or parsed.get("summary"), "reasoning": repair.get("reasoning") or parsed.get("reasoning")}
                            group_issues = []
                        elif not repaired_group:
                            sql = repair["sql"]
                            parsed = {"sql": sql, "summary": repair.get("summary") or parsed.get("summary"), "reasoning": repair.get("reasoning") or parsed.get("reasoning")}
                            group_issues = []
                        elif repaired_columns is not None and not repaired_columns:
                            sql = repair["sql"]
                            parsed = {"sql": sql, "summary": repair.get("summary") or parsed.get("summary"), "reasoning": repair.get("reasoning") or parsed.get("reasoning")}
                            LOGGER.warning("GROUP BY repair partially fixed columns but GROUP BY still incomplete: %s", repaired_group)
            if group_issues and len((analysis or {}).get("dimensions") or []) > 1:
                last_errors.append(
                    f"Attempt {attempt+1}: GROUP BY incomplete ({group_issues[0] if group_issues else 'unknown'})"
                )
                if _should_retry_generation(attempt, "group_by"):
                    continue
            fixed_sql = _fix_type_mismatch_multiply(sql, hit_models)
            if fixed_sql != sql:
                LOGGER.info("Fixed string*aggregate type mismatch in SQL: replaced * with ,")
                sql = fixed_sql
                parsed = {"sql": sql, "summary": parsed.get("summary"), "reasoning": parsed.get("reasoning")}
            sql, agg_issues = _auto_repair_aggregation_issues(
                sql,
                dimensions=(analysis or {}).get("dimensions") or [],
                hit_models=hit_models,
                resolved=resolved,
            )
            parsed = {"sql": sql, "summary": parsed.get("summary"), "reasoning": parsed.get("reasoning")}
            if agg_issues:
                LOGGER.warning("SQL aggregation consistency issues: %s", agg_issues)
            warning_parts = []
            if group_issues:
                warning_parts.append(f"GROUP BY incomplete: {', '.join(str(g) for g in group_issues)}")
            if agg_issues:
                warning_parts.append(f"Aggregation issues: {', '.join(str(a) for a in agg_issues)}")
            summary = parsed.get("summary") or "Generated SQL for your question."
            reasoning = parsed.get("reasoning") or ""
            if warning_parts:
                reasoning = f"{reasoning} [WARN: {'; '.join(warning_parts)}]" if reasoning else "; ".join(warning_parts)
            return {
                "sql": sql,
                "summary": summary,
                "reasoning": reasoning,
                "retrieved_tables": retrieved_tables,
                "configured": True,
                "sql_engine": active_engine_label,
            }
        except LLMCircuitOpenError as e:
            LOGGER.warning("LLM circuit is open; aborting retry loop")
            return _sql_gen_error_result(
                _sql_msg("circuit_breaker_open", language),
                str(e),
                "llm_fallback_circuit_open",
                retrieved_tables=retrieved_tables,
            )
        except (json.JSONDecodeError, KeyError, AttributeError, TypeError, ValueError, httpx.HTTPError, ConnectionError, TimeoutError) as e:
            LOGGER.warning("LLM request failed (attempt %d/%d): %s", attempt + 1, max_retries, e)
            last_errors.append(f"Attempt {attempt+1}: {type(e).__name__} ({e})")
            if _should_retry_generation(attempt, "transport"):
                continue
            return _sql_gen_error_result(
                _sql_msg("llm_response_not_parsed", language),
                "; ".join(last_errors),
                "llm_fallback_parse_error",
                retrieved_tables=retrieved_tables,
            )
    return _sql_gen_error_result(
        _sql_msg("failed_generate_sql", language),
        "; ".join(last_errors),
        "llm_fallback_retry_exhausted",
        retrieved_tables=retrieved_tables,
    )


def _repair_sql(
    question: str,
    failed_sql: str,
    error: str,
    project_id: int,
    semantic_context: Optional[str] = None,
    language: Optional[str] = None,
    hit_models: Optional[list[dict[str, Any]]] = None,
    analysis: Optional[dict] = None,
    schema_link_plan: Optional[dict[str, Any]] = None,
    cancel_check: Optional[Callable[[], None]] = None,
    timeout_cap_s: Optional[float] = None,
) -> dict[str, Any]:
    if cancel_check:
        cancel_check()
    from services.llm_service import get_llm_config as _get_llm_config
    from services.sql_routing.llm_capability import get_model_capabilities, _capabilities_to_tier, _get_repair_config
    _llm_cfg = _get_llm_config()
    caps = get_model_capabilities(
        _llm_cfg.get("provider", ""),
        _llm_cfg.get("endpoint", ""),
        _llm_cfg.get("model", ""),
    )
    tier = _capabilities_to_tier(caps)
    repair_config = _get_repair_config(tier)
    if repair_config.get("max_repair_attempts", 0) <= 0:
        return {
            "sql": None,
            "summary": None,
            "reasoning": "Repair not supported for this model tier.",
            "configured": True,
        }

    repair_timeout = repair_config.get("repair_timeout_s", 60)
    try:
        repair_timeout = float(repair_timeout)
    except Exception:
        repair_timeout = 60.0
    if not math.isfinite(repair_timeout) or repair_timeout <= 0.0:
        repair_timeout = 60.0
    repair_timeout_cap = _coerce_float_setting(
        ROUTER_CONFIG.get("sql_repair_timeout_cap_s"),
        20.0,
        2.0,
        120.0,
    )
    repair_timeout = min(repair_timeout, repair_timeout_cap)
    try:
        timeout_cap = None if timeout_cap_s is None else float(timeout_cap_s)
    except Exception:
        timeout_cap = None
    if timeout_cap is not None and math.isfinite(timeout_cap) and timeout_cap > 0.0:
        repair_timeout = min(float(repair_timeout), timeout_cap)
    repair_timeout = max(0.5, float(repair_timeout))

    repair_skip_if_remaining_budget_below_s = _coerce_float_setting(
        ROUTER_CONFIG.get("sql_repair_skip_if_remaining_budget_below_s"),
        8.0,
        0.5,
        60.0,
    )
    if timeout_cap is not None and math.isfinite(timeout_cap) and timeout_cap < repair_skip_if_remaining_budget_below_s:
        _emit_route_event(
            "sql_repair_short_circuit",
            {
                "reason": "repair_budget_low",
                "remaining_budget_seconds": round(max(0.0, timeout_cap), 3),
                "threshold_seconds": repair_skip_if_remaining_budget_below_s,
            },
            project_id=project_id,
        )
        return {
            "sql": None,
            "summary": None,
            "reasoning": (
                "Repair skipped due to low remaining generation budget "
                f"({max(0.0, timeout_cap):.2f}s < {repair_skip_if_remaining_budget_below_s:.2f}s)."
            ),
            "configured": True,
        }
    skip_if_empty = repair_config.get("skip_repair_if_json_empty", False)
    retry_binder = repair_config.get("retry_on_binder_error", True)

    if not failed_sql.strip():
        return {
            "sql": None,
            "summary": None,
            "reasoning": "Empty or whitespace-only SQL — repair skipped.",
            "configured": True,
        }

    if not retry_binder and any(kw in error.lower() for kw in ["binder", "catalog", "not found"]):
        return {
            "sql": None,
            "summary": None,
            "reasoning": "Binder/catalog error — repair not retried per tier config.",
            "configured": True,
        }

    if _normalize_bool(ROUTER_CONFIG.get("sql_repair_local_preflight_enabled", True)):
        local_sql, local_stages = _local_sql_repair_preflight(failed_sql, error, project_id)
        if local_sql:
            _emit_route_event(
                "sql_repair_short_circuit",
                {
                    "reason": "local_preflight",
                    "stages": local_stages,
                },
                project_id=project_id,
            )
            return {
                "sql": local_sql,
                "summary": _sql_msg("repaired_sql", language),
                "reasoning": f"Applied local repair preflight ({', '.join(local_stages)}).",
                "configured": True,
            }

    if semantic_context is None:
        semantic_context, _, _ = _semantic_prompt(project_id, question, require_hits=True, analysis=analysis, language=language)
    strict_json = _strict_json_capability()
    prompt_selection = _prompt_profile_selection(
        "sql_repair",
        strict_json_mode=strict_json.get("mode", "none"),
        model_tier=tier,
    )
    use_profile = _is_sql_route_v2_enabled(project_id) or bool(ROUTER_CONFIG.get("sql_route_shadow_mode", False))
    response_format = prompt_selection.response_format if (use_profile and prompt_selection.response_format) else "json"
    system_suffix = f"\n<PROFILE>{prompt_selection.system_suffix}</PROFILE>" if use_profile and prompt_selection.system_suffix else ""
    llm = LLMService()
    if not llm.is_configured():
        return {"sql": None, "reasoning": "LLM provider is not configured.", "configured": False}
    column_map_hint = ""
    if hit_models:
        col_owners: dict[str, list[str]] = {}
        for model in hit_models:
            mname = model.get("name", "")
            columns = model.get("matched_columns") if model.get("matched_columns") is not None else model.get("columns", [])
            for c in columns:
                cn = (c.get("name") or "").lower()
                if cn:
                    col_owners.setdefault(cn, [])
                    if mname not in col_owners[cn]:
                        col_owners[cn].append(mname)
        misowned = [p for p in re.split(r';\s+', error) if "belongs on:" in p or "not found" in p]
        if misowned:
            column_map_hint = "\nColumn ownership reference (use these mappings to fix aliases):\n"
            for m in misowned:
                column_map_hint += f"- {m.strip()}\n"
    ambiguous_owner_hint = _build_ambiguous_owner_hint(
        failed_sql,
        error,
        hit_models,
        schema_link_plan=schema_link_plan,
    )
    repair_system_msg = {
        "role": "system",
        "content": _build_sql_system_message(system_suffix, _dialect_hint_for_project(project_id), language),
    }
    _safe_error = _sanitize_error_message(error)
    repair_user_base = (
        f"Semantic model:\n{semantic_context}\n\n"
        f"Question: {_truncate_question(question)}\nFailed SQL:\n```\n{failed_sql}\n```\nError:\n{_safe_error}\n"
        f"{_sql_msg('fix_sql_rules', language)}"
        "- Every column must be prefixed with its owning model's alias. If a column does not exist under its current prefix, move it to the correct model alias per the ownership mapping.\n"
        "- Within the same SELECT scope, every table/CTE alias must be unique. Do not reuse the same alias (e.g. T1) for multiple sources.\n"
        "- In grouped or aggregated queries, ORDER BY and HAVING columns must be GROUP BY keys or wrapped in aggregate functions (e.g. MAX(...), ANY_VALUE(...)).\n"
        "- Do not guess — look up each column in the semantic model above and use its owning model's alias."
        f"{column_map_hint}{ambiguous_owner_hint}"
    )
    if cancel_check:
        cancel_check()
    max_retries = max(1, repair_config.get("max_repair_attempts", 2))
    repair_started_at = time.monotonic()
    last_errors: list[str] = []
    last_unknown_issue_bucket = ""
    unknown_issue_bucket_streak = 0
    circuitable_issue_bucket_streak = 0
    unknown_issue_bucket_circuit_threshold = int(ROUTER_CONFIG.get("unknown_issue_bucket_circuit_threshold", 3))
    circuitable_issue_buckets = {
        "alias_scope_leak",
        "wrong_alias_owner",
        "ambiguous_owner",
        "hallucinated_column",
        "cte_projection_missing",
    }
    for attempt in range(max_retries):
        if cancel_check:
            cancel_check()
        if timeout_cap_s is not None and time.monotonic() - repair_started_at >= timeout_cap_s:
            LOGGER.warning("Repair budget exhausted (attempt %d/%d)", attempt + 1, max_retries)
            break
        user_content = _build_retry_user_message(repair_user_base, last_errors, attempt, max_retries)
        messages = [repair_system_msg, {"role": "user", "content": user_content}]
        try:
            result = _llm_chat_with_response_format_fallback(
                llm,
                messages,
                response_format=response_format,
                stage="sql_repair",
                timeout=repair_timeout,
                cancel_check=cancel_check,
            )
            raw_content = _llm_content_text(result)
            if not raw_content.strip():
                LOGGER.warning("LLM returned empty SQL repair payload (attempt %d/%d)", attempt + 1, max_retries)
                last_errors.append(f"Attempt {attempt+1}: empty LLM content")
                if attempt < max_retries - 1:
                    continue
                break
            try:
                parsed = parse_json_object(raw_content)
                sql = _normalize_sql_candidate(parsed.get("sql"))
                if not sql:
                    last_errors.append(f"Attempt {attempt+1}: empty repaired SQL")
                    if attempt < max_retries - 1:
                        continue
                    break
                if hit_models:
                    repair_bad_columns = _validate_sql_columns(sql, hit_models)
                    if repair_bad_columns:
                        issue_buckets = _summarize_unknown_column_issues(repair_bad_columns)
                        dominant_issue_bucket = _dominant_issue_bucket(issue_buckets)
                        current_circuitable_buckets = {
                            str(bucket or "")
                            for bucket, count in (issue_buckets or {}).items()
                            if str(bucket or "") in circuitable_issue_buckets and int(count or 0) > 0
                        }
                        if dominant_issue_bucket:
                            if dominant_issue_bucket == last_unknown_issue_bucket:
                                unknown_issue_bucket_streak += 1
                            else:
                                last_unknown_issue_bucket = dominant_issue_bucket
                                unknown_issue_bucket_streak = 1
                        else:
                            last_unknown_issue_bucket = ""
                            unknown_issue_bucket_streak = 0
                        if current_circuitable_buckets:
                            circuitable_issue_bucket_streak += 1
                        else:
                            circuitable_issue_bucket_streak = 0
                        dominant_bucket_repeated = (
                            bool(dominant_issue_bucket)
                            and dominant_issue_bucket in circuitable_issue_buckets
                            and unknown_issue_bucket_streak >= unknown_issue_bucket_circuit_threshold
                        )
                        circuitable_bucket_streak_open = (
                            bool(current_circuitable_buckets)
                            and circuitable_issue_bucket_streak >= unknown_issue_bucket_circuit_threshold
                        )
                        bucket_circuit_open = dominant_bucket_repeated or circuitable_bucket_streak_open
                        circuit_bucket_label = (
                            dominant_issue_bucket
                            if dominant_issue_bucket in current_circuitable_buckets
                            else (sorted(current_circuitable_buckets)[0] if current_circuitable_buckets else dominant_issue_bucket)
                        )
                        LOGGER.warning(
                            "Repaired SQL still references unknown columns (attempt %d/%d): %s; buckets=%s",
                            attempt + 1,
                            max_retries,
                            repair_bad_columns,
                            issue_buckets,
                        )
                        if bucket_circuit_open:
                            _emit_route_event(
                                "sql_repair_short_circuit",
                                {
                                    "reason": "repeated_issue_bucket",
                                    "issue_bucket": circuit_bucket_label,
                                    "dominant_issue_bucket": dominant_issue_bucket,
                                    "attempt": attempt + 1,
                                    "max_retries": max_retries,
                                    "issue_bucket_streak": unknown_issue_bucket_streak,
                                    "circuitable_issue_bucket_streak": circuitable_issue_bucket_streak,
                                },
                                project_id=project_id,
                            )
                            reason = (
                                f"Repair stopped after repeated unknown-column bucket '{circuit_bucket_label}'"
                            )
                            if repair_bad_columns:
                                reason += f" (example: {repair_bad_columns[0]})."
                            return {
                                "sql": None,
                                "summary": None,
                                "reasoning": reason,
                                "configured": True,
                            }
                        last_errors.append(
                            "Attempt "
                            + str(attempt + 1)
                            + ": repair unresolved columns ("
                            + str(repair_bad_columns[0] if repair_bad_columns else "unknown")
                            + f") buckets={issue_buckets}"
                        )
                        if attempt < max_retries - 1:
                            continue
                        break
                return {
                    "sql": sql,
                    "summary": parsed.get("summary") or _sql_msg("repaired_sql", language),
                    "reasoning": parsed.get("reasoning"),
                    "configured": True,
                }
            except (json.JSONDecodeError, KeyError, AttributeError, TypeError, ValueError) as e:
                fallback_sql = _extract_sql_from_llm_text(raw_content)
                if fallback_sql:
                    LOGGER.warning("LLM call failed in _repair_sql: %s; using plain-text SQL fallback", e)
                    return {
                        "sql": fallback_sql,
                        "summary": _sql_msg("repaired_sql", language),
                        "reasoning": f"{_sql_msg('repair_failed', language)}: {e}; extracted SQL from plain-text fallback.",
                        "configured": True,
                    }
                LOGGER.warning("LLM call failed (attempt %d/%d): %s", attempt + 1, max_retries, e)
                last_errors.append(f"Attempt {attempt+1}: {type(e).__name__} ({e})")
                if attempt < max_retries - 1:
                    continue
                break
        except LLMCircuitOpenError as e:
            LOGGER.warning("LLM circuit is open; aborting repair retry loop")
            return {
                "sql": None,
                "summary": None,
                "reasoning": f"{_sql_msg('repair_failed', language)}: {e}",
                "configured": True,
            }
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            LOGGER.warning("LLM request failed (attempt %d/%d): %s", attempt + 1, max_retries, e)
            last_errors.append(f"Attempt {attempt+1}: {type(e).__name__} ({e})")
            is_timeout = isinstance(e, (TimeoutError, socket.timeout, httpx.TimeoutException))
            if is_timeout:
                _emit_route_event(
                    "sql_repair_short_circuit",
                    {
                        "reason": "repair_timeout",
                        "attempt": attempt + 1,
                        "max_retries": max_retries,
                    },
                    project_id=project_id,
                )
                break
            if attempt < max_retries - 1:
                continue
            break
    return {
        "sql": None,
        "summary": None,
        "reasoning": f"{_sql_msg('repair_failed', language)}: {'; '.join(last_errors) if last_errors else _sql_msg('llm_call_error', language)}",
        "configured": True,
    }


def _extract_sql_from_llm_text(content: Any) -> str | None:
    text = str(content or "").strip()
    if not text:
        return None
    candidates: list[str] = []
    for match in re.finditer(r"```(?:\w+)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE):
        block = str(match.group(1) or "").strip()
        if block:
            candidates.append(block)
    candidates.append(text)
    for candidate in candidates:
        sample = str(candidate or "").strip()
        if not sample:
            continue
        try:
            parsed = parse_json_object(sample)
            if isinstance(parsed, dict):
                json_sql = _normalize_sql_candidate(parsed.get("sql"))
                if json_sql:
                    return json_sql
        except Exception:
            pass
        sql_match = re.search(r"(?is)\b(?:WITH|SELECT)\b[\s\S]*", sample)
        if not sql_match:
            continue
        sql_candidate = sql_match.group(0).strip()
        sql_candidate = re.split(
            r"(?im)^\s*(?:summary|reasoning|explanation|notes?)\s*[:：]",
            sql_candidate,
            maxsplit=1,
        )[0].strip()
        if not sql_candidate:
            continue
        sql_candidate = _normalize_sql_candidate(sql_candidate)
        if sql_candidate:
            return sql_candidate
    return None


def _strip_sql_json_leak(content: str) -> str:
    def _strip_reasoning_trace(text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return cleaned

        # Some reasoning-oriented models may emit hidden deliberation in <think> blocks.
        cleaned = re.sub(r"(?is)<think\b[^>]*>\s*[\s\S]*?\s*</think>", "", cleaned).strip()
        if not cleaned:
            return ""

        starts_with_thinking = bool(
            re.match(r"(?is)^\s*(?:thinking\s*process|reasoning\s*process|思考过程|推理过程)\s*[:：]", cleaned)
        )
        if starts_with_thinking:
            parts = re.split(
                r"(?im)^\s*(?:final\s*answer|final\s*response|answer|response|最终答案|最终回复|最终回答|回答|答复)\s*[:：]\s*",
                cleaned,
                maxsplit=1,
            )
            if len(parts) == 2 and parts[1].strip():
                cleaned = parts[1].strip()
            else:
                return ""

        cleaned = re.sub(
            r"(?im)^\s*(?:final\s*answer|final\s*response|answer|response|最终答案|最终回复|最终回答|回答|答复)\s*[:：]\s*",
            "",
            cleaned,
        ).strip()
        return cleaned

    text = str(content or "").strip()
    if not text:
        return text
    try:
        parsed = parse_json_object(text)
        if isinstance(parsed, dict) and parsed.get("sql"):
            return str(parsed.get("summary") or "I found a possible data question, but I could not safely run it against matched project metadata.").strip()
    except Exception:
        pass
    text = re.sub(r"```(?:json|sql)?\s*[\s\S]*?```", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\{\s*\"sql\"\s*:\s*\"[\s\S]*?\}\s*$", "", text).strip()
    text = _strip_reasoning_trace(text)
    return text or "I could not safely answer from the current project metadata. Please add or describe the relevant models and fields, then try again."


def _fallback_general_chat_text(question: str, language: Optional[str], *, project_scoped: bool) -> str:
    effective_lang = "zh" if (_in_chinese(language) or _contains_cjk(question)) else language
    key = "fallback_chat_project_scoped" if project_scoped else "fallback_chat_general"
    return _sql_msg(key, effective_lang)


def _ensure_general_chat_content(
    raw_content: Any,
    *,
    question: str,
    language: Optional[str],
    project_scoped: bool,
) -> str:
    cleaned = _strip_sql_json_leak(str(raw_content or ""))
    if cleaned.strip():
        return cleaned.strip()
    return _fallback_general_chat_text(question, language, project_scoped=project_scoped)


def _non_metadata_completion(
    question_part: str,
    full_question: str,
    project_id: int,
    previous_questions: Optional[list[str]] = None,
    previous_answers: Optional[list[str]] = None,
    language: Optional[str] = None,
) -> dict[str, Any]:
    if not question_part.strip():
        return {"content": "", "configured": True, "latency_ms": None}
    llm = LLMService()
    if not llm.is_configured():
        return _llm_not_configured_error(language)
    meta = _project_meta(project_id) or {}
    messages = [
        {"role": "system", "content": f"{_render_system_prompt()}\n{_language_instruction(language)}"},
    ]
    if previous_questions and previous_answers:
        history_limit = min(len(previous_questions), len(previous_answers), 3)
        for i in range(max(0, len(previous_questions) - history_limit), len(previous_questions)):
            ans_idx = i if i < len(previous_answers) else len(previous_answers) - 1
            messages.append({"role": "user", "content": (previous_questions[i] or "")[:200]})
            messages.append({"role": "assistant", "content": (previous_answers[ans_idx] or "")[:200]})
    messages.append({
        "role": "user",
        "content": (
            _sql_msg("non_metadata_instruction", language)
            + "\n"
            + _sql_msg("project_context_prefix", language) + f": {_sanitize_error_message(str(meta.get('display_name') or meta.get('name') or project_id), 80)}\n"
            + _sql_msg("project_desc_prefix", language) + f": {_sanitize_error_message(str(meta.get('description') or ''), 200)}\n"
            + f"Full question: {_sanitize_error_message(full_question, 1000)}\n"
            + f"Unmatched part: {_sanitize_error_message(question_part, 1000)}"
        ),
    })
    result = llm.chat(messages)
    result["content"] = _ensure_general_chat_content(
        result.get("content"),
        question=full_question or question_part,
        language=language,
        project_scoped=True,
    )
    return result



def _build_general_chat_messages(
    language: Optional[str],
    user_content: str,
    system_suffix: str = "",
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": f"{_render_system_prompt()}{system_suffix}\n{_language_instruction(language)}"},
        {"role": "user", "content": user_content},
    ]


def _general_chat(question: str, previous_questions: Optional[list[str]] = None, previous_answers: Optional[list[str]] = None, language: Optional[str] = None) -> dict[str, Any]:
    llm = LLMService()
    if not llm.is_configured():
        return _llm_not_configured_error(language)
    messages = _build_general_chat_messages(language, question)
    idx = 1
    if previous_questions and previous_answers:
        history_limit = min(len(previous_questions), len(previous_answers), 5)
        for i in range(max(0, len(previous_questions) - history_limit), len(previous_questions)):
            ans_idx = i if i < len(previous_answers) else len(previous_answers) - 1
            messages.insert(idx, {"role": "assistant", "content": previous_answers[ans_idx][:500] if previous_answers[ans_idx] else ""})
            messages.insert(idx, {"role": "user", "content": previous_questions[i]})
            idx += 2
    result = llm.chat(messages)
    result["content"] = _ensure_general_chat_content(
        result.get("content"),
        question=question,
        language=language,
        project_scoped=False,
    )
    return result


def _binding_rows(project_id: int) -> list[tuple[int, str, dict[str, Any]]]:
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            """
            SELECT pd.id, d.type, d.properties_encrypted
            FROM metadata.project_datasources pd
            JOIN metadata.datasources d ON d.id = pd.datasource_id
            WHERE pd.project_id = ?
            ORDER BY pd.id
            """,
            [project_id],
        ).fetchall()
        return [
            (row[0], normalize_datasource_type(str(row[1] or "")), _safe_json_loads(row[2], {}))
            for row in rows
        ]


def _primary_datasource_type(project_id: int) -> str:
    bindings = _binding_rows(project_id)
    if not bindings:
        return "duckdb"
    model_counts: dict[int, int] = {}
    for model in _models_for_project(project_id):
        bid = model.get("source_binding_id")
        if bid is not None:
            model_counts[int(bid)] = model_counts.get(int(bid), 0) + 1
    if model_counts:
        primary_binding = max(model_counts, key=model_counts.get)
        for bid, ds_type, _ in bindings:
            if bid == primary_binding:
                return ds_type
    return bindings[0][1]


_DIALECT_SQL_HINTS: dict[str, str] = {
    # Keys are normalized by _dialect_hint_for_project via normalize_datasource_type()
    # Alias entries (postgres→postgresql, sqlserver→mssql) are handled by normalization
    "postgresql": "\nIMPORTANT: Target database is PostgreSQL. Use PostgreSQL-compatible SQL: use DOUBLE PRECISION instead of DOUBLE, TIMESTAMP instead of DATETIME, STRING_AGG instead of LIST, standard ANSI SQL identifiers (double quotes). Avoid DuckDB-specific functions like TRY_CAST, LIST, ARBITRARY, QUANTILE.",
    "redshift": "\nIMPORTANT: Target database is Amazon Redshift. Use Redshift-compatible SQL: use DOUBLE PRECISION, LISTAGG instead of STRING_AGG, avoid DuckDB-specific functions.",
    "mysql": "\nIMPORTANT: Target database is MySQL. Use MySQL-compatible SQL: use backtick identifiers, GROUP_CONCAT instead of STRING_AGG/LIST, DATE_FORMAT instead of DATE_TRUNC, LIMIT without OFFSET, avoid TRY_CAST.",
    "mariadb": "\nIMPORTANT: Target database is MariaDB. Use MySQL-compatible SQL: use backtick identifiers, GROUP_CONCAT instead of STRING_AGG/LIST, DATE_FORMAT instead of DATE_TRUNC.",
    "mssql": "\nIMPORTANT: Target database is Microsoft SQL Server. Use T-SQL syntax: use square bracket identifiers, STRING_AGG instead of LIST, DATEFROMPARTS instead of MAKE_DATE, TOP N instead of LIMIT, avoid DuckDB-specific functions.",
    "clickhouse": "\nIMPORTANT: Target database is ClickHouse. Use ClickHouse-compatible SQL: use backtick identifiers, groupArray instead of LIST/ARRAY_AGG, toDateTime instead of CAST, avoid DuckDB-specific functions.",
    "trino": "\nIMPORTANT: Target database is Trino. Use Trino-compatible SQL: use DOUBLE instead of DOUBLE PRECISION, ARRAY_AGG instead of LIST, use DATE_TRUNC, use double-quote identifiers.",
    "athena": "\nIMPORTANT: Target database is Amazon Athena (Trino-compatible). Use Trino-compatible SQL: use ARRAY_AGG instead of LIST, use DATE_TRUNC, use double-quote identifiers.",
    "bigquery": "\nIMPORTANT: Target database is BigQuery. Use BigQuery-compatible SQL: use backtick identifiers, TIMESTAMP_TRUNC/DATE_TRUNC for time bucketing, SAFE_CAST for risky conversions, avoid DuckDB-specific functions.",
    "snowflake": "\nIMPORTANT: Target database is Snowflake. Use Snowflake-compatible SQL: use DOUBLE/NUMBER types, DATE_TRUNC for time bucketing, IFF/CASE for conditions, avoid DuckDB-specific functions.",
    "oracle": "\nIMPORTANT: Target database is Oracle. Use Oracle-compatible SQL: use FETCH FIRST N ROWS ONLY instead of LIMIT, TO_CHAR/TO_DATE as needed, avoid DuckDB-specific functions.",
    "databricks": "\nIMPORTANT: Target database is Databricks SQL (Spark). Use Spark SQL-compatible syntax: use backtick identifiers, date_trunc for time bucketing, avoid DuckDB-specific functions.",
}


def _dialect_hint_for_project(project_id: int) -> str:
    ds_type = normalize_datasource_type(_primary_datasource_type(project_id))
    return _DIALECT_SQL_HINTS.get(ds_type, "")


def _models_by_binding(project_id: int) -> dict[int, list[dict[str, Any]]]:
    models = _models_for_project(project_id)
    result: dict[int, list[dict[str, Any]]] = {}
    for model in models:
        binding_id = model.get("source_binding_id")
        if binding_id is None:
            continue
        result.setdefault(int(binding_id), []).append({
            "name": str(model["name"]),
            "table_reference": str(model.get("table_reference") or model["name"]),
            "source_binding_id": int(binding_id),
            "columns": [str(c.get("name") or "").lower() for c in model.get("columns", []) if c.get("name")],
        })
    return result


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_sql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _unquote_identifier(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', '`'}:
        return text[1:-1].replace(text[0] * 2, text[0])
    if len(text) >= 2 and text[0] == "[" and text[-1] == "]":
        return text[1:-1].replace("]]", "]")
    return text


def _split_table_reference(reference: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in str(reference or "").strip():
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {'"', '`'}:
            quote = char
            current.append(char)
            continue
        if char == ".":
            parts.append(_unquote_identifier("".join(current)))
            current = []
            continue
        current.append(char)
    if current:
        parts.append(_unquote_identifier("".join(current)))
    return [part for part in parts if part]


def _quote_external_identifier(value: str, quote_char: str = '"') -> str:
    text = _unquote_identifier(value)
    close_quote = "]" if quote_char == "[" else quote_char
    escaped = text.replace(close_quote, close_quote * 2)
    return f"{quote_char}{escaped}{close_quote}"


def _quote_table_reference(reference: str, quote_char: str = '"') -> str:
    parts = _split_table_reference(reference)
    if not parts:
        raise ValueError("Missing table reference")
    return ".".join(_quote_external_identifier(part, quote_char) for part in parts)


def _apply_limit(sql: str, limit: int) -> str:
    return f"SELECT * FROM ({sql}) AS prismbi_limited LIMIT {int(limit)}"


def _apply_binding_limit(sql: str, ds_type: str, limit: int) -> str:
    return apply_limit_for_datasource(sql, normalize_datasource_type(ds_type), int(limit))


def _looks_like_general_chat(question: str) -> bool:
    return bool(GENERAL_CHAT_RE.match(question or ""))


def _safe_duckdb_name(raw_name: str, binding_id: int) -> str:
    name = os.path.basename(str(raw_name or "").strip())
    if name.endswith(".duckdb"):
        name = name[: -len(".duckdb")]
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._-") or f"datasource_{binding_id}"
    return f"{name}.duckdb"


def _resolve_duckdb_path(props: dict[str, Any], project_id: int, binding_id: int) -> str:
    if project_id <= 0:
        raise ValueError("A real project is required for DuckDB file materialization")
    dbname = str(props.get("dbname") or "").strip()
    return os.path.join(PROJECT_DATA_DIR, str(project_id), _safe_duckdb_name(dbname, binding_id))


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _result_rows(columns: list[str], tuples: list[tuple]) -> list[dict[str, Any]]:
    return [{column: _json_safe_value(value) for column, value in zip(columns, row)} for row in tuples]


def _driver_columns(description: Any) -> list[str]:
    return [str(getattr(item, "name", None) or item[0]) for item in description or []]


def _unsupported_execution(ds_type: str, package: str) -> dict[str, Any]:
    return {
        "columns": [],
        "tuples": [],
        "warning": f"Query execution for datasource type '{ds_type}' requires optional Python package: {package}.",
    }


def _coerce_json_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    if os.path.exists(text):
        try:
            with open(text, encoding="utf-8") as handle:
                parsed = json.load(handle)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
    return None


def _bigquery_credentials_from_props(props: dict[str, Any]):
    raw_credentials = props.get("credentials")
    info = _coerce_json_dict(raw_credentials)
    if not info:
        return None
    service_account = _import_optional("google.oauth2.service_account")
    credentials_cls = getattr(service_account, "Credentials", None) if service_account else None
    if credentials_cls is None:
        return None
    try:
        return credentials_cls.from_service_account_info(info)
    except Exception:
        return None


def _execute_postgresql(
    props: dict[str, Any],
    sql: str,
    row_limit: int,
    *,
    pool_ds_type: str = "postgresql",
) -> dict[str, Any]:
    psycopg = _import_optional("psycopg")
    psycopg2 = None if psycopg else _import_optional("psycopg2")
    driver = psycopg or psycopg2
    if not driver:
        return _unsupported_execution("postgresql", "psycopg or psycopg2")
    driver_tag = "psycopg" if psycopg else "psycopg2"
    pool_key = _external_pool_key(str(pool_ds_type or "postgresql"), props, driver_tag=driver_tag)

    def _connect():
        return driver.connect(
            host=props.get("host"),
            port=int(props.get("port") or 5432),
            user=props.get("user") or props.get("username"),
            password=props.get("password"),
            dbname=props.get("database") or props.get("dbname"),
            sslmode="require" if _normalize_bool(props.get("ssl")) else "prefer",
            connect_timeout=_CONNECTION_TIMEOUTS["postgresql"]["connect"],
        )

    conn = None
    cursor = None
    keep_connection = False
    try:
        conn = _acquire_pooled_connection(pool_key, _connect, _is_postgres_connection_healthy)
        cursor = conn.cursor()
        cursor.execute(f"SET statement_timeout = {_CONNECTION_TIMEOUTS['postgresql']['statement']}")
        cursor.execute(sql)
        result = {"columns": _driver_columns(cursor.description), "tuples": cursor.fetchmany(row_limit)}
        keep_connection = True
        return result
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                keep_connection = False
        if conn is not None:
            _release_pooled_connection(pool_key, conn, healthy=keep_connection)


def _execute_redshift(props: dict[str, Any], sql: str, row_limit: int) -> dict[str, Any]:
    try:
        return _execute_postgresql(props, sql, row_limit, pool_ds_type="redshift")
    except TypeError as exc:
        message = str(exc)
        if "pool_ds_type" in message and "unexpected keyword" in message:
            return _execute_postgresql(props, sql, row_limit)
        raise


def _execute_mysql(props: dict[str, Any], sql: str, row_limit: int) -> dict[str, Any]:
    connector = _import_optional("mysql.connector")
    pymysql = None if connector else _import_optional("pymysql")
    database = props.get("database") or props.get("dbname")
    driver_tag = "mysql.connector" if connector else "pymysql" if pymysql else "none"
    pool_key = _external_pool_key("mysql", props, driver_tag=driver_tag)

    def _connect_mysql_connector():
        return connector.connect(
            host=props.get("host"),
            port=int(props.get("port") or 3306),
            user=props.get("user") or props.get("username"),
            password=props.get("password"),
            database=database,
            connection_timeout=_CONNECTION_TIMEOUTS["mysql"]["connect"],
            ssl_disabled=not _normalize_bool(props.get("ssl")),
        )

    def _connect_pymysql():
        return pymysql.connect(
            host=props.get("host"),
            port=int(props.get("port") or 3306),
            user=props.get("user") or props.get("username"),
            password=props.get("password"),
            database=database,
            connect_timeout=_CONNECTION_TIMEOUTS["mysql"]["connect"],
            ssl={} if _normalize_bool(props.get("ssl")) else None,
        )

    conn = None
    cursor = None
    keep_connection = False
    try:
        if connector:
            conn = _acquire_pooled_connection(pool_key, _connect_mysql_connector, _is_mysql_connection_healthy)
        elif pymysql:
            conn = _acquire_pooled_connection(pool_key, _connect_pymysql, _is_mysql_connection_healthy)
        else:
            return _unsupported_execution("mysql", "mysql-connector-python or pymysql")
        cursor = conn.cursor()
        cursor.execute(f"SET SESSION max_execution_time = {_CONNECTION_TIMEOUTS['mysql']['query']}")
        cursor.execute(sql)
        result = {"columns": _driver_columns(cursor.description), "tuples": cursor.fetchmany(row_limit)}
        keep_connection = True
        return result
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                keep_connection = False
        if conn is not None:
            _release_pooled_connection(pool_key, conn, healthy=keep_connection)


def _execute_clickhouse(props: dict[str, Any], sql: str, row_limit: int) -> dict[str, Any]:
    clickhouse_connect = _import_optional("clickhouse_connect")
    if not clickhouse_connect:
        return _unsupported_execution("clickhouse", "clickhouse-connect")
    client = clickhouse_connect.get_client(
        host=props.get("host"),
        port=int(props.get("port") or (8443 if _normalize_bool(props.get("ssl")) else 8123)),
        username=props.get("user") or props.get("username") or "default",
        password=props.get("password") or "",
        database=props.get("database") or "default",
        secure=_normalize_bool(props.get("ssl")),
        connect_timeout=_CONNECTION_TIMEOUTS["clickhouse"]["connect"],
        send_receive_timeout=_CONNECTION_TIMEOUTS["clickhouse"]["send_receive"],
    )
    try:
        client.command(f"SET max_execution_time = {_CONNECTION_TIMEOUTS['clickhouse']['query']}")
        result = client.query(sql)
        return {"columns": [str(col) for col in result.column_names], "tuples": result.result_rows[:row_limit]}
    finally:
        client.close()


def _execute_mssql(props: dict[str, Any], sql: str, row_limit: int) -> dict[str, Any]:
    pyodbc = _import_optional("pyodbc")
    pymssql = _import_optional("pymssql")
    conn = None
    cursor = None
    keep_connection = False
    pool_key_used = ""
    pyodbc_error: Exception | None = None

    def _connect_pyodbc():
        driver = props.get("driver") or "ODBC Driver 18 for SQL Server"
        trust = "yes" if _normalize_bool(props.get("ssl")) else "no"
        return pyodbc.connect(
            driver=driver,
            server=f"{props.get('host')},{int(props.get('port') or 1433)}",
            database=props.get("database"),
            uid=props.get("user") or props.get("username"),
            pwd=props.get("password") or "",
            trustservercertificate=trust,
            timeout=_CONNECTION_TIMEOUTS["mssql"]["connect"],
        )

    def _connect_pymssql():
        return pymssql.connect(
            server=props.get("host"),
            port=int(props.get("port") or 1433),
            user=props.get("user") or props.get("username"),
            password=props.get("password"),
            database=props.get("database"),
            timeout=_CONNECTION_TIMEOUTS["mssql"]["connect"],
            login_timeout=_CONNECTION_TIMEOUTS["mssql"]["login"],
        )

    try:
        if pyodbc:
            try:
                pool_key_used = _external_pool_key("mssql", props, driver_tag="pyodbc")
                conn = _acquire_pooled_connection(
                    pool_key_used,
                    _connect_pyodbc,
                    _is_generic_connection_healthy,
                )
            except Exception as exc:
                pyodbc_error = exc
                conn = None
                pool_key_used = ""
        if conn is None and pymssql:
            pool_key_used = _external_pool_key("mssql", props, driver_tag="pymssql")
            conn = _acquire_pooled_connection(
                pool_key_used,
                _connect_pymssql,
                _is_generic_connection_healthy,
            )
        if conn is None:
            if pyodbc_error is not None:
                safe_pyodbc_error = _sanitize_error_message(pyodbc_error)
                return {
                    "columns": [],
                    "tuples": [],
                    "warning": f"MSSQL execution via pyodbc failed ({safe_pyodbc_error}); install or configure pymssql as fallback.",
                }
            return _unsupported_execution("mssql", "pyodbc or pymssql")
        cursor = conn.cursor()
        cursor.execute(f"SET LOCK_TIMEOUT {_CONNECTION_TIMEOUTS['mssql']['lock']}")
        cursor.execute(sql)
        result = {"columns": _driver_columns(cursor.description), "tuples": cursor.fetchmany(row_limit)}
        keep_connection = True
        return result
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                keep_connection = False
        if conn is not None:
            if pool_key_used:
                _release_pooled_connection(pool_key_used, conn, healthy=keep_connection)
            else:
                _close_connection_quietly(conn)


def _execute_trino(props: dict[str, Any], sql: str, row_limit: int) -> dict[str, Any]:
    trino = _import_optional("trino")
    if not trino:
        return _unsupported_execution("trino", "trino")
    auth = None
    if props.get("password"):
        auth = trino.auth.BasicAuthentication(props.get("username") or props.get("user"), props.get("password"))
    kwargs = {
        "host": props.get("host"),
        "port": int(props.get("port") or 8080),
        "user": props.get("username") or props.get("user"),
        "http_scheme": "https" if _normalize_bool(props.get("ssl")) else "http",
        "auth": auth,
    }
    if props.get("catalog"):
        kwargs["catalog"] = props.get("catalog")
    if props.get("schema"):
        kwargs["schema"] = props.get("schema")
    pool_key = _external_pool_key("trino", props, driver_tag="trino")

    def _connect_trino():
        return trino.dbapi.connect(**kwargs)

    conn = None
    cursor = None
    keep_connection = False
    try:
        conn = _acquire_pooled_connection(pool_key, _connect_trino, _is_generic_connection_healthy)
        cursor = conn.cursor()
        cursor.execute(sql)
        result = {"columns": _driver_columns(cursor.description), "tuples": cursor.fetchmany(row_limit)}
        keep_connection = True
        return result
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                keep_connection = False
        if conn is not None:
            _release_pooled_connection(pool_key, conn, healthy=keep_connection)


def _execute_athena(props: dict[str, Any], sql: str, row_limit: int) -> dict[str, Any]:
    pyathena = _import_optional("pyathena")
    if not pyathena:
        if props.get("host"):
            return _execute_trino(props, sql, row_limit)
        return _unsupported_execution("athena", "pyathena or trino")
    connect_kwargs: dict[str, Any] = {}
    schema_name = props.get("schema") or props.get("database") or props.get("dbname")
    if schema_name:
        connect_kwargs["schema_name"] = schema_name
    if props.get("catalog"):
        connect_kwargs["catalog_name"] = props.get("catalog")
    if props.get("s3_staging_dir"):
        connect_kwargs["s3_staging_dir"] = props.get("s3_staging_dir")
    if props.get("aws_region"):
        connect_kwargs["region_name"] = props.get("aws_region")
    if props.get("work_group"):
        connect_kwargs["work_group"] = props.get("work_group")
    if props.get("aws_access_key"):
        connect_kwargs["aws_access_key_id"] = props.get("aws_access_key")
    if props.get("aws_secret_key"):
        connect_kwargs["aws_secret_access_key"] = props.get("aws_secret_key")
    if props.get("aws_session_token"):
        connect_kwargs["aws_session_token"] = props.get("aws_session_token")
    conn = None
    try:
        conn = pyathena.connect(**connect_kwargs)
        cur = conn.cursor()
        cur.execute(sql)
        return {"columns": _driver_columns(cur.description), "tuples": cur.fetchmany(row_limit)}
    finally:
        if conn:
            conn.close()


def _execute_oracle(props: dict[str, Any], sql: str, row_limit: int) -> dict[str, Any]:
    oracledb = _import_optional("oracledb")
    cx_oracle = None if oracledb else _import_optional("cx_Oracle")
    driver = oracledb or cx_oracle
    if not driver:
        return _unsupported_execution("oracle", "oracledb or cx_Oracle")
    dsn = str(props.get("dsn") or "").strip()
    if not dsn:
        host = props.get("host")
        port = int(props.get("port") or 1521)
        service_name = props.get("service_name") or props.get("serviceName") or props.get("database") or props.get("dbname")
        if hasattr(driver, "makedsn") and host and service_name:
            dsn = driver.makedsn(host, port, service_name=service_name)
        else:
            dsn = f"{host}:{port}/{service_name}"
    conn = None
    try:
        conn = driver.connect(
            user=props.get("user") or props.get("username"),
            password=props.get("password"),
            dsn=dsn,
        )
        if hasattr(conn, "call_timeout"):
            conn.call_timeout = _CONNECTION_TIMEOUTS["oracle"]["call"]
        cur = conn.cursor()
        cur.execute(sql)
        return {"columns": _driver_columns(cur.description), "tuples": cur.fetchmany(row_limit)}
    finally:
        if conn:
            conn.close()


def _execute_snowflake(props: dict[str, Any], sql: str, row_limit: int) -> dict[str, Any]:
    snowflake_connector = _import_optional("snowflake.connector")
    if not snowflake_connector:
        return _unsupported_execution("snowflake", "snowflake-connector-python")
    conn = None
    try:
        conn = snowflake_connector.connect(
            account=props.get("account"),
            user=props.get("user") or props.get("username"),
            password=props.get("password"),
            database=props.get("database") or props.get("dbname"),
            schema=props.get("schema"),
            warehouse=props.get("warehouse"),
            role=props.get("role"),
            login_timeout=_CONNECTION_TIMEOUTS["snowflake"]["login"],
            network_timeout=_CONNECTION_TIMEOUTS["snowflake"]["network"],
        )
        cur = conn.cursor()
        cur.execute(sql)
        return {"columns": _driver_columns(cur.description), "tuples": cur.fetchmany(row_limit)}
    finally:
        if conn:
            conn.close()


def _execute_bigquery(props: dict[str, Any], sql: str, row_limit: int) -> dict[str, Any]:
    bigquery = _import_optional("google.cloud.bigquery")
    if not bigquery:
        return _unsupported_execution("bigquery", "google-cloud-bigquery")
    client_kwargs: dict[str, Any] = {}
    project_id = props.get("project_id") or props.get("projectId")
    if project_id:
        client_kwargs["project"] = project_id
    credentials = _bigquery_credentials_from_props(props)
    if credentials is not None:
        client_kwargs["credentials"] = credentials
    client = bigquery.Client(**client_kwargs)
    try:
        query_kwargs: dict[str, Any] = {}
        default_dataset = str(props.get("dataset_id") or props.get("datasetId") or "").split(",")[0].strip()
        if default_dataset:
            if project_id and "." not in default_dataset:
                default_dataset = f"{project_id}.{default_dataset}"
            query_job_config_cls = getattr(bigquery, "QueryJobConfig", None)
            if query_job_config_cls is not None:
                query_kwargs["job_config"] = query_job_config_cls(default_dataset=default_dataset)
        query_job = client.query(sql, **query_kwargs)
        result = query_job.result(max_results=row_limit)
        schema = list(getattr(result, "schema", None) or [])
        columns = [str(getattr(field, "name", "")) for field in schema if getattr(field, "name", None)]
        tuples: list[tuple[Any, ...]] = []
        if columns:
            width = len(columns)
            for row in result:
                tuples.append(tuple(row[index] for index in range(width)))
        else:
            for row in result:
                tuples.append(tuple(row))
        return {"columns": columns, "tuples": tuples}
    finally:
        close_fn = getattr(client, "close", None)
        if callable(close_fn):
            close_fn()


def _execute_databricks(props: dict[str, Any], sql: str, row_limit: int) -> dict[str, Any]:
    databricks_sql = _import_optional("databricks.sql")
    if not databricks_sql:
        return _unsupported_execution("databricks", "databricks-sql-connector")
    server_hostname = props.get("server_hostname") or props.get("serverHostname") or props.get("host")
    http_path = props.get("http_path") or props.get("httpPath")
    access_token = props.get("access_token") or props.get("accessToken") or props.get("token")
    if not server_hostname or not http_path or not access_token:
        return {
            "columns": [],
            "tuples": [],
            "warning": "Databricks execution requires properties: server_hostname, http_path, access_token.",
        }
    connect_kwargs: dict[str, Any] = {
        "server_hostname": server_hostname,
        "http_path": http_path,
        "access_token": access_token,
    }
    if props.get("catalog"):
        connect_kwargs["catalog"] = props.get("catalog")
    if props.get("schema"):
        connect_kwargs["schema"] = props.get("schema")
    conn = None
    try:
        conn = databricks_sql.connect(**connect_kwargs)
        cur = conn.cursor()
        cur.execute(sql)
        return {"columns": _driver_columns(cur.description), "tuples": cur.fetchmany(row_limit)}
    finally:
        if conn:
            conn.close()


_EXTERNAL_QUERY_TIMEOUT_SECONDS = 60


def _execute_external_raw_query(
    ds_type: str,
    props: dict[str, Any],
    sql: str,
    row_limit: int,
    project_id: Optional[int] = None,
) -> dict[str, Any]:
    normalized = normalize_datasource_type(ds_type)
    definition = resolve_datasource_definition(normalized)
    canonical = definition.canonical_type if definition is not None else normalized
    metric_ds_type = canonical or normalized or str(ds_type or "")
    started = time.perf_counter()
    executors = {
        "postgresql": _execute_postgresql,
        "redshift": _execute_redshift,
        "mysql": _execute_mysql,
        "clickhouse": _execute_clickhouse,
        "mssql": _execute_mssql,
        "trino": _execute_trino,
        "athena": _execute_athena,
        "oracle": _execute_oracle,
        "snowflake": _execute_snowflake,
        "bigquery": _execute_bigquery,
        "databricks": _execute_databricks,
    }
    executor = executors.get(canonical)
    if not executor:
        result = {
            "columns": [],
            "tuples": [],
            "warning": f"Query execution for datasource type '{ds_type}' is not implemented yet.",
        }
        _record_execution_metric(
            metric_ds_type,
            "warning",
            (time.perf_counter() - started) * 1000,
            0,
            project_id=project_id,
        )
        return result
    try:
        result = executor(props, sql, row_limit)
    except Exception as exc:
        safe_error = _sanitize_error_message(exc)
        timeout_msg = f"Query execution timed out after {_EXTERNAL_QUERY_TIMEOUT_SECONDS}s on {ds_type}"
        if isinstance(exc, (TimeoutError,)) or "timeout" in str(exc).lower() or "timed out" in str(exc).lower():
            LOGGER.warning("External query timeout on %s: %s", ds_type, safe_error)
            _record_execution_metric(
                metric_ds_type,
                "timeout",
                (time.perf_counter() - started) * 1000,
                0,
                project_id=project_id,
            )
            return {"columns": [], "tuples": [], "warning": timeout_msg}
        LOGGER.warning("External query failed on %s: %s", ds_type, safe_error)
        _record_execution_metric(
            metric_ds_type,
            "error",
            (time.perf_counter() - started) * 1000,
            0,
            project_id=project_id,
        )
        raise
    status = "warning" if result.get("warning") else "success"
    rows_count = len(result.get("tuples") or [])
    _record_execution_metric(
        metric_ds_type,
        status,
        (time.perf_counter() - started) * 1000,
        rows_count,
        project_id=project_id,
    )
    return result


def _duckdb_model_source_sql(table_reference: str, binding_id: int, primary_binding: int | None = None) -> str:
    parts = _split_table_reference(table_reference)
    if not parts:
        raise ValueError("Missing table reference")
    if primary_binding is not None and binding_id != primary_binding:
        parts = [f"ds_{binding_id}", *parts]
    return ".".join(_quote_identifier(part) for part in parts)


def _needs_duckdb_model_view(model: dict[str, Any], binding_id: int, primary_binding: int | None = None) -> bool:
    parts = _split_table_reference(str(model.get("table_reference") or model["name"]))
    if not parts:
        return True
    if primary_binding is not None and binding_id != primary_binding:
        return True
    if len(parts) != 1:
        return True
    return parts[0].lower() != str(model["name"]).lower()


def _execute_duckdb_raw_query(project_id: int, binding_id: int, props: dict[str, Any], sql: str, row_limit: int) -> dict[str, Any]:
    path = _resolve_duckdb_path(props, project_id, binding_id)
    started = time.perf_counter()
    conn = duckdb.connect(path)
    try:
        result = conn.execute(sql)
        columns = [desc[0] for desc in result.description or []]
        tuples = result.fetchmany(row_limit)
        _record_execution_metric(
            "duckdb",
            "success",
            (time.perf_counter() - started) * 1000,
            len(tuples),
            project_id=project_id,
        )
        return {"columns": columns, "tuples": tuples}
    except Exception:
        _record_execution_metric(
            "duckdb",
            "error",
            (time.perf_counter() - started) * 1000,
            0,
            project_id=project_id,
        )
        raise
    finally:
        conn.close()


def _execute_binding_raw_query(
    ds_type: str,
    props: dict[str, Any],
    sql: str,
    row_limit: int,
    project_id: int,
    binding_id: int,
) -> dict[str, Any]:
    normalized = normalize_datasource_type(ds_type)
    if normalized in {"duckdb", "sample"}:
        return _execute_duckdb_raw_query(project_id, binding_id, props, sql, row_limit)
    return _execute_external_raw_query_scoped(normalized, props, sql, row_limit, project_id)


def _execute_external_raw_query_scoped(
    ds_type: str,
    props: dict[str, Any],
    sql: str,
    row_limit: int,
    project_id: Optional[int],
) -> dict[str, Any]:
    normalized = normalize_datasource_type(ds_type)
    try:
        return _execute_external_raw_query(
            normalized,
            props,
            sql,
            row_limit,
            project_id=project_id,
        )
    except TypeError as exc:
        message = str(exc)
        if "project_id" in message and "unexpected keyword" in message:
            return _execute_external_raw_query(normalized, props, sql, row_limit)
        raise


def _identifier_quote_for_ds(ds_type: str) -> str:
    normalized = normalize_datasource_type(ds_type)
    definition = resolve_datasource_definition(normalized)
    canonical = definition.canonical_type if definition is not None else normalized
    if canonical in {"mysql", "clickhouse", "bigquery", "databricks"}:
        return "`"
    if canonical == "mssql":
        return "["
    return '"'


def _dialect_for_ds(ds_type: str) -> str:
    return dialect_for_datasource(normalize_datasource_type(ds_type))


def _transpile_sql_for_dialect(sql: str, target_ds_type: str, language: Optional[str] = None) -> tuple[str, str | None]:
    target_dialect = _dialect_for_ds(target_ds_type)
    if target_dialect == "duckdb":
        return sql, None
    if sqlglot is None:
        return (
            sql,
            _sql_msg("transpile_unavailable_fmt", language).format(target_dialect),
        )
    try:
        transpiled = sqlglot.transpile(sql, read="duckdb", write=target_dialect)
        if transpiled and transpiled[0]:
            return transpiled[0], None
    except Exception as exc:
        safe_error = _sanitize_error_message(exc)
        LOGGER.warning("SQL transpilation to %s failed: %s; using DuckDB SQL as fallback", target_dialect, safe_error)
        return sql, _sql_msg("transpile_failed_fmt", language).format(target_dialect, safe_error)
    return sql, _sql_msg("transpile_empty_fmt", language).format(target_dialect)


def _model_source_select(model: dict[str, Any], ds_type: str, row_limit: int, where_clauses: list[str] | None = None, select_columns: list[str] | None = None) -> str:
    table_reference = str(model.get("table_reference") or model["name"])
    model_name = str(model["name"])
    normalized = normalize_datasource_type(ds_type)
    if normalized in {"duckdb", "sample"}:
        table_sql = _duckdb_model_source_sql(table_reference, int(model["source_binding_id"]))
    else:
        table_sql = _quote_table_reference(table_reference, _identifier_quote_for_ds(normalized))
    quote = _identifier_quote_for_ds(normalized) if normalized not in {"duckdb", "sample"} else '"'
    select_expr = ", ".join(_quote_external_identifier(c, quote) for c in select_columns) if select_columns else "*"
    where_sql = ""
    if where_clauses:
        where_sql = " WHERE " + " AND ".join(where_clauses)
    alias_sql = _quote_external_identifier(model_name, _identifier_quote_for_ds(normalized))
    from_sql = f"{table_sql} {alias_sql}" if normalized == "oracle" else f"{table_sql} AS {alias_sql}"
    base_sql = f"SELECT {select_expr} FROM {from_sql}{where_sql}"
    return _apply_binding_limit(base_sql, normalized, row_limit)


def _rewrite_model_refs_for_source(sql: str, models: list[dict[str, Any]], ds_type: str) -> tuple[str, list[str]]:
    cte_names: set[str] = set()
    if sqlglot is not None and exp is not None:
        try:
            parsed = sqlglot.parse_one(sql, read="duckdb")
            for cte in parsed.find_all(exp.CTE):
                alias = cte.alias
                if alias:
                    cte_names.add(str(alias).lower())
        except Exception:
            pass
    rewritten = sql
    missing: list[str] = []
    quote_char = _identifier_quote_for_ds(normalize_datasource_type(ds_type))
    for model in models:
        model_name = str(model.get("name") or "")
        if not model_name:
            continue
        if model_name.lower() in cte_names:
            continue
        physical = _quote_table_reference(str(model.get("table_reference") or model_name), quote_char)
        escaped = re.escape(model_name)
        table_pattern = rf'(?:(?:"{escaped}")|(?:`{escaped}`)|(?:\[{escaped}\])|(?:{escaped}))'
        pattern = re.compile(rf"(\b(?:FROM|(?:LEFT\s+|RIGHT\s+|INNER\s+|OUTER\s+|CROSS\s+|NATURAL\s+)?(?:JOIN))\s+){table_pattern}(?=\s|$|\)|,)", re.IGNORECASE)
        rewritten, count = pattern.subn(lambda match, replacement=physical: f"{match.group(1)}{replacement}", rewritten)
        if count == 0:
            missing.append(model_name)
    return rewritten, missing


def _infer_duckdb_type(values: list[Any]) -> str:
    non_null = [value for value in values if value is not None]
    if not non_null:
        return "VARCHAR"
    if all(isinstance(value, bool) for value in non_null):
        return "BOOLEAN"
    if all(isinstance(value, int) and not isinstance(value, bool) for value in non_null):
        return "BIGINT"
    if all(isinstance(value, (int, float, Decimal)) and not isinstance(value, bool) for value in non_null):
        return "DOUBLE"
    if all(isinstance(value, (datetime, date)) for value in non_null):
        return "TIMESTAMP"
    if all(isinstance(value, (bytes, bytearray)) for value in non_null):
        return "BLOB"
    return "VARCHAR"


def _materialize_rows(conn: duckdb.DuckDBPyConnection, table_name: str, columns: list[str], tuples: list[tuple]) -> None:
    if not columns:
        raise ValueError(f"No columns returned while materializing {table_name}")
    safe_columns = []
    seen: dict[str, int] = {}
    for index, column in enumerate(columns):
        name = str(column or f"column_{index + 1}")
        count = seen.get(name, 0)
        candidate = name if count == 0 else f"{name}_{count + 1}"
        while candidate in seen:
            count += 1
            candidate = f"{name}_{count + 1}"
        seen[name] = count + 1
        safe_columns.append(candidate)
        seen[candidate] = 1
    column_defs = []
    for index, column in enumerate(safe_columns):
        values = [row[index] for row in tuples if index < len(row)]
        column_defs.append(f"{_quote_identifier(column)} {_infer_duckdb_type(values)}")
    conn.execute(f"CREATE TEMP TABLE {_quote_identifier(table_name)} ({', '.join(column_defs)})")
    if not tuples:
        return
    placeholders = ", ".join("?" for _ in safe_columns)
    insert_sql = f"INSERT INTO {_quote_identifier(table_name)} VALUES ({placeholders})"
    n_cols = len(safe_columns)
    normalized = [tuple(list(row) + [None] * (n_cols - len(row)))[:n_cols] for row in tuples]
    conn.executemany(insert_sql, normalized)


def _finalize_query_result(
    columns: list[str],
    tuples: list[tuple],
    plan: dict[str, Any],
    start: float,
    warning: str | None = None,
) -> dict[str, Any]:
    rows = _result_rows(columns, tuples)
    cls_policies = plan.get("security", {}).get("cls", [])
    rows = apply_cls_to_rows(rows, cls_policies)
    if rows:
        columns = list(rows[0].keys())
    else:
        hidden = {p["column_name"] for p in cls_policies if str(p.get("access_type", "")).upper() == "HIDE"}
        columns = [column for column in columns if column not in hidden]
    result = {
        "columns": columns,
        "rows": rows,
        "total_rows": len(rows),
        "execution_time_ms": round((time.perf_counter() - start) * 1000, 2),
        "security_plan": plan,
    }
    if warning:
        result["warning"] = warning
    return result


def _warning_query_result(message: str, plan: dict[str, Any], start: float | None = None) -> dict[str, Any]:
    return {
        "columns": [],
        "rows": [],
        "total_rows": 0,
        "execution_time_ms": round((time.perf_counter() - start) * 1000, 2) if start else 0,
        "warning": message,
        "security_plan": plan,
    }


def _execute_project_sql_routed(sql: str, project_id: int, user_id: int, limit: Optional[int] = None) -> dict[str, Any]:
    refresh_runtime_router_settings(force=False)
    if project_id <= 0:
        raise ValueError("A real project is required")
    input_sql = _normalize_sql_candidate(sql)
    if not input_sql:
        raise ValueError("SQL is required")
    if input_sql != str(sql or "").strip():
        LOGGER.info("Normalized SQL punctuation before security planning (project_id=%d)", project_id)

    plan: dict[str, Any] = {}
    planned_sql = input_sql
    planned_limited_sql = input_sql
    row_limit = _normalize_execution_row_limit(limit) if limit is not None else MAX_SQL_ROWS
    start: float | None = None
    bindings: list[tuple[int, str, dict[str, Any]]] = []
    binding_lookup: dict[int, tuple[str, dict[str, Any]]] = {}
    models_by_binding: dict[int, list[dict[str, Any]]] = {}
    referenced_by_binding: dict[int, list[dict[str, Any]]] = {}
    final_execution_sql = input_sql
    routing_stage = "security_plan"
    route_v2_enabled = _is_sql_route_v2_enabled(project_id)
    shadow_mode = bool(ROUTER_CONFIG.get("sql_route_shadow_mode", False))
    should_emit_route_events = route_v2_enabled or shadow_mode
    pipeline = _execution_pipeline()

    def _emit_execution_route_decision(warning: str | None = None) -> None:
        if not should_emit_route_events:
            return
        decision = pipeline.build_decision(
            planned_sql=planned_sql,
            final_execution_sql=final_execution_sql,
            routing_stage=routing_stage,
            referenced_by_binding=referenced_by_binding,
            binding_lookup=binding_lookup,
            warning=warning,
            model_refs=sorted(str(item) for item in set(plan.get("model_refs") or [])),
        )
        payload = decision.to_audit_payload()
        payload.update(
            {
                "route_v2_enabled": route_v2_enabled,
                "shadow_mode": shadow_mode,
            }
        )
        _emit_route_event("execution_route_decision", payload, project_id=project_id)
        if shadow_mode:
            shadow_payload = pipeline.shadow_diff(
                planned_sql=planned_sql,
                final_execution_sql=final_execution_sql,
                routing_stage=routing_stage,
                referenced_by_binding=referenced_by_binding,
                binding_lookup=binding_lookup,
                warning=warning,
                model_refs=sorted(str(item) for item in set(plan.get("model_refs") or [])),
            )
            shadow_payload.update(
                {
                    "route_v2_enabled": route_v2_enabled,
                    "shadow_mode": shadow_mode,
                }
            )
            _emit_route_event("execution_route_shadow_diff", shadow_payload, project_id=project_id)

    try:
        prepared, early_exit = pipeline.prepare(
            input_sql=input_sql,
            project_id=project_id,
            user_id=user_id,
            limit=limit,
        )

        if early_exit is not None:
            plan = early_exit.plan
            planned_sql = early_exit.planned_sql
            planned_limited_sql = early_exit.planned_limited_sql
            binding_lookup = dict(early_exit.binding_lookup)
            referenced_by_binding = dict(early_exit.referenced_by_binding)
            routing_stage = early_exit.routing_stage
            final_execution_sql = early_exit.final_execution_sql
            _emit_execution_route_decision(early_exit.warning)
            return _warning_query_result(early_exit.warning, plan, early_exit.start)

        if prepared is None:
            raise ValueError("Execution routing preparation returned no plan")

        plan = prepared.plan
        planned_sql = prepared.planned_sql
        planned_limited_sql = prepared.planned_limited_sql
        row_limit = prepared.row_limit
        start = prepared.start
        bindings = prepared.bindings
        binding_lookup = prepared.binding_lookup
        models_by_binding = prepared.models_by_binding
        referenced_by_binding = prepared.referenced_by_binding

        LOGGER.info(
            "SQL execution routing: project_id=%d, referenced_models=%s, bindings=%s, models_by_binding=%s",
            project_id,
            prepared.referenced_models,
            list(binding_lookup.keys()),
            {bid: [m["name"] for m in models] for bid, models in models_by_binding.items()},
        )

        if len(referenced_by_binding) == 1:
            binding_id, models = next(iter(referenced_by_binding.items()))
            ds_type, props = binding_lookup.get(binding_id, (None, None))
            if not ds_type or props is None:
                routing_stage = "single_binding_missing"
                final_execution_sql = planned_limited_sql
                warning = f"Datasource binding {binding_id} was not found for referenced model(s)."
                _emit_execution_route_decision(warning)
                return _warning_query_result(warning, plan, start)
            if ds_type in {"duckdb", "sample"}:
                routing_stage = f"duckdb_binding_{binding_id}"
                final_execution_sql = planned_limited_sql
                _emit_execution_route_decision(None)
                return _execute_duckdb_semantic_query(project_id, planned_limited_sql, row_limit, plan, start, bindings, models_by_binding)
            source_sql, missing_models = _rewrite_model_refs_for_source(planned_sql, models, ds_type)
            if missing_models:
                routing_stage = f"external_binding_{binding_id}_rewrite_failed"
                final_execution_sql = planned_limited_sql
                warning = "Could not safely rewrite model reference(s) for direct datasource execution: " + ", ".join(missing_models)
                _emit_execution_route_decision(warning)
                return _warning_query_result(warning, plan, start)
            transpiled_sql, transpile_warning = _transpile_sql_for_dialect(source_sql, ds_type)
            final_execution_sql = _apply_binding_limit(transpiled_sql, ds_type, row_limit)
            routing_stage = f"external_binding_{binding_id}"
            raw_result = _execute_external_raw_query_scoped(
                ds_type,
                props,
                final_execution_sql,
                row_limit,
                project_id,
            )
            if raw_result.get("warning"):
                if transpile_warning:
                    raw_result["warning"] += "; " + transpile_warning
                _emit_execution_route_decision(str(raw_result.get("warning") or ""))
                return _warning_query_result(raw_result["warning"], plan, start)
            if transpile_warning:
                _emit_execution_route_decision(transpile_warning)
                result = _finalize_query_result(raw_result["columns"], raw_result["tuples"], plan, start)
                result["warning"] = (result.get("warning") or "") + ("; " if result.get("warning") else "") + transpile_warning
                return result
            _emit_execution_route_decision(None)
            return _finalize_query_result(raw_result["columns"], raw_result["tuples"], plan, start)

        routing_stage = "cross_source"
        final_execution_sql = planned_limited_sql
        _emit_execution_route_decision(None)
        return _execute_cross_source_query(planned_sql, planned_limited_sql, project_id, row_limit, plan, start, referenced_by_binding, binding_lookup)
    except Exception as exc:
        safe_error = _sanitize_error_message(exc)
        bindings_summary = {binding_id: ds_type for binding_id, (ds_type, _props) in binding_lookup.items()}
        referenced_summary = {
            binding_id: [str(model.get("name") or "") for model in models]
            for binding_id, models in referenced_by_binding.items()
        }
        LOGGER.warning(
            "SQL execution routing failed at %s (project_id=%d, user_id=%d): error=%s, sql=%s, planned_sql=%s, final_sql=%s, bindings=%s, referenced_bindings=%s",
            routing_stage,
            project_id,
            user_id,
            safe_error,
            _sql_log_snippet(input_sql),
            _sql_log_snippet(planned_sql),
            _sql_log_snippet(final_execution_sql or planned_limited_sql),
            bindings_summary,
            referenced_summary,
            exc_info=True,
        )
        if should_emit_route_events:
            _emit_route_event(
                "execution_route_failure",
                {
                    "routing_stage": routing_stage,
                    "error": safe_error,
                    "planned_sql": planned_sql,
                    "final_execution_sql": final_execution_sql,
                    "binding_lookup": {int(k): v[0] for k, v in binding_lookup.items()},
                    "referenced_by_binding": {
                        int(binding_id): [str(model.get("name") or "") for model in models]
                        for binding_id, models in referenced_by_binding.items()
                    },
                    "route_v2_enabled": route_v2_enabled,
                    "shadow_mode": shadow_mode,
                },
                project_id=project_id,
            )
        if isinstance(exc, ValueError) and str(exc) == safe_error:
            raise
        raise ValueError(safe_error) from exc


_DID_YOU_MEAN_RE = re.compile(
    r"Table with name\s+(?P<wrong>\S+)\s+does not exist!?\s*Did you mean\s+(?P<suggestion>\S+)\??",
    re.IGNORECASE,
)


_DUCKDB_INTERNAL_TABLE_PREFIXES = ("duckdb_", "sqlite_", "pg_", "pragma_")
_DUCKDB_INTERNAL_TABLE_NAMES = frozenset(
    {
        "duckdb_types",
        "duckdb_columns",
        "duckdb_tables",
        "duckdb_schemas",
        "duckdb_views",
        "duckdb_databases",
        "sqlite_master",
        "sqlite_temp_master",
    }
)


def _duckdb_table_name_candidates(value: str) -> set[str]:
    text = str(value or "").strip().strip("?").strip()
    if not text:
        return set()
    candidates: set[str] = set()
    parts = _split_table_reference(text)
    if parts:
        normalized_parts = [str(part or "").strip().lower() for part in parts if str(part or "").strip()]
        if normalized_parts:
            candidates.add(".".join(normalized_parts))
            candidates.add(normalized_parts[-1])
    unquoted = _unquote_identifier(text).strip().lower()
    if unquoted:
        candidates.add(unquoted)
        if "." in unquoted:
            candidates.add(unquoted.split(".")[-1])
    return {candidate for candidate in candidates if candidate}


def _duckdb_is_internal_table_name(value: str) -> bool:
    for candidate in _duckdb_table_name_candidates(value):
        if candidate.startswith("information_schema.") or candidate.startswith("pg_catalog."):
            return True
        leaf = candidate.split(".")[-1]
        if leaf in _DUCKDB_INTERNAL_TABLE_NAMES:
            return True
        if any(leaf.startswith(prefix) for prefix in _DUCKDB_INTERNAL_TABLE_PREFIXES):
            return True
    return False


def _duckdb_semantic_visible_table_tokens(models_by_binding: dict[int, list[dict[str, Any]]]) -> set[str]:
    visible_tokens: set[str] = set()
    for models in (models_by_binding or {}).values():
        for model in models or []:
            for raw_name in (model.get("name"), model.get("table_reference")):
                for candidate in _duckdb_table_name_candidates(str(raw_name or "")):
                    visible_tokens.add(candidate)
    return visible_tokens


def _duckdb_dataset_suffix_alias_map(models_by_binding: dict[int, list[dict[str, Any]]]) -> dict[str, str]:
    alias_to_canonical: dict[str, str] = {}
    ambiguous_aliases: set[str] = set()
    suffix = "_dataset"
    for models in (models_by_binding or {}).values():
        for model in models or []:
            for raw_name in (model.get("name"), model.get("table_reference")):
                parts = _split_table_reference(str(raw_name or ""))
                if not parts:
                    continue
                leaf_name = str(parts[-1] or "").strip().lower()
                if not leaf_name.endswith(suffix):
                    continue
                alias_name = leaf_name[: -len(suffix)].strip()
                if not alias_name:
                    continue
                if alias_name in ambiguous_aliases:
                    continue
                existing = alias_to_canonical.get(alias_name)
                if existing and existing != leaf_name:
                    ambiguous_aliases.add(alias_name)
                    alias_to_canonical.pop(alias_name, None)
                    continue
                alias_to_canonical[alias_name] = leaf_name
    return alias_to_canonical


def _normalize_duckdb_dataset_suffix_tables(
    sql: str,
    models_by_binding: dict[int, list[dict[str, Any]]],
) -> str:
    alias_map = _duckdb_dataset_suffix_alias_map(models_by_binding)
    if not alias_map:
        return str(sql or "")
    if sqlglot is None or exp is None:
        return str(sql or "")
    source_sql = str(sql or "")
    try:
        parsed = sqlglot.parse_one(source_sql, read="duckdb")
    except Exception:
        return source_sql

    cte_names = {
        str(cte.alias or "").strip().lower()
        for cte in parsed.find_all(exp.CTE)
        if str(cte.alias or "").strip()
    }
    rewrites = 0
    for table in parsed.find_all(exp.Table):
        table_name = str(table.name or "").strip().lower()
        if not table_name or table_name in cte_names:
            continue
        if str(table.db or "").strip() or str(table.catalog or "").strip():
            continue
        replacement = alias_map.get(table_name)
        if not replacement or replacement == table_name:
            continue
        table.set("this", exp.to_identifier(replacement))
        table.set("db", None)
        table.set("catalog", None)
        rewrites += 1

    if rewrites <= 0:
        return source_sql
    try:
        rewritten_sql = parsed.sql(dialect="duckdb")
    except Exception:
        return source_sql
    LOGGER.info(
        "Normalized %d DuckDB table reference(s) using semantic '_dataset' alias map",
        rewrites,
    )
    return rewritten_sql


def _replace_table_identifier_once(sql: str, wrong: str, suggestion: str) -> str | None:
    raw_sql = str(sql or "")
    replacement = str(suggestion or "").strip()
    if not raw_sql or not replacement:
        return None
    candidates = []
    for candidate in [str(wrong or "").strip(), _unquote_identifier(str(wrong or "").strip())]:
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_]){re.escape(candidate)}(?![A-Za-z0-9_])"
        )
        replaced, count = pattern.subn(replacement, raw_sql, count=1)
        if count > 0 and replaced != raw_sql:
            return replaced
    return None


def _try_duckdb_did_you_mean_fix(
    sql: str,
    error_msg: str,
    *,
    semantic_visible_tables: set[str] | None = None,
    allow_internal_tables: bool = False,
) -> str | None:
    """If DuckDB suggests a table name via 'Did you mean', apply a guarded table-name rewrite."""
    m = _DID_YOU_MEAN_RE.search(error_msg)
    if not m:
        return None
    wrong = m.group("wrong").strip("\"'")
    suggestion = m.group("suggestion").strip("\"'?")
    if not wrong or not suggestion or wrong == suggestion:
        return None
    if not allow_internal_tables and _duckdb_is_internal_table_name(suggestion):
        LOGGER.warning(
            "Skipping DuckDB 'Did you mean' fix: suggestion '%s' looks like internal table",
            suggestion,
        )
        return None
    if semantic_visible_tables:
        suggestion_tokens = _duckdb_table_name_candidates(suggestion)
        if not suggestion_tokens.intersection(semantic_visible_tables):
            LOGGER.warning(
                "Skipping DuckDB 'Did you mean' fix: suggestion '%s' is outside semantic model table scope",
                suggestion,
            )
            return None
    return _replace_table_identifier_once(sql, wrong, suggestion)


def _execute_duckdb_semantic_query(
    project_id: int,
    planned_sql: str,
    row_limit: int,
    plan: dict[str, Any],
    start: float,
    bindings: list[tuple[int, str, dict[str, Any]]],
    models_by_binding: dict[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    route_started = time.perf_counter()
    duckdb_bindings = [(bid, props) for bid, ds_type, props in bindings if ds_type in {"duckdb", "sample"}]
    if not duckdb_bindings:
        _record_execution_metric(
            "duckdb",
            "warning",
            (time.perf_counter() - route_started) * 1000,
            0,
            project_id=project_id,
        )
        return _warning_query_result("No DuckDB/sample datasource binding is available for this semantic query.", plan, start)
    primary_binding, primary_props = duckdb_bindings[0]
    path = _resolve_duckdb_path(primary_props, project_id, primary_binding)
    
    if not os.path.exists(path):
        LOGGER.error("DuckDB file not found: %s (project_id=%d, binding_id=%d)", path, project_id, primary_binding)
        _record_execution_metric(
            "duckdb",
            "warning",
            (time.perf_counter() - route_started) * 1000,
            0,
            project_id=project_id,
        )
        return _warning_query_result(
            f"DuckDB database file not found at {path}. Please ensure the datasource is properly initialized and data has been synced.",
            plan,
            start,
        )
    
    LOGGER.info("Executing DuckDB query on file: %s", path)

    emit_route_events = _is_sql_route_v2_enabled(project_id) or bool(ROUTER_CONFIG.get("sql_route_shadow_mode", False))
    did_you_mean_fix_enabled = bool(ROUTER_CONFIG.get("duckdb_did_you_mean_fix_enabled", True))
    did_you_mean_allow_internal_tables = bool(ROUTER_CONFIG.get("duckdb_did_you_mean_allow_internal_tables", False))
    try:
        did_you_mean_max_retries = int(ROUTER_CONFIG.get("duckdb_did_you_mean_max_retries", 1) or 0)
    except Exception:
        did_you_mean_max_retries = 1
    did_you_mean_max_retries = max(0, min(did_you_mean_max_retries, 5))
    semantic_visible_tables = _duckdb_semantic_visible_table_tokens(models_by_binding)

    # ── LLM capability pre-check for weak models ──
    try:
        from services.llm_service import get_llm_config
        from services.sql_routing.llm_capability import get_model_capabilities, _capabilities_to_tier, _fast_precheck_bad_sql
        llm_cfg = get_llm_config()
        if llm_cfg.get("model"):
            caps = get_model_capabilities(
                llm_cfg.get("provider", ""),
                llm_cfg.get("endpoint", ""),
                llm_cfg.get("model", ""),
            )
            tier = _capabilities_to_tier(caps)
            precheck_error = _fast_precheck_bad_sql(planned_sql, tier, caps)
            if precheck_error:
                LOGGER.warning("SQL pre-check rejected for %s model: %s", tier, precheck_error)
                conn = duckdb.connect(path)
                conn.close()
                return _warning_query_result(
                    f"SQL pre-check failed: {precheck_error}",
                    plan,
                    start,
                )
    except Exception:
        LOGGER.debug("SQL pre-check unavailable (LLM may not be configured)", exc_info=True)

    conn = duckdb.connect(path)
    try:
        duckdb_binding_ids = {binding_id for binding_id, _ in duckdb_bindings}
        attached_binding_ids: set[int] = {primary_binding}
        for binding_id, props in duckdb_bindings[1:]:
            attach_path = _resolve_duckdb_path(props, project_id, binding_id)
            if not os.path.exists(attach_path):
                LOGGER.warning("Attached DuckDB file not found: %s (binding_id=%d)", attach_path, binding_id)
                continue
            if os.path.abspath(attach_path) == os.path.abspath(path):
                LOGGER.info("Skipping ATTACH for binding %d — same file as primary binding: %s", binding_id, attach_path)
                attached_binding_ids.add(binding_id)
                continue
            schema_name = f"ds_{binding_id}"
            try:
                conn.execute(f"ATTACH {_quote_sql_literal(attach_path)} AS {_quote_identifier(schema_name)}")
            except Exception as attach_exc:
                LOGGER.warning(
                    "Failed to ATTACH DuckDB file %s as %s: %s; skipping binding %d",
                    attach_path, schema_name, _sanitize_error_message(attach_exc), binding_id,
                )
                continue
            attached_binding_ids.add(binding_id)
        
        created_views = []
        for binding_id, models in models_by_binding.items():
            if binding_id not in duckdb_binding_ids:
                continue
            if binding_id not in attached_binding_ids:
                LOGGER.warning("Skipping models from unattached binding %d", binding_id)
                continue
            for model in models:
                if not _needs_duckdb_model_view(model, binding_id, primary_binding):
                    continue
                source = _duckdb_model_source_sql(model["table_reference"], binding_id, primary_binding)
                view_name = model["name"]
                try:
                    conn.execute(f"CREATE OR REPLACE TEMP VIEW {_quote_identifier(view_name)} AS SELECT * FROM {source}")
                    created_views.append(view_name)
                except Exception as e:
                    LOGGER.warning("Failed to create TEMP VIEW for model %s (source: %s): %s", view_name, source, e)

        LOGGER.info("Created %d TEMP VIEWs: %s", len(created_views), created_views)
        executable_sql = _normalize_duckdb_dataset_suffix_tables(planned_sql, models_by_binding)
        if executable_sql != planned_sql:
            LOGGER.warning("Applied semantic '_dataset' table normalization before DuckDB binder preflight")
        LOGGER.debug("Executing SQL: %s", executable_sql)

        scalar_subquery_retry = False
        aggregation_rewrite_retry = False
        did_you_mean_retry_count = 0
        while True:
            try:
                conn.execute(f"EXPLAIN {executable_sql}")
                break
            except Exception as binder_exc:
                binder_msg = _sanitize_error_message(binder_exc)
                binder_lower = binder_msg.lower()
                if (
                    ("scalar_subquery_error_on_multiple_rows" in binder_lower or ("subquery" in binder_lower and "single row" in binder_lower))
                    and not scalar_subquery_retry
                ):
                    LOGGER.warning(
                        "Retrying DuckDB binder preflight with scalar_subquery_error_on_multiple_rows=false: %s",
                        binder_msg,
                    )
                    conn.execute("SET scalar_subquery_error_on_multiple_rows=false")
                    scalar_subquery_retry = True
                    continue

                if _is_group_by_aggregate_binder_error(binder_msg) and not aggregation_rewrite_retry:
                    rewritten_sql, rewrite_notes = _apply_group_by_aggregation_rewrite_rules(executable_sql)
                    if rewritten_sql != executable_sql:
                        LOGGER.warning(
                            "Retrying DuckDB binder preflight with local aggregation rewrite: %s; rewrites=%s",
                            binder_msg,
                            rewrite_notes,
                        )
                        executable_sql = rewritten_sql
                        aggregation_rewrite_retry = True
                        continue

                full_msg = str(binder_exc)
                if did_you_mean_fix_enabled and did_you_mean_retry_count < did_you_mean_max_retries:
                    fixed_sql = _try_duckdb_did_you_mean_fix(
                        executable_sql,
                        full_msg,
                        semantic_visible_tables=semantic_visible_tables,
                        allow_internal_tables=did_you_mean_allow_internal_tables,
                    )
                    if fixed_sql is not None:
                        did_you_mean_retry_count += 1
                        LOGGER.warning(
                            "Retrying DuckDB binder preflight after guarded 'Did you mean' fix (%d/%d): %s",
                            did_you_mean_retry_count,
                            did_you_mean_max_retries,
                            binder_msg,
                        )
                        if emit_route_events:
                            _emit_route_event(
                                "duckdb_did_you_mean_fix",
                                {
                                    "status": "applied",
                                    "retry_index": did_you_mean_retry_count,
                                    "retry_limit": did_you_mean_max_retries,
                                    "allow_internal_tables": did_you_mean_allow_internal_tables,
                                },
                                project_id=project_id,
                            )
                        executable_sql = fixed_sql
                        continue

                LOGGER.warning("DuckDB binder preflight failed: %s", binder_msg)
                raise

        try:
            result = conn.execute(executable_sql)
        except duckdb.InvalidInputException as duckdb_exc:
            err_msg = str(duckdb_exc)
            if "scalar_subquery_error_on_multiple_rows" in err_msg.lower() or ("subquery" in err_msg.lower() and "single row" in err_msg.lower()):
                LOGGER.warning("Retrying DuckDB query with scalar_subquery_error_on_multiple_rows=false: %s", err_msg)
                conn.execute("SET scalar_subquery_error_on_multiple_rows=false")
                result = conn.execute(executable_sql)
            else:
                raise
        columns = [desc[0] for desc in result.description or []]
        tuples = result.fetchmany(row_limit)
        _record_execution_metric(
            "duckdb",
            "success",
            (time.perf_counter() - route_started) * 1000,
            len(tuples),
            project_id=project_id,
        )
    except Exception as e:
        LOGGER.error("DuckDB query execution failed: %s", _sanitize_error_message(e), exc_info=True)
        _record_execution_metric(
            "duckdb",
            "error",
            (time.perf_counter() - route_started) * 1000,
            0,
            project_id=project_id,
        )
        raise
    finally:
        conn.close()
    return _finalize_query_result(columns, tuples, plan, start)


def _extract_predicate_pushdown(sql: str, model_name: str, ds_type: str = "duckdb", model_columns: set[str] | None = None, alias_map: dict[str, str] | None = None) -> list[str]:
    if sqlglot is None or exp is None:
        return []
    dialect = _dialect_for_ds(ds_type) if ds_type not in {"duckdb", "sample"} else "duckdb"
    try:
        parsed = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return []
    if not isinstance(parsed, exp.Select):
        return []
    effective_alias_map = alias_map or _build_alias_map(sql)
    outer_where = None
    for node in parsed.walk():
        if isinstance(node, exp.Where):
            parent = node.parent
            is_subquery_where = isinstance(parent, exp.Subquery) or (isinstance(parent, exp.Select) and parent != parsed)
            if not is_subquery_where:
                outer_where = node
                break
    where_clause = outer_where
    if not where_clause:
        return []
    predicates: list[str] = []
    condition = where_clause.this
    for conj in _flatten_and(condition):
        if _references_model(conj, model_name, model_columns, effective_alias_map):
            predicates.append(conj.sql(dialect=dialect))
    return predicates


def _flatten_and(node: exp.Expression) -> list[exp.Expression]:
    if isinstance(node, exp.And):
        return _flatten_and(node.left) + _flatten_and(node.right)
    return [node]


def _build_alias_map(sql: str) -> dict[str, str]:
    if sqlglot is None or exp is None:
        return {}
    try:
        parsed = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return {}
    alias_map: dict[str, str] = {}
    if isinstance(parsed, exp.Select):
        for cte in parsed.find_all(exp.CTE):
            cte_alias = str(cte.alias or "").lower()
            if not cte_alias:
                continue
            inner_select = cte.this
            if inner_select and hasattr(inner_select, "find_all"):
                for inner_table in inner_select.find_all(exp.Table):
                    inner_name = str(inner_table.name or "").lower()
                    if inner_name and inner_name != cte_alias:
                        alias_map[cte_alias] = inner_name
                        break
        for from_expr in parsed.find_all(exp.From):
            for table_expr in from_expr.find_all(exp.Table):
                tbl_alias = str(table_expr.alias or "").lower()
                tbl_name = str(table_expr.name or "").lower()
                if tbl_alias and tbl_name:
                    alias_map[tbl_alias] = tbl_name
        for join_expr in parsed.find_all(exp.Join):
            for table_expr in join_expr.find_all(exp.Table):
                tbl_alias = str(table_expr.alias or "").lower()
                tbl_name = str(table_expr.name or "").lower()
                if tbl_alias and tbl_name:
                    alias_map[tbl_alias] = tbl_name
    return alias_map


def _resolve_table_alias(table: str, alias_map: dict[str, str]) -> str:
    if not table:
        return ""
    table_lower = table.lower()
    resolved = alias_map.get(table_lower, table_lower)
    return resolved


def _references_model(node: exp.Expression, model_name: str, model_columns: set[str] | None = None, alias_map: dict[str, str] | None = None) -> bool:
    effective_alias_map = alias_map or {}
    for col in node.find_all(exp.Column):
        table = str(col.table or "").lower()
        name = str(col.name or "").lower()
        resolved_table = _resolve_table_alias(table, effective_alias_map)
        if resolved_table == model_name.lower():
            return True
        if not table and name and model_columns and name in model_columns:
            return True
    return False


def _extract_projection_pushdown(sql: str, model_name: str, alias_map: dict[str, str] | None = None) -> list[str] | None:
    if sqlglot is None or exp is None:
        return None
    try:
        parsed = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return None
    if not isinstance(parsed, exp.Select):
        return None
    effective_alias_map = alias_map or _build_alias_map(sql)
    if not parsed.args.get("group") and not parsed.find(exp.Star):
        columns = []
        model_name_lower = model_name.lower()
        for expr in parsed.expressions:
            for col in expr.find_all(exp.Column):
                table = str(col.table or "").lower()
                resolved_table = _resolve_table_alias(table, effective_alias_map) if table else ""
                if resolved_table == model_name_lower or not table:
                    col_name = str(col.name)
                    if col_name and col_name not in columns:
                        columns.append(col_name)
        if columns:
            return columns
    return None


def _detect_aggregate_pushdown(sql: str, model_name: str, alias_map: dict[str, str] | None = None) -> dict | None:
    if sqlglot is None or exp is None:
        return None
    try:
        parsed = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return None
    if not isinstance(parsed, exp.Select):
        return None
    effective_alias_map = alias_map or _build_alias_map(sql)
    model_name_lower = model_name.lower()
    group_clause = parsed.args.get("group")
    has_group_by = bool(group_clause)
    has_agg = any(isinstance(expr, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max, exp.ApproxDistinct)) for expr in parsed.find_all(exp.AggFunc))
    if not has_group_by and not has_agg:
        return None
    group_cols = []
    if group_clause:
        for g in group_clause.expressions if hasattr(group_clause, 'expressions') else [group_clause]:
            for col in g.find_all(exp.Column):
                group_cols.append(str(col.name))
    agg_funcs = []
    for expr in parsed.find_all(exp.AggFunc):
        agg_type = type(expr).__name__.upper()
        inner_cols = [str(c.name) for c in expr.find_all(exp.Column)]
        alias = str(expr.alias) if expr.alias else None
        agg_funcs.append({"type": agg_type, "columns": inner_cols, "alias": alias})
    referenced_in_where = []
    where_clause = parsed.find(exp.Where)
    if where_clause:
        for col in where_clause.find_all(exp.Column):
            table = str(col.table or "").lower()
            resolved_table = _resolve_table_alias(table, effective_alias_map) if table else ""
            if resolved_table == model_name_lower or not table:
                referenced_in_where.append(str(col.name))
    referenced_in_group = group_cols + [c for func in agg_funcs for c in func["columns"]]
    all_referenced = list(dict.fromkeys(referenced_in_where + referenced_in_group))
    return {
        "has_group_by": has_group_by,
        "group_columns": group_cols,
        "agg_functions": agg_funcs,
        "referenced_columns": all_referenced,
        "is_pushdown_safe": len(agg_funcs) > 0 and all(c in all_referenced or not c for func in agg_funcs for c in func["columns"]),
    }


def _extract_required_columns_for_model(sql: str, model_name: str, alias_map: dict[str, str] | None = None) -> list[str]:
    if sqlglot is None or exp is None:
        return []
    try:
        parsed = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return []
    effective_alias_map = alias_map or _build_alias_map(sql)
    model_name_lower = model_name.lower()
    required: list[str] = []
    seen: set[str] = set()
    for col in parsed.find_all(exp.Column):
        col_name = str(col.name or "").strip()
        if not col_name:
            continue
        table = str(col.table or "").lower()
        if not table:
            continue
        resolved_table = _resolve_table_alias(table, effective_alias_map)
        if resolved_table != model_name_lower:
            continue
        normalized_col_name = col_name.lower()
        if normalized_col_name in seen:
            continue
        seen.add(normalized_col_name)
        required.append(col_name)
    return required


def _should_pushdown_aggregate(agg_info: dict | None, model_row_count_estimate: int, materialization_limit: int) -> bool:
    if agg_info is None:
        return False
    if not agg_info.get("is_pushdown_safe"):
        return False
    if model_row_count_estimate > materialization_limit * 3:
        return True
    return False


def _execute_cross_source_fetch_job(job: dict[str, Any], project_id: int) -> dict[str, Any]:
    started = time.perf_counter()
    raw_result = _execute_binding_raw_query(
        job["ds_type"],
        job["props"],
        job["source_sql"],
        job["effective_limit"],
        project_id,
        job["binding_id"],
    )
    return {
        "binding_id": job["binding_id"],
        "model": job["model"],
        "effective_limit": job["effective_limit"],
        "source_sql": job["source_sql"],
        "raw_result": raw_result,
        "fetch_time_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def _run_cross_source_fetch_jobs(jobs: list[dict[str, Any]], project_id: int) -> list[dict[str, Any]]:
    if not jobs:
        return []
    configured_workers = int(ROUTER_CONFIG.get("cross_source_max_workers", 4) or 4)
    max_workers = max(1, min(configured_workers, len(jobs)))
    if max_workers <= 1 or len(jobs) <= 1:
        return [_execute_cross_source_fetch_job(job, project_id) for job in jobs]

    ordered_results: list[dict[str, Any] | None] = [None] * len(jobs)
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cross-source-fetch") as executor:
        future_to_index = {
            executor.submit(_execute_cross_source_fetch_job, job, project_id): index
            for index, job in enumerate(jobs)
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            ordered_results[index] = future.result()
    return [result for result in ordered_results if result is not None]


def _execute_cross_source_query(
    planned_sql: str,
    planned_limited_sql: str,
    project_id: int,
    row_limit: int,
    plan: dict[str, Any],
    start: float,
    referenced_by_binding: dict[int, list[dict[str, Any]]],
    binding_lookup: dict[int, tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    route_started = time.perf_counter()
    conn = duckdb.connect(":memory:")
    warnings: list[str] = []
    alias_map = _build_alias_map(planned_sql)
    source_sql_trace: dict[int, list[str]] = {}
    try:
        fetch_jobs: list[dict[str, Any]] = []
        for binding_id, models in referenced_by_binding.items():
            ds_type, props = binding_lookup.get(binding_id, (None, None))
            if not ds_type or props is None:
                return _warning_query_result(f"Datasource binding {binding_id} was not found for referenced model(s).", plan, start)
            for model in models:
                model_name_lower = str(model["name"]).lower()
                model_col_set = set(model.get("columns") or [])
                model_where = _extract_predicate_pushdown(planned_sql, model_name_lower, ds_type, model_col_set, alias_map)
                model_cols = _extract_projection_pushdown(planned_sql, model_name_lower, alias_map)
                agg_info = _detect_aggregate_pushdown(planned_sql, model_name_lower, alias_map)
                effective_cols = model_cols
                effective_where = model_where
                effective_limit = MAX_SOURCE_MATERIALIZATION_ROWS
                if agg_info and agg_info.get("is_pushdown_safe"):
                    referenced_cols = [
                        str(column).strip()
                        for column in (agg_info.get("referenced_columns") or [])
                        if str(column).strip() and (not model_col_set or str(column).strip().lower() in model_col_set)
                    ]
                    if referenced_cols:
                        if effective_cols is None:
                            effective_cols = list(dict.fromkeys(referenced_cols))
                        else:
                            effective_cols = list(dict.fromkeys(effective_cols + referenced_cols))
                    effective_limit = MAX_SOURCE_MATERIALIZATION_ROWS * 5
                required_cols = _extract_required_columns_for_model(planned_sql, model_name_lower, alias_map)
                if required_cols:
                    if effective_cols is None:
                        effective_cols = list(required_cols)
                    else:
                        effective_cols = list(dict.fromkeys(effective_cols + required_cols))
                if effective_cols and model_col_set:
                    filtered_cols: list[str] = []
                    seen_filtered: set[str] = set()
                    for column in effective_cols:
                        col_text = str(column or "").strip()
                        if not col_text:
                            continue
                        col_key = col_text.lower()
                        if col_key not in model_col_set or col_key in seen_filtered:
                            continue
                        seen_filtered.add(col_key)
                        filtered_cols.append(col_text)
                    effective_cols = filtered_cols or None
                source_sql = _model_source_select(model, ds_type, effective_limit, where_clauses=effective_where if effective_where else None, select_columns=effective_cols if effective_cols else None)
                source_sql_trace.setdefault(binding_id, []).append(f"{model['name']}: {_sql_log_snippet(source_sql)}")
                fetch_jobs.append(
                    {
                        "binding_id": binding_id,
                        "ds_type": ds_type,
                        "props": props,
                        "model": model,
                        "source_sql": source_sql,
                        "effective_limit": effective_limit,
                    }
                )

        fetched_results = _run_cross_source_fetch_jobs(fetch_jobs, project_id)
        for fetched in fetched_results:
            model = fetched["model"]
            raw_result = fetched["raw_result"]
            if raw_result.get("warning"):
                _record_execution_metric(
                    "cross_source",
                    "warning",
                    (time.perf_counter() - route_started) * 1000,
                    0,
                    project_id=project_id,
                )
                return _warning_query_result(raw_result["warning"], plan, start)
            if len(raw_result["tuples"]) >= MAX_SOURCE_MATERIALIZATION_ROWS:
                binding_id = fetched["binding_id"]
                fetch_ms = fetched.get("fetch_time_ms")
                prefix = f"{fetch_ms} ms" if isinstance(fetch_ms, (int, float)) else "unknown latency"
                warnings.append(
                    f"Model {model['name']} from binding {binding_id} fetched in {prefix} and was materialized with the first {MAX_SOURCE_MATERIALIZATION_ROWS} rows before local merge."
                )
            _materialize_rows(conn, model["name"], raw_result["columns"], raw_result["tuples"])
            fetch_ms = fetched.get("fetch_time_ms")
            if isinstance(fetch_ms, (int, float)) and fetch_ms >= 2000:
                warnings.append(
                    f"Cross-source fetch for model {model['name']} took {int(fetch_ms)} ms."
                )
        result = conn.execute(planned_limited_sql)
        columns = [desc[0] for desc in result.description or []]
        tuples = result.fetchmany(row_limit)
        _record_execution_metric(
            "cross_source",
            "success",
            (time.perf_counter() - route_started) * 1000,
            len(tuples),
            project_id=project_id,
        )
    except Exception as exc:
        binding_models = {
            binding_id: [str(model.get("name") or "") for model in models]
            for binding_id, models in referenced_by_binding.items()
        }
        LOGGER.warning(
            "Cross-source execution failed (project_id=%d): error=%s, planned_sql=%s, limited_sql=%s, binding_models=%s, materialized_sql=%s",
            project_id,
            _sanitize_error_message(exc),
            _sql_log_snippet(planned_sql),
            _sql_log_snippet(planned_limited_sql),
            binding_models,
            source_sql_trace,
            exc_info=True,
        )
        _record_execution_metric(
            "cross_source",
            "error",
            (time.perf_counter() - route_started) * 1000,
            0,
            project_id=project_id,
        )
        raise
    finally:
        conn.close()
    return _finalize_query_result(columns, tuples, plan, start, "; ".join(warnings) if warnings else None)


def _project_general_chat(
    question: str,
    project_id: int,
    previous_questions: Optional[list[str]] = None,
    previous_answers: Optional[list[str]] = None,
    semantic_context: Optional[str] = None,
    language: Optional[str] = None,
    metadata_summary: Optional[dict] = None,
) -> dict[str, Any]:
    llm = LLMService()
    if not llm.is_configured():
        return _llm_not_configured_error(language)
    if semantic_context is None:
        semantic_context, _, _ = _semantic_prompt(project_id, question, language=language)
    meta = _project_meta(project_id) or {}
    project_capabilities = _build_project_capabilities(project_id, language)
    messages = _build_general_chat_messages(
        language,
        f"{_sql_msg('project_context_label', language)}{_render_project_general_context(project_id, semantic_context, language)}\n\n{_sql_msg('capabilities_label', language)}{project_capabilities}",
    )
    idx = 1
    if previous_questions and previous_answers:
        history_limit = min(len(previous_questions), len(previous_answers), 5)
        for i in range(max(0, len(previous_questions) - history_limit), len(previous_questions)):
            ans_idx = i if i < len(previous_answers) else len(previous_answers) - 1
            messages.insert(idx, {"role": "assistant", "content": previous_answers[ans_idx][:500] if previous_answers[ans_idx] else ""})
            messages.insert(idx, {"role": "user", "content": previous_questions[i]})
            idx += 2
    guidance = ""
    if metadata_summary and metadata_summary.get("models_count", 0) > 0:
        if ROUTER_CONFIG.get("guidance_llm_available", True):
            guidance = _guidance_prompt(language).format(
                model_summary=metadata_summary["summary"],
                suggested_questions="\n".join(f"- {q}" for q in (metadata_summary.get("suggested_questions") or [])),
            )
        else:
            guidance = (
                _sql_msg("available_models_label", language)
                + metadata_summary['summary'] + "\n"
                + _sql_msg("example_questions_label", language)
                + "\n".join(f"- {q}" for q in (metadata_summary.get("suggested_questions") or []))
            )
    messages.append({
        "role": "user",
        "content": (
            _sql_msg("project_chat_instruction", language) + "\n"
            f"{guidance}\n"
            + (_sql_msg("project_context_prefix", language) + f"：{question}" if _in_chinese(language) else f"Question: {question}")
        ),
    })
    result = llm.chat(messages)
    result["content"] = _ensure_general_chat_content(
        result.get("content"),
        question=question,
        language=language,
        project_scoped=True,
    )
    return result


def _summarize_query_result(
    question: str,
    sql: str,
    query_result: dict[str, Any],
    generated_summary: str,
    language: Optional[str] = None,
    preview_row_limit: Optional[int] = None,
    previous_questions: Optional[list[str]] = None,
    analysis: Optional[dict] = None,
    project_id: Optional[int] = None,
) -> str:
    limit = _normalize_preview_row_limit(preview_row_limit)
    normalized_analysis = _normalize_question_analysis(analysis)
    analysis_payload = analysis if isinstance(analysis, dict) else {}
    sub_questions = normalized_analysis.get("sub_questions") or []
    metadata_focus = str(analysis_payload.get("metadata_question_part") or "").strip()
    non_metadata_focus = str(analysis_payload.get("non_metadata_question_part") or "").strip()
    clause_routing_prompt = _format_clause_routing_for_prompt(analysis_payload.get("clause_routing"))
    llm = LLMService()
    llm_configured = llm.is_configured()
    model_tier = None
    if llm_configured:
        model_tier, _ = _current_llm_model_tier(llm)
    strict_json = _strict_json_capability()
    prompt_selection = _prompt_profile_selection(
        "final_answer",
        strict_json_mode=strict_json.get("mode", "none"),
        model_tier=model_tier,
    )
    use_profile = bool(
        (project_id is not None and _is_sql_route_v2_enabled(project_id))
        or ROUTER_CONFIG.get("sql_route_shadow_mode", False)
    )
    system_suffix = f"\n<PROFILE>{prompt_selection.system_suffix}</PROFILE>" if use_profile and prompt_selection.system_suffix else ""
    if not llm_configured:
        if project_id is not None:
            _emit_route_event(
                "final_answer_fallback",
                {
                    "reason": "llm_unconfigured",
                    "mode": "deterministic_row_summary",
                },
                project_id=project_id,
            )
        return _generic_result_answer(query_result, generated_summary, language, limit, sub_questions=sub_questions)
    if query_result.get("warning"):
        if project_id is not None:
            _emit_route_event(
                "final_answer_fallback",
                {
                    "reason": "sql_warning_present",
                    "mode": "deterministic_row_summary",
                },
                project_id=project_id,
            )
        return _generic_result_answer(query_result, generated_summary, language, limit, sub_questions=sub_questions)
    preview_rows = query_result.get("rows", [])[:limit]
    _DISPLAY_ROW_LIMIT = 20
    display_rows = preview_rows[:_DISPLAY_ROW_LIMIT]
    sql_data = {
        "columns": query_result.get("columns", []),
        "rows": display_rows,
        "total_rows": query_result.get("total_rows", 0),
    }
    sub_q_hint = ""
    if sub_questions:
        sub_q_hint = f"\nOriginal question contains {len(sub_questions)} sub-questions. Answer must cover all: {'; '.join(sub_questions)}\n"
    route_focus_hint = ""
    if metadata_focus or non_metadata_focus or clause_routing_prompt:
        lines: list[str] = []
        if metadata_focus:
            lines.append(f"SQL-related question part: {metadata_focus}")
        if non_metadata_focus:
            lines.append(f"Non-SQL question part (already answered separately): {non_metadata_focus}")
        if clause_routing_prompt:
            lines.append("Clause routing details:")
            lines.extend(clause_routing_prompt.split("\n"))
        route_focus_hint = "\n" + "\n".join(lines) + "\n"
    user_content = (
        f"{sub_q_hint}"
        f"{route_focus_hint}"
    )
    if previous_questions:
        user_content += f"Conversation context (previous questions): {previous_questions}\n"
    user_content += f"\nQuestion: {question}\nSQL: {sql}\nSQL Data: {json.dumps(sql_data, ensure_ascii=False, default=str)}"
    if len(preview_rows) > _DISPLAY_ROW_LIMIT:
        user_content += f"\n(Only showing first {_DISPLAY_ROW_LIMIT} of {len(preview_rows)} rows)"
    messages = [
        {"role": "system", "content": f"{_render_system_prompt()}{system_suffix}\n{localized_contract('final_answer', language)}\n{_language_instruction(language)}"},
        {"role": "user", "content": user_content},
    ]
    try:
        result = llm.chat(messages)
        content = _strip_sql_json_leak(result.get("content") or "")
        if _answer_uses_result_data(content, query_result):
            return content
        LOGGER.warning("Discarded ungrounded SQL result summary; using deterministic row summary")
        if project_id is not None:
            _emit_route_event(
                "final_answer_fallback",
                {
                    "reason": "ungrounded_summary",
                    "mode": "deterministic_row_summary",
                },
                project_id=project_id,
            )
        return _generic_result_answer(query_result, generated_summary, language, limit, sub_questions=sub_questions)
    except Exception:
        LOGGER.exception("Result summarization failed")
        if project_id is not None:
            _emit_route_event(
                "final_answer_fallback",
                {
                    "reason": "summary_exception",
                    "mode": "deterministic_row_summary",
                },
                project_id=project_id,
            )
        return _generic_result_answer(query_result, generated_summary, language, limit, sub_questions=sub_questions)


def _basic_result_summary(query_result: dict[str, Any], fallback: str = "", language: Optional[str] = None) -> str:
    columns = query_result.get("columns", [])
    rows = query_result.get("rows", [])[:5]
    total = query_result.get("total_rows", 0)
    warning = query_result.get("warning")
    returned_text = _sql_msg("result_summary_returned", language)
    empty_columns_text = _sql_msg("result_summary_no_columns", language)
    empty_rows_text = _sql_msg("result_summary_no_rows", language)
    warning_label = _sql_msg("result_summary_warning", language)
    if not columns:
        parts = [empty_columns_text]
        if warning:
            parts.append(f"{warning_label}: {warning}")
        return "\n".join(parts)
    if not rows:
        parts = [empty_rows_text]
        if warning:
            parts.append(f"{warning_label}: {warning}")
        return "\n".join(parts)
    preview = []
    for index, row in enumerate(rows, start=1):
        values = ", ".join(f"{column}: {row.get(column)}" for column in columns[:6])
        preview.append(f"{index}. {values}")
    total_fmt = _sql_msg("result_summary_rows", language)
    first_fmt = _sql_msg("result_summary_first", language)
    rows_short = _sql_msg("result_summary_rows_short", language)
    parts = [f"{returned_text} {total} {total_fmt}。{first_fmt} {len(rows)} {rows_short}:", *preview]
    if warning:
        parts.append(f"{warning_label}: {warning}")
    return "\n".join(parts) if parts else fallback


def _to_number(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _format_metric(value: float, digits: int = 2) -> str:
    if abs(value - round(value)) < 0.000001:
        return f"{int(round(value)):,}"
    return f"{value:,.{digits}f}".rstrip("0").rstrip(".")


def _result_detail_hint(use_chinese: bool, language: Optional[str] = None) -> str:
    return _sql_msg("generic_result_hint", language)


def _generate_data_insights(columns: list[str], rows: list[dict[str, Any]], numeric_columns: list[str], language: Optional[str] = None) -> list[str]:
    insights: list[str] = []
    if len(rows) < 2 or not numeric_columns:
        return insights
    text_columns = [c for c in columns if c not in numeric_columns and len({str(row.get(c) or "") for row in rows}) > 1]
    col_sums: dict[str, float] = {}
    col_values: dict[str, list[float]] = {}
    for col in numeric_columns:
        values = [_to_number(row.get(col)) for row in rows if row.get(col) is not None]
        col_sums[col] = sum(values)
        col_values[col] = values
    for col in numeric_columns:
        values = col_values.get(col, [])
        if len(values) < 2 or col_sums.get(col, 0) == 0:
            continue
        max_val = max(values)
        min_val = min(v for v in values if v > 0) if any(v > 0 for v in values) else 0
        if min_val > 0 and max_val >= min_val * 3:
            ratio = max_val / min_val if min_val else 0
            insights.append(_sql_msg("insight_wide_spread_fmt", language).format(col, _format_metric(max_val), _format_metric(min_val), _format_metric(ratio, 1)))
            if len(insights) >= 4:
                break
        if col_sums.get(col, 0) != 0 and len(values) >= 3 and text_columns:
            break
    if text_columns and len(numeric_columns) > 0 and len(rows) >= 3:
        primary_text_col = text_columns[0]
        primary_num_col = numeric_columns[0]
        group_sums: dict[str, float] = {}
        for row in rows:
            key = str(row.get(primary_text_col) or "Unknown")
            group_sums[key] = group_sums.get(key, 0) + _to_number(row.get(primary_num_col))
        if group_sums:
            total = sum(group_sums.values())
            top_group = max(group_sums, key=group_sums.get)
            top_pct = (group_sums[top_group] / total * 100) if total else 0
            if top_pct >= 50 and len(group_sums) >= 2:
                if _in_chinese(language):
                    insights.append(_sql_msg("insight_top_group_fmt", language).format(top_group, primary_num_col, _format_metric(group_sums[top_group]), top_pct))
                else:
                    insights.append(_sql_msg("insight_top_group_fmt", language).format(top_group, top_pct, primary_num_col, _format_metric(group_sums[top_group])))
            bottom_group = min(group_sums, key=group_sums.get)
            if len(group_sums) >= 3 and group_sums[top_group] > 0 and group_sums[bottom_group] > 0:
                diff_ratio = group_sums[top_group] / group_sums[bottom_group]
                if diff_ratio >= 2:
                    insights.append(_sql_msg("insight_gap_fmt", language).format(primary_text_col, top_group, _format_metric(group_sums[top_group]), bottom_group, _format_metric(group_sums[bottom_group]), primary_num_col, _format_metric(diff_ratio, 1)))
    if len(numeric_columns) >= 2 and len(rows) >= 2:
        for i, col_a in enumerate(numeric_columns[:3]):
            for col_b in numeric_columns[i + 1 : 4]:
                vals_a = col_values.get(col_a, [])
                vals_b = col_values.get(col_b, [])
                if not vals_a or not vals_b:
                    continue
                min_len = min(len(vals_a), len(vals_b))
                correlation = _quick_correlation(vals_a[:min_len], vals_b[:min_len])
                if correlation >= 0.85:
                    insights.append(_sql_msg("insight_correlation_fmt", language).format(col_a, col_b, correlation))
                    if len(insights) >= 4:
                        break
            if len(insights) >= 4:
                break
    return insights[:4]


def _quick_correlation(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    var_x = sum((xi - mean_x) ** 2 for xi in x)
    var_y = sum((yi - mean_y) ** 2 for yi in y)
    if var_x == 0 or var_y == 0:
        return 0.0
    return cov / (var_x * var_y) ** 0.5


def _generic_result_answer(query_result: dict[str, Any], fallback: str = "", language: Optional[str] = None, preview_row_limit: Optional[int] = None, sub_questions: Optional[list[str]] = None) -> str:
    columns = [str(column) for column in query_result.get("columns", [])]
    limit = _normalize_preview_row_limit(preview_row_limit)
    rows = (query_result.get("rows", []) or [])[:limit]
    if not columns or not rows:
        return _basic_result_summary(query_result, fallback, language)

    total_rows = int(query_result.get("total_rows", len(rows)) or len(rows))
    show_all = total_rows <= limit
    preview_rows = rows if show_all else rows[: min(5, limit)]
    numeric_columns: list[str] = []
    for column in columns:
        values = [row.get(column) for row in rows[:50] if row.get(column) is not None]
        if values and sum(1 for value in values if _to_number(value) != 0 or str(value).strip() in {"0", "0.0"}) >= max(1, len(values) // 2):
            numeric_columns.append(column)

    display_columns = columns[:8]
    warning = query_result.get("warning")
    lines = [
        _sql_msg("generic_takeaway", language),
        _sql_msg("generic_preview_fmt", language).format(total_rows, len(columns)),
    ]
    if numeric_columns:
        lines.extend(["", _sql_msg("generic_key_metrics", language), _sql_msg("generic_metrics_header", language), "|---|---:|---:|---:|"])
        for column in numeric_columns[:4]:
            values = [_to_number(row.get(column)) for row in rows if row.get(column) is not None]
            if values:
                lines.append(f"| {column} | {_format_metric(sum(values))} | {_format_metric(sum(values) / len(values))} | {_format_metric(max(values))} |")
    insights = _generate_data_insights(columns, rows, numeric_columns, language)
    if insights:
        lines.extend(["", _sql_msg("generic_data_insights", language)])
        for insight in insights:
            lines.append(f"- {insight}")
    label = _sql_msg("generic_all_rows", language)[4:] if show_all else _sql_msg("generic_preview", language)[4:]
    lines.extend(["", "### " + label, "| # | " + " | ".join(display_columns) + " |", "|---:|" + "|".join("---" for _ in display_columns) + "|"])
    for index, row in enumerate(preview_rows, start=1):
        values = " | ".join(str(row.get(column) if row.get(column) is not None else "-") for column in display_columns)
        lines.append(f"| {index} | {values} |")
    if len(columns) > len(display_columns):
        lines.append(_sql_msg("generic_truncated_fmt", language).format(len(display_columns)))
    if total_rows > len(rows):
        lines.append(_sql_msg("generic_result_hint", language))
    if warning:
        lines.append(f"\n{_sql_msg("generic_warning_label", language)} {warning}")
    if sub_questions:
        lines.append(f"\n{_sql_msg("generic_subq_note_fmt", language).format(len(sub_questions))}")
    return "\n".join(lines)


def _normalized_marker(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff. -]", "", text)
    return text.strip()


def _answer_uses_result_data(content: str, query_result: dict[str, Any]) -> bool:
    text = _normalized_marker(content)
    if not text:
        return False
    rows = query_result.get("rows", []) or []
    columns = [str(column) for column in query_result.get("columns", [])]
    if not rows:
        return True

    off_topic_markers = [
        "thinking process",
        "reasoning process",
        "analyze the request",
        "final answer contract",
        "let me think",
        "思考过程",
        "推理过程",
        "what you want to know",
        "please specify",
        "provide more detail",
        "need to know",
        "provided data appears",
        "preliminary observations",
        "if you just want",
        "i need to know",
        "请指定",
        "请明确",
        "需要知道",
        "无法确定",
        "不能确定",
        "数据结构",
    ]
    if any(marker in text for marker in off_topic_markers):
        return False

    column_hits = 0
    for column in columns:
        marker = _normalized_marker(column)
        if len(marker) >= 3 and marker in text:
            column_hits += 1

    value_hits = 0
    seen_values: set[str] = set()
    for row in rows[:20]:
        for column in columns[:10]:
            value = row.get(column)
            if value is None:
                continue
            markers = {_normalized_marker(value)}
            number = _to_number(value)
            if number:
                markers.add(_format_metric(number).lower())
                markers.add(_format_metric(number, 0).lower())
            for marker in markers:
                if len(marker) < 3 or marker in seen_values:
                    continue
                seen_values.add(marker)
                if marker in text:
                    value_hits += 1
                    break
    return value_hits >= 2 or (value_hits >= 1 and column_hits >= 1) or column_hits >= 3


def _compose_final_answer(
    question: str,
    sql: Optional[str],
    query_result: Optional[dict[str, Any]],
    sql_summary: Optional[str],
    supplemental_answer: Optional[str],
    language: Optional[str] = None,
    preview_row_limit: Optional[int] = None,
) -> str:
    if not query_result:
        return supplemental_answer or sql_summary or ""
    limit = _normalize_preview_row_limit(preview_row_limit)
    if sql_summary:
        parts = [part for part in [sql_summary, supplemental_answer] if part]
        if query_result.get("warning"):
            warning = f"{_sql_msg('generic_warning_label', language)} {query_result['warning']}"
            parts.append(warning)
        return "\n\n".join(parts) if parts else _sql_msg("returned_query_results", language)
    parts = [part for part in [_generic_result_answer(query_result, "", language, limit), supplemental_answer] if part]
    if query_result.get("warning"):
        warning = f"{_sql_msg('generic_warning_label', language)} {query_result['warning']}"
        parts.append(warning)
    return "\n\n".join(parts) if parts else _sql_msg("returned_query_results", language)


def _fallback_answer_after_sql_failure(question: str, project_id: int, error: str, previous_questions: Optional[list[str]] = None, previous_answers: Optional[list[str]] = None, language: Optional[str] = None, semantic_context: Optional[str] = None) -> dict[str, Any]:
    llm = LLMService()
    if not llm.is_configured():
        return _llm_not_configured_error(language)
    if not semantic_context:
        semantic_context, _, _ = _semantic_prompt(project_id, question, language=language)
    messages = [
        {"role": "system", "content": f"{_render_system_prompt()}\n{_language_instruction(language)}"},
        {"role": "user", "content": f"{_sql_msg('project_context_label', language)}{_render_project_general_context(project_id, semantic_context, language)}"},
    ]
    if previous_questions and previous_answers:
        history_limit = min(len(previous_questions), len(previous_answers), 3)
        for i in range(max(0, len(previous_questions) - history_limit), len(previous_questions)):
            ans_idx = i if i < len(previous_answers) else len(previous_answers) - 1
            messages.append({"role": "user", "content": previous_questions[i]})
            messages.append({"role": "assistant", "content": previous_answers[ans_idx][:300] if previous_answers[ans_idx] else ""})
    messages.append({
        "role": "user",
        "content": (
            _sql_msg("answer_helpful_assistant", language)
            + _sql_msg("fallback_ask_capabilities", language)
        ),
    })
    result = llm.chat(messages)
    result["content"] = _strip_sql_json_leak(result.get("content") or "")
    return result


def execute_project_sql(sql: str, project_id: int, user_id: int, limit: Optional[int] = None) -> dict[str, Any]:
    return _execute_project_sql_routed(sql, project_id, user_id, limit)


# ── Main ask_question entry point ──────────────────────────────────
# TODO: Extract to ask_question.py
def ask_question(
    question: str,
    user_id: int,
    thread_id: Optional[int] = None,
    previous_questions: Optional[list[str]] = None,
    previous_answers: Optional[list[str]] = None,
    language: Optional[str] = None,
    preview_row_limit: Optional[int] = None,
    temporary: bool = False,
    progress_cb: Any = None,
    cancel_event: Optional[threading.Event] = None,
) -> dict[str, Any]:
    refresh_runtime_router_settings(force=False)
    project_id: Optional[int]
    if temporary:
            thread_id = int(thread_id or int(time.time() * 1000))
            project_id = None
            preview_row_limit = _normalize_preview_row_limit(preview_row_limit)
            response_builder = temporary_thread_response
    else:
        project_id = get_thread_project_id(thread_id, user_id) if thread_id else get_user_default_project_id(user_id)
        if not project_id:
            raise ValueError("No active project. Use temporary ask for empty-project chat.")
        thread_id = ensure_thread(project_id, user_id, question, thread_id, preview_row_limit)
        update_auto_thread_summary(thread_id, user_id, question, language)
        preview_row_limit = get_thread_preview_row_limit(thread_id, user_id)
        response_builder = create_thread_response
    route_v2_enabled = _is_sql_route_v2_enabled(project_id) if project_id else False
    shadow_mode = bool(ROUTER_CONFIG.get("sql_route_shadow_mode", False))
    emit_route_events = bool(project_id) and (route_v2_enabled or shadow_mode)
    metadata_part_for_event = question
    non_metadata_part_for_event = ""
    clause_routing_for_event: dict[str, Any] = _event_clause_routing_summary({})
    ask_started_at = time.perf_counter()
    stage_order = ("understand", "retrieve", "generate", "execute", "answer")
    stage_durations_ms: dict[str, float] = {stage: 0.0 for stage in stage_order}
    active_stage = "understand"
    active_stage_started_at = ask_started_at
    attempt_count_for_event = 0
    fallback_chain_for_event: list[str] = []

    def _normalize_attempt_count(value: Any, *, default: int = 0) -> int:
        try:
            return max(0, int(value))
        except Exception:
            return max(0, int(default))

    def _append_unique(bucket: list[str], marker: Any) -> None:
        normalized = str(marker or "").strip()
        if not normalized or normalized.lower() == "none":
            return
        if normalized not in bucket:
            bucket.append(normalized)

    def _extend_fallback_chain(markers: Any) -> None:
        if isinstance(markers, (list, tuple, set)):
            for marker in markers:
                _append_unique(fallback_chain_for_event, marker)
            return
        if isinstance(markers, str):
            _append_unique(fallback_chain_for_event, markers)

    def _fallback_chain_from_sql_engine(sql_engine: Any) -> list[str]:
        engine = str(sql_engine or "").strip().lower()
        if not engine:
            return []
        chain: list[str] = []
        if engine.startswith("decompose_merge") and engine != "decompose_merge":
            chain.append("decompose_merge_fallback")
        if "_rehint" in engine:
            chain.append("generation_rehint")
        if "_repair" in engine:
            chain.append("generation_repair")
        if "validation_circuit_open" in engine:
            chain.append("validation_circuit_open")
        if "_failed" in engine or engine.startswith("llm_fallback"):
            chain.append("generation_failed")
        return chain

    def _record_generated_observability(generated_payload: dict[str, Any]) -> None:
        nonlocal attempt_count_for_event
        payload = generated_payload if isinstance(generated_payload, dict) else {}
        if not payload:
            return
        generated_attempt_count = payload.get("attempt_count")
        if generated_attempt_count is None:
            generated_attempt_count = 1 if payload.get("sql") or payload.get("sql_engine") else 0
        attempt_count_for_event = max(
            attempt_count_for_event,
            _normalize_attempt_count(generated_attempt_count, default=0),
        )
        _extend_fallback_chain(payload.get("fallback_chain"))
        _extend_fallback_chain(_fallback_chain_from_sql_engine(payload.get("sql_engine")))

    def _flush_active_stage(*, now: float | None = None) -> None:
        nonlocal active_stage_started_at
        current_ts = float(now if now is not None else time.perf_counter())
        elapsed_ms = max(0.0, (current_ts - active_stage_started_at) * 1000.0)
        stage_durations_ms[active_stage] = float(stage_durations_ms.get(active_stage) or 0.0) + elapsed_ms
        active_stage_started_at = current_ts

    def _switch_stage(stage: str) -> None:
        nonlocal active_stage
        normalized_stage = str(stage or "").strip().lower()
        if normalized_stage not in stage_durations_ms:
            return
        if normalized_stage == active_stage:
            return
        now_ts = time.perf_counter()
        _flush_active_stage(now=now_ts)
        active_stage = normalized_stage

    def _assert_not_cancelled(stage: str) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise AskCancelledError(f"Ask request cancelled during {stage}")

    _assert_not_cancelled("start")

    def _repair_path_for_terminal(sql_engine: Optional[str], *, execution_repair: bool = False) -> str:
        engine = str(sql_engine or "").strip().lower()
        tags: list[str] = []
        if "_rehint" in engine:
            tags.append("generation_rehint")
        if "_repair" in engine:
            tags.append("generation_repair")
        if "validation_circuit_open" in engine:
            tags.append("validation_circuit_open")
        if execution_repair:
            tags.append("execution_repair")
        if not tags:
            return "none"
        return "+".join(dict.fromkeys(tags))

    def _emit_ask_terminal_event(
        success: bool,
        *,
        error: str | None = None,
        query_id: str | None = None,
        sql_engine: str | None = None,
        rows: int | None = None,
        execution_time_ms: float | None = None,
        sql_warning: str | None = None,
        has_sql: bool | None = None,
        repair_path: str | None = None,
        duration_ms: float | None = None,
        attempt_count: int | None = None,
        fallback_chain: list[str] | None = None,
    ) -> None:
        if not emit_route_events or project_id is None:
            return
        _flush_active_stage()
        elapsed_ms = duration_ms
        if elapsed_ms is None:
            elapsed_ms = max(0.0, (time.perf_counter() - ask_started_at) * 1000.0)
        terminal_attempt_count = _normalize_attempt_count(
            attempt_count if attempt_count is not None else attempt_count_for_event,
            default=0,
        )
        if terminal_attempt_count <= 0 and bool(has_sql):
            terminal_attempt_count = 1
        terminal_fallback_chain: list[str] = []
        for marker in fallback_chain_for_event:
            _append_unique(terminal_fallback_chain, marker)
        for marker in _fallback_chain_from_sql_engine(sql_engine):
            _append_unique(terminal_fallback_chain, marker)
        if repair_path is not None:
            for marker in str(repair_path).split("+"):
                _append_unique(terminal_fallback_chain, marker)
        if fallback_chain is not None:
            if isinstance(fallback_chain, list):
                for marker in fallback_chain:
                    _append_unique(terminal_fallback_chain, marker)
            else:
                _append_unique(terminal_fallback_chain, fallback_chain)
        payload: dict[str, Any] = {
            "metadata_question_part": metadata_part_for_event,
            "non_metadata_question_part": non_metadata_part_for_event,
            "clause_routing": clause_routing_for_event,
            "clause_mixed": bool(clause_routing_for_event.get("mixed")),
            "metadata_clause_count": int(clause_routing_for_event.get("metadata_clause_count") or 0),
            "non_metadata_clause_count": int(clause_routing_for_event.get("non_metadata_clause_count") or 0),
            "route_v2_enabled": route_v2_enabled,
            "shadow_mode": shadow_mode,
            "duration_ms": float(elapsed_ms),
            "stage_durations_ms": {
                stage: round(float(stage_durations_ms.get(stage) or 0.0), 3)
                for stage in stage_order
            },
            "attempt_count": int(terminal_attempt_count),
            "fallback_chain": terminal_fallback_chain,
        }
        if query_id is not None:
            payload["query_id"] = query_id
        if sql_engine is not None:
            payload["sql_engine"] = sql_engine
        if rows is not None:
            payload["rows"] = int(rows)
        if execution_time_ms is not None:
            payload["execution_time_ms"] = float(execution_time_ms)
        if sql_warning is not None:
            payload["sql_warning"] = sql_warning
        if error is not None:
            payload["error"] = error
        if has_sql is not None:
            payload["has_sql"] = bool(has_sql)
        if repair_path is not None:
            payload["repair_path"] = str(repair_path)
        _emit_route_event("ask_route_success" if success else "ask_route_failure", payload, project_id=project_id)

    if emit_route_events and project_id is not None:
        _assert_not_cancelled("emit_route_events")
        ask_input = AskInput(
            question=question,
            user_id=int(user_id),
            project_id=int(project_id),
            thread_id=int(thread_id) if thread_id is not None else None,
            previous_questions=list(previous_questions or []),
            previous_answers=list(previous_answers or []),
            language=language,
            preview_row_limit=_normalize_preview_row_limit(preview_row_limit),
        )
        _emit_route_event(
            "ask_input",
            {
                **ask_input.to_dict(),
                "route_v2_enabled": route_v2_enabled,
                "shadow_mode": shadow_mode,
            },
            project_id=project_id,
        )
    if not project_id or not _project_has_context(project_id):
        _assert_not_cancelled("general_chat")
        _switch_stage("answer")
        result = _general_chat(question, previous_questions, previous_answers, language)
        answer_detail = {
            "status": "FINISHED" if result.get("configured") else "FAILED",
            "content": result.get("content"),
            "error": None if result.get("configured") else result.get("content"),
            "numRowsUsedInLLM": 0,
            "queryId": None,
        }
        asking_task = {
            "type": "GENERAL",
            "status": answer_detail["status"],
            "traceId": f"ask-{thread_id}-{int(time.time() * 1000)}",
            "queryId": None,
            "invalidSql": None,
            "candidates": [],
            "retrievedTables": [],
            "rephrasedQuestion": question,
            "intentReasoning": _sql_msg("intent_empty_project", language),
            "sqlGenerationReasoning": None,
            "error": answer_detail.get("error"),
            "processSteps": [
                {"key": "understand", "title": _step_title("understand", language), "status": "FINISHED", "detail": _step_detail_text("empty_project_or_no_project", language)},
                {"key": "answer", "title": _step_title("answer", language), "status": answer_detail["status"], "detail": answer_detail.get("content")},
            ],
        }
        breakdown_detail = {
            "status": answer_detail["status"],
            "description": _sql_msg("desc_empty_project", language),
            "steps": ["understand_question", "general_llm_answer"],
            "processSteps": asking_task["processSteps"],
            "error": answer_detail.get("error"),
        }
        response = response_builder(thread_id, user_id, question, None, asking_task, answer_detail, breakdown_detail)
        _emit_ask_terminal_event(
            True,
            error=answer_detail.get("error"),
            sql_engine="general_llm",
            rows=0,
            execution_time_ms=0.0,
            has_sql=False,
            repair_path="none",
        )
        return {"thread_id": thread_id, "response": response, "summary": answer_detail["content"], "sql": None}

    if _looks_like_general_chat(question):
        _assert_not_cancelled("project_general_chat")
        _switch_stage("answer")
        metadata_summary = _build_metadata_summary(project_id) if project_id else None
        result = _project_general_chat(question, project_id, previous_questions, previous_answers, language=language, metadata_summary=metadata_summary)
        answer_detail = {
            "status": "FINISHED" if result.get("configured") else "FAILED",
            "content": result.get("content"),
            "error": None if result.get("configured") else result.get("content"),
            "numRowsUsedInLLM": 0,
            "queryId": None,
        }
        asking_task = {
            "type": "GENERAL",
            "status": answer_detail["status"],
            "traceId": f"ask-{thread_id}-{int(time.time() * 1000)}",
            "queryId": None,
            "invalidSql": None,
            "candidates": [],
            "retrievedTables": [],
            "rephrasedQuestion": question,
            "intentReasoning": _sql_msg("intent_general_chat", language),
            "sqlGenerationReasoning": None,
            "error": answer_detail.get("error"),
            "processSteps": [
                {"key": "understand", "title": _step_title("understand", language), "status": "FINISHED", "detail": _step_detail_text("in_project_general", language)},
                {"key": "answer", "title": _step_title("answer", language), "status": answer_detail["status"], "detail": answer_detail.get("content")},
            ],
        }
        breakdown_detail = {
            "status": answer_detail["status"],
            "description": _sql_msg("intent_general_chat", language),
            "steps": ["understand_question", "project_general_llm_answer"],
            "processSteps": asking_task["processSteps"],
            "error": answer_detail.get("error"),
        }
        response = response_builder(thread_id, user_id, question, None, asking_task, answer_detail, breakdown_detail)
        _emit_ask_terminal_event(
            True,
            error=answer_detail.get("error"),
            sql_engine="project_general_llm",
            rows=0,
            execution_time_ms=0.0,
            has_sql=False,
            repair_path="none",
        )
        return {"thread_id": thread_id, "response": response, "summary": answer_detail["content"], "sql": None}

    try:
        _assert_not_cancelled("route")
        sql = None
        repaired_sql: Optional[str] = None
        generated: dict[str, Any] = {}
        route: dict[str, Any] = {}
        metadata_part = question
        non_metadata_part = ""
        if progress_cb:
            progress_cb("understand", _step_title("understand", language))
        _assert_not_cancelled("analyze_question")
        analysis = _analyze_question(question, project_id, previous_questions, language)
        _switch_stage("retrieve")
        route = _classify_question_route(question, project_id, previous_questions, analysis, language)
        metadata_part = route.get("metadata_question_part") or question
        non_metadata_part = route.get("non_metadata_question_part") or ""
        analysis_for_sql = _analysis_with_route_context(analysis, route, metadata_part, non_metadata_part)
        metadata_part_for_event = metadata_part
        non_metadata_part_for_event = non_metadata_part
        clause_routing_for_event = _event_clause_routing_summary(route.get("clause_routing"))
        if emit_route_events:
            metadata_context = MetadataHitContext(
                semantic_context=str(route.get("semantic_context") or ""),
                retrieved_tables=list(route.get("retrieved_tables") or []),
                semantic_hits=dict(route.get("semantic_hits") or {}),
                knowledge_context=str(route.get("knowledge_context") or ""),
                knowledge_hits=dict(route.get("knowledge_hits") or {}),
                analysis=dict(route.get("analysis") or analysis or {}),
            )
            metadata_payload = metadata_context.to_dict()
            metadata_payload.update(
                {
                    "metadata_question_part": metadata_part,
                    "non_metadata_question_part": non_metadata_part,
                    "clause_routing": clause_routing_for_event,
                    "clause_mixed": bool(clause_routing_for_event.get("mixed")),
                    "metadata_clause_count": int(clause_routing_for_event.get("metadata_clause_count") or 0),
                    "non_metadata_clause_count": int(clause_routing_for_event.get("non_metadata_clause_count") or 0),
                }
            )
            _emit_route_event("metadata_hit_context", metadata_payload, project_id=project_id)

        generation_pipeline = _generation_pipeline()
        generation_prompt_model_tier: str | None = None
        generation_prompt_model_tier_resolved = False

        def _emit_generation_decision(generated_payload: dict[str, Any] | None = None, *, requires_sql: bool) -> None:
            nonlocal generation_prompt_model_tier, generation_prompt_model_tier_resolved
            if not emit_route_events:
                return
            if not generation_prompt_model_tier_resolved:
                generation_prompt_model_tier_resolved = True
                if route_v2_enabled or shadow_mode:
                    generation_prompt_model_tier, _ = _current_llm_model_tier()
            strict_json = _strict_json_capability()
            try:
                prompt_selection = _prompt_profile_selection(
                    "sql_generation",
                    strict_json_mode=strict_json.get("mode", "none"),
                    model_tier=generation_prompt_model_tier,
                )
            except TypeError:
                prompt_selection = _prompt_profile_selection(
                    "sql_generation",
                    strict_json_mode=strict_json.get("mode", "none"),
                )
            generated_payload = generated_payload or {}
            sql_engine = str(generated_payload.get("sql_engine") or "")
            fallback_chain: list[str] = []
            if sql_engine.endswith("_repair"):
                fallback_chain.append("repair")
            if "_failed" in sql_engine or sql_engine.startswith("llm_fallback"):
                fallback_chain.append("generation_failed")
            if sql_engine.startswith("decompose_merge") and sql_engine != "decompose_merge":
                fallback_chain.append(sql_engine)
            decision = _get_generation_router().build_decision(
                requires_sql=requires_sql,
                metadata_question_part=metadata_part if requires_sql else "",
                non_metadata_question_part=non_metadata_part if not requires_sql else "",
                generation_engine=sql_engine or ("direct_llm" if requires_sql else "not_applicable"),
                prompt_profile_id=prompt_selection.profile_id,
                prompt_profile_version=prompt_selection.profile_version,
                strict_json_mode=strict_json.get("mode", "none"),
                reasoning=str((generated_payload or {}).get("reasoning") or route.get("reasoning") or ""),
                analysis_tier=str((analysis or {}).get("tier") or "simple"),
                fallback_chain=fallback_chain,
            )
            payload = decision.to_audit_payload()
            strategy_payload = _select_sql_strategy(
                analysis_for_sql if requires_sql else analysis,
                bool(route.get("knowledge_context")),
            )
            payload.update(
                {
                    "strategy_selected_engine": str(strategy_payload.get("engine") or "direct_llm"),
                    "strategy_mode": str(strategy_payload.get("mode") or "legacy_tier"),
                    "strategy_policy": str(strategy_payload.get("policy") or "tier_default"),
                    "strategy_risk_score": int(strategy_payload.get("risk_score") or 0),
                    "strategy_risk_level": str(strategy_payload.get("risk_level") or "low"),
                    "strategy_signals": (
                        strategy_payload.get("signals")
                        if isinstance(strategy_payload.get("signals"), dict)
                        else {}
                    ),
                    "clause_routing": clause_routing_for_event,
                    "clause_mixed": bool(clause_routing_for_event.get("mixed")),
                    "metadata_clause_count": int(clause_routing_for_event.get("metadata_clause_count") or 0),
                    "non_metadata_clause_count": int(clause_routing_for_event.get("non_metadata_clause_count") or 0),
                    "route_v2_enabled": route_v2_enabled,
                    "shadow_mode": shadow_mode,
                }
            )
            _emit_route_event("generation_route_decision", payload, project_id=project_id)
            if shadow_mode:
                has_knowledge = bool(route.get("knowledge_context"))
                shadow_payload = generation_pipeline.shadow_diff(
                    analysis=analysis or {},
                    has_knowledge=has_knowledge,
                    generation_engine=str(decision.generation_engine or ""),
                )
                shadow_payload.update(
                    {
                        "route_v2_enabled": route_v2_enabled,
                        "shadow_mode": shadow_mode,
                    }
                )
                _emit_route_event(
                    "generation_route_shadow_diff",
                    shadow_payload,
                    project_id=project_id,
                )

        if not route.get("requires_sql"):
            _assert_not_cancelled("non_sql_answer")
            _emit_generation_decision({}, requires_sql=False)
            _switch_stage("answer")
            result = _project_general_chat(non_metadata_part or question, project_id, previous_questions, previous_answers, route.get("combined_context"), language, route.get("metadata_summary"))
            answer_detail = {
                "status": "FINISHED" if result.get("configured") else "FAILED",
                "content": result.get("content"),
                "error": None if result.get("configured") else result.get("content"),
                "numRowsUsedInLLM": 0,
                "queryId": None,
            }
            asking_task = {
                "type": "GENERAL",
                "status": answer_detail["status"],
                "traceId": f"ask-{thread_id}-{int(time.time() * 1000)}",
                "queryId": None,
                "invalidSql": None,
                "candidates": [],
                "retrievedTables": [],
                "rephrasedQuestion": question,
                "intentReasoning": route.get("reasoning") or _sql_msg("intent_no_metadata", language),
                "sqlGenerationReasoning": None,
                "error": answer_detail.get("error"),
                "processSteps": [
                    {"key": "understand", "title": _step_title("understand", language), "status": "FINISHED", "detail": route.get("reasoning")},
                    {"key": "retrieve", "title": _step_title("retrieve", language), "status": "FINISHED", "detail": _step_detail_text("no_metadata_hit", language)},
                    {"key": "answer", "title": _step_title("answer", language), "status": answer_detail["status"], "detail": answer_detail.get("content")},
                ],
            }
            breakdown_detail = {
                "status": answer_detail["status"],
                "description": _sql_msg("desc_no_metadata", language),
                "steps": ["retrieve_metadata", "general_llm_answer"],
                "processSteps": asking_task["processSteps"],
                "error": answer_detail.get("error"),
            }
            response = response_builder(thread_id, user_id, question, None, asking_task, answer_detail, breakdown_detail)
            _emit_ask_terminal_event(
                True,
                error=answer_detail.get("error"),
                sql_engine="project_general_llm",
                rows=0,
                execution_time_ms=0.0,
                has_sql=False,
                repair_path="none",
            )
            return {"thread_id": thread_id, "response": response, "summary": answer_detail["content"], "sql": None}

        if progress_cb:
            progress_cb("retrieve", _step_title("retrieve", language))
        if progress_cb:
            progress_cb("organize", _sql_msg("progress_organizing", language))
        _switch_stage("generate")
        _assert_not_cancelled("generate_sql")
        generated = _generate_sql(
            metadata_part,
            project_id,
            previous_questions,
            route.get("semantic_context"),
            route.get("retrieved_tables"),
            route.get("semantic_hits"),
            language,
            route.get("knowledge_context"),
            analysis_for_sql,
            cancel_check=lambda: _assert_not_cancelled("generate_sql"),
        )
        _record_generated_observability(generated)
        _emit_generation_decision(generated, requires_sql=True)
        sql = generated.get("sql")
        if progress_cb:
            progress_cb("organize", generated.get("reasoning") or generated.get("summary") or _step_title("organize", language))
        if not sql:
            generation_reason = generated.get("reasoning") or generated.get("summary") or _sql_msg("failed_generate_sql_short", language)
            LOGGER.warning("SQL generation returned no SQL for thread %s: %s", thread_id, generation_reason)
            _switch_stage("answer")
            fallback = _fallback_answer_after_sql_failure(question, project_id, generation_reason, previous_questions, previous_answers, language) if non_metadata_part else {"content": None, "configured": False}
            if fallback.get("configured") and fallback.get("content"):
                _append_unique(fallback_chain_for_event, "answer_fallback")
                answer_detail = {
                    "status": "FINISHED",
                    "content": fallback["content"],
                    "error": generation_reason,
                    "numRowsUsedInLLM": 0,
                    "queryId": None,
                }
                asking_task = {
                    "type": "GENERAL",
                    "status": "FINISHED",
                    "traceId": f"ask-{thread_id}-{int(time.time() * 1000)}",
                    "queryId": None,
                    "invalidSql": None,
                    "candidates": [],
                    "retrievedTables": generated.get("retrieved_tables", []),
                    "rephrasedQuestion": metadata_part,
                    "intentReasoning": route.get("reasoning") or _sql_msg("intent_sql_failed_llm_fallback", language),
                    "sqlGenerationReasoning": generation_reason,
                    "error": generation_reason,
                    "processSteps": [
                        {"key": "understand", "title": _step_title("understand", language), "status": "FINISHED", "detail": route.get("reasoning")},
                        {"key": "retrieve", "title": _step_title("retrieve", language), "status": "FINISHED", "detail": _retrieve_detail(generated, route, language)},
                        {"key": "organize", "title": _step_title("organize", language), "status": "FAILED", "detail": generation_reason},
                        {"key": "answer", "title": _step_title("answer", language), "status": "FINISHED", "detail": fallback["content"][:200]},
                    ],
                }
                breakdown_detail = {
                    "status": "FAILED",
                    "description": _sql_msg("desc_sql_failed_retries", language),
                    "steps": ["retrieve_metadata", "generate_sql_failed", "fallback_general_answer"],
                    "processSteps": asking_task["processSteps"],
                    "error": generation_reason,
                }
                response = response_builder(thread_id, user_id, question, None, asking_task, answer_detail, breakdown_detail)
                _emit_ask_terminal_event(
                    False,
                    error=generation_reason,
                    sql_engine=str(generated.get("sql_engine") or "llm_fallback"),
                    rows=0,
                    execution_time_ms=0.0,
                    has_sql=False,
                    repair_path=_repair_path_for_terminal(str(generated.get("sql_engine") or "llm_fallback")),
                )
                return {"thread_id": thread_id, "response": response, "summary": fallback["content"], "sql": None}
            content = _sql_msg("err_unable_generate_sql", language)
            answer_detail = {
                "status": "FAILED",
                "content": content,
                "error": generation_reason,
                "numRowsUsedInLLM": 0,
                "queryId": None,
                "columns": [],
                "rows": [],
                "totalRows": 0,
                "executionTimeMs": 0,
                "metadataQuestionPart": metadata_part,
                "nonMetadataQuestionPart": non_metadata_part,
            }
            asking_task = {
                "type": "NL2SQL",
                "status": "FAILED",
                "traceId": f"ask-{thread_id}-{int(time.time() * 1000)}",
                "queryId": None,
                "invalidSql": None,
                "candidates": [],
                "retrievedTables": generated.get("retrieved_tables", []),
                "rephrasedQuestion": metadata_part,
                "intentReasoning": route.get("reasoning") or _sql_msg("intent_sql_failed", language),
                "sqlGenerationReasoning": generation_reason,
                "error": generation_reason,
                "processSteps": [
                    {"key": "understand", "title": _step_title("understand", language), "status": "FINISHED", "detail": route.get("reasoning")},
                    {"key": "retrieve", "title": _step_title("retrieve", language), "status": "FINISHED", "detail": _retrieve_detail(generated, route, language)},
                    {"key": "organize", "title": _step_title("organize", language), "status": "FAILED", "detail": generation_reason},
                ],
            }
            breakdown_detail = {
                "status": "FAILED",
                "description": _sql_msg("desc_sql_failed_retries_short", language),
                "steps": ["retrieve_metadata", "generate_sql_failed"],
                "processSteps": asking_task["processSteps"],
                "error": generation_reason,
            }
            response = response_builder(thread_id, user_id, question, None, asking_task, answer_detail, breakdown_detail)
            _emit_ask_terminal_event(
                False,
                error=generation_reason,
                sql_engine=str(generated.get("sql_engine") or "generation_failed"),
                rows=0,
                execution_time_ms=0.0,
                has_sql=False,
                repair_path=_repair_path_for_terminal(str(generated.get("sql_engine") or "generation_failed")),
            )
            return {"thread_id": thread_id, "response": response, "summary": content, "sql": None}
        repaired_sql = None
        repair_reasoning = None
        if progress_cb:
            progress_cb("execute", _step_title("execute", language))
        _switch_stage("execute")
        _assert_not_cancelled("execute_sql")
        try:
            query_result = execute_project_sql(sql, project_id, user_id, preview_row_limit)
        except Exception as sql_exc:
            sql_engine = str(generated.get("sql_engine") or "")
            if sql_engine.startswith("decompose_merge"):
                _record_decompose_merge_failure(project_id, "execution_failed")
            try:
                repair = _repair_sql(
                    metadata_part,
                    sql,
                    str(sql_exc),
                    project_id,
                    route.get("semantic_context"),
                    language,
                    hit_models=route.get("semantic_hits", {}).get("models", []),
                    analysis=analysis,
                    cancel_check=lambda: _assert_not_cancelled("repair_sql"),
                )
            except Exception:
                raise sql_exc from None
            repaired_sql = repair.get("sql")
            repair_reasoning = repair.get("reasoning")
            if not repaired_sql or repaired_sql == sql:
                raise
            repair_inspected = _candidate_guard().inspect(
                repaired_sql,
                dimensions=(analysis_for_sql or {}).get("dimensions") or [],
                hit_models=list(((route.get("semantic_hits") or {}).get("models") or [])),
                resolved=(analysis_for_sql or {}).get("resolved"),
                project_id=project_id,
            )
            repair_validation_errors: list[str] = []
            repair_issue_buckets: dict[str, int] = {}
            if not repair_inspected.columns_inconclusive and repair_inspected.bad_columns:
                repair_issue_buckets = _summarize_unknown_column_issues(list(repair_inspected.bad_columns))
                repair_validation_errors.append(
                    "repair returned unresolved SQL references: "
                    + "; ".join(str(item) for item in repair_inspected.bad_columns[:4])
                )
            if repair_inspected.syntax_issues:
                repair_issue_buckets["repair_syntax_issue"] = int(repair_issue_buckets.get("repair_syntax_issue") or 0) + len(
                    repair_inspected.syntax_issues
                )
                repair_validation_errors.append(
                    "repair returned invalid SQL syntax: "
                    + "; ".join(str(item) for item in repair_inspected.syntax_issues[:3])
                )
            if repair_validation_errors:
                if emit_route_events:
                    _emit_route_event(
                        "repair_guard_blocked",
                        {
                            "generation_engine": str(generated.get("sql_engine") or ""),
                            "issue_buckets": repair_issue_buckets,
                            "errors": repair_validation_errors,
                        },
                        project_id=project_id,
                    )
                raise ValueError("; ".join(repair_validation_errors))
            query_result = execute_project_sql(repaired_sql, project_id, user_id, preview_row_limit)
            sql = repaired_sql
            _append_unique(fallback_chain_for_event, "execution_repair")
        generated_content = generated.get("summary") or f"Returned {query_result['total_rows']} rows."
        _switch_stage("answer")
        _assert_not_cancelled("summarize_result")
        sql_content = _summarize_query_result(
            metadata_part,
            sql,
            query_result,
            generated_content,
            language,
            preview_row_limit,
            previous_questions,
            analysis_for_sql,
            project_id=project_id,
        )
        supplemental = _non_metadata_completion(non_metadata_part, question, project_id, previous_questions, previous_answers, language) if non_metadata_part else {"content": "", "configured": True}
        if non_metadata_part and not supplemental.get("configured"):
            raise ValueError(supplemental.get("content") or _sql_msg("supplemental_llm_not_configured", language))
        if progress_cb:
            progress_cb("answer", _step_title("answer", language))
        _assert_not_cancelled("compose_answer")
        content = _compose_final_answer(question, sql, query_result, sql_content, supplemental.get("content"), language, preview_row_limit)
        query_id = f"query-{thread_id}-{int(time.time() * 1000)}"
        task_type = "MIXED" if non_metadata_part else "NL2SQL"
        answer_detail = {
            "status": "FINISHED",
            "content": content,
            "error": query_result.get("warning"),
            "numRowsUsedInLLM": min(query_result["total_rows"], preview_row_limit),
            "previewRowLimit": preview_row_limit,
            "queryId": query_id,
            "columns": query_result["columns"],
            "rows": query_result["rows"],
            "totalRows": query_result["total_rows"],
            "executionTimeMs": query_result["execution_time_ms"],
            "securityPlan": query_result.get("security_plan"),
            "metadataQuestionPart": metadata_part,
            "nonMetadataQuestionPart": non_metadata_part,
            "knowledgeHits": route.get("knowledge_hits"),
        }
        asking_task = {
            "type": task_type,
            "status": "FINISHED",
            "traceId": f"ask-{thread_id}-{int(time.time() * 1000)}",
            "queryId": answer_detail["queryId"],
            "invalidSql": None,
            "candidates": [{"sql": sql, "summary": content}],
            "retrievedTables": generated.get("retrieved_tables", []),
            "rephrasedQuestion": metadata_part if metadata_part != question else question,
            "intentReasoning": route.get("reasoning") or _sql_msg("intent_sql_ok", language),
            "sqlGenerationReasoning": generated.get("reasoning"),
            "error": None,
            "metadataQuestionPart": metadata_part,
            "nonMetadataQuestionPart": non_metadata_part,
            "sqlEngine": generated.get("sql_engine"),
            "knowledgeHits": route.get("knowledge_hits"),
            "processSteps": [
                {"key": "understand", "title": _step_title("understand", language), "status": "FINISHED", "detail": route.get("reasoning")},
                {"key": "retrieve", "title": _step_title("retrieve", language), "status": "FINISHED", "detail": _retrieve_detail(generated, route, language)},
                {"key": "organize", "title": _step_title("organize", language), "status": "FINISHED", "detail": repair_reasoning or generated.get("reasoning") or generated.get("summary")},
                {"key": "execute", "title": _step_title("execute", language), "status": "FINISHED", "detail": _execution_detail(query_result, language)},
                {"key": "answer", "title": _step_title("answer", language), "status": "FINISHED", "detail": content},
            ],
        }
        steps = ["interpret_question", "retrieve_metadata", "route_metadata_and_llm_parts", "generate_sql", "security_plan", "execute_query"]
        if non_metadata_part:
            steps.append("complete_non_metadata_part")
        steps.append("compose_final_answer")
        breakdown_detail = {
            "status": "FINISHED",
            "description": _sql_msg("desc_sql_exec_ok", language) if non_metadata_part else _sql_msg("desc_sql_full_ok", language),
            "queryId": answer_detail["queryId"],
            "steps": steps,
            "processSteps": asking_task["processSteps"],
            "error": None,
        }
        _emit_ask_terminal_event(
            True,
            query_id=answer_detail["queryId"],
            sql_engine=str(generated.get("sql_engine") or "sql_generation"),
            rows=int(query_result.get("total_rows") or 0),
            execution_time_ms=float(query_result.get("execution_time_ms") or 0.0),
            sql_warning=query_result.get("warning"),
            has_sql=True,
            repair_path=_repair_path_for_terminal(
                str(generated.get("sql_engine") or "sql_generation"),
                execution_repair=bool(repaired_sql),
            ),
        )
        response = response_builder(thread_id, user_id, question, sql, asking_task, answer_detail, breakdown_detail)
        return {"thread_id": thread_id, "response": response, "summary": content, "sql": sql}
    except AskCancelledError:
        raise
    except Exception as exc:
        safe_exc = _sanitize_error_message(exc)
        failed_sql = _normalize_sql_candidate(sql)
        _emit_ask_terminal_event(
            False,
            error=safe_exc,
            sql_engine=str(generated.get("sql_engine") or "unknown"),
            has_sql=bool(failed_sql),
            repair_path=_repair_path_for_terminal(
                str(generated.get("sql_engine") or "unknown"),
                execution_repair=bool(repaired_sql),
            ),
        )
        if failed_sql:
            LOGGER.warning(
                "Ask SQL execution failed; returning editable SQL response",
                extra={"project_id": project_id, "thread_id": thread_id, "user_id": user_id, "error": safe_exc},
            )
            error_message = safe_exc
            content = _sql_msg("err_sql_exec_failed", language) if _language_name(language).lower().startswith("chinese") or str(language or "").lower().startswith("zh") else _sql_msg("err_sql_exec_failed", language)
            answer_detail = {
                "status": "FAILED",
                "content": content,
                "error": error_message,
                "numRowsUsedInLLM": 0,
                "queryId": None,
                "columns": [],
                "rows": [],
                "totalRows": 0,
                "executionTimeMs": 0,
                "metadataQuestionPart": metadata_part,
                "nonMetadataQuestionPart": non_metadata_part,
                "knowledgeHits": route.get("knowledge_hits"),
            }
            asking_task = {
                "type": "NL2SQL",
                "status": "FAILED",
                "traceId": f"ask-{thread_id}-{int(time.time() * 1000)}",
                "queryId": None,
                "invalidSql": failed_sql,
                "candidates": [{"sql": failed_sql, "summary": content}],
                "retrievedTables": generated.get("retrieved_tables", []),
                "rephrasedQuestion": metadata_part,
                "intentReasoning": route.get("reasoning") or _sql_msg("intent_exec_attempt", language),
                "sqlGenerationReasoning": generated.get("reasoning"),
                "error": error_message,
                "metadataQuestionPart": metadata_part,
                "nonMetadataQuestionPart": non_metadata_part,
                "sqlEngine": generated.get("sql_engine"),
                "knowledgeHits": route.get("knowledge_hits"),
                "processSteps": [
                    {"key": "understand", "title": _step_title("understand", language), "status": "FINISHED", "detail": route.get("reasoning")},
                    {"key": "retrieve", "title": _step_title("retrieve", language), "status": "FINISHED", "detail": _retrieve_detail(generated, route, language)},
                    {"key": "organize", "title": _step_title("organize", language), "status": "FINISHED", "detail": generated.get("reasoning") or generated.get("summary")},
                    {"key": "execute", "title": _step_title("execute", language), "status": "FAILED", "detail": error_message},
                ],
            }
            breakdown_detail = {
                "status": "FAILED",
                "description": _sql_msg("desc_sql_exec_failed", language),
                "queryId": None,
                "steps": ["interpret_question", "retrieve_metadata", "generate_sql", "execute_query_failed"],
                "processSteps": asking_task["processSteps"],
                "error": error_message,
            }
            response = response_builder(thread_id, user_id, question, failed_sql, asking_task, answer_detail, breakdown_detail)
            return {"thread_id": thread_id, "response": response, "summary": content, "sql": failed_sql}
        LOGGER.exception(
            "Ask question failed",
            extra={"project_id": project_id, "thread_id": thread_id, "user_id": user_id, "error": safe_exc},
        )
        try:
            fallback = _fallback_answer_after_sql_failure(
                question, project_id, safe_exc, previous_questions, previous_answers, language,
                semantic_context=route.get("semantic_context") if isinstance(route, dict) else None,
            )
        except Exception:
            fallback = {"content": None, "configured": False, "error": safe_exc}
        fallback_content = fallback.get("content")
        if fallback.get("configured") and fallback_content:
            answer_detail = {
                "status": "FINISHED",
                "content": fallback_content,
                "error": safe_exc,
                "numRowsUsedInLLM": 0,
                "queryId": None,
            }
            asking_task = {
                "type": "GENERAL",
                "status": "FINISHED",
                "traceId": f"ask-{thread_id}-{int(time.time() * 1000)}",
                "queryId": None,
                "invalidSql": None,
                "candidates": [],
                "retrievedTables": [],
                "rephrasedQuestion": question,
                "intentReasoning": _sql_msg("intent_metadata_to_sql_failed", language),
                "sqlGenerationReasoning": None,
                "error": safe_exc,
                "processSteps": [
                    {"key": "retrieve", "title": _step_title("retrieve", language), "status": "FAILED", "detail": safe_exc},
                    {"key": "answer", "title": _step_title("answer", language), "status": "FINISHED", "detail": fallback_content},
                ],
            }
            breakdown_detail = {
                "status": "FAILED",
                "description": _sql_msg("desc_sql_or_exec_failed", language),
                "steps": ["retrieve_metadata", "generate_sql_or_execute", "fallback_general_answer"],
                "processSteps": asking_task["processSteps"],
                "error": safe_exc,
            }
            response = response_builder(thread_id, user_id, question, None, asking_task, answer_detail, breakdown_detail)
            return {"thread_id": thread_id, "response": response, "summary": fallback_content, "sql": None}
        answer_detail = {
            "status": "FAILED",
            "content": None,
            "error": safe_exc,
            "numRowsUsedInLLM": 0,
            "queryId": None,
        }
        asking_task = {
            "type": "NL2SQL",
            "status": "FAILED",
            "traceId": f"ask-{thread_id}-{int(time.time() * 1000)}",
            "queryId": None,
            "invalidSql": None,
            "candidates": [],
            "retrievedTables": [],
            "rephrasedQuestion": question,
            "intentReasoning": None,
            "sqlGenerationReasoning": None,
            "error": safe_exc,
        }
        response = response_builder(thread_id, user_id, question, None, asking_task, answer_detail)
        return {"thread_id": thread_id, "response": response, "summary": None, "sql": None}
    finally:
        _clear_thread_progress()


def reset_all_test_state() -> None:
    """Reset all mutable module-level state for test isolation."""
    _analysis_cache.clear()
    _analysis_cache_computing.clear()
    _decompose_merge_state_by_project.clear()
    global _runtime_settings_loaded
    _runtime_settings_loaded = False


# ── Lazy bindings ──────────────────────────────────────────────────
# Imports ask_observability and ask_decompose (which resolve external
# bindings from the fully-loaded ask_service) and then injects their
# definitions into this module's namespace so that bare-name references
# inside functions resolve correctly.
def _bind_observability():
    import sys
    import services.ask_observability as _obs  # noqa: F811
    mod = sys.modules[__name__]
    for name in dir(_obs):
        if name == 'LOGGER' or name.startswith('__'):
            continue
        if hasattr(mod, name):
            continue
        setattr(mod, name, getattr(_obs, name))


_bind_observability()


def _bind_decompose():
    import sys
    import services.ask_decompose as _dec  # noqa: F811
    mod = sys.modules[__name__]
    _DEC_NAMES = [
        '_decompose_merge_state_lock',
        '_decompose_merge_state_by_project',
        '_is_decompose_merge_temporarily_disabled',
        '_record_decompose_merge_failure',
        '_record_decompose_merge_success',
        '_decompose_merge_sql',
    ]
    for name in _DEC_NAMES:
        if hasattr(mod, name):
            continue
        setattr(mod, name, getattr(_dec, name))


_bind_decompose()

# ── Auto-propagate monkeypatched attributes ─────────────────────────
import sys
# This ensures tests that monkeypatch ask_service also affect the obs
# and decompose functions whose __globals__ points to sub-modules.
import types as _types


class _AskServiceModule(_types.ModuleType):
    """Wraps ask_service so that attribute writes propagate to ask_observability and ask_decompose.
    Propagation is restricted to names that exist in the target module, preventing
    accidental propagation of internal module state.
    """

    _PROPAGATION_SKIPLIST = frozenset({
        "LOGGER", "__name__", "__doc__", "__package__", "__loader__",
        "__spec__", "__path__", "__file__", "__cached__",
    })

    def __setattr__(self, name: str, value) -> None:
        super().__setattr__(name, value)
        if name in self._PROPAGATION_SKIPLIST or name.startswith('__'):
            return
        _obs_prop = sys.modules.get("services.ask_observability")
        if _obs_prop is not None and hasattr(_obs_prop, name):
            setattr(_obs_prop, name, value)
        _dec_prop = sys.modules.get("services.ask_decompose")
        if _dec_prop is not None and hasattr(_dec_prop, name):
            setattr(_dec_prop, name, value)


sys.modules[__name__].__class__ = _AskServiceModule
