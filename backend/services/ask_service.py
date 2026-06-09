from __future__ import annotations

import hashlib
import importlib
import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import lru_cache
from typing import Any, Callable, Optional

import duckdb

try:
    import sqlglot
    from sqlglot import exp
except Exception:
    sqlglot = None
    exp = None

from db import connection_lock, get_connection
from services.crypto_service import decrypt_json, is_encrypted_value
from services.llm_service import LLMService, parse_json_object
from services.prompt_templates import (
    DEFAULT_PROJECT_PROMPT,
    DEFAULT_SYSTEM_PROMPT,
    common_prompt_variables,
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

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PROJECT_DATA_DIR = os.path.join(BACKEND_DIR, "data", "projects")
MAX_SQL_ROWS = 200
DEFAULT_PREVIEW_ROW_LIMIT = 20
MIN_PREVIEW_ROW_LIMIT = 5
MAX_PREVIEW_ROW_LIMIT = 100
MIN_EXECUTION_ROW_LIMIT = 1
MAX_EXECUTION_ROW_LIMIT = 10000
MAX_SOURCE_MATERIALIZATION_ROWS = 5000
ROUTER_CONFIG = {
    "tier1_max_retries": 1,
    "tier2_max_retries": 2,
    "tier3_max_retries": 3,
    "adaptive_strategy_enabled": True,
    "adaptive_strategy_consensus_risk_threshold": 4,
    "adaptive_strategy_decompose_risk_threshold": 7,
    "adaptive_strategy_min_subquestions_for_decompose": 2,
    "tier1_max_columns_per_model": 12,
    "tier2_max_columns_per_model": 15,
    "tier3_max_columns_per_model": 20,
    "max_sub_questions": 5,
    "max_suggested_questions": 5,
    "metadata_summary_max_models": 10,
    "guidance_llm_available": True,
    "schema_pruning_enabled": True,
    "cross_source_max_workers": 4,
    "decompose_merge_enabled": True,
    "decompose_merge_circuit_enabled": True,
    "decompose_merge_failure_threshold": 1,
    "decompose_merge_disable_seconds": 3600,
    "external_connection_pool_enabled": True,
    "external_connection_pool_max_per_key": 4,
    "external_connection_pool_idle_seconds": 300,
    "execution_metrics_log_every": 25,
    "execution_metrics_log_interval_seconds": 180,
    "execution_metrics_max_samples": 400,
    "route_observability_window_seconds": 1800,
    "route_observability_max_events_per_project": 20000,
    "route_observability_persist_enabled": True,
    "route_observability_persist_interval_seconds": 30,
    "route_observability_persist_event_delta": 20,
    "route_observability_strategy_trend_max_points": 24,
    "route_observability_strategy_trend_persist_interval_seconds": 60,
    "route_observability_strategy_trend_persist_decision_delta": 5,
    "sql_route_v2_enabled": True,
    "sql_route_allowlist_projects": [],
    "sql_route_shadow_mode": False,
    "sql_route_event_persist_enabled": True,
    "model_ref_case_sensitive": True,
    "sql_route_profile_id": "prismbi.default",
    "sql_route_profile_version": "v2",
    "sql_route_strict_json_probe_enabled": True,
}
SQL_RESPONSE_CONTRACT = (
    "When generating SQL, return only JSON with keys sql, summary, reasoning. "
    "Only generate SELECT or WITH queries. Use the provided model table names exactly. "
    "You must use only columns explicitly listed in the provided semantic model. Never invent columns or infer names from general SQL examples. "
    "CRITICAL: Every column name in the SQL must match exactly one column name or display_name exposed in the semantic model above. "
    "CRITICAL: A column MUST be prefixed with the alias of the model that OWNS that column in the semantic model. "
    "For example, if 'customer_city' is listed under model 'customers' and 'quantity' is listed under model 'order_items', "
    "you MUST write 'customers.customer_city' or its alias (e.g., 'c.customer_city'), never 'order_items.customer_city'. "
    "Prefixing a column with the wrong table alias is the most common SQL generation error — always verify each column belongs to the aliased table before writing it. "
    "If a column appears in multiple models, use the relation section to determine which model instance owns it in your query context. "
    "When in doubt, use the full model name as alias (e.g., FROM customers AS customers) to avoid ambiguity. "
    "If a desired metric column is unavailable, derive the closest valid metric from listed columns and explain the assumption in reasoning. "
    "Select only fields that directly answer the user's question or are necessary for joins, filters, grouping, sorting, or interpreting the requested result. "
    "Do not select unrelated columns, raw IDs, audit fields, or descriptive fields unless the user asks for them. "
    "Prefer aggregated metrics and dimensions that match the question over SELECT *. "
    "Always alias tables when joining (e.g., orders o, customers c) and prefix every column with its alias. "
    "Within the same SELECT scope, every table/CTE alias must be unique; never reuse the same alias for different sources. "
    "Use INNER JOIN by default; use LEFT JOIN only when you intend to preserve rows from the left table that have no match. "
    "When aggregating, include only the GROUP BY columns and the aggregate expressions in SELECT — nothing else. "
    "CRITICAL: When the question asks about multiple dimensions or has compound sub-questions (e.g., 'which products sell best AND how they perform by city'), "
    "you MUST include ALL requested dimensions in GROUP BY. Do not simplify a multi-dimensional question to a single dimension. "
    "For 'which products sell best by city', GROUP BY must include both the product dimension AND the city dimension — not just one. "
    "If the question asks about A 'in/by/ across' B, include both A and B in GROUP BY and SELECT. "
    "For top-N or ranking questions, use ORDER BY with LIMIT rather than window functions unless a true rank is needed. "
    "If you use a CTE (WITH clause), use the correct syntax: WITH cte_name AS (SELECT ...) — the CTE body MUST be wrapped in parentheses. "
    "Never write 'WITH name AS SELECT' without parentheses. "
    "If you define a CTE, the main query MUST reference it. Never define a CTE that is unused by the final SELECT. "
    "For compound questions with multiple sub-questions (e.g., 'which products sell best AND how by city'), "
    "generate a SINGLE SQL query that covers all dimensions in one GROUP BY — do not create separate CTEs that duplicate the same joins. "
    "If the response is complex, prefer one well-structured flat query over multiple CTEs."
    "{dialect_hint}"
)
QUESTION_ROUTING_CONTRACT = (
    "Classify the user's question for PrismBI. Return only JSON with keys "
    "requires_sql, metadata_question_part, non_metadata_question_part, reasoning. "
    "requires_sql must be true when ANY part of the question can be answered with project data (counts, totals, averages, rankings, comparisons, trends, top-N, filters, percentages, cross-tabulations, breakdowns, or any measurable metric). "
    "CRITICAL: If a question contains multiple sub-questions that ALL involve data or metrics, put the ENTIRE question in metadata_question_part and leave non_metadata_question_part empty. "
    "For example, 'Which products sell best and how do they perform in different cities?' is a single compound data question — both halves need SQL with a GROUP BY that covers product and city dimensions. "
    "Only put text in non_metadata_question_part if it is genuinely unrelated to project data (e.g., greetings, concept explanations, opinions, or general knowledge that no SQL table can answer). "
    "Examples that require SQL (put in metadata_question_part): asking for counts, totals, averages, rankings, comparisons, trends, top-N, filters, breakdowns by dimension, performance across groups. "
    "Examples that do NOT require SQL (put in non_metadata_question_part): greetings, explanations of concepts, asking how to do something, opinions, or general knowledge questions. "
    "When in doubt, put the question in metadata_question_part rather than splitting it."
)
FINAL_ANSWER_CONTRACT = (
    "Write the final answer for the user. Use the provided SQL result columns and rows as the only source for data-backed claims. "
    "Do not use SQL-generation summaries, query intent, or assumptions as facts. "
    "Use the supplemental LLM answer only for parts that are not covered by project metadata. "
    "Do not include SQL text, JSON, or implementation reasoning in the answer. "
    "Structure your answer: lead with a direct answer to the question, then support with specific data points. "
    "CRITICAL: If the question has multiple sub-questions or dimensions, address ALL of them in your answer. "
    "For example, if the question asks 'which products sell best AND how they perform by city', "
    "you must provide BOTH the top products AND their city-level breakdown — not just one aspect. "
    "When presenting numbers, include units (e.g., '1,234 orders', '$56,789 in revenue'). "
    "For comparisons, state the direction and magnitude (e.g., 'X is 15% higher than Y'). "
    "For rankings or top-N, list the items with their values. "
    "Highlight the most important rows or patterns, and mention warnings or truncation when present. "
    "Analyze patterns, trends, outliers, and notable differences visible in the data rather than restating raw values. "
    "When the question implies comparison, structure the answer as a comparison with direction and magnitude. "
    "When the question asks about a specific entity, focus the answer on that entity first. "
    "Present numbers with context (e.g., 'X accounts for 60% of total', 'Y is 2.3x higher than Z'). "
    "Avoid simply listing all rows from the data — summarize the key findings first, then briefly illustrate with the most important examples. "
    "Mention that more rows are available in the Result view when relevant. "
    "If a target language is provided, answer in that language."
)
QUESTION_ANALYZER_CONTRACT = (
    "Analyze the user's data question and classify it. "
    "Return only JSON with keys: tier, sub_questions, entities, metrics, dimensions, filters, reasoning. "
    'tier must be one of: "simple" (one metric, 0-1 dimension), '
    '"multi_dimension" (1-2 metrics, 1-2 dimensions with GROUP BY), '
    '"compound" (multiple sub-questions needing separate group-bys or joins). '
    "sub_questions: list of individual sub-questions if compound, otherwise empty list. "
    "entities: extracted business entities (e.g., products, customers, orders). "
    "metrics: extracted business metrics (e.g., revenue, count, average, total). "
    "dimensions: extracted grouping dimensions (e.g., city, category, month, region). "
    "filters: extracted filter conditions as list of {field, operator, value}. "
    "reasoning: brief explanation of classification. "
    "A simple question asks for one metric with at most one dimension. "
    "A multi-dimension question asks for one metric broken down by 2+ dimensions. "
    "A compound question has 2+ separate sub-questions that may need different group-bys. "
    "If you cannot determine some fields, leave them as empty lists. "
    "Respond only in JSON, no markdown."
)
GUIDANCE_PROMPT = (
    "The user's question did not match available project metadata. "
    "Your task is to guide them helpfully:\n"
    "1) Briefly explain what data topics and models are available in this project.\n"
    "   Available models overview:\n{model_summary}\n"
    "2) Suggest 2-3 example questions they could ask, using actual model and column names.\n"
    "   Suggested questions:\n{suggested_questions}\n"
    "3) If the user's wording is close to available data, show the naming mapping.\n"
    "Format your response helpfully and naturally — not as an error message."
)
LOGGER = logging.getLogger(__name__)


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

_PROMPT_PROFILE_ROUTER = PromptProfileRouter()
_GENERATION_ROUTER = GenerationRouter(config_getter=lambda: ROUTER_CONFIG)
_EXECUTION_ROUTER = ExecutionRouter()


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


def _prompt_profile_selection(stage: str, *, strict_json_mode: str) -> Any:
    return _PROMPT_PROFILE_ROUTER.select(
        stage,
        strict_json_mode=strict_json_mode,
        profile_id=str(ROUTER_CONFIG.get("sql_route_profile_id") or "prismbi.default"),
        profile_version=str(ROUTER_CONFIG.get("sql_route_profile_version") or "v1"),
    )


def _emit_route_event(event_type: str, payload: dict[str, Any], project_id: int | None = None) -> None:
    _record_route_dimension_metric(event_type, payload, project_id)
    emit_sql_route_event(
        event_type,
        payload,
        project_id=project_id,
        persist=bool(ROUTER_CONFIG.get("sql_route_event_persist_enabled", True)),
    )


def _looks_like_response_format_error(exc: Exception) -> bool:
    lowered = str(exc or "").strip().lower()
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
        "schema",
    )
    return any(marker in lowered for marker in markers)


def _contains_sql_placeholder_markers(sql: str) -> bool:
    text = str(sql or "")
    if not text:
        return False
    return bool(_DECOMPOSE_SQL_PLACEHOLDER_RE.search(text))


def _llm_chat_with_response_format_fallback(
    llm: LLMService,
    messages: list[dict[str, Any]],
    *,
    response_format: Any,
    stage: str,
) -> dict[str, Any]:
    if not isinstance(response_format, dict):
        return llm.chat(messages, response_format=response_format)
    try:
        return llm.chat(messages, response_format=response_format)
    except Exception as exc:
        if not _looks_like_response_format_error(exc):
            raise
        safe_error = _sanitize_error_message(exc)
        LOGGER.warning(
            "Structured response_format rejected at stage=%s (%s); retrying with json mode",
            stage,
            safe_error,
        )
        return llm.chat(messages, response_format="json")


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
    if isinstance(value, (dict, list, bool)):
        return value
    if isinstance(value, (int, float)):
        return value
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


def _in_chinese(language: Optional[str]) -> bool:
    if not language:
        return False
    return str(language).lower().replace("_", "-").startswith("zh")


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
    }
    cn, en = details.get(key, (key, key))
    return cn if _in_chinese(language) else en


def _retrieve_detail(generated: dict, route: dict, language: Optional[str] = None) -> str:
    tables = ', '.join(generated.get('retrieved_tables', [])) or 'none'
    instructions_count = len((route.get('knowledge_hits') or {}).get('instructions') or [])
    sql_pairs_count = len((route.get('knowledge_hits') or {}).get('sql_pairs') or [])
    return f"Matched tables: {tables}; Knowledge hits: {instructions_count} instructions, {sql_pairs_count} SQL pairs."


def _execution_detail(query_result: dict, language: Optional[str] = None) -> str:
    rows = query_result.get('total_rows', 0)
    ms = query_result.get('execution_time_ms', 0)
    return f"Returned {rows} rows, took {ms} ms."


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
    variables = common_prompt_variables({
        "app_name": data.get("app_name") or "PrismBI",
        "language": data.get("language") or "en",
        "timezone": data.get("timezone") or "UTC",
        "date_format": data.get("date_format") or "YYYY-MM-DD",
        "llm_provider": data.get("llm_provider") or "",
        "llm_model": data.get("llm_model") or "",
        "llm_endpoint": data.get("llm_endpoint") or "",
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


def _render_project_general_context(project_id: int, semantic_context: str) -> str:
    meta = _project_meta(project_id) or {}
    parts = [
        f"Project: {meta.get('display_name') or meta.get('name') or project_id}",
        f"Description: {meta.get('description') or ''}",
        "Use this project context only to understand the business domain. Do not generate SQL, JSON, code fences, or query plans in normal chat answers.",
    ]
    if semantic_context:
        parts.append(f"Available metadata context:\n{semantic_context}")
    return "\n\n".join(parts)


def _build_project_capabilities(project_id: int) -> str:
    meta = _project_meta(project_id) or {}
    model_count = int(meta.get("model_count") or 0)
    datasource_count = int(meta.get("datasource_count") or 0)
    capabilities = [
        "PrismBI can answer data questions in natural language and generate SQL queries automatically.",
        "You can ask questions about trends, rankings, comparisons, totals, averages, and any metrics available in the project data.",
        "Results are presented with summary answers, data tables, and interactive charts.",
    ]
    if model_count > 0:
        capabilities.append(f"This project has {model_count} semantic model(s) connecting to its data source(s).")
    if datasource_count > 0:
        capabilities.append(f"The project is connected to {datasource_count} data source(s).")
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
        summary_lines.append(f"- {dname}: {len(cols)} fields")
        if metric_cols and dim_cols:
            suggested_questions.append(
                f"查询 {dname} 的 {metric_cols[0]} 按 {dim_cols[0]} 分组"
            )
        elif metric_cols:
            suggested_questions.append(f"统计 {dname} 的 {metric_cols[0]}")
    return {
        "summary": "\n".join(summary_lines),
        "suggested_questions": suggested_questions[:max_suggested],
        "models_count": len(all_models),
        "relations_count": len(relations),
    }


_analysis_cache: dict[str, tuple[dict, float]] = {}
_analysis_cache_max = 128
_CACHE_TTL_SECONDS = 300.0
_analysis_cache_lock = threading.Lock()
_analysis_cache_computing: set[str] = set()
_decompose_merge_state_lock = threading.Lock()
_decompose_merge_state_by_project: dict[int, dict[str, float]] = {}
_runtime_settings_lock = threading.Lock()
_runtime_settings_loaded = False
_runtime_settings_snapshot: dict[str, Any] = {}
_RUNTIME_ASK_DEFAULTS = {
    "max_sql_rows": 200,
    "default_preview_row_limit": 20,
    "min_preview_row_limit": 5,
    "max_preview_row_limit": 100,
    "max_source_materialization_rows": 5000,
    "analysis_cache_max": 128,
    "analysis_cache_ttl_s": 300.0,
}


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


def refresh_runtime_router_settings(force: bool = False) -> dict[str, Any]:
    global MAX_SQL_ROWS
    global DEFAULT_PREVIEW_ROW_LIMIT
    global MIN_PREVIEW_ROW_LIMIT
    global MAX_PREVIEW_ROW_LIMIT
    global MAX_SOURCE_MATERIALIZATION_ROWS
    global _analysis_cache_max
    global _CACHE_TTL_SECONDS
    global _runtime_settings_loaded
    global _runtime_settings_snapshot

    with _runtime_settings_lock:
        if _runtime_settings_loaded and not force:
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
        "router_max_sub_questions": (updated_router.get("max_sub_questions", 5), 1, 20),
        "router_max_suggested_questions": (updated_router.get("max_suggested_questions", 5), 1, 20),
        "router_metadata_summary_max_models": (updated_router.get("metadata_summary_max_models", 10), 1, 200),
        "router_cross_source_max_workers": (updated_router.get("cross_source_max_workers", 4), 1, 32),
        "router_decompose_merge_failure_threshold": (updated_router.get("decompose_merge_failure_threshold", 1), 1, 20),
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
    }
    float_router_settings: dict[str, tuple[float, float, float]] = {
        "router_decompose_merge_disable_seconds": (updated_router.get("decompose_merge_disable_seconds", 3600), 30.0, 86400.0),
        "router_external_connection_pool_idle_seconds": (updated_router.get("external_connection_pool_idle_seconds", 300), 30.0, 86400.0),
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
    }
    bool_router_settings: dict[str, str] = {
        "router_adaptive_strategy_enabled": "adaptive_strategy_enabled",
        "router_guidance_llm_available": "guidance_llm_available",
        "router_schema_pruning_enabled": "schema_pruning_enabled",
        "router_decompose_merge_enabled": "decompose_merge_enabled",
        "router_decompose_merge_circuit_enabled": "decompose_merge_circuit_enabled",
        "router_external_connection_pool_enabled": "external_connection_pool_enabled",
        "router_route_observability_persist_enabled": "route_observability_persist_enabled",
        "router_sql_route_v2_enabled": "sql_route_v2_enabled",
        "router_sql_route_shadow_mode": "sql_route_shadow_mode",
        "router_sql_route_event_persist_enabled": "sql_route_event_persist_enabled",
        "router_sql_route_strict_json_probe_enabled": "sql_route_strict_json_probe_enabled",
        "router_model_ref_case_sensitive": "model_ref_case_sensitive",
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
        "router_external_connection_pool_max_per_key": "external_connection_pool_max_per_key",
        "router_execution_metrics_log_every": "execution_metrics_log_every",
        "router_execution_metrics_max_samples": "execution_metrics_max_samples",
        "router_route_observability_window_seconds": "route_observability_window_seconds",
        "router_route_observability_max_events_per_project": "route_observability_max_events_per_project",
        "router_route_observability_persist_event_delta": "route_observability_persist_event_delta",
        "router_route_observability_strategy_trend_max_points": "route_observability_strategy_trend_max_points",
        "router_route_observability_strategy_trend_persist_decision_delta": "route_observability_strategy_trend_persist_decision_delta",
    }
    float_router_mapping = {
        "router_decompose_merge_disable_seconds": "decompose_merge_disable_seconds",
        "router_external_connection_pool_idle_seconds": "external_connection_pool_idle_seconds",
        "router_execution_metrics_log_interval_seconds": "execution_metrics_log_interval_seconds",
        "router_route_observability_persist_interval_seconds": "route_observability_persist_interval_seconds",
        "router_route_observability_strategy_trend_persist_interval_seconds": "route_observability_strategy_trend_persist_interval_seconds",
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

        ROUTER_CONFIG.update(updated_router)

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

    if previous_pool_enabled and not next_pool_enabled:
        _clear_external_connection_pool()
    return dict(snapshot)


def _is_decompose_merge_temporarily_disabled(project_id: int) -> bool:
    if not ROUTER_CONFIG.get("decompose_merge_circuit_enabled", True):
        return False
    now = time.monotonic()
    with _decompose_merge_state_lock:
        state = _decompose_merge_state_by_project.get(int(project_id))
        if not state:
            return False
        disabled_until = float(state.get("disabled_until") or 0.0)
        if disabled_until <= 0:
            return False
        if now >= disabled_until:
            state["disabled_until"] = 0.0
            state["failures"] = 0.0
            return False
        return True


def _record_decompose_merge_failure(project_id: int, reason: str | None = None) -> None:
    if not ROUTER_CONFIG.get("decompose_merge_circuit_enabled", True):
        return
    threshold = max(1, int(ROUTER_CONFIG.get("decompose_merge_failure_threshold", 1) or 1))
    disable_seconds = max(30.0, float(ROUTER_CONFIG.get("decompose_merge_disable_seconds", 3600) or 3600))
    now = time.monotonic()
    with _decompose_merge_state_lock:
        state = _decompose_merge_state_by_project.setdefault(int(project_id), {"failures": 0.0, "disabled_until": 0.0})
        disabled_until = float(state.get("disabled_until") or 0.0)
        if disabled_until and now < disabled_until:
            return
        failures = int(state.get("failures") or 0) + 1
        if failures >= threshold:
            state["failures"] = 0.0
            state["disabled_until"] = now + disable_seconds
            LOGGER.warning(
                "Temporarily disabling decompose-merge for project_id=%d after %d failure(s)%s",
                project_id,
                threshold,
                f" ({reason})" if reason else "",
            )
            return
        state["failures"] = float(failures)
    LOGGER.info(
        "Recorded decompose-merge failure for project_id=%d (%d/%d)%s",
        project_id,
        failures,
        threshold,
        f" ({reason})" if reason else "",
    )


def _record_decompose_merge_success(project_id: int) -> None:
    if not ROUTER_CONFIG.get("decompose_merge_circuit_enabled", True):
        return
    with _decompose_merge_state_lock:
        state = _decompose_merge_state_by_project.get(int(project_id))
        if not state:
            return
        state["failures"] = 0.0
        state["disabled_until"] = 0.0


_external_connection_pool_lock = threading.Lock()
_external_connection_pool: dict[str, list[tuple[Any, float]]] = {}


def _close_connection_quietly(conn: Any) -> None:
    if conn is None:
        return
    close_fn = getattr(conn, "close", None)
    if callable(close_fn):
        try:
            close_fn()
        except Exception:
            pass


def _external_pool_key(ds_type: str, props: dict[str, Any], driver_tag: str = "") -> str:
    normalized = (ds_type or "").lower()
    identity_fields = {
        "host": props.get("host"),
        "port": props.get("port"),
        "database": props.get("database") or props.get("dbname"),
        "schema": props.get("schema"),
        "user": props.get("user") or props.get("username"),
        "ssl": bool(_normalize_bool(props.get("ssl"))),
        "driver": driver_tag,
    }
    payload = json.dumps(identity_fields, sort_keys=True, ensure_ascii=True, default=str)
    signature = hashlib.sha256(payload.encode()).hexdigest()[:24]
    return f"{normalized}:{signature}"


def _is_postgres_connection_healthy(conn: Any) -> bool:
    closed = getattr(conn, "closed", None)
    if isinstance(closed, bool):
        return not closed
    if isinstance(closed, (int, float)):
        return int(closed) == 0
    return True


def _is_mysql_connection_healthy(conn: Any) -> bool:
    checker = getattr(conn, "is_connected", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    open_attr = getattr(conn, "open", None)
    if isinstance(open_attr, bool):
        return open_attr
    if isinstance(open_attr, (int, float)):
        return bool(open_attr)
    closed = getattr(conn, "closed", None)
    if isinstance(closed, bool):
        return not closed
    if isinstance(closed, (int, float)):
        return int(closed) == 0
    pinger = getattr(conn, "ping", None)
    if callable(pinger):
        try:
            pinger(reconnect=False)
            return True
        except TypeError:
            try:
                pinger()
                return True
            except Exception:
                return False
        except Exception:
            return False
    return True


def _is_generic_connection_healthy(conn: Any) -> bool:
    checker = getattr(conn, "is_connected", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    closed = getattr(conn, "closed", None)
    if isinstance(closed, bool):
        return not closed
    if isinstance(closed, (int, float)):
        return int(closed) == 0
    open_attr = getattr(conn, "open", None)
    if isinstance(open_attr, bool):
        return open_attr
    if isinstance(open_attr, (int, float)):
        return bool(open_attr)
    return True


def _acquire_pooled_connection(
    pool_key: str,
    connector: Any,
    health_check: Any,
) -> Any:
    if not ROUTER_CONFIG.get("external_connection_pool_enabled", True):
        return connector()
    idle_seconds = max(30.0, float(ROUTER_CONFIG.get("external_connection_pool_idle_seconds", 300) or 300))
    now = time.monotonic()
    candidate = None
    stale: list[Any] = []
    with _external_connection_pool_lock:
        bucket = _external_connection_pool.get(pool_key, [])
        kept: list[tuple[Any, float]] = []
        for pooled_conn, ts in bucket:
            if now - ts > idle_seconds:
                stale.append(pooled_conn)
                continue
            kept.append((pooled_conn, ts))
        if kept:
            candidate, _ = kept.pop()
        if kept:
            _external_connection_pool[pool_key] = kept
        else:
            _external_connection_pool.pop(pool_key, None)
    for stale_conn in stale:
        _close_connection_quietly(stale_conn)
    if candidate is not None:
        try:
            if health_check(candidate):
                return candidate
        except Exception:
            pass
        _close_connection_quietly(candidate)
    return connector()


def _release_pooled_connection(pool_key: str, conn: Any, healthy: bool = True) -> None:
    if conn is None:
        return
    if (not ROUTER_CONFIG.get("external_connection_pool_enabled", True)) or (not healthy):
        _close_connection_quietly(conn)
        return
    max_per_key = max(1, int(ROUTER_CONFIG.get("external_connection_pool_max_per_key", 4) or 4))
    overflow_conn = None
    with _external_connection_pool_lock:
        bucket = _external_connection_pool.setdefault(pool_key, [])
        bucket.append((conn, time.monotonic()))
        if len(bucket) > max_per_key:
            overflow_conn, _ = bucket.pop(0)
    if overflow_conn is not None:
        _close_connection_quietly(overflow_conn)


def _clear_external_connection_pool() -> None:
    to_close: list[Any] = []
    with _external_connection_pool_lock:
        for bucket in _external_connection_pool.values():
            to_close.extend(conn for conn, _ in bucket)
        _external_connection_pool.clear()
    for conn in to_close:
        _close_connection_quietly(conn)


_execution_metrics_lock = threading.Lock()
_execution_metrics_by_ds: dict[str, dict[str, Any]] = {}
_execution_metrics_by_project_ds: dict[tuple[int, str], dict[str, Any]] = {}
_route_dimension_metrics_lock = threading.Lock()
_route_dimension_metrics_by_project: dict[int, dict[str, Any]] = {}
_route_dimension_events_by_project: dict[int, deque[tuple[float, str, dict[str, Any]]]] = {}
_route_observability_snapshot_state_by_project: dict[int, dict[str, float]] = {}
_route_strategy_trend_points_by_project: dict[int, deque[dict[str, Any]]] = {}
_route_strategy_trend_snapshot_state_by_project: dict[int, dict[str, float]] = {}
_ROUTE_OBSERVABILITY_RETENTION_SECONDS = 86400.0
_ROUTE_OBSERVABILITY_SNAPSHOT_KEY_PREFIX = "router_route_observability_snapshot_project_"
_ROUTE_OBSERVABILITY_STRATEGY_TREND_KEY_PREFIX = "router_route_observability_strategy_trend_project_"
_ROUTE_OBSERVABILITY_STRATEGY_TREND_DEFAULT_MAX_POINTS = 24
_ROUTE_OBSERVABILITY_STRATEGY_TREND_MAX_POINTS = 240


def _new_execution_metric_bucket() -> dict[str, Any]:
    return {
        "total": 0,
        "success": 0,
        "warning": 0,
        "error": 0,
        "timeout": 0,
        "rows_total": 0,
        "latencies_ms": [],
        "last_logged": 0.0,
        "last_updated": 0.0,
    }


def _execution_metric_summary(bucket: dict[str, Any]) -> dict[str, Any]:
    latencies = list(bucket.get("latencies_ms") or [])
    sorted_latencies = sorted(float(item) for item in latencies if isinstance(item, (int, float)))
    p95_ms = 0.0
    avg_ms = 0.0
    if sorted_latencies:
        p95_index = max(0, math.ceil(len(sorted_latencies) * 0.95) - 1)
        p95_ms = round(sorted_latencies[p95_index], 2)
        avg_ms = round(sum(sorted_latencies) / len(sorted_latencies), 2)
    total = int(bucket.get("total") or 0)
    rows_total = int(bucket.get("rows_total") or 0)
    return {
        "total": total,
        "success": int(bucket.get("success") or 0),
        "warning": int(bucket.get("warning") or 0),
        "error": int(bucket.get("error") or 0),
        "timeout": int(bucket.get("timeout") or 0),
        "avg_ms": avg_ms,
        "p95_ms": p95_ms,
        "avg_rows": round(rows_total / total, 2) if total > 0 else 0.0,
        "last_updated": float(bucket.get("last_updated") or 0.0),
    }


def _new_route_dimension_bucket() -> dict[str, Any]:
    return {
        "events_total": 0,
        "route_kind": {},
        "generation_engine": {},
        "strategy_selected_engine": {},
        "strategy_mode": {},
        "strategy_policy": {},
        "strategy_risk_level": {},
        "strategy_risk_score_total": 0,
        "strategy_risk_score_max": 0,
        "strict_json_mode": {},
        "generation_decision_total": 0,
        "fallback_count_total": 0,
        "fallback_count_max": 0,
        "repair_used": 0,
        "generation_retry_reason": {},
        "validation_issue_bucket": {},
        "llm_empty_response_retry": 0,
        "repair_guard_blocked": 0,
        "repair_short_circuit": 0,
        "repair_short_circuit_reason": {},
        "schema_link_fallback_total": 0,
        "schema_link_fallback_reason": {},
        "sql_generation_fallback_total": 0,
        "sql_generation_fallback_reason": {},
        "final_answer_fallback_total": 0,
        "final_answer_fallback_reason": {},
        "last_updated": 0.0,
    }


def _route_observability_window_seconds() -> int:
    return _coerce_int_setting(
        ROUTER_CONFIG.get("route_observability_window_seconds"),
        1800,
        300,
        86400,
    )


def _route_dimension_retention_seconds(window_seconds: int) -> float:
    return max(float(window_seconds), _ROUTE_OBSERVABILITY_RETENTION_SECONDS)


def _route_observability_persist_enabled() -> bool:
    return _normalize_bool(ROUTER_CONFIG.get("route_observability_persist_enabled", True))


def _route_observability_persist_interval_seconds() -> float:
    return _coerce_float_setting(
        ROUTER_CONFIG.get("route_observability_persist_interval_seconds"),
        30.0,
        1.0,
        3600.0,
    )


def _route_observability_persist_event_delta() -> int:
    return _coerce_int_setting(
        ROUTER_CONFIG.get("route_observability_persist_event_delta"),
        20,
        1,
        10000,
    )


def _route_observability_strategy_trend_max_points() -> int:
    return _coerce_int_setting(
        ROUTER_CONFIG.get("route_observability_strategy_trend_max_points"),
        _ROUTE_OBSERVABILITY_STRATEGY_TREND_DEFAULT_MAX_POINTS,
        6,
        _ROUTE_OBSERVABILITY_STRATEGY_TREND_MAX_POINTS,
    )


def _route_observability_strategy_trend_persist_interval_seconds() -> float:
    return _coerce_float_setting(
        ROUTER_CONFIG.get("route_observability_strategy_trend_persist_interval_seconds"),
        60.0,
        1.0,
        3600.0,
    )


def _route_observability_strategy_trend_persist_decision_delta() -> int:
    return _coerce_int_setting(
        ROUTER_CONFIG.get("route_observability_strategy_trend_persist_decision_delta"),
        5,
        1,
        10000,
    )


def _route_observability_snapshot_setting_key(project_id: int) -> str:
    return f"{_ROUTE_OBSERVABILITY_SNAPSHOT_KEY_PREFIX}{int(project_id)}"


def _route_observability_strategy_trend_setting_key(project_id: int) -> str:
    return f"{_ROUTE_OBSERVABILITY_STRATEGY_TREND_KEY_PREFIX}{int(project_id)}"


def _route_dimension_zero_snapshot(window_seconds: int) -> dict[str, Any]:
    return {
        "events_total": 0,
        "route_kind": {},
        "generation_engine": {},
        "strategy_selected_engine": {},
        "strategy_mode": {},
        "strategy_policy": {},
        "strategy_risk_level": {},
        "strategy_risk_score_total": 0,
        "strategy_risk_score_avg": 0.0,
        "strategy_risk_score_max": 0,
        "strict_json_mode": {},
        "generation_decision_total": 0,
        "fallback_count_total": 0,
        "fallback_count_avg": 0.0,
        "fallback_count_max": 0,
        "schema_link_fallback_total": 0,
        "schema_link_fallback_reason": {},
        "schema_link_fallback_rate": 0.0,
        "sql_generation_fallback_total": 0,
        "sql_generation_fallback_reason": {},
        "sql_generation_fallback_rate": 0.0,
        "final_answer_fallback_total": 0,
        "final_answer_fallback_reason": {},
        "final_answer_fallback_rate": 0.0,
        "repair_used": 0,
        "generation_retry_reason": {},
        "validation_issue_bucket": {},
        "llm_empty_response_retry": 0,
        "repair_guard_blocked": 0,
        "repair_short_circuit": 0,
        "repair_short_circuit_reason": {},
        "window_seconds": int(window_seconds),
        "last_updated": 0.0,
    }


def _route_dimension_bucket_snapshot(bucket: dict[str, Any], window_seconds: int) -> dict[str, Any]:
    generation_total = sum(int(item) for item in (bucket.get("generation_engine") or {}).values())
    fallback_total = int(bucket.get("fallback_count_total") or 0)
    schema_link_fallback_total = int(bucket.get("schema_link_fallback_total") or 0)
    sql_generation_fallback_total = int(bucket.get("sql_generation_fallback_total") or 0)
    final_answer_fallback_total = int(bucket.get("final_answer_fallback_total") or 0)
    strategy_risk_score_total = int(bucket.get("strategy_risk_score_total") or 0)
    return {
        "events_total": int(bucket.get("events_total") or 0),
        "route_kind": dict(bucket.get("route_kind") or {}),
        "generation_engine": dict(bucket.get("generation_engine") or {}),
        "strategy_selected_engine": dict(bucket.get("strategy_selected_engine") or {}),
        "strategy_mode": dict(bucket.get("strategy_mode") or {}),
        "strategy_policy": dict(bucket.get("strategy_policy") or {}),
        "strategy_risk_level": dict(bucket.get("strategy_risk_level") or {}),
        "strategy_risk_score_total": strategy_risk_score_total,
        "strategy_risk_score_avg": round(strategy_risk_score_total / generation_total, 2) if generation_total > 0 else 0.0,
        "strategy_risk_score_max": int(bucket.get("strategy_risk_score_max") or 0),
        "strict_json_mode": dict(bucket.get("strict_json_mode") or {}),
        "generation_decision_total": int(bucket.get("generation_decision_total") or generation_total),
        "fallback_count_total": fallback_total,
        "fallback_count_avg": round(fallback_total / generation_total, 2) if generation_total > 0 else 0.0,
        "fallback_count_max": int(bucket.get("fallback_count_max") or 0),
        "schema_link_fallback_total": schema_link_fallback_total,
        "schema_link_fallback_reason": dict(bucket.get("schema_link_fallback_reason") or {}),
        "schema_link_fallback_rate": round(schema_link_fallback_total / generation_total, 4) if generation_total > 0 else 0.0,
        "sql_generation_fallback_total": sql_generation_fallback_total,
        "sql_generation_fallback_reason": dict(bucket.get("sql_generation_fallback_reason") or {}),
        "sql_generation_fallback_rate": round(sql_generation_fallback_total / generation_total, 4) if generation_total > 0 else 0.0,
        "final_answer_fallback_total": final_answer_fallback_total,
        "final_answer_fallback_reason": dict(bucket.get("final_answer_fallback_reason") or {}),
        "final_answer_fallback_rate": round(final_answer_fallback_total / generation_total, 4) if generation_total > 0 else 0.0,
        "repair_used": int(bucket.get("repair_used") or 0),
        "generation_retry_reason": dict(bucket.get("generation_retry_reason") or {}),
        "validation_issue_bucket": dict(bucket.get("validation_issue_bucket") or {}),
        "llm_empty_response_retry": int(bucket.get("llm_empty_response_retry") or 0),
        "repair_guard_blocked": int(bucket.get("repair_guard_blocked") or 0),
        "repair_short_circuit": int(bucket.get("repair_short_circuit") or 0),
        "repair_short_circuit_reason": dict(bucket.get("repair_short_circuit_reason") or {}),
        "window_seconds": int(window_seconds),
        "last_updated": float(bucket.get("last_updated") or 0.0),
    }


def _coerce_non_negative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return max(0, int(default))


def _coerce_non_negative_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        return max(0.0, float(default))
    if not math.isfinite(number):
        return max(0.0, float(default))
    return max(0.0, number)


def _coerce_counter_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, amount in value.items():
        count = _coerce_non_negative_int(amount, 0)
        if count <= 0:
            continue
        normalized[str(key or "unknown")] = count
    return normalized


def _counter_top_key(counter: dict[str, int]) -> str:
    if not counter:
        return ""
    ranked = sorted(counter.items(), key=lambda item: (-int(item[1] or 0), str(item[0] or "")))
    return str(ranked[0][0] or "") if ranked else ""


def _ensure_strategy_trend_history(project_id: int, max_points: int) -> deque[dict[str, Any]]:
    pid = int(project_id)
    normalized_max_points = max(6, int(max_points))
    existing = _route_strategy_trend_points_by_project.get(pid)
    if existing is not None and existing.maxlen == normalized_max_points:
        return existing
    next_history = deque(existing or [], maxlen=normalized_max_points)
    _route_strategy_trend_points_by_project[pid] = next_history
    return next_history


def _coerce_strategy_trend_point(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    captured_at_unix = _coerce_non_negative_float(value.get("captured_at_unix"), 0.0)
    decision_total = _coerce_non_negative_int(value.get("decision_total"), 0)
    if captured_at_unix <= 0 or decision_total <= 0:
        return None
    risk_score_avg = _coerce_non_negative_float(value.get("risk_score_avg"), 0.0)
    high_risk_rate = min(1.0, _coerce_non_negative_float(value.get("high_risk_rate"), 0.0))
    decompose_policy_rate = min(1.0, _coerce_non_negative_float(value.get("decompose_policy_rate"), 0.0))
    dominant_mode = str(value.get("dominant_mode") or "").strip().lower()
    dominant_policy = str(value.get("dominant_policy") or "").strip().lower()
    return {
        "captured_at_unix": round(captured_at_unix, 3),
        "decision_total": decision_total,
        "risk_score_avg": round(risk_score_avg, 3),
        "high_risk_rate": round(high_risk_rate, 6),
        "decompose_policy_rate": round(decompose_policy_rate, 6),
        "dominant_mode": dominant_mode,
        "dominant_policy": dominant_policy,
    }


def _normalize_strategy_trend_points(
    value: Any,
    *,
    retention_seconds: float,
    max_points: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    retention = max(60.0, float(retention_seconds))
    limit = max(6, int(max_points))
    now_unix = time.time()
    normalized: list[dict[str, Any]] = []
    for item in value:
        point = _coerce_strategy_trend_point(item)
        if point is None:
            continue
        captured_at_unix = float(point.get("captured_at_unix") or 0.0)
        if captured_at_unix <= 0:
            continue
        if (now_unix - captured_at_unix) > retention:
            continue
        normalized.append(point)
    normalized.sort(key=lambda item: float(item.get("captured_at_unix") or 0.0))
    if len(normalized) > limit:
        normalized = normalized[-limit:]
    return normalized


def _append_strategy_trend_point(history: deque[dict[str, Any]], point: dict[str, Any], *, max_points: int) -> bool:
    limit = max(6, int(max_points))
    if not history:
        history.append(dict(point))
        return True
    last = history[-1]
    unchanged = (
        int(last.get("decision_total") or 0) == int(point.get("decision_total") or 0)
        and abs(float(last.get("risk_score_avg") or 0.0) - float(point.get("risk_score_avg") or 0.0)) < 0.001
        and abs(float(last.get("high_risk_rate") or 0.0) - float(point.get("high_risk_rate") or 0.0)) < 0.0005
        and abs(float(last.get("decompose_policy_rate") or 0.0) - float(point.get("decompose_policy_rate") or 0.0)) < 0.0005
        and str(last.get("dominant_mode") or "") == str(point.get("dominant_mode") or "")
        and str(last.get("dominant_policy") or "") == str(point.get("dominant_policy") or "")
    )
    if unchanged:
        return False
    history.append(dict(point))
    while len(history) > limit:
        history.popleft()
    return True


def _strategy_trend_point_from_snapshot(snapshot: dict[str, Any], captured_at_unix: float) -> dict[str, Any] | None:
    generation_total = _coerce_non_negative_int(snapshot.get("generation_decision_total"), 0)
    if generation_total <= 0:
        generation_total = sum(int(item) for item in _coerce_counter_map(snapshot.get("generation_engine")).values())
    if generation_total <= 0:
        return None
    strategy_modes = _coerce_counter_map(snapshot.get("strategy_mode"))
    strategy_policies = _coerce_counter_map(snapshot.get("strategy_policy"))
    strategy_risk_levels = _coerce_counter_map(snapshot.get("strategy_risk_level"))
    high_risk_count = int(strategy_risk_levels.get("high") or 0)
    decompose_policy_count = max(
        int(strategy_policies.get("risk_decompose_merge") or 0),
        int(strategy_policies.get("decompose_merge") or 0),
    )
    point = _coerce_strategy_trend_point(
        {
            "captured_at_unix": captured_at_unix,
            "decision_total": generation_total,
            "risk_score_avg": _coerce_non_negative_float(snapshot.get("strategy_risk_score_avg"), 0.0),
            "high_risk_rate": (high_risk_count / generation_total) if generation_total > 0 else 0.0,
            "decompose_policy_rate": (decompose_policy_count / generation_total) if generation_total > 0 else 0.0,
            "dominant_mode": _counter_top_key(strategy_modes),
            "dominant_policy": _counter_top_key(strategy_policies),
        }
    )
    return point


def _persist_route_strategy_trend_points(
    project_id: int,
    points: list[dict[str, Any]],
    captured_at_unix: float,
    *,
    max_points: int,
) -> None:
    key = _route_observability_strategy_trend_setting_key(project_id)
    limit = max(6, int(max_points))
    payload = {
        "captured_at_unix": float(captured_at_unix),
        "points": list(points[-limit:]),
    }
    try:
        with connection_lock():
            con = get_connection()
            con.execute(
                "INSERT OR REPLACE INTO metadata.settings (key, value, updated_at) VALUES (?, ?::JSON, CURRENT_TIMESTAMP)",
                [key, json.dumps(payload)],
            )
    except Exception:
        LOGGER.warning("Failed to persist route strategy trend points for project_id=%d", project_id, exc_info=True)


def _load_route_strategy_trend_points(project_id: int, retention_seconds: float, *, max_points: int) -> list[dict[str, Any]]:
    key = _route_observability_strategy_trend_setting_key(project_id)
    try:
        with connection_lock():
            con = get_connection()
            row = con.execute("SELECT value FROM metadata.settings WHERE key = ?", [key]).fetchone()
    except Exception:
        return []
    if not row:
        return []

    raw_value = row[0]
    parsed: dict[str, Any] | None = None
    if isinstance(raw_value, dict):
        parsed = raw_value
    elif isinstance(raw_value, str):
        try:
            loaded = json.loads(raw_value)
            if isinstance(loaded, dict):
                parsed = loaded
        except Exception:
            parsed = None
    if not parsed:
        return []

    return _normalize_strategy_trend_points(
        parsed.get("points"),
        retention_seconds=retention_seconds,
        max_points=max_points,
    )


def _delete_route_strategy_trend_snapshot(project_id: Optional[int] = None) -> None:
    try:
        with connection_lock():
            con = get_connection()
            if project_id is None:
                con.execute(
                    "DELETE FROM metadata.settings WHERE key LIKE ?",
                    [f"{_ROUTE_OBSERVABILITY_STRATEGY_TREND_KEY_PREFIX}%"],
                )
            else:
                con.execute(
                    "DELETE FROM metadata.settings WHERE key = ?",
                    [_route_observability_strategy_trend_setting_key(int(project_id))],
                )
    except Exception:
        LOGGER.warning("Failed to clear persisted strategy trend snapshot", exc_info=True)


def _build_window_route_dimension_bucket(
    history: deque[tuple[float, str, dict[str, Any]]],
    *,
    window_seconds: int,
    now: float,
) -> dict[str, Any]:
    window_bucket = _new_route_dimension_bucket()
    window_start = now - float(window_seconds)
    for event_ts, marker, event_payload in history:
        if event_ts < window_start:
            continue
        _apply_route_dimension_event_to_bucket(window_bucket, marker, event_payload, event_ts)
    return window_bucket


def _persist_route_observability_snapshot(project_id: int, snapshot: dict[str, Any], captured_at_unix: float) -> None:
    key = _route_observability_snapshot_setting_key(project_id)
    payload = dict(snapshot)
    payload["captured_at_unix"] = float(captured_at_unix)
    try:
        with connection_lock():
            con = get_connection()
            con.execute(
                "INSERT OR REPLACE INTO metadata.settings (key, value, updated_at) VALUES (?, ?::JSON, CURRENT_TIMESTAMP)",
                [key, json.dumps(payload)],
            )
    except Exception:
        LOGGER.warning("Failed to persist route observability snapshot for project_id=%d", project_id, exc_info=True)


def _load_route_observability_snapshot(project_id: int, window_seconds: int) -> dict[str, Any] | None:
    key = _route_observability_snapshot_setting_key(project_id)
    try:
        with connection_lock():
            con = get_connection()
            row = con.execute("SELECT value FROM metadata.settings WHERE key = ?", [key]).fetchone()
    except Exception:
        return None
    if not row:
        return None

    raw_value = row[0]
    parsed: dict[str, Any] | None = None
    if isinstance(raw_value, dict):
        parsed = raw_value
    elif isinstance(raw_value, str):
        try:
            loaded = json.loads(raw_value)
            if isinstance(loaded, dict):
                parsed = loaded
        except Exception:
            parsed = None
    if not parsed:
        return None

    try:
        captured_at = float(parsed.get("captured_at_unix") or 0.0)
    except Exception:
        captured_at = 0.0
    if captured_at <= 0:
        return None
    if (time.time() - captured_at) > float(window_seconds):
        return None

    try:
        stored_window = int(parsed.get("window_seconds") or 0)
    except Exception:
        stored_window = 0
    if stored_window != int(window_seconds):
        return None

    return {
        "events_total": _coerce_non_negative_int(parsed.get("events_total"), 0),
        "route_kind": _coerce_counter_map(parsed.get("route_kind")),
        "generation_engine": _coerce_counter_map(parsed.get("generation_engine")),
        "strategy_selected_engine": _coerce_counter_map(parsed.get("strategy_selected_engine")),
        "strategy_mode": _coerce_counter_map(parsed.get("strategy_mode")),
        "strategy_policy": _coerce_counter_map(parsed.get("strategy_policy")),
        "strategy_risk_level": _coerce_counter_map(parsed.get("strategy_risk_level")),
        "strategy_risk_score_total": _coerce_non_negative_int(parsed.get("strategy_risk_score_total"), 0),
        "strategy_risk_score_avg": _coerce_non_negative_float(parsed.get("strategy_risk_score_avg"), 0.0),
        "strategy_risk_score_max": _coerce_non_negative_int(parsed.get("strategy_risk_score_max"), 0),
        "strict_json_mode": _coerce_counter_map(parsed.get("strict_json_mode")),
        "generation_decision_total": _coerce_non_negative_int(parsed.get("generation_decision_total"), 0),
        "fallback_count_total": _coerce_non_negative_int(parsed.get("fallback_count_total"), 0),
        "fallback_count_avg": _coerce_non_negative_float(parsed.get("fallback_count_avg"), 0.0),
        "fallback_count_max": _coerce_non_negative_int(parsed.get("fallback_count_max"), 0),
        "schema_link_fallback_total": _coerce_non_negative_int(parsed.get("schema_link_fallback_total"), 0),
        "schema_link_fallback_reason": _coerce_counter_map(parsed.get("schema_link_fallback_reason")),
        "schema_link_fallback_rate": _coerce_non_negative_float(parsed.get("schema_link_fallback_rate"), 0.0),
        "sql_generation_fallback_total": _coerce_non_negative_int(parsed.get("sql_generation_fallback_total"), 0),
        "sql_generation_fallback_reason": _coerce_counter_map(parsed.get("sql_generation_fallback_reason")),
        "sql_generation_fallback_rate": _coerce_non_negative_float(parsed.get("sql_generation_fallback_rate"), 0.0),
        "final_answer_fallback_total": _coerce_non_negative_int(parsed.get("final_answer_fallback_total"), 0),
        "final_answer_fallback_reason": _coerce_counter_map(parsed.get("final_answer_fallback_reason")),
        "final_answer_fallback_rate": _coerce_non_negative_float(parsed.get("final_answer_fallback_rate"), 0.0),
        "repair_used": _coerce_non_negative_int(parsed.get("repair_used"), 0),
        "generation_retry_reason": _coerce_counter_map(parsed.get("generation_retry_reason")),
        "validation_issue_bucket": _coerce_counter_map(parsed.get("validation_issue_bucket")),
        "llm_empty_response_retry": _coerce_non_negative_int(parsed.get("llm_empty_response_retry"), 0),
        "repair_guard_blocked": _coerce_non_negative_int(parsed.get("repair_guard_blocked"), 0),
        "repair_short_circuit": _coerce_non_negative_int(parsed.get("repair_short_circuit"), 0),
        "repair_short_circuit_reason": _coerce_counter_map(parsed.get("repair_short_circuit_reason")),
        "window_seconds": int(window_seconds),
        "last_updated": _coerce_non_negative_float(parsed.get("last_updated"), 0.0),
    }


def _delete_route_observability_snapshot(project_id: Optional[int] = None) -> None:
    try:
        with connection_lock():
            con = get_connection()
            if project_id is None:
                con.execute(
                    "DELETE FROM metadata.settings WHERE key LIKE ?",
                    [f"{_ROUTE_OBSERVABILITY_SNAPSHOT_KEY_PREFIX}%"],
                )
            else:
                con.execute(
                    "DELETE FROM metadata.settings WHERE key = ?",
                    [_route_observability_snapshot_setting_key(int(project_id))],
                )
    except Exception:
        LOGGER.warning("Failed to clear persisted route observability snapshot", exc_info=True)


def _minimize_route_dimension_payload(marker: str, payload: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    if marker == "execution_route_decision":
        compact["route_kind"] = str(payload.get("route_kind") or "unknown")
        return compact
    if marker == "sql_generation_retry":
        compact["reason"] = str(payload.get("reason") or "unknown")
        return compact
    if marker == "sql_repair_short_circuit":
        compact["reason"] = str(payload.get("reason") or "unknown")
        return compact
    if marker in {"schema_link_fallback", "sql_generation_fallback", "final_answer_fallback"}:
        compact["reason"] = str(payload.get("reason") or "unknown")
        return compact
    if marker in {"sql_validation_issue", "repair_guard_blocked"}:
        normalized_issue_buckets: dict[str, int] = {}
        issue_buckets = payload.get("issue_buckets")
        if isinstance(issue_buckets, dict):
            for issue, amount in issue_buckets.items():
                try:
                    normalized_amount = max(0, int(amount or 0))
                except Exception:
                    normalized_amount = 0
                if normalized_amount > 0:
                    normalized_issue_buckets[str(issue or "unknown")] = normalized_amount
        compact["issue_buckets"] = normalized_issue_buckets
        return compact
    if marker != "generation_route_decision":
        return compact
    compact["generation_engine"] = str(payload.get("generation_engine") or "unknown")
    compact["strict_json_mode"] = str(payload.get("strict_json_mode") or "none")
    compact["strategy_selected_engine"] = str(
        payload.get("strategy_selected_engine") or payload.get("generation_engine") or "unknown"
    )
    compact["strategy_mode"] = str(payload.get("strategy_mode") or "legacy_tier")
    compact["strategy_policy"] = str(payload.get("strategy_policy") or "tier_default")
    compact["strategy_risk_level"] = str(payload.get("strategy_risk_level") or "low")
    try:
        compact["strategy_risk_score"] = max(0, int(payload.get("strategy_risk_score") or 0))
    except Exception:
        compact["strategy_risk_score"] = 0
    try:
        compact["fallback_count"] = max(0, int(payload.get("fallback_count") or 0))
    except Exception:
        compact["fallback_count"] = 0
    fallback_chain = payload.get("fallback_chain")
    if isinstance(fallback_chain, list):
        compact["fallback_chain"] = [str(item or "") for item in fallback_chain[:8]]
    return compact


def _apply_route_dimension_event_to_bucket(
    bucket: dict[str, Any],
    marker: str,
    payload: dict[str, Any],
    event_ts: float,
) -> None:
    bucket["events_total"] = int(bucket.get("events_total") or 0) + 1
    bucket["last_updated"] = max(float(bucket.get("last_updated") or 0.0), float(event_ts or 0.0))
    if marker == "execution_route_decision":
        _increment_counter(bucket.setdefault("route_kind", {}), str(payload.get("route_kind") or "unknown"))
        return
    if marker == "sql_generation_retry":
        reason = str(payload.get("reason") or "unknown")
        _increment_counter(bucket.setdefault("generation_retry_reason", {}), reason)
        if reason == "empty_llm_content":
            bucket["llm_empty_response_retry"] = int(bucket.get("llm_empty_response_retry") or 0) + 1
        return
    if marker == "sql_repair_short_circuit":
        bucket["repair_short_circuit"] = int(bucket.get("repair_short_circuit") or 0) + 1
        reason = str(payload.get("reason") or "unknown")
        _increment_counter(bucket.setdefault("repair_short_circuit_reason", {}), reason)
        return
    if marker == "schema_link_fallback":
        bucket["schema_link_fallback_total"] = int(bucket.get("schema_link_fallback_total") or 0) + 1
        reason = str(payload.get("reason") or "unknown")
        _increment_counter(bucket.setdefault("schema_link_fallback_reason", {}), reason)
        return
    if marker == "sql_generation_fallback":
        bucket["sql_generation_fallback_total"] = int(bucket.get("sql_generation_fallback_total") or 0) + 1
        reason = str(payload.get("reason") or "unknown")
        _increment_counter(bucket.setdefault("sql_generation_fallback_reason", {}), reason)
        return
    if marker == "final_answer_fallback":
        bucket["final_answer_fallback_total"] = int(bucket.get("final_answer_fallback_total") or 0) + 1
        reason = str(payload.get("reason") or "unknown")
        _increment_counter(bucket.setdefault("final_answer_fallback_reason", {}), reason)
        return
    if marker in {"sql_validation_issue", "repair_guard_blocked"}:
        if marker == "repair_guard_blocked":
            bucket["repair_guard_blocked"] = int(bucket.get("repair_guard_blocked") or 0) + 1
        issue_buckets = payload.get("issue_buckets")
        if isinstance(issue_buckets, dict):
            for issue, amount in issue_buckets.items():
                try:
                    normalized_amount = int(amount or 0)
                except Exception:
                    normalized_amount = 0
                _increment_counter_by(
                    bucket.setdefault("validation_issue_bucket", {}),
                    str(issue or "unknown"),
                    normalized_amount,
                )
        return
    if marker != "generation_route_decision":
        return

    generation_engine = str(payload.get("generation_engine") or "unknown")
    strict_json_mode = str(payload.get("strict_json_mode") or "none")
    strategy_selected_engine = str(payload.get("strategy_selected_engine") or generation_engine)
    strategy_mode = str(payload.get("strategy_mode") or "legacy_tier")
    strategy_policy = str(payload.get("strategy_policy") or "tier_default")
    strategy_risk_level = str(payload.get("strategy_risk_level") or "low")
    try:
        strategy_risk_score = max(0, int(payload.get("strategy_risk_score") or 0))
    except Exception:
        strategy_risk_score = 0
    bucket["generation_decision_total"] = int(bucket.get("generation_decision_total") or 0) + 1
    _increment_counter(bucket.setdefault("generation_engine", {}), generation_engine)
    _increment_counter(bucket.setdefault("strategy_selected_engine", {}), strategy_selected_engine)
    _increment_counter(bucket.setdefault("strategy_mode", {}), strategy_mode)
    _increment_counter(bucket.setdefault("strategy_policy", {}), strategy_policy)
    _increment_counter(bucket.setdefault("strategy_risk_level", {}), strategy_risk_level)
    _increment_counter(bucket.setdefault("strict_json_mode", {}), strict_json_mode)
    bucket["strategy_risk_score_total"] = int(bucket.get("strategy_risk_score_total") or 0) + strategy_risk_score
    bucket["strategy_risk_score_max"] = max(int(bucket.get("strategy_risk_score_max") or 0), strategy_risk_score)

    fallback_count = 0
    try:
        fallback_count = max(0, int(payload.get("fallback_count") or 0))
    except Exception:
        fallback_count = 0
    bucket["fallback_count_total"] = int(bucket.get("fallback_count_total") or 0) + fallback_count
    bucket["fallback_count_max"] = max(int(bucket.get("fallback_count_max") or 0), fallback_count)

    fallback_chain = payload.get("fallback_chain")
    has_repair = generation_engine.endswith("_repair")
    if isinstance(fallback_chain, list):
        has_repair = has_repair or any("repair" in str(item or "").lower() for item in fallback_chain)
    if has_repair:
        bucket["repair_used"] = int(bucket.get("repair_used") or 0) + 1


def _increment_counter(bucket: dict[str, int], key: str) -> None:
    _increment_counter_by(bucket, key, 1)


def _increment_counter_by(bucket: dict[str, int], key: str, amount: int) -> None:
    marker = str(key or "unknown")
    increment = max(0, int(amount))
    if increment <= 0:
        return
    bucket[marker] = int(bucket.get(marker) or 0) + increment


def clear_route_dimension_metrics(project_id: Optional[int] = None) -> None:
    target_project = int(project_id) if project_id is not None else None
    with _route_dimension_metrics_lock:
        if project_id is None:
            _route_dimension_metrics_by_project.clear()
            _route_dimension_events_by_project.clear()
            _route_observability_snapshot_state_by_project.clear()
            _route_strategy_trend_points_by_project.clear()
            _route_strategy_trend_snapshot_state_by_project.clear()
        else:
            pid = int(project_id)
            _route_dimension_metrics_by_project.pop(pid, None)
            _route_dimension_events_by_project.pop(pid, None)
            _route_observability_snapshot_state_by_project.pop(pid, None)
            _route_strategy_trend_points_by_project.pop(pid, None)
            _route_strategy_trend_snapshot_state_by_project.pop(pid, None)
    _delete_route_observability_snapshot(target_project)
    _delete_route_strategy_trend_snapshot(target_project)


def get_route_dimension_metrics_snapshot(project_id: int) -> dict[str, Any]:
    pid = int(project_id)
    window_seconds = _route_observability_window_seconds()
    retention_seconds = _route_dimension_retention_seconds(window_seconds)
    now = time.monotonic()
    snapshot: dict[str, Any] | None = None
    with _route_dimension_metrics_lock:
        history = _route_dimension_events_by_project.get(pid)
        if history:
            while history and (now - history[0][0]) > retention_seconds:
                history.popleft()
            if not history:
                _route_dimension_events_by_project.pop(pid, None)
                history = None

        if history:
            window_bucket = _build_window_route_dimension_bucket(
                history,
                window_seconds=window_seconds,
                now=now,
            )
            snapshot = _route_dimension_bucket_snapshot(window_bucket, window_seconds)
        else:
            snapshot = None

    if snapshot is not None:
        return snapshot

    persisted_snapshot = _load_route_observability_snapshot(pid, window_seconds)
    if persisted_snapshot is not None:
        return persisted_snapshot
    return _route_dimension_zero_snapshot(window_seconds)


def get_route_strategy_trend_history(
    project_id: int,
    route_dimensions: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    pid = int(project_id)
    window_seconds = _route_observability_window_seconds()
    retention_seconds = _route_dimension_retention_seconds(window_seconds)
    max_points = _route_observability_strategy_trend_max_points()
    now_unix = time.time()

    history_snapshot: list[dict[str, Any]] = []
    should_load_persisted = False
    with _route_dimension_metrics_lock:
        history = _route_strategy_trend_points_by_project.get(pid)
        if history is not None and history.maxlen != max_points:
            history = _ensure_strategy_trend_history(pid, max_points)
        if history:
            while history and (now_unix - float(history[0].get("captured_at_unix") or 0.0)) > retention_seconds:
                history.popleft()
            if not history:
                _route_strategy_trend_points_by_project.pop(pid, None)
                _route_strategy_trend_snapshot_state_by_project.pop(pid, None)
                history = None
        if history is None:
            should_load_persisted = True
        else:
            history_snapshot = [dict(item) for item in history]

    if should_load_persisted:
        loaded_points = _load_route_strategy_trend_points(pid, retention_seconds, max_points=max_points)
        if loaded_points:
            with _route_dimension_metrics_lock:
                current = _route_strategy_trend_points_by_project.get(pid)
                if current is not None and current.maxlen != max_points:
                    current = _ensure_strategy_trend_history(pid, max_points)
                if current is None:
                    current = deque(maxlen=max_points)
                    for item in loaded_points:
                        current.append(dict(item))
                    _route_strategy_trend_points_by_project[pid] = current
                history_snapshot = [dict(item) for item in current]
        else:
            history_snapshot = []

    snapshot = route_dimensions if isinstance(route_dimensions, dict) else None
    if snapshot is None:
        snapshot = get_route_dimension_metrics_snapshot(pid)
    trend_point = _strategy_trend_point_from_snapshot(snapshot, now_unix)
    if trend_point is None:
        return history_snapshot

    with _route_dimension_metrics_lock:
        history = _ensure_strategy_trend_history(pid, max_points)
        while history and (now_unix - float(history[0].get("captured_at_unix") or 0.0)) > retention_seconds:
            history.popleft()
        _append_strategy_trend_point(history, trend_point, max_points=max_points)
        history_snapshot = [dict(item) for item in history]

    return history_snapshot


def _record_route_dimension_metric(event_type: str, payload: dict[str, Any], project_id: int | None) -> None:
    if project_id is None:
        return
    marker = str(event_type or "unknown_event")
    safe_payload = payload if isinstance(payload, dict) else {}
    compact_payload = _minimize_route_dimension_payload(marker, safe_payload)
    now = time.monotonic()
    wall_now = time.time()
    window_seconds = _route_observability_window_seconds()
    retention_seconds = _route_dimension_retention_seconds(window_seconds)
    max_events = _coerce_int_setting(
        ROUTER_CONFIG.get("route_observability_max_events_per_project"),
        20000,
        1000,
        200000,
    )
    persist_enabled = _route_observability_persist_enabled()
    persist_interval_seconds = _route_observability_persist_interval_seconds()
    persist_event_delta = _route_observability_persist_event_delta()
    trend_max_points = _route_observability_strategy_trend_max_points()
    trend_persist_interval_seconds = _route_observability_strategy_trend_persist_interval_seconds()
    trend_persist_decision_delta = _route_observability_strategy_trend_persist_decision_delta()
    snapshot_to_persist: dict[str, Any] | None = None
    strategy_trend_points_to_persist: list[dict[str, Any]] | None = None
    with _route_dimension_metrics_lock:
        pid = int(project_id)
        bucket = _route_dimension_metrics_by_project.setdefault(pid, _new_route_dimension_bucket())
        _apply_route_dimension_event_to_bucket(bucket, marker, compact_payload, now)

        history = _route_dimension_events_by_project.setdefault(pid, deque())
        history.append((now, marker, compact_payload))
        while history and (now - history[0][0]) > retention_seconds:
            history.popleft()
        while len(history) > max_events:
            history.popleft()

        if persist_enabled:
            persist_state = _route_observability_snapshot_state_by_project.setdefault(
                pid,
                {"last_persisted_ts": 0.0, "last_persisted_events_total": 0.0},
            )
            events_total = int(bucket.get("events_total") or 0)
            last_persisted_ts = float(persist_state.get("last_persisted_ts") or 0.0)
            last_persisted_events_total = int(persist_state.get("last_persisted_events_total") or 0)
            should_persist = (
                (now - last_persisted_ts) >= persist_interval_seconds
                or (events_total - last_persisted_events_total) >= persist_event_delta
            )
            if should_persist:
                window_bucket = _build_window_route_dimension_bucket(
                    history,
                    window_seconds=window_seconds,
                    now=now,
                )
                snapshot_to_persist = _route_dimension_bucket_snapshot(window_bucket, window_seconds)
                persist_state["last_persisted_ts"] = now
                persist_state["last_persisted_events_total"] = float(events_total)

        if marker == "generation_route_decision":
            trend_snapshot = snapshot_to_persist
            if trend_snapshot is None:
                window_bucket = _build_window_route_dimension_bucket(
                    history,
                    window_seconds=window_seconds,
                    now=now,
                )
                trend_snapshot = _route_dimension_bucket_snapshot(window_bucket, window_seconds)
            trend_point = _strategy_trend_point_from_snapshot(trend_snapshot, wall_now)
            if trend_point is not None:
                trend_history = _ensure_strategy_trend_history(pid, trend_max_points)
                history_changed = False
                while trend_history and (wall_now - float(trend_history[0].get("captured_at_unix") or 0.0)) > retention_seconds:
                    trend_history.popleft()
                    history_changed = True
                appended = _append_strategy_trend_point(trend_history, trend_point, max_points=trend_max_points)
                if persist_enabled and (appended or history_changed):
                    trend_state = _route_strategy_trend_snapshot_state_by_project.setdefault(
                        pid,
                        {"last_persisted_ts": 0.0, "last_persisted_decision_total": 0.0},
                    )
                    last_persisted_ts = float(trend_state.get("last_persisted_ts") or 0.0)
                    last_persisted_decision_total = int(trend_state.get("last_persisted_decision_total") or 0)
                    decision_total = int(trend_point.get("decision_total") or 0)
                    should_persist_trend = (
                        history_changed
                        or (now - last_persisted_ts) >= trend_persist_interval_seconds
                        or (decision_total - last_persisted_decision_total) >= trend_persist_decision_delta
                    )
                    if should_persist_trend:
                        strategy_trend_points_to_persist = [dict(item) for item in trend_history]
                        trend_state["last_persisted_ts"] = now
                        trend_state["last_persisted_decision_total"] = float(decision_total)

    if snapshot_to_persist is not None:
        _persist_route_observability_snapshot(int(project_id), snapshot_to_persist, wall_now)
    if persist_enabled and strategy_trend_points_to_persist is not None:
        _persist_route_strategy_trend_points(
            int(project_id),
            strategy_trend_points_to_persist,
            wall_now,
            max_points=trend_max_points,
        )


def clear_execution_metrics(project_id: Optional[int] = None) -> None:
    with _execution_metrics_lock:
        if project_id is None:
            _execution_metrics_by_ds.clear()
            _execution_metrics_by_project_ds.clear()
        else:
            pid = int(project_id)
            keys = [key for key in _execution_metrics_by_project_ds if key[0] == pid]
            for key in keys:
                _execution_metrics_by_project_ds.pop(key, None)
    clear_route_dimension_metrics(project_id)


def get_execution_metrics_snapshot(project_id: Optional[int] = None) -> dict[str, dict[str, Any]]:
    with _execution_metrics_lock:
        snapshot: dict[str, dict[str, Any]] = {}
        if project_id is None:
            for ds_type, data in _execution_metrics_by_ds.items():
                snapshot[ds_type] = _execution_metric_summary(data)
            return snapshot
        pid = int(project_id)
        for (project_key, ds_type), data in _execution_metrics_by_project_ds.items():
            if project_key != pid:
                continue
            snapshot[ds_type] = _execution_metric_summary(data)
        return snapshot


def _record_execution_metric(
    ds_type: str,
    status: str,
    latency_ms: float,
    rows_count: int = 0,
    project_id: Optional[int] = None,
) -> None:
    normalized = str(ds_type or "unknown").lower()
    status_key = str(status or "success").lower()
    if status_key not in {"success", "warning", "error", "timeout"}:
        status_key = "error"
    log_payload = None
    now = time.monotonic()
    with _execution_metrics_lock:
        data = _execution_metrics_by_ds.setdefault(
            normalized,
            _new_execution_metric_bucket(),
        )
        data["total"] = int(data.get("total") or 0) + 1
        data[status_key] = int(data.get(status_key) or 0) + 1
        data["rows_total"] = int(data.get("rows_total") or 0) + max(0, int(rows_count or 0))
        latencies = data.setdefault("latencies_ms", [])
        latencies.append(float(latency_ms or 0.0))
        max_samples = max(50, int(ROUTER_CONFIG.get("execution_metrics_max_samples", 400) or 400))
        if len(latencies) > max_samples:
            del latencies[: len(latencies) - max_samples]
        data["last_updated"] = now

        if project_id is not None:
            pid = int(project_id)
            proj_key = (pid, normalized)
            proj_bucket = _execution_metrics_by_project_ds.setdefault(proj_key, _new_execution_metric_bucket())
            proj_bucket["total"] = int(proj_bucket.get("total") or 0) + 1
            proj_bucket[status_key] = int(proj_bucket.get(status_key) or 0) + 1
            proj_bucket["rows_total"] = int(proj_bucket.get("rows_total") or 0) + max(0, int(rows_count or 0))
            proj_latencies = proj_bucket.setdefault("latencies_ms", [])
            proj_latencies.append(float(latency_ms or 0.0))
            if len(proj_latencies) > max_samples:
                del proj_latencies[: len(proj_latencies) - max_samples]
            proj_bucket["last_updated"] = now

        log_every = max(5, int(ROUTER_CONFIG.get("execution_metrics_log_every", 25) or 25))
        log_interval = max(30.0, float(ROUTER_CONFIG.get("execution_metrics_log_interval_seconds", 180) or 180))
        should_log = (data["total"] % log_every == 0) or (now - float(data.get("last_logged") or 0.0) >= log_interval)
        if should_log:
            log_payload = _execution_metric_summary(data)
            data["last_logged"] = now
    if log_payload:
        LOGGER.info(
            "Datasource execution metrics: ds_type=%s total=%d success=%d warning=%d error=%d timeout=%d avg_ms=%.2f p95_ms=%.2f avg_rows=%.2f",
            normalized,
            log_payload["total"],
            log_payload["success"],
            log_payload["warning"],
            log_payload["error"],
            log_payload["timeout"],
            log_payload["avg_ms"],
            log_payload["p95_ms"],
            log_payload["avg_rows"],
        )

def _analysis_cache_key(question: str, project_id: int, previous_questions: Optional[list[str]] = None) -> str:
    prev_hash = hashlib.sha256(json.dumps(previous_questions or [], sort_keys=True).encode()).hexdigest()[:16] if previous_questions else ""
    return f"{project_id}::{question}::{prev_hash}"

def _purge_expired_cache_entries() -> None:
    now = time.monotonic()
    expired = [k for k, (_, ts) in _analysis_cache.items() if now - ts > _CACHE_TTL_SECONDS]
    for k in expired:
        _analysis_cache.pop(k, None)

def clear_analysis_cache(project_id: Optional[int] = None) -> None:
    with _analysis_cache_lock:
        if project_id is None:
            _analysis_cache.clear()
        else:
            prefix = f"{project_id}::"
            keys = [k for k in _analysis_cache if k.startswith(prefix)]
            for k in keys:
                _analysis_cache.pop(k, None)


def _analysis_item_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value).strip()
    if isinstance(value, dict):
        for key in ("question", "text", "name", "field", "column", "value"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return str(value).strip()
    if isinstance(value, (list, tuple, set)):
        items = [item for item in (_analysis_item_to_text(v) for v in value) if item]
        return ", ".join(items)
    return str(value).strip()


def _normalize_analysis_string_list(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _analysis_item_to_text(item)
        if not text:
            continue
        normalized = text.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def _normalize_analysis_filters(values: Any) -> list[dict[str, Any]]:
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    out: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, dict):
            field = _analysis_item_to_text(item.get("field") or item.get("column") or item.get("name"))
            operator = _analysis_item_to_text(item.get("operator") or item.get("op"))
            value = item.get("value")
            if value is None and "values" in item:
                value = item.get("values")
            if field or operator or value is not None:
                out.append({"field": field, "operator": operator, "value": value})
                continue
            fallback = _analysis_item_to_text(item)
            if fallback:
                out.append({"field": fallback, "operator": "", "value": ""})
            continue
        text = _analysis_item_to_text(item)
        if text:
            out.append({"field": text, "operator": "", "value": ""})
    return out


def _normalize_question_analysis(analysis: Optional[dict[str, Any]]) -> dict[str, Any]:
    source = analysis if isinstance(analysis, dict) else {}
    tier = _analysis_item_to_text(source.get("tier") or "simple").lower()
    if tier not in ("simple", "multi_dimension", "compound"):
        tier = "simple"
    reasoning = _analysis_item_to_text(source.get("reasoning"))
    return {
        "tier": tier,
        "sub_questions": _normalize_analysis_string_list(source.get("sub_questions")),
        "entities": _normalize_analysis_string_list(source.get("entities")),
        "metrics": _normalize_analysis_string_list(source.get("metrics")),
        "dimensions": _normalize_analysis_string_list(source.get("dimensions")),
        "filters": _normalize_analysis_filters(source.get("filters")),
        "reasoning": reasoning or "LLM analyzer skipped or unavailable; using simple fallback.",
    }


def _format_analysis_filters(filters: list[dict[str, Any]]) -> str:
    if not filters:
        return ""
    chunks: list[str] = []
    for item in filters:
        field = _analysis_item_to_text(item.get("field"))
        operator = _analysis_item_to_text(item.get("operator"))
        value_text = _analysis_item_to_text(item.get("value"))
        if field and operator and value_text:
            chunks.append(f"{field} {operator} {value_text}")
        elif field and value_text:
            chunks.append(f"{field} {value_text}")
        elif field:
            chunks.append(field)
        elif value_text:
            chunks.append(value_text)
    return ", ".join(chunks)


def _analysis_with_route_context(
    analysis: Optional[dict[str, Any]],
    route: Optional[dict[str, Any]],
    metadata_part: str,
    non_metadata_part: str,
) -> dict[str, Any]:
    merged = dict(analysis) if isinstance(analysis, dict) else {}
    if metadata_part:
        merged["metadata_question_part"] = metadata_part
    if non_metadata_part:
        merged["non_metadata_question_part"] = non_metadata_part
    if isinstance(route, dict) and isinstance(route.get("clause_routing"), dict):
        merged["clause_routing"] = route.get("clause_routing")
    return merged

def _analyze_question(question: str, project_id: int, previous_questions: Optional[list[str]] = None) -> dict:
    cache_key = _analysis_cache_key(question, project_id, previous_questions)
    with _analysis_cache_lock:
        _purge_expired_cache_entries()
        entry = _analysis_cache.get(cache_key)
        if entry is not None:
            cached_result, ts = entry
            if time.monotonic() - ts <= _CACHE_TTL_SECONDS:
                LOGGER.debug("Question analysis cache hit for: %s", question[:80])
                normalized_cached = _normalize_question_analysis(cached_result)
                if normalized_cached != cached_result:
                    _analysis_cache[cache_key] = (normalized_cached, ts)
                return normalized_cached
        _analysis_cache_computing.add(cache_key)
    meta = _project_meta(project_id) or {}
    default = _normalize_question_analysis({})
    llm = LLMService()
    if not llm.is_configured():
        with _analysis_cache_lock:
            _analysis_cache_computing.discard(cache_key)
        return default
    try:
        messages = [
            {"role": "system", "content": f"{_render_system_prompt()}\n\n{QUESTION_ANALYZER_CONTRACT}"},
            {"role": "user", "content": f"Project: {meta.get('display_name') or meta.get('name') or project_id}\nPrevious questions: {previous_questions or []}\nQuestion: {question}"},
        ]
        result = llm.chat(messages, response_format="json")
        parsed = parse_json_object(result["content"])
        result_dict = _normalize_question_analysis(parsed)
        with _analysis_cache_lock:
            if len(_analysis_cache) >= _analysis_cache_max:
                _analysis_cache.pop(next(iter(_analysis_cache)), None)
            _analysis_cache[cache_key] = (result_dict, time.monotonic())
            _analysis_cache_computing.discard(cache_key)
        return result_dict
    except Exception:
        LOGGER.warning("Question analysis failed; using fallback", exc_info=True)
        with _analysis_cache_lock:
            _analysis_cache_computing.discard(cache_key)
        return default


def _resolve_analysis_to_schema(analysis: dict, models: list[dict[str, Any]]) -> dict:
    resolved: dict[str, list[dict[str, str]]] = {
        "dimensions_resolved": [],
        "metrics_resolved": [],
        "entities_resolved": [],
    }
    if not models:
        return resolved
    all_columns: list[dict[str, Any]] = []
    for model in models:
        mname = model.get("name", "")
        columns = model.get("matched_columns") if model.get("matched_columns") is not None else model.get("columns", [])
        for col in columns:
            cn = (col.get("name") or "").lower()
            dn = (col.get("display_name") or "").lower()
            if cn:
                alias_markers: set[str] = set()
                for alias in _column_alias_strings(col):
                    alias_markers.update(_identifier_markers(alias))
                alias_markers.update(_identifier_markers(cn))
                if dn:
                    alias_markers.update(_identifier_markers(dn))
                all_columns.append(
                    {
                        "column": cn,
                        "display": dn,
                        "model": mname,
                        "aliases": alias_markers,
                    }
                )
    for category, key in [("dimensions_resolved", "dimensions"), ("metrics_resolved", "metrics"), ("entities_resolved", "entities")]:
        for raw_term in (analysis.get(key) or []):
            term = _analysis_item_to_text(raw_term)
            if not term:
                continue
            tl = term.strip().lower()
            tl_norm = tl.replace(" ", "_").replace("-", "_")
            term_markers = _identifier_markers(term)
            if not term_markers and tl:
                term_markers = {tl, tl_norm}
            best_matches: list[dict[str, str]] = []
            for col_info in all_columns:
                cn = col_info["column"]
                dn = col_info["display"]
                mname = col_info["model"]
                alias_markers: set[str] = set(col_info.get("aliases") or set())
                if not alias_markers:
                    alias_markers.update(_identifier_markers(cn))
                    if dn:
                        alias_markers.update(_identifier_markers(dn))

                if tl in alias_markers or tl_norm in alias_markers:
                    best_matches.append({"column": cn, "model": mname, "match_type": "exact"})
                    continue

                if any(marker.endswith("_" + tl_norm) or tl_norm.endswith("_" + marker) for marker in alias_markers if marker):
                    best_matches.append({"column": cn, "model": mname, "match_type": "suffix"})
                    continue

                if any((tl and tl in marker) or (tl_norm and tl_norm in marker) for marker in alias_markers if marker):
                    best_matches.append({"column": cn, "model": mname, "match_type": "substring"})
                    continue

                term_tokens = set(re.split(r"[\s_]+", tl_norm))
                alias_tokens: set[str] = set()
                for marker in alias_markers:
                    alias_tokens.update(token for token in re.split(r"[\s_]+", marker) if token)
                if term_tokens & alias_tokens and len(term_tokens & alias_tokens) >= min(len(term_tokens), 1):
                    best_matches.append({"column": cn, "model": mname, "match_type": "token_overlap"})
                    continue

                if term_markers:
                    marker_overlap = False
                    for term_marker in term_markers:
                        for alias_marker in alias_markers:
                            if not term_marker or not alias_marker:
                                continue
                            if term_marker in alias_marker or alias_marker in term_marker:
                                marker_overlap = True
                                break
                        if marker_overlap:
                            break
                    if marker_overlap:
                        best_matches.append({"column": cn, "model": mname, "match_type": "marker_overlap"})
                        continue

                if _contains_cjk(tl):
                    for ch in tl:
                        if any(ch in marker for marker in alias_markers):
                            best_matches.append({"column": cn, "model": mname, "match_type": "cjk_char"})
                            break
                    continue
            seen = set()
            for m in best_matches:
                key_uniq = f"{m['column']}@{m['model']}"
                if key_uniq not in seen:
                    seen.add(key_uniq)
                    resolved[category].append(m)
    return resolved


def _format_dimension_mapping(analysis: dict, resolved: dict) -> str:
    lines = []
    for label, key, resolved_key in [
        ("Dimension", "dimensions", "dimensions_resolved"),
        ("Metric", "metrics", "metrics_resolved"),
        ("Entity", "entities", "entities_resolved"),
    ]:
        terms = _normalize_analysis_string_list(analysis.get(key))
        mappings = resolved.get(resolved_key) or []
        if not terms:
            continue
        if not mappings:
            lines.append(f"- {label}s: {', '.join(terms)} → no matching columns found")
            continue
        for term in terms:
            term_mappings = []
            term_lower = term.strip().lower()
            for m in mappings:
                m_col_lower = m.get("column", "").lower()
                if m_col_lower == term_lower or term_lower in m_col_lower or m_col_lower in term_lower:
                    term_mappings.append(m)
            if term_mappings:
                mapping_str = ", ".join(f"{m['column']} (model: {m['model']})" for m in term_mappings[:3])
                lines.append(f"  '{term}' → {mapping_str}")
            else:
                lines.append(f"  '{term}' → no matching columns found")
    if not lines:
        return ""
    return "\nQuestion-to-column mapping:\n" + "\n".join(lines)


def _model_rank_map(hit_models: list[dict[str, Any]]) -> dict[str, int]:
    rank: dict[str, int] = {}
    for idx, model in enumerate(hit_models or []):
        model_name = str(model.get("name") or "").lower()
        table_ref = str(model.get("table_reference") or "").lower()
        if model_name and model_name not in rank:
            rank[model_name] = idx
        if table_ref and table_ref not in rank:
            rank[table_ref] = idx
    return rank


def _collect_column_owners(hit_models: list[dict[str, Any]]) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for model in hit_models or []:
        model_name = str(model.get("name") or "").lower()
        table_ref = str(model.get("table_reference") or "").lower()
        owner_name = table_ref or model_name
        columns = model.get("matched_columns") if model.get("matched_columns") is not None else model.get("columns", [])
        for col in columns or []:
            col_name = str(col.get("name") or "").lower()
            if not col_name or not owner_name:
                continue
            bucket = owners.setdefault(col_name, [])
            if owner_name not in bucket:
                bucket.append(owner_name)
    return owners


def _build_schema_linking_plan(
    question: str,
    analysis: Optional[dict[str, Any]],
    semantic_hits: Optional[dict[str, Any]],
    resolved: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    normalized_analysis = _normalize_question_analysis(analysis)
    hit_models = (semantic_hits or {}).get("models") or []
    relations = (semantic_hits or {}).get("relations") or []
    column_owners = _collect_column_owners(hit_models)
    model_rank = _model_rank_map(hit_models)
    preferred_models: set[str] = set()
    for key in ("dimensions_resolved", "metrics_resolved", "entities_resolved"):
        for entry in (resolved or {}).get(key) or []:
            model_name = str(entry.get("model") or "").lower()
            if model_name:
                preferred_models.add(model_name)
    relation_degree: dict[str, int] = {}
    for rel in relations:
        src = str(rel.get("source_model") or "").lower()
        tgt = str(rel.get("target_model") or "").lower()
        if src:
            relation_degree[src] = relation_degree.get(src, 0) + 1
        if tgt:
            relation_degree[tgt] = relation_degree.get(tgt, 0) + 1

    def _owner_sort_key(owner: str) -> tuple[int, int, str]:
        preferred = 0 if owner in preferred_models else 1
        rank = model_rank.get(owner, 10_000)
        degree = -relation_degree.get(owner, 0)
        return (preferred, rank, degree if degree < 0 else 0, owner)

    selected_owner_map: dict[str, str] = {}
    ambiguous_columns: list[dict[str, Any]] = []
    for col_name, owners in column_owners.items():
        unique_owners = list(dict.fromkeys(o for o in owners if o))
        if not unique_owners:
            continue
        if len(unique_owners) == 1:
            selected_owner_map[col_name] = unique_owners[0]
            continue
        chosen = sorted(unique_owners, key=_owner_sort_key)[0]
        selected_owner_map[col_name] = chosen
        ambiguous_columns.append({
            "column": col_name,
            "owners": unique_owners,
            "preferred_owner": chosen,
        })

    return {
        "question": question,
        "analysis_tier": str(normalized_analysis.get("tier") or "simple"),
        "column_owners": column_owners,
        "selected_owner_map": selected_owner_map,
        "ambiguous_columns": ambiguous_columns,
        "model_rank": model_rank,
    }


def _build_sql_planning_artifact(
    question: str,
    analysis: Optional[dict[str, Any]],
    semantic_hits: Optional[dict[str, Any]],
    resolved: Optional[dict[str, Any]],
    schema_link_plan: Optional[dict[str, Any]],
) -> dict[str, Any]:
    a = _normalize_question_analysis(analysis)
    hits = semantic_hits or {}
    rels = hits.get("relations") or []
    selected_owner_map = (schema_link_plan or {}).get("selected_owner_map") or {}
    dims = a.get("dimensions") or []
    metrics = a.get("metrics") or []
    entities = a.get("entities") or []
    filters = a.get("filters") or []

    resolved_dims = [str(e.get("column") or "").lower() for e in (resolved or {}).get("dimensions_resolved") or [] if e.get("column")]
    resolved_metrics = [str(e.get("column") or "").lower() for e in (resolved or {}).get("metrics_resolved") or [] if e.get("column")]
    resolved_entities = [str(e.get("column") or "").lower() for e in (resolved or {}).get("entities_resolved") or [] if e.get("column")]

    fact_models: list[str] = []
    for col in resolved_metrics:
        owner = selected_owner_map.get(col)
        if owner and owner not in fact_models:
            fact_models.append(owner)
    if not fact_models and hits.get("models"):
        first_model = str((hits["models"][0] or {}).get("table_reference") or (hits["models"][0] or {}).get("name") or "").lower()
        if first_model:
            fact_models.append(first_model)

    involved_models: set[str] = set(fact_models)
    for col in resolved_dims + resolved_entities + resolved_metrics:
        owner = selected_owner_map.get(col)
        if owner:
            involved_models.add(owner)

    join_path_hints: list[str] = []
    for rel in rels:
        src = str(rel.get("source_model") or "").lower()
        tgt = str(rel.get("target_model") or "").lower()
        if not src or not tgt:
            continue
        if involved_models and (src not in involved_models and tgt not in involved_models):
            continue
        s_col = str(rel.get("source_column") or "")
        t_col = str(rel.get("target_column") or "")
        join_path_hints.append(f"{src}.{s_col} = {tgt}.{t_col}")

    return {
        "question": question,
        "tier": str(a.get("tier") or "simple"),
        "facts": fact_models,
        "dimensions": dims,
        "metrics": metrics,
        "entities": entities,
        "filters": filters,
        "group_by_columns": list(dict.fromkeys(resolved_dims)),
        "metric_columns": list(dict.fromkeys(resolved_metrics)),
        "entity_columns": list(dict.fromkeys(resolved_entities)),
        "join_path_hints": join_path_hints[:12],
    }


def _format_schema_linking_hint(schema_link_plan: Optional[dict[str, Any]]) -> str:
    plan = schema_link_plan or {}
    ambiguous = plan.get("ambiguous_columns") or []
    if not ambiguous:
        return ""
    lines = ["Schema linking owner preferences:"]
    for item in ambiguous[:12]:
        col = item.get("column")
        owners = ", ".join(item.get("owners") or [])
        preferred = item.get("preferred_owner") or ""
        if col and preferred:
            lines.append(f"- {col}: prefer {preferred} (candidates: {owners})")
    if len(lines) == 1:
        return ""
    return "\n" + "\n".join(lines)


def _format_sql_plan_hint(sql_plan: Optional[dict[str, Any]]) -> str:
    plan = sql_plan or {}
    if not plan:
        return ""
    lines = ["Structured SQL plan:"]
    lines.append(f"- tier: {plan.get('tier') or 'simple'}")
    lines.append(f"- facts: {', '.join(plan.get('facts') or []) or 'none'}")
    lines.append(f"- dimensions: {', '.join(plan.get('dimensions') or []) or 'none'}")
    lines.append(f"- metrics: {', '.join(plan.get('metrics') or []) or 'none'}")
    lines.append(f"- group_by_columns: {', '.join(plan.get('group_by_columns') or []) or 'none'}")
    join_hints = plan.get("join_path_hints") or []
    if join_hints:
        lines.append("- join_hints: " + "; ".join(join_hints[:6]))
    return "\n" + "\n".join(lines)


def _resolved_owner_lock_pairs(
    resolved: Optional[dict[str, Any]],
    schema_link_plan: Optional[dict[str, Any]],
) -> list[tuple[str, str]]:
    selected_owner_map = {
        str(key).strip().lower(): str(value).strip().lower()
        for key, value in ((schema_link_plan or {}).get("selected_owner_map") or {}).items()
        if str(key).strip() and str(value).strip()
    }
    owner_locks: list[tuple[str, str]] = []
    seen_owner_locks: set[tuple[str, str]] = set()
    for key in ("dimensions_resolved", "metrics_resolved", "entities_resolved"):
        for entry in (resolved or {}).get(key) or []:
            col_name = str(entry.get("column") or "").strip().lower()
            if not col_name:
                continue
            owner = selected_owner_map.get(col_name) or str(entry.get("model") or "").strip().lower()
            if not owner:
                continue
            pair = (col_name, owner)
            if pair in seen_owner_locks:
                continue
            seen_owner_locks.add(pair)
            owner_locks.append(pair)
    return owner_locks


def _format_owner_lock_constraints_hint(
    resolved: Optional[dict[str, Any]],
    schema_link_plan: Optional[dict[str, Any]],
) -> str:
    owner_locks = _resolved_owner_lock_pairs(resolved, schema_link_plan)
    if not owner_locks:
        return ""
    lines = [
        "Owner lock constraints:",
        "- Keep each locked column on its owner model/alias in SELECT/JOIN/GROUP BY/HAVING/ORDER BY.",
    ]
    for col_name, owner in owner_locks[:12]:
        lines.append(f"  - {col_name} -> {owner}")
    return "\n" + "\n".join(lines)


def _format_direct_fallback_sql_constraints_hint(
    hit_models: Optional[list[dict[str, Any]]],
    resolved: Optional[dict[str, Any]],
    schema_link_plan: Optional[dict[str, Any]],
) -> str:
    models = hit_models or []
    if not models:
        return ""

    lines = [
        "Direct-generation fallback constraints:",
        "- Use only model.column pairs from the semantic model; never invent columns.",
        "- If a table/model has an alias in FROM/JOIN, all references in SELECT/GROUP BY/ORDER BY/HAVING must use that alias, not the raw model name.",
        "- Do not reference any table/model that is not visible in the current SELECT scope.",
    ]

    for model in models[:6]:
        model_name = str(model.get("table_reference") or model.get("name") or "").strip()
        if not model_name:
            continue
        columns = model.get("matched_columns") if model.get("matched_columns") is not None else model.get("columns", [])
        col_names: list[str] = []
        for col in columns or []:
            col_name = str(col.get("name") or "").strip()
            if col_name and col_name not in col_names:
                col_names.append(col_name)
        if not col_names:
            continue
        lines.append(f"  - {model_name}: {', '.join(col_names[:12])}")

    owner_lock_lines = [f"  - {col_name} -> {owner}" for col_name, owner in _resolved_owner_lock_pairs(resolved, schema_link_plan)]

    if owner_lock_lines:
        lines.append("- Preferred owner locks for resolved terms:")
        lines.extend(owner_lock_lines[:12])

    if len(lines) <= 3:
        return ""
    return "\n" + "\n".join(lines)


def _owner_preferences_from_issues(
    bad_columns: Optional[list[str]],
    hit_models: Optional[list[dict[str, Any]]] = None,
    schema_link_plan: Optional[dict[str, Any]] = None,
) -> dict[str, str]:
    selected_owner_map = {
        str(key).strip().lower(): str(value).strip().lower()
        for key, value in ((schema_link_plan or {}).get("selected_owner_map") or {}).items()
        if str(key).strip() and str(value).strip()
    }
    preferences = dict(selected_owner_map)
    model_rank = _model_rank_map(hit_models or [])

    def _owner_key(owner: str) -> tuple[int, str]:
        return (model_rank.get(owner.lower(), 10_000), owner.lower())

    for issue in bad_columns or []:
        text = str(issue or "")
        if "belongs on:" not in text:
            continue
        left, right = text.split("belongs on:", 1)
        col_ref = left.split("(", 1)[0].strip().lower()
        if not col_ref:
            continue
        col_name = col_ref.split(".")[-1]
        owners = [o.strip().lower() for o in right.strip(" )").split(",") if o.strip()]
        if not owners:
            continue
        unique_owners = list(dict.fromkeys(owners))
        if not unique_owners:
            continue
        preferred_owner = selected_owner_map.get(col_name)
        if preferred_owner and preferred_owner in unique_owners:
            preferences[col_name] = preferred_owner
            continue
        chosen_owner = sorted(unique_owners, key=_owner_key)[0]
        preferences[col_name] = chosen_owner
    return preferences


def _apply_owner_selector_rules(
    sql: str,
    hit_models: list[dict[str, Any]],
    bad_columns: Optional[list[str]] = None,
    schema_link_plan: Optional[dict[str, Any]] = None,
) -> str:
    preferences = _owner_preferences_from_issues(bad_columns, hit_models=hit_models, schema_link_plan=schema_link_plan)
    if not preferences:
        return sql
    return _rehint_columns(sql, hit_models, bad_columns, owner_preferences=preferences)


def _rewrite_outer_scope_columns_to_single_cte(sql: str) -> str:
    if sqlglot is None or exp is None:
        return sql
    normalized_sql = _normalize_sql_text(sql)
    try:
        from sqlglot.optimizer.scope import traverse_scope

        parsed = sqlglot.parse_one(normalized_sql, read="duckdb")
    except Exception:
        return sql
    if not isinstance(parsed, exp.Select):
        return sql

    with_clause = parsed.args.get("with_")
    if not with_clause:
        return sql
    cte_expr_map: dict[str, exp.Expression] = {}
    for cte in with_clause.expressions or []:
        cte_alias = str(cte.alias or "").strip().lower()
        if cte_alias:
            cte_expr_map[cte_alias] = cte.this
    if not cte_expr_map:
        return sql

    try:
        scopes = list(traverse_scope(parsed))
    except Exception:
        return sql
    outer_scope = next((scope for scope in scopes if scope.expression is parsed), None)
    if outer_scope is None:
        return sql

    selected_sources = dict(outer_scope.selected_sources or {})
    if len(selected_sources) != 1:
        return sql
    source_alias = str(next(iter(selected_sources.keys())) or "").strip().lower()
    source_payload = next(iter(selected_sources.values()))
    source_table_expr = source_payload[0] if isinstance(source_payload, tuple) and source_payload else None
    source_table_name = str(getattr(source_table_expr, "name", "") or "").strip().lower()

    cte_name = ""
    if source_alias in cte_expr_map:
        cte_name = source_alias
    elif source_table_name in cte_expr_map:
        cte_name = source_table_name
    if not cte_name:
        return sql

    cte_select = cte_expr_map.get(cte_name)
    projection_names: set[str] = set()
    if isinstance(cte_select, exp.Select):
        for projection in cte_select.expressions or []:
            if isinstance(projection, exp.Star):
                projection_names.add("*")
                break
            if isinstance(projection, exp.Alias):
                alias_name = str(projection.alias or "").strip().lower()
                if alias_name:
                    projection_names.add(alias_name)
                    continue
                projection = projection.this
            if isinstance(projection, exp.Column):
                col_name = str(projection.name or "").strip().lower()
                if col_name:
                    projection_names.add(col_name)
    if not projection_names:
        return sql

    rewrite_target = source_alias or cte_name
    changed = False
    for col in outer_scope.columns:
        table = str(col.table or "").strip().lower()
        col_name = str(col.name or "").strip().lower()
        if not table or not col_name:
            continue
        if table == rewrite_target:
            continue
        if "*" not in projection_names and col_name not in projection_names:
            continue
        col.set("table", rewrite_target)
        changed = True

    if not changed:
        return sql
    try:
        rewritten = parsed.sql(dialect="duckdb")
        sqlglot.parse_one(rewritten, read="duckdb")
        return rewritten
    except Exception:
        return sql


def _apply_group_by_completion_rules(sql: str) -> str:
    try:
        import sqlglot
        from sqlglot import exp

        sql = _rewrite_outer_scope_columns_to_single_cte(sql)
        parsed = sqlglot.parse_one(_normalize_sql_text(sql), read="duckdb")
        if not isinstance(parsed, exp.Select):
            return sql
        has_aggregate = any(isinstance(node, exp.AggFunc) for node in parsed.find_all(exp.AggFunc))
        if not has_aggregate:
            return sql

        required_group_exprs: list[exp.Expression] = []
        for select_expr in parsed.args.get("expressions") or []:
            inner = select_expr.this if isinstance(select_expr, exp.Alias) else select_expr
            if isinstance(inner, exp.Column):
                required_group_exprs.append(inner.copy())

        if not required_group_exprs:
            return sql

        group_expr = parsed.args.get("group")
        if not group_expr:
            group_expr = exp.Group(expressions=[])
            parsed.set("group", group_expr)

        existing_keys = {
            e.sql(dialect="duckdb").strip().lower()
            for e in (group_expr.expressions or [])
        }
        changed = False
        for req in required_group_exprs:
            key = req.sql(dialect="duckdb").strip().lower()
            if key in existing_keys:
                continue
            group_expr.append("expressions", req)
            existing_keys.add(key)
            changed = True

        if not changed:
            return sql
        rewritten = parsed.sql(dialect="duckdb")
        try:
            sqlglot.parse_one(rewritten)
            return rewritten
        except Exception:
            return sql
    except Exception:
        return sql


def _apply_resolved_dimension_group_by_rules(
    sql: str,
    resolved: Optional[dict[str, Any]],
    hit_models: Optional[list[dict[str, Any]]] = None,
) -> str:
    if sqlglot is None or exp is None:
        return sql
    dimensions_resolved = list((resolved or {}).get("dimensions_resolved") or [])
    if not dimensions_resolved:
        return sql
    normalized_sql = _normalize_sql_text(sql)
    try:
        parsed = sqlglot.parse_one(normalized_sql, read="duckdb")
    except Exception:
        return sql
    if not isinstance(parsed, exp.Select):
        return sql
    has_aggregate = any(isinstance(node, exp.AggFunc) for node in parsed.find_all(exp.AggFunc))
    if not has_aggregate:
        return sql

    alias_map = _build_alias_map(normalized_sql)
    model_aliases: dict[str, list[str]] = {}
    for table_expr in parsed.find_all(exp.Table):
        table_name = str(table_expr.name or "").lower()
        alias_name = str(table_expr.alias_or_name or table_expr.name or "").lower()
        if table_name and alias_name:
            model_aliases.setdefault(table_name, [])
            if alias_name not in model_aliases[table_name]:
                model_aliases[table_name].append(alias_name)

    model_columns: dict[str, set[str]] = {}
    for model in hit_models or []:
        name_keys = {
            str(model.get("name") or "").lower(),
            str(model.get("table_reference") or "").lower(),
        }
        cols = {
            str(column.get("name") or "").lower()
            for column in (model.get("matched_columns") if model.get("matched_columns") is not None else model.get("columns") or [])
            if str(column.get("name") or "").strip()
        }
        for key in name_keys:
            if key:
                model_columns[key] = cols

    select_refs: set[tuple[str, str]] = set()
    for select_expr in parsed.args.get("expressions") or []:
        inner = select_expr.this if isinstance(select_expr, exp.Alias) else select_expr
        if not isinstance(inner, exp.Column):
            continue
        col_name = str(inner.name or "").lower()
        if not col_name:
            continue
        table = str(inner.table or "").lower()
        resolved_table = _resolve_table_alias(table, alias_map) if table else ""
        select_refs.add((resolved_table, col_name))
        select_refs.add((table, col_name))
        select_refs.add(("", col_name))

    group_expr = parsed.args.get("group")
    if not group_expr:
        group_expr = exp.Group(expressions=[])
        parsed.set("group", group_expr)
    group_refs: set[tuple[str, str]] = set()
    for group_item in group_expr.expressions or []:
        for group_col in group_item.find_all(exp.Column):
            col_name = str(group_col.name or "").lower()
            if not col_name:
                continue
            table = str(group_col.table or "").lower()
            resolved_table = _resolve_table_alias(table, alias_map) if table else ""
            group_refs.add((resolved_table, col_name))
            group_refs.add((table, col_name))
            group_refs.add(("", col_name))

    def _candidate_aliases(column_name: str, model_name: str) -> list[str]:
        candidates: list[str] = []
        model_markers = _identifier_markers(model_name)
        for table_name, aliases in model_aliases.items():
            table_markers = _identifier_markers(table_name)
            if model_markers and not (model_markers & table_markers):
                continue
            for alias in aliases:
                if alias not in candidates:
                    candidates.append(alias)
        if candidates:
            return candidates
        for table_name, aliases in model_aliases.items():
            model_cols = model_columns.get(table_name, set())
            if column_name.lower() not in model_cols:
                continue
            for alias in aliases:
                if alias not in candidates:
                    candidates.append(alias)
        return candidates

    changed = False
    for entry in dimensions_resolved:
        column_name = str(entry.get("column") or "").strip()
        model_name = str(entry.get("model") or "").strip().lower()
        if not column_name:
            continue
        col_lower = column_name.lower()
        aliases = _candidate_aliases(column_name, model_name)
        alias = aliases[0] if aliases else ""
        resolved_table = _resolve_table_alias(alias, alias_map) if alias else ""
        ref_variants = {(resolved_table, col_lower), (alias, col_lower), ("", col_lower)}
        if not any(ref in select_refs for ref in ref_variants):
            parsed.append("expressions", exp.column(column_name, table=alias or None))
            for ref in ref_variants:
                select_refs.add(ref)
            changed = True
        if not any(ref in group_refs for ref in ref_variants):
            group_expr.append("expressions", exp.column(column_name, table=alias or None))
            for ref in ref_variants:
                group_refs.add(ref)
            changed = True

    if not changed:
        return sql
    try:
        rewritten = parsed.sql(dialect="duckdb")
        sqlglot.parse_one(rewritten, read="duckdb")
        return rewritten
    except Exception:
        return sql


def _enforce_group_by_constraints(
    sql: str,
    dimensions: Optional[list[str]],
    hit_models: Optional[list[dict[str, Any]]] = None,
    resolved: Optional[dict[str, Any]] = None,
) -> tuple[str, list[str]]:
    dim_list = [str(item or "").strip() for item in (dimensions or []) if str(item or "").strip()]
    if not dim_list:
        return sql, []

    candidate = _apply_group_by_completion_rules(sql)
    remaining = _validate_sql_group_by(
        candidate,
        dim_list,
        hit_models=hit_models,
        resolved=resolved,
    )

    if remaining and resolved and resolved.get("dimensions_resolved"):
        resolved_candidate = _apply_resolved_dimension_group_by_rules(
            candidate,
            resolved,
            hit_models=hit_models,
        )
        if resolved_candidate != candidate:
            candidate = _apply_group_by_completion_rules(resolved_candidate)
            remaining = _validate_sql_group_by(
                candidate,
                dim_list,
                hit_models=hit_models,
                resolved=resolved,
            )

    return candidate, remaining


def _select_sql_strategy(analysis: dict, has_knowledge: bool) -> dict:
    strategy = _GENERATION_ROUTER.select_strategy(_normalize_question_analysis(analysis), bool(has_knowledge))
    return {
        "engine": strategy.engine,
        "max_retries": strategy.max_retries,
        "use_examples": strategy.use_examples,
        "mode": strategy.mode,
        "policy": strategy.policy,
        "risk_score": strategy.risk_score,
        "risk_level": strategy.risk_level,
        "signals": dict(strategy.signals or {}),
    }


def _candidate_guard() -> CandidateGuard:
    return CandidateGuard(
        validate_sql_columns=_validate_sql_columns,
        validate_sql_group_by=_validate_sql_group_by,
        validate_sql_aggregation=_validate_sql_aggregation,
        validate_sql_syntax_for_project=_validate_sql_syntax_for_project,
    )


def _execution_pipeline() -> ExecutionPipeline:
    return ExecutionPipeline(
        plan_secured_sql=plan_secured_sql,
        binding_rows=_binding_rows,
        models_by_binding=_models_by_binding,
        models_for_project=_models_for_project,
        normalize_sql_candidate=_normalize_sql_candidate,
        apply_limit=_apply_limit,
        normalize_row_limit=_normalize_execution_row_limit,
        default_sql_rows=MAX_SQL_ROWS,
        execution_router=_EXECUTION_ROUTER,
    )


def _generation_pipeline() -> GenerationPipeline:
    return GenerationPipeline(
        normalize_question_analysis=_normalize_question_analysis,
        semantic_prompt=_semantic_prompt,
        resolve_analysis_to_schema=_resolve_analysis_to_schema,
        prune_schema=_prune_schema,
        reformat_schema_context=_reformat_schema_context,
        build_schema_linking_plan=_build_schema_linking_plan,
        build_sql_planning_artifact=_build_sql_planning_artifact,
        select_sql_strategy=_select_sql_strategy,
        estimate_sql_generation_complexity=_estimate_sql_generation_complexity,
        strict_json_capability=_strict_json_capability,
        prompt_profile_selection=_prompt_profile_selection,
        is_sql_route_v2_enabled=_is_sql_route_v2_enabled,
    )


def _legacy_generation_engine(analysis: dict[str, Any], has_knowledge: bool) -> str:
    return _generation_pipeline().legacy_engine(analysis, has_knowledge)


def _normalize_generation_engine_name(engine: str) -> str:
    return _generation_pipeline().normalize_engine(engine)


def _prune_schema(
    models: list[dict],
    relations: list[dict],
    analysis: dict,
    resolved: Optional[dict] = None,
    max_columns_per_model: int = 15,
    column_mapping: Optional[list[dict]] = None,
) -> tuple[list[dict], list[dict]]:
    normalized_analysis = _normalize_question_analysis(analysis)
    entities = set(item.lower() for item in _normalize_analysis_string_list(normalized_analysis.get("entities")))
    metrics = set(item.lower() for item in _normalize_analysis_string_list(normalized_analysis.get("metrics")))
    dimensions = set(item.lower() for item in _normalize_analysis_string_list(normalized_analysis.get("dimensions")))
    filter_fields = {
        field
        for field in (
            _analysis_item_to_text(item.get("field")).strip().lower()
            for item in _normalize_analysis_filters(normalized_analysis.get("filters"))
        )
        if field
    }
    all_terms = entities | metrics | dimensions | filter_fields
    resolved_columns: dict[str, set[str]] = {}
    if resolved:
        for key in ("dimensions_resolved", "metrics_resolved", "entities_resolved"):
            for entry in (resolved.get(key) or []):
                model_name = entry.get("model", "").lower()
                col_name = entry.get("column", "").lower()
                if model_name and col_name:
                    resolved_columns.setdefault(model_name, set()).add(col_name)
    llm_mapped_columns: dict[str, set[str]] = {}
    if column_mapping:
        for mapping in column_mapping:
            mn = (mapping.get("model_name") or "").lower()
            cn = (mapping.get("column_name") or "").lower()
            confidence = (mapping.get("confidence") or "").lower()
            if mn and cn and confidence in ("high", "medium"):
                llm_mapped_columns.setdefault(mn, set()).add(cn)
    if not all_terms and not resolved_columns and not llm_mapped_columns:
        return models, relations
    term_tokens = set()
    for term in all_terms:
        for t in re.split(r"[\s_]+", term):
            t = t.strip().lower()
            if len(t) >= 2:
                term_tokens.add(t)
    if resolved_columns:
        for model_cols in resolved_columns.values():
            for col_name in model_cols:
                for t in re.split(r"[\s_]+", col_name):
                    t = t.strip().lower()
                    if len(t) >= 2:
                        term_tokens.add(t)
        for model_name, col_names in resolved_columns.items():
            all_terms = all_terms | col_names
    relation_cols = set()
    for rel in relations:
        relation_cols.add((rel["source_model"].lower(), rel["source_column"].lower()))
        relation_cols.add((rel["target_model"].lower(), rel["target_column"].lower()))
    pruned_models = []
    for model in models:
        model_name_lower = model["name"].lower()
        resolved_for_model = resolved_columns.get(model_name_lower, set())
        llm_mapped_for_model = llm_mapped_columns.get(model_name_lower, set())
        model_has_relevant_tokens = any(t in model_name_lower for t in term_tokens) or model_name_lower in all_terms
        kept = []
        type_kept = []
        for col in model.get("columns", []):
            col_name = col.get("name", "")
            col_name_lower = col_name.lower()
            col_display = (col.get("display_name") or "").lower()
            if col.get("is_primary_key"):
                kept.append(col)
                continue
            if (
                col_name_lower in {"id", "to_date", "from_date", "created_at", "updated_at"}
                or col_name_lower.endswith("_id")
                or col_name_lower.endswith("_no")
            ):
                kept.append(col)
                continue
            if (model_name_lower, col_name_lower) in relation_cols:
                kept.append(col)
                continue
            if col_name_lower in resolved_for_model:
                kept.append(col)
                continue
            if col_name_lower in llm_mapped_for_model:
                kept.append(col)
                model_has_relevant_tokens = True
                continue
            col_text = f"{col_name_lower} {col_display}"
            if any(t in col_text for t in term_tokens):
                kept.append(col)
                model_has_relevant_tokens = True
                continue
            col_type = col.get("type", "").upper()
            if metrics and col_type in ("INTEGER", "BIGINT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC") and (model_has_relevant_tokens or bool(kept)):
                type_kept.append(col)
                continue
            if (dimensions or entities) and col_type in ("VARCHAR", "TEXT", "DATE", "TIMESTAMP") and model_has_relevant_tokens:
                type_kept.append(col)
                continue
        remaining = max_columns_per_model - len(kept)
        if remaining > 0 and type_kept:
            kept.extend(type_kept[:remaining])
        if kept:
            m = dict(model)
            m["columns"] = kept
            pruned_models.append(m)
    kept_model_names = {m["name"] for m in pruned_models}
    pruned_relations = [
        rel for rel in relations
        if rel["source_model"] in kept_model_names and rel["target_model"] in kept_model_names
    ]
    return pruned_models, pruned_relations


def _expand_via_relations(
    hit_models: list[dict],
    all_models: list[dict],
    relations: list[dict],
    analysis: Optional[dict] = None,
    max_related_models: int = 3,
) -> tuple[list[dict], list[dict]]:
    expanded = {m["name"] for m in hit_models}
    expanded_models = {m["name"]: m for m in hit_models}
    expanded_relations = []
    relation_pairs: dict[tuple[str, str], dict] = {}
    for rel in relations:
        src = rel.get("source_model", "")
        tgt = rel.get("target_model", "")
        if src and tgt:
            relation_pairs[(src, tgt)] = rel
    question_tokens = set()
    normalized_analysis = _normalize_question_analysis(analysis)
    for key in ("dimensions", "metrics", "entities"):
        for term in _normalize_analysis_string_list(normalized_analysis.get(key)):
            for t in re.split(r"[\s_]+", term.lower()):
                if len(t) >= 2:
                    question_tokens.add(t)
    frontier = set(expanded)
    depth = 0
    while frontier and depth < max_related_models:
        next_frontier = set()
        for model_name in frontier:
            for (src, tgt), rel in relation_pairs.items():
                related_name = None
                if src == model_name and tgt not in expanded:
                    related_name = tgt
                elif tgt == model_name and src not in expanded:
                    related_name = src
                if related_name is None:
                    continue
                related_model = next((m for m in all_models if m["name"] == related_name), None)
                if related_model is None:
                    continue
                model_lower = related_model.get("name", "").lower()
                model_display = (related_model.get("display_name") or "").lower()
                model_desc = (related_model.get("description") or "").lower()
                table_ref = (related_model.get("table_reference") or "").lower()
                has_relevance = any(t in f"{model_lower} {model_display} {model_desc} {table_ref}" for t in question_tokens) if question_tokens else True
                src_col = (rel.get("source_column") or "").lower()
                tgt_col = (rel.get("target_column") or "").lower()
                join_cols = [
                    c for c in related_model.get("columns", [])
                    if c.get("name", "").lower() in (src_col, tgt_col) or c.get("is_primary_key", False)
                ]
                scored_cols = []
                for c in related_model.get("columns", []):
                    cn = (c.get("name") or "").lower()
                    dn = (c.get("display_name") or "").lower()
                    if any(t in f"{cn} {dn}" for t in question_tokens):
                        scored_cols.append(c)
                kept = join_cols[:]
                seen = {c.get("name", "").lower() for c in kept}
                for c in scored_cols:
                    cn = c.get("name", "").lower()
                    if cn not in seen:
                        seen.add(cn)
                        kept.append(c)
                if has_relevance or kept:
                    expanded_models[related_name] = {**related_model, "matched_columns": kept if scored_cols else join_cols}
                    expanded.add(related_name)
                    next_frontier.add(related_name)
                    expanded_relations.append(rel)
        frontier = next_frontier
        depth += 1
    result_models = [expanded_models[name] for name in expanded if name in expanded_models]
    all_model_names = {m["name"] for m in result_models}
    result_relations = [r for r in relations if r.get("source_model") in all_model_names or r.get("target_model") in all_model_names]
    return result_models, result_relations


def _reformat_schema_context(
    models: list[dict],
    relations: list[dict],
    retrieved_tables: list[str],
    question: Optional[str] = None,
    has_hits: bool = True,
) -> str:
    lines = ["Project semantic model:"]
    if question:
        lines.append(
            "Metadata retrieval: "
            + ("using question-matched models, fields, and relations" if has_hits else "no matching project metadata was found")
        )
    for model in models:
        columns = model.get("matched_columns") if model.get("matched_columns") is not None else model["columns"]
        model_desc = f" ({model['description']})" if model.get("description") else ""
        display_name = f" (display: {model.get('display_name')})" if model.get("display_name") else ""
        lines.append(f"- model {model['name']}{display_name}{model_desc}")
        lines.append(f"  table: {model['table_reference']}")
        lines.append(f"  columns ({len(columns)}):")
        for c in columns:
            pk = " [PK]" if c.get("is_primary_key") else ""
            cdesc = f" - {c.get('description')}" if c.get("description") else ""
            cdisplay = f" (display: {c.get('display_name')})" if c.get("display_name") else ""
            lines.append(f"    • {c['name']} {c.get('type', '')}{cdisplay}{cdesc}{pk}")
    if relations:
        lines.append("Relations:")
        for rel in relations:
            rel_desc = f" - {rel['description']}" if rel.get("description") else ""
            lines.append(f"- {rel['source_model']}.{rel['source_column']} -> {rel['target_model']}.{rel['target_column']} ({rel['relation_type']}){rel_desc}")
    if models:
        lines.append("SQL generation rules:")
        lines.append("- A column MUST be prefixed with the alias of the model that OWNS that column in the list above. If 'customer_city' is under model 'customers' aliased as 'c', write 'c.customer_city' — never prefix it with another model's alias.")
        lines.append("- Before writing any column reference, verify that the column appears in the model you are prefixing it with. Do not move columns across aliases.")
        lines.append("- Use the listed relations as join paths when fields from multiple models are needed. Do not invent join conditions.")
        lines.append("- When joining, use the relation type to decide join direction: MANY_TO_ONE typically means the 'source' side has many rows per 'target' row.")
        lines.append("- If a model has a primary key column, use it for counting distinct entities (COUNT(DISTINCT pk)) rather than COUNT(*).")
        lines.append("- When a question asks about multiple dimensions (e.g., products AND cities, categories AND regions), include ALL dimensions in GROUP BY and SELECT. Do not simplify a multi-dimensional question to a single dimension.")
        # Build a column-to-model ownership map for disambiguation
        col_to_models: dict[str, list[str]] = {}
        for model in models:
            model_name_lower = model.get("name", "").lower()
            columns = model.get("matched_columns") if model.get("matched_columns") is not None else model.get("columns", [])
            for c in columns:
                cn = (c.get("name") or "").lower()
                if cn:
                    col_to_models.setdefault(cn, [])
                    if model_name_lower not in col_to_models[cn]:
                        col_to_models[cn].append(model_name_lower)
        shared_cols = {cn: models_list for cn, models_list in col_to_models.items() if len(models_list) > 1}
        if shared_cols:
            lines.append("- AMBIGUOUS COLUMNS (appear in multiple models — you MUST pick the right owner model):")
            for cn in sorted(shared_cols):
                lines.append(f"  • {cn}: appears in {', '.join(shared_cols[cn])} — prefix with the aliased model that should own it in your query")
    return "\n".join(lines)


def _normalize_preview_row_limit(value: Optional[int]) -> int:
    if value is None:
        return DEFAULT_PREVIEW_ROW_LIMIT
    try:
        return max(MIN_PREVIEW_ROW_LIMIT, min(MAX_PREVIEW_ROW_LIMIT, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_PREVIEW_ROW_LIMIT


def normalize_preview_row_limit(value: Optional[int]) -> int:
    refresh_runtime_router_settings(force=False)
    return _normalize_preview_row_limit(value)


def _normalize_execution_row_limit(value: Optional[int]) -> int:
    if value is None:
        return MAX_SQL_ROWS
    try:
        return max(MIN_EXECUTION_ROW_LIMIT, min(MAX_EXECUTION_ROW_LIMIT, int(value)))
    except (TypeError, ValueError):
        return MAX_SQL_ROWS


def ensure_thread(project_id: Optional[int], user_id: int, summary: str, thread_id: Optional[int] = None, preview_row_limit: Optional[int] = None) -> int:
    with connection_lock():
        con = get_connection()
        if thread_id:
            row = con.execute("SELECT id FROM metadata.threads WHERE id = ? AND user_id = ?", [thread_id, user_id]).fetchone()
            if row:
                if preview_row_limit is not None:
                    con.execute(
                        "UPDATE metadata.threads SET preview_row_limit = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
                        [_normalize_preview_row_limit(preview_row_limit), thread_id, user_id],
                    )
                return int(thread_id)
            raise ValueError("Thread not found or no longer accessible.")
        if not project_id:
            raise ValueError("No active project. Use temporary ask for empty-project chat.")
        resolved_project_id = int(project_id)
        row = con.execute("SELECT id FROM metadata.projects WHERE id = ?", [resolved_project_id]).fetchone()
        if not row:
            raise ValueError("Project not found")
        max_id = _max_id(con, "metadata.threads")
        con.execute(
            "INSERT INTO metadata.threads (id, project_id, summary, user_id, preview_row_limit) VALUES (?, ?, ?, ?, ?)",
            [max_id, resolved_project_id, summary[:128], user_id, _normalize_preview_row_limit(preview_row_limit)],
        )
        return int(max_id)


def _auto_thread_title(question: str) -> str:
    text = re.sub(r"\s+", " ", (question or "").strip())
    text = re.sub(r"[?？。.!！]+$", "", text).strip()
    if not text:
        return "新会话"
    return text[:24] if len(text) <= 24 else f"{text[:24]}..."


def update_auto_thread_summary(thread_id: int, user_id: int, question: str) -> None:
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT summary, summary_manual FROM metadata.threads WHERE id = ? AND user_id = ?",
            [thread_id, user_id],
        ).fetchone()
        if not row or row[1]:
            return
        con.execute(
            "UPDATE metadata.threads SET summary = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ? AND COALESCE(summary_manual, false) = false",
            [_auto_thread_title(question), thread_id, user_id],
        )


def get_thread_preview_row_limit(thread_id: int, user_id: int) -> int:
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT preview_row_limit FROM metadata.threads WHERE id = ? AND user_id = ?",
            [thread_id, user_id],
        ).fetchone()
        return _normalize_preview_row_limit(row[0] if row else None)


def list_thread_responses(thread_id: int, user_id: int) -> list[dict[str, Any]]:
    with connection_lock():
        con = get_connection()
        thread = con.execute("SELECT id FROM metadata.threads WHERE id = ? AND user_id = ?", [thread_id, user_id]).fetchone()
        if not thread:
            return []
        rows = con.execute(
            "SELECT id, thread_id, user_id, question, sql, asking_task_id, breakdown_detail, answer_detail, chart_detail, adjustment, created_at FROM metadata.thread_responses WHERE thread_id = ? ORDER BY created_at ASC",
            [thread_id],
        ).fetchall()
        return [_response_row(row) for row in rows]


def get_thread_project_id(thread_id: int, user_id: Optional[int] = None) -> Optional[int]:
    with connection_lock():
        con = get_connection()
        if user_id is not None:
            row = con.execute("SELECT project_id FROM metadata.threads WHERE id = ? AND user_id = ?", [thread_id, user_id]).fetchone()
        else:
            row = con.execute("SELECT project_id FROM metadata.threads WHERE id = ?", [thread_id]).fetchone()
        return int(row[0]) if row and row[0] is not None else None


def create_thread_response(
    thread_id: int,
    user_id: int,
    question: str,
    sql: Optional[str],
    asking_task: dict[str, Any],
    answer_detail: dict[str, Any],
    breakdown_detail: Optional[dict[str, Any]] = None,
    chart_detail: Optional[dict[str, Any]] = None,
    adjustment: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    with connection_lock():
        con = get_connection()
        max_id = _max_id(con, "metadata.thread_responses")
        asking_task_id = asking_task.get("traceId") or f"ask-{max_id}"
        con.execute(
            """
            INSERT INTO metadata.thread_responses
              (id, thread_id, user_id, question, sql, asking_task_id, breakdown_detail, answer_detail, chart_detail, adjustment)
            VALUES (?, ?, ?, ?, ?, ?, ?::JSON, ?::JSON, ?::JSON, ?::JSON)
            """,
            [
                max_id,
                thread_id,
                user_id,
                question,
                sql,
                asking_task_id,
                _json_dumps(breakdown_detail),
                _json_dumps(answer_detail),
                _json_dumps(chart_detail),
                _json_dumps(adjustment),
            ],
        )
        con.execute("UPDATE metadata.threads SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", [thread_id])
        row = con.execute(
            "SELECT id, thread_id, user_id, question, sql, asking_task_id, breakdown_detail, answer_detail, chart_detail, adjustment, created_at FROM metadata.thread_responses WHERE id = ?",
            [max_id],
        ).fetchone()
        item = _response_row(row)
        item["askingTask"] = asking_task
        return item


def temporary_thread_response(
    thread_id: int,
    user_id: int,
    question: str,
    sql: Optional[str],
    asking_task: dict[str, Any],
    answer_detail: dict[str, Any],
    breakdown_detail: Optional[dict[str, Any]] = None,
    chart_detail: Optional[dict[str, Any]] = None,
    adjustment: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    asking_task_id = asking_task.get("traceId") or f"temp-{thread_id}"
    return {
        "id": int(time.time() * 1000),
        "thread_id": thread_id,
        "threadId": thread_id,
        "user_id": user_id,
        "question": question,
        "sql": sql,
        "asking_task_id": asking_task_id,
        "askingTask": asking_task,
        "breakdown_detail": breakdown_detail,
        "breakdownDetail": breakdown_detail,
        "answer_detail": answer_detail,
        "answerDetail": answer_detail,
        "chart_detail": chart_detail,
        "chartDetail": chart_detail,
        "adjustment": adjustment,
        "view": None,
        "created_at": now,
    }


def _response_row(row) -> dict[str, Any]:
    breakdown_detail = _safe_json_loads(row[6], None)
    answer_detail = _safe_json_loads(row[7], None)
    chart_detail = _safe_json_loads(row[8], None)
    adjustment = _safe_json_loads(row[9], None)
    asking_task = _build_stored_asking_task(row[5], row[4], answer_detail)
    if isinstance(breakdown_detail, dict) and breakdown_detail.get("processSteps"):
        asking_task["processSteps"] = breakdown_detail.get("processSteps")
    return {
        "id": row[0],
        "thread_id": row[1],
        "threadId": row[1],
        "user_id": row[2],
        "question": row[3],
        "sql": row[4],
        "asking_task_id": row[5],
        "askingTask": asking_task,
        "breakdown_detail": breakdown_detail,
        "breakdownDetail": breakdown_detail,
        "answer_detail": answer_detail,
        "answerDetail": answer_detail,
        "chart_detail": chart_detail,
        "chartDetail": chart_detail,
        "adjustment": adjustment,
        "view": None,
        "created_at": str(row[10]) if row[10] else None,
    }


def _build_stored_asking_task(task_id: Optional[str], sql: Optional[str], answer_detail: Optional[dict[str, Any]]) -> dict[str, Any]:
    status = "FINISHED" if not answer_detail or not answer_detail.get("error") else "FAILED"
    return {
        "type": "GENERAL" if not sql else "NL2SQL",
        "status": status,
        "traceId": task_id,
        "queryId": answer_detail.get("queryId") if isinstance(answer_detail, dict) else None,
        "invalidSql": None,
        "candidates": [],
        "retrievedTables": [],
        "rephrasedQuestion": None,
        "intentReasoning": None,
        "sqlGenerationReasoning": None,
        "error": answer_detail.get("error") if isinstance(answer_detail, dict) else None,
        "processSteps": answer_detail.get("processSteps", []) if isinstance(answer_detail, dict) else [],
    }


def _models_for_project(project_id: int) -> list[dict[str, Any]]:
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            "SELECT id, name, display_name, description, table_reference, source_binding_id, column_defs FROM metadata.models WHERE project_id = ? ORDER BY id",
            [project_id],
        ).fetchall()
        models = []
        for row in rows:
            columns = _safe_json_loads(row[6], [])
            model_columns: list[dict[str, Any]] = []
            for col in columns:
                if not isinstance(col, dict) or not col.get("name"):
                    continue
                display_name = col.get("display_name") or col.get("displayName")
                if not display_name:
                    fallback_aliases = _fallback_column_aliases(col.get("name"))
                    if fallback_aliases:
                        display_name = fallback_aliases[0]
                normalized_col = {
                    "name": col.get("name"),
                    "type": col.get("type") or "UNKNOWN",
                    "display_name": display_name,
                    "description": col.get("description") or "",
                    "is_primary_key": bool(col.get("is_primary_key") or col.get("primaryKey") or col.get("isPrimaryKey")),
                }
                normalized_col["aliases"] = _column_alias_strings(normalized_col)
                model_columns.append(normalized_col)
            models.append({
                "id": row[0],
                "name": row[1],
                "display_name": row[2],
                "description": row[3] or "",
                "table_reference": row[4] or row[1],
                "source_binding_id": row[5],
                "_type": "model",
                "aliases": _collect_aliases(row[1], row[2], row[4]),
                "columns": model_columns,
            })
        view_rows = con.execute(
            "SELECT id, name, display_name, description, model_id, column_defs, sql FROM metadata.views WHERE project_id = ? ORDER BY id",
            [project_id],
        ).fetchall()
        for row in view_rows:
            columns = _safe_json_loads(row[5], [])
            view_columns: list[dict[str, Any]] = []
            for col in columns:
                if not isinstance(col, dict) or not col.get("name"):
                    continue
                display_name = col.get("display_name") or col.get("displayName")
                if not display_name:
                    fallback_aliases = _fallback_column_aliases(col.get("name"))
                    if fallback_aliases:
                        display_name = fallback_aliases[0]
                normalized_col = {
                    "name": col.get("name"),
                    "type": col.get("type") or col.get("result_type") or "UNKNOWN",
                    "display_name": display_name,
                    "description": col.get("description") or "",
                    "is_primary_key": False,
                }
                normalized_col["aliases"] = _column_alias_strings(normalized_col)
                view_columns.append(normalized_col)
            models.append({
                "id": row[0],
                "name": row[1],
                "display_name": row[2],
                "description": row[3] or "",
                "table_reference": row[1],
                "source_binding_id": None,
                "statement": row[6],
                "_type": "view",
                "aliases": _collect_aliases(row[1], row[2]),
                "columns": view_columns,
            })
        cf_rows = con.execute(
            "SELECT id, name, display_name, description, model_id, expression, result_type FROM metadata.calculated_fields WHERE project_id = ? ORDER BY id",
            [project_id],
        ).fetchall()
        for row in cf_rows:
            models.append({
                "id": row[0],
                "name": row[1],
                "display_name": row[2],
                "description": row[3] or "",
                "table_reference": row[1],
                "source_binding_id": None,
                "expression": row[5],
                "result_type": row[6] or "UNKNOWN",
                "_type": "calculated_field",
                "aliases": _collect_aliases(row[1], row[2], row[5]),
                "columns": [],
            })
        return models


def _relations_for_project(project_id: int) -> list[dict[str, Any]]:
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            """
            SELECT r.name, r.description, sm.name, r.source_column, tm.name, r.target_column, r.relation_type
            FROM metadata.relations r
            JOIN metadata.models sm ON sm.id = r.source_model_id
            JOIN metadata.models tm ON tm.id = r.target_model_id
            WHERE r.project_id = ?
            ORDER BY r.id
            """,
            [project_id],
        ).fetchall()
        return [
            {
                "name": row[0],
                "description": row[1] or "",
                "source_model": row[2],
                "source_column": row[3],
                "target_model": row[4],
                "target_column": row[5],
                "relation_type": row[6],
            }
            for row in rows
        ]


def _tokenize(text: str) -> set[str]:
    raw_tokens = _tokenize_cached(str(text or ""))
    expanded: set[str] = set(raw_tokens)
    for token in raw_tokens:
        if _contains_cjk(token):
            for i in range(len(token)):
                expanded.add(token[i])
            for i in range(len(token) - 1):
                expanded.add(token[i:i+2])
    return expanded


def _join_clauses(clauses: list[str]) -> str:
    normalized = [str(item or "").strip() for item in clauses if str(item or "").strip()]
    if not normalized:
        return ""
    if len(normalized) == 1:
        return normalized[0]
    return "； ".join(normalized)


def _split_question_clauses(question: str) -> list[str]:
    text = str(question or "").strip()
    if not text:
        return []
    compact = re.sub(r"\s+", " ", text)
    raw_parts = _CLAUSE_SPLIT_RE.split(compact)
    clauses: list[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        cleaned = str(part or "").strip(" ,，、;；。！？!?")
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        clauses.append(cleaned)
    if clauses:
        return clauses
    return [compact] if compact else []


def _clause_has_data_intent(clause: str, analysis: Optional[dict[str, Any]] = None) -> bool:
    text = str(clause or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if any(indicator in lowered for indicator in _DATA_ROUTE_INDICATORS):
        return True
    normalized_analysis = _normalize_question_analysis(analysis)
    for term in (
        (normalized_analysis.get("metrics") or [])
        + (normalized_analysis.get("dimensions") or [])
        + (normalized_analysis.get("entities") or [])
    ):
        marker = str(term or "").strip().lower()
        if marker and marker in lowered:
            return True
    for item in normalized_analysis.get("filters") or []:
        field = str(item.get("field") or "").strip().lower()
        value = str(item.get("value") or "").strip().lower()
        if field and field in lowered:
            return True
        if value and len(value) >= 2 and value in lowered:
            return True
    return False


def _classify_clause_routing(
    question: str,
    *,
    models: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    analysis: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    clauses = _split_question_clauses(question)
    if not clauses and str(question or "").strip():
        clauses = [str(question or "").strip()]
    metadata_clauses: list[str] = []
    non_metadata_clauses: list[str] = []
    routed: list[dict[str, Any]] = []
    for clause in clauses:
        clause_hits = _semantic_hits(clause, models, relations)
        has_hits = bool(clause_hits.get("has_hits"))
        data_intent = _clause_has_data_intent(clause, analysis)
        route = "sql" if has_hits else "general"
        if route == "sql":
            metadata_clauses.append(clause)
        else:
            non_metadata_clauses.append(clause)
        routed.append(
            {
                "text": clause,
                "route": route,
                "semantic_hit": has_hits,
                "semantic_models": len(clause_hits.get("models") or []),
                "semantic_relations": len(clause_hits.get("relations") or []),
                "semantic_score": float(clause_hits.get("score") or 0),
                "data_intent": data_intent,
            }
        )
    metadata_part = _join_clauses(metadata_clauses)
    non_metadata_part = _join_clauses(non_metadata_clauses)
    return {
        "clauses": routed,
        "metadata_clauses": metadata_clauses,
        "non_metadata_clauses": non_metadata_clauses,
        "metadata_question_part": metadata_part,
        "non_metadata_question_part": non_metadata_part,
        "metadata_clause_count": len(metadata_clauses),
        "non_metadata_clause_count": len(non_metadata_clauses),
        "mixed": bool(metadata_clauses and non_metadata_clauses),
    }


def _format_clause_routing_for_prompt(clause_routing: Optional[dict[str, Any]]) -> str:
    if not isinstance(clause_routing, dict):
        return ""
    clauses = clause_routing.get("clauses") or []
    if not isinstance(clauses, list) or not clauses:
        return ""
    lines: list[str] = []
    for index, item in enumerate(clauses, start=1):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        route = "SQL" if str(item.get("route") or "").lower() == "sql" else "GENERAL"
        lines.append(
            f"- Clause {index} [{route}] (semantic_hit={bool(item.get('semantic_hit'))}, data_intent={bool(item.get('data_intent'))}): {text}"
        )
    metadata_part = str(clause_routing.get("metadata_question_part") or "").strip()
    non_metadata_part = str(clause_routing.get("non_metadata_question_part") or "").strip()
    if metadata_part:
        lines.append(f"SQL-focused clause text: {metadata_part}")
    if non_metadata_part:
        lines.append(f"Non-SQL clause text: {non_metadata_part}")
    return "\n".join(lines)


def _event_clause_routing_summary(clause_routing: Optional[dict[str, Any]]) -> dict[str, Any]:
    payload = clause_routing if isinstance(clause_routing, dict) else {}
    clauses = payload.get("clauses") if isinstance(payload.get("clauses"), list) else []
    summarized_clauses: list[dict[str, Any]] = []
    for item in clauses[:8]:
        if not isinstance(item, dict):
            continue
        summarized_clauses.append(
            {
                "text": str(item.get("text") or "").strip()[:240],
                "route": str(item.get("route") or "").strip().lower() or "general",
                "semantic_hit": bool(item.get("semantic_hit")),
                "data_intent": bool(item.get("data_intent")),
                "semantic_models": int(item.get("semantic_models") or 0),
            }
        )
    return {
        "total_clauses": len(clauses),
        "metadata_clause_count": int(payload.get("metadata_clause_count") or 0),
        "non_metadata_clause_count": int(payload.get("non_metadata_clause_count") or 0),
        "mixed": bool(payload.get("mixed")),
        "clauses": summarized_clauses,
    }


def _score_metadata_text(question_tokens: set[str], *parts: Any) -> int:
    score = 0
    for index, part in enumerate(parts):
        tokens = _tokenize(str(part or ""))
        if not tokens:
            continue
        overlap = len(question_tokens & tokens)
        weight = 4 if index == 0 else 2 if index == 1 else 1
        score += overlap * weight
    return score


def _metadata_question_score(question: str, question_tokens: set[str], models: list[dict[str, Any]], relations: list[dict[str, Any]]) -> int:
    if not question_tokens:
        return 0
    score = 0
    for model in models:
        model_score = _score_metadata_text(
            question_tokens,
            model.get("name"),
            model.get("display_name"),
            model.get("description"),
            model.get("table_reference"),
        )
        for column in model.get("columns", []):
            model_score += _score_metadata_text(
                question_tokens,
                column.get("name"),
                column.get("display_name"),
                column.get("description"),
                column.get("type"),
            )
        score += model_score
    return score


def _semantic_hits(question: str, models: list[dict[str, Any]], relations: list[dict[str, Any]]) -> dict[str, Any]:
    question_tokens = _tokenize(question)
    if not question_tokens:
        return {"models": [], "relations": [], "fallback": False, "has_hits": False}
    question_lower = question.lower().strip()
    relation_columns: dict[str, set[str]] = {}
    for rel in relations:
        source_model = rel.get("source_model", "")
        target_model = rel.get("target_model", "")
        source_col = rel.get("source_column", "")
        target_col = rel.get("target_column", "")
        if source_model and source_col:
            relation_columns.setdefault(source_model, set()).add(source_col.lower())
        if target_model and target_col:
            relation_columns.setdefault(target_model, set()).add(target_col.lower())
    name_to_aliases: dict[str, list[str]] = {}
    for model in models:
        mn = (model.get("name") or "").lower()
        mdn = (model.get("display_name") or "").lower()
        entries = [mn]
        if mdn:
            entries.append(mdn)
        for alias in (model.get("aliases") or []):
            a = (alias or "").strip().lower()
            if a and a not in entries:
                entries.append(a)
        name_to_aliases[mn] = entries
        for column in model.get("columns", []):
            cn = (column.get("name") or "").lower()
            cdn = (column.get("display_name") or "").lower()
            col_entries = [cn]
            if cdn:
                col_entries.append(cdn)
            for alias in (column.get("aliases") or []):
                a = (alias or "").strip().lower()
                if a and a not in col_entries:
                    col_entries.append(a)
            name_to_aliases[f"{mn}.{cn}"] = col_entries

    def _exact_match(tokens: set[str], text_lower: str) -> bool:
        for t in tokens:
            if t in text_lower:
                return True
        return False

    def _phase_score_model(model: dict) -> tuple[float, bool]:
        model_name = model.get("name", "")
        model_lower = model_name.lower()
        model_display = (model.get("display_name") or "").lower()
        model_desc = (model.get("description") or "").lower()
        model_table = (model.get("table_reference") or "").lower()
        text_blob = f"{model_lower} {model_display} {model_desc} {model_table}"
        aliases = name_to_aliases.get(model_lower, [model_lower])
        has_exact = any(a in question_lower for a in aliases)
        if has_exact:
            return (5.0, True)
        score = _score_metadata_text(question_tokens, model.get("name"), model.get("display_name"), model.get("description"), model.get("table_reference"))
        return (score, False)

    def _phase_score_column(column: dict, model_name: str) -> tuple[float, bool, bool]:
        col_name = (column.get("name") or "").lower()
        is_pk = column.get("is_primary_key", False)
        is_relation_col = col_name in relation_columns.get(model_name, set())
        col_key = f"{model_name.lower()}.{col_name}"
        aliases = name_to_aliases.get(col_key, [col_name])
        has_exact = any(a in question_lower for a in aliases)
        if has_exact:
            return (3.0, is_pk or is_relation_col, True)
        score = _score_metadata_text(question_tokens, column.get("name"), column.get("display_name"), column.get("description"), column.get("type"))
        return (score, is_pk or is_relation_col, False)

    EXACT_COL_MIN = 1
    APPROX_MODEL_MIN = 2.0

    phase1_models = []
    for model in models:
        model_score, model_exact = _phase_score_model(model)
        model_name = model.get("name", "")
        essential_columns = []
        exact_columns = []
        approx_columns = []
        column_score_total = 0.0
        for column in model.get("columns", []):
            col_score, is_essential, col_exact = _phase_score_column(column, model_name)
            if is_essential:
                essential_columns.append(column)
            elif col_exact:
                exact_columns.append(column)
                column_score_total += col_score
            elif col_score > 0:
                approx_columns.append((col_score, column))
                column_score_total += col_score
        has_exact_columns = bool(exact_columns)
        combined_score = model_score + column_score_total
        model_qualifies = model_exact or has_exact_columns
        if model_qualifies and (model_exact or len(exact_columns) >= EXACT_COL_MIN):
            all_columns = essential_columns + exact_columns
            seen = {c.get("name", "").lower() for c in all_columns}
            approx_columns.sort(key=lambda item: item[0], reverse=True)
            max_approx = 20 if len(question_tokens) > 5 else 12
            for _, col in approx_columns[:max_approx]:
                if col.get("name", "").lower() not in seen:
                    seen.add(col.get("name", "").lower())
                    all_columns.append(col)
            phase1_models.append((combined_score, {**model, "matched_columns": all_columns}))
        elif combined_score >= APPROX_MODEL_MIN:
            all_columns = essential_columns[:]
            seen = {c.get("name", "").lower() for c in all_columns}
            approx_columns.sort(key=lambda item: item[0], reverse=True)
            for _, col in approx_columns[:12]:
                if col.get("name", "").lower() not in seen:
                    seen.add(col.get("name", "").lower())
                    all_columns.append(col)
            if not all_columns and combined_score < APPROX_MODEL_MIN * 2:
                continue
            phase1_models.append((combined_score, {**model, "matched_columns": all_columns, "approximate_match": True}))

    phase1_models.sort(key=lambda item: item[0], reverse=True)
    hit_models = [item[1] for item in phase1_models[:8]]
    hit_model_names = {m["name"] for m in hit_models}
    hit_relations = [r for r in relations if r.get("source_model") in hit_model_names or r.get("target_model") in hit_model_names]
    if not hit_models:
        return {"models": [], "relations": [], "fallback": False, "has_hits": False}
    total_score = sum(s for s, _ in phase1_models[:8])
    result = {"models": hit_models, "relations": hit_relations, "fallback": False, "has_hits": True, "score": total_score}
    has_approx = any(m.get("approximate_match") for m in hit_models)
    if has_approx:
        result["broad_match"] = True
    return result


_LLM_MATCHING_CONTRACT = """You are a metadata matching assistant. Given a user question and a project's full metadata catalog, identify which models, views, calculated fields, and columns are relevant to answering the question.

CRITICAL: You MUST return only valid JSON (no markdown, no code fences, no extra text). The JSON must parse with json.loads().

Output JSON with these fields:
- "matched_models": list of objects with "name" (the exact model/view/calculated_field name from the catalog), "matched_columns" (list of exact column names from the catalog, or empty list), "relevance" (short reason)
- "reasoning": brief explanation of matches

Rules:
- Map user terms (in any language) to the exact metadata names. For example, "订单" or "orders" should match a model named "orders" or "order".
- A column with name "customer_city" and display_name "客户城市" should match both "city" and "城市".
- If the user asks about a broad category (like "sales", "销售"), match models/tables that logically contain sales data even if the exact word doesn't appear in the metadata name.
- Include a model/view even if it only partially matches — prefer false positives over false negatives.
- For calculated fields, match if the expression or display name aligns with the question.
- Use "matched_columns" to indicate which specific column names are relevant. Only include column names that EXACTLY match the catalog (case-insensitive). If the question is broad (like "show me all orders"), include all columns. For specific questions (like "total revenue by city"), only include relevant columns.
- ALWAYS include primary key columns (marked with [PK]) in matched_columns — they are needed for COUNT(DISTINCT) and joins.
- ALWAYS include columns that appear in the RELATIONS section — they are needed for JOIN operations.
- When the question implies aggregation (count, sum, average, total, 统计, 总计, 平均), include numeric columns that could be aggregated and grouping columns.
- When the question implies ranking or top-N (top, best, worst, 最高, 最低, 排名), include the metric column and a LIMIT-compatible ordering column.
- If no model or column is relevant, return {"matched_models": [], "reasoning": "No relevant matches"}."""


def _metadata_catalog_text(meta: Optional[dict[str, Any]], models: list[dict[str, Any]], relations: list[dict[str, Any]]) -> str:
    lines = []
    if meta:
        lines.append(f"Project: {meta.get('display_name') or meta.get('name')}")
        if meta.get("description"):
            lines.append(f"Project description: {meta['description']}")
    lines.append(f"\nTotal models/views: {len(models)}, total relations: {len(relations)}")
    lines.append("\n=== MODELS ===")
    for model in models:
        model_type = model.get("_type", "model")
        desc = f" ({model.get('description')})" if model.get("description") else ""
        dname = f" (display: {model.get('display_name')})" if model.get("display_name") else ""
        lines.append(f"\n[{model_type}] {model['name']}{dname}{desc}")
        if model.get("_type") == "calculated_field":
            lines.append(f"  expression: {model.get('expression', '')}")
            lines.append(f"  result_type: {model.get('result_type', '')}")
        else:
            lines.append(f"  table: {model.get('table_reference', '')}")
            for col in model.get("columns", []):
                cdesc = f" - {col.get('description')}" if col.get("description") else ""
                cdisplay = f" (display: {col.get('display_name')})" if col.get("display_name") else ""
                pk = " [PK]" if col.get("is_primary_key") else ""
                lines.append(f"  - {col['name']} {col.get('type', '')}{cdisplay}{cdesc}{pk}")
    if relations:
        lines.append("\n=== RELATIONS ===")
        for rel in relations:
            rdesc = f" - {rel.get('description')}" if rel.get("description") else ""
            lines.append(f"  {rel['source_model']}.{rel['source_column']} -> {rel['target_model']}.{rel['target_column']} ({rel['relation_type']}){rdesc}")
    return "\n".join(lines)


def _llm_semantic_matching(question: str, project_id: int, *, models: Optional[list[dict]] = None, relations: Optional[list[dict]] = None, llm: Optional[LLMService] = None) -> Optional[dict[str, Any]]:
    if llm is None:
        llm = LLMService()
    if not llm.is_configured():
        return None
    if models is None:
        models = _models_for_project(project_id)
    if relations is None:
        relations = _relations_for_project(project_id)
    meta = _project_meta(project_id)
    catalog = _metadata_catalog_text(meta, models, relations)
    messages = [
        {"role": "system", "content": _LLM_MATCHING_CONTRACT},
        {"role": "user", "content": f"Metadata catalog:\n{catalog}"},
        {"role": "user", "content": f"User question: {question}"},
    ]
    try:
        result = llm.chat(messages, response_format="json")
        content = _llm_content_text(result)
        if not content.strip():
            LOGGER.warning("LLM semantic matching returned empty content; using token-based semantic matching")
            return None
        parsed = parse_json_object(content)
    except Exception:
        LOGGER.exception("LLM semantic matching failed")
        return None

    def _normalized_llm_matches(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        raw_matches = payload.get("matched_models")
        if not isinstance(raw_matches, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in raw_matches:
            if isinstance(item, dict):
                name = _analysis_item_to_text(item.get("name"))
                if not name:
                    continue
                normalized.append({
                    "name": name,
                    "matched_columns": _normalize_analysis_string_list(item.get("matched_columns")),
                })
                continue
            name = _analysis_item_to_text(item)
            if name:
                normalized.append({"name": name, "matched_columns": []})
        return normalized

    normalized_matches = _normalized_llm_matches(parsed)
    matched_names = {m.get("name") for m in normalized_matches if m.get("name")}
    if not matched_names:
        return None
    hit_models = []
    for model in models:
        if model["name"] in matched_names:
            llm_match = next((m for m in normalized_matches if m.get("name") == model["name"]), {})
            matched_col_names = {c.lower() for c in llm_match.get("matched_columns") or []}
            if matched_col_names:
                matched_cols = [
                    col for col in model.get("columns", [])
                    if col["name"].lower() in matched_col_names
                    or (col.get("display_name") or "").lower() in matched_col_names
                ]
                hit_models.append({**model, "matched_columns": matched_cols})
            else:
                hit_models.append(model)
    hit_model_names = {m["name"] for m in hit_models}
    hit_relations = [
        rel for rel in relations
        if rel.get("source_model") in hit_model_names or rel.get("target_model") in hit_model_names
    ]
    return {
        "models": hit_models,
        "relations": hit_relations,
        "fallback": True,
        "has_hits": True,
        "llm_match": True,
    }


_LLM_SCHEMA_LINK_CONTRACT = """You are a schema linking assistant. Given a user question and a project's metadata catalog, identify:
1. Which models, views, and calculated fields are relevant
2. Which specific columns in those models are relevant
3. A mapping from question terms to the exact column names they refer to

CRITICAL: You MUST return only valid JSON (no markdown, no code fences, no extra text). The JSON must parse with json.loads().

Output JSON with these fields:
- "matched_models": list of objects with:
  - "name": exact model/view/calculated_field name from the catalog
  - "matched_columns": list of exact column names from the catalog (or empty list)
  - "relevance": short reason
- "column_mapping": list of objects with:
  - "question_term": the user's term (e.g. "revenue", "城市", "top")
  - "model_name": the model this term maps to
  - "column_name": the exact column name this term maps to
  - "confidence": "high", "medium", or "low"
- "reasoning": brief explanation

Rules:
- Map user terms (in any language) to exact metadata names. "订单"/"orders" → model named "orders", "客户城市" → column "customer_city" with display_name "客户城市".
- Include PK columns and join columns even if not explicitly asked about — they are needed for COUNT(DISTINCT) and JOIN operations.
- For aggregation questions (count, sum, total, 统计, 总计), include the metric columns and grouping columns.
- For ranking/top-N questions, include the ordering column and the measure column.
- Prefer "high" confidence when the term directly matches a name/display_name; use "medium" for semantic/logical matches; use "low" only when uncertain.
- If no model or column is relevant, return {"matched_models": [], "column_mapping": [], "reasoning": "No relevant matches"}."""


def _llm_schema_link(question: str, project_id: int, *, models: Optional[list[dict]] = None, relations: Optional[list[dict]] = None, llm: Optional[LLMService] = None) -> Optional[dict[str, Any]]:
    if llm is None:
        llm = LLMService()
    if not llm.is_configured():
        return None
    if models is None:
        models = _models_for_project(project_id)
    if relations is None:
        relations = _relations_for_project(project_id)
    meta = _project_meta(project_id)
    catalog = _metadata_catalog_text(meta, models, relations)
    messages = [
        {"role": "system", "content": _LLM_SCHEMA_LINK_CONTRACT},
        {"role": "user", "content": f"Metadata catalog:\n{catalog}"},
        {"role": "user", "content": f"User question: {question}"},
    ]
    try:
        result = llm.chat(messages, response_format="json")
        content = _llm_content_text(result)
        if not content.strip():
            LOGGER.warning("LLM schema link returned empty content; using token-based semantic matching")
            _emit_route_event(
                "schema_link_fallback",
                {
                    "reason": "empty_content",
                    "fallback": "token_semantic_matching",
                },
                project_id=project_id,
            )
            return None
        parsed = parse_json_object(content)
    except Exception as exc:
        LOGGER.exception("LLM schema link failed")
        _emit_route_event(
            "schema_link_fallback",
            {
                "reason": "exception",
                "error_type": type(exc).__name__,
                "fallback": "token_semantic_matching",
            },
            project_id=project_id,
        )
        return None

    def _normalized_llm_matches(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        raw_matches = payload.get("matched_models")
        if not isinstance(raw_matches, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in raw_matches:
            if isinstance(item, dict):
                name = _analysis_item_to_text(item.get("name"))
                if not name:
                    continue
                normalized.append({
                    "name": name,
                    "matched_columns": _normalize_analysis_string_list(item.get("matched_columns")),
                })
                continue
            name = _analysis_item_to_text(item)
            if name:
                normalized.append({"name": name, "matched_columns": []})
        return normalized

    def _normalized_column_mapping(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        raw_mapping = payload.get("column_mapping")
        if not isinstance(raw_mapping, list):
            return []
        out: list[dict[str, Any]] = []
        for item in raw_mapping:
            if not isinstance(item, dict):
                continue
            model_name = _analysis_item_to_text(item.get("model_name") or item.get("model"))
            column_name = _analysis_item_to_text(item.get("column_name") or item.get("column"))
            confidence = _analysis_item_to_text(item.get("confidence")).lower() or "low"
            question_term = _analysis_item_to_text(item.get("question_term") or item.get("term"))
            if not model_name or not column_name:
                continue
            out.append(
                {
                    "question_term": question_term,
                    "model_name": model_name,
                    "column_name": column_name,
                    "confidence": confidence,
                }
            )
        return out

    normalized_matches = _normalized_llm_matches(parsed)
    matched_names = {m.get("name") for m in normalized_matches if m.get("name")}
    if not matched_names:
        return None
    hit_models = []
    for model in models:
        if model["name"] in matched_names:
            llm_match = next((m for m in normalized_matches if m.get("name") == model["name"]), {})
            matched_col_names = {c.lower() for c in llm_match.get("matched_columns") or []}
            if matched_col_names:
                matched_cols = [
                    col for col in model.get("columns", [])
                    if col["name"].lower() in matched_col_names
                    or (col.get("display_name") or "").lower() in matched_col_names
                ]
                hit_models.append({**model, "matched_columns": matched_cols})
            else:
                hit_models.append(model)
    hit_model_names = {m["name"] for m in hit_models}
    hit_relations = [
        rel for rel in relations
        if rel.get("source_model") in hit_model_names or rel.get("target_model") in hit_model_names
    ]
    column_mapping = _normalized_column_mapping(parsed)
    return {
        "models": hit_models,
        "relations": hit_relations,
        "fallback": True,
        "has_hits": True,
        "llm_match": True,
        "column_mapping": column_mapping,
    }


def _semantic_prompt(project_id: int, question: Optional[str] = None, *, require_hits: bool = False, analysis: Optional[dict] = None) -> tuple[str, list[str], dict[str, Any]]:
    models = _models_for_project(project_id)
    relations = _relations_for_project(project_id)
    token_hits = _semantic_hits(question or "", models, relations) if question else {"models": models, "relations": relations, "fallback": True, "has_hits": bool(models)}
    llm = LLMService()
    llm_configured = llm.is_configured()
    hits = token_hits
    if question and llm_configured:
        llm_hits = _llm_schema_link(question, project_id, models=models, relations=relations, llm=llm)
        if not llm_hits or not llm_hits.get("has_hits"):
            llm_hits = _llm_semantic_matching(question, project_id, models=models, relations=relations, llm=llm)
        if llm_hits and llm_hits.get("has_hits"):
            token_model_names = {m["name"] for m in token_hits.get("models", [])}
            llm_model_names = {m["name"] for m in llm_hits.get("models", [])}
            merged_models = {}
            for m in llm_hits["models"]:
                merged_models[m["name"]] = m
            for m in token_hits.get("models", []):
                if m["name"] not in merged_models:
                    merged_models[m["name"]] = m
                else:
                    existing = merged_models[m["name"]]
                    llm_cols = {(c.get("name") or "").lower() for c in existing.get("matched_columns", [])}
                    for c in m.get("matched_columns", []):
                        if (c.get("name") or "").lower() not in llm_cols:
                            existing.setdefault("matched_columns", []).append(c)
            all_model_names = set(merged_models.keys()) | token_model_names | llm_model_names
            merged_relations = [r for r in relations if r.get("source_model") in all_model_names or r.get("target_model") in all_model_names]
            hits = {
                "models": list(merged_models.values()),
                "relations": merged_relations,
                "fallback": False,
                "has_hits": True,
                "llm_match": True,
                "score": token_hits.get("score", 0),
                "column_mapping": llm_hits.get("column_mapping", []),
            }
        elif not token_hits.get("has_hits"):
            hits = {"models": [], "relations": [], "fallback": False, "has_hits": False}
    if question and hits.get("has_hits"):
        expanded_models, expanded_relations = _expand_via_relations(
            hits["models"], models, relations, analysis,
        )
        if len(expanded_models) > len(hits["models"]):
            hits = {**hits, "models": expanded_models, "relations": expanded_relations}
    if require_hits and question and not hits.get("has_hits"):
        if models:
            hits = {"models": models, "relations": relations, "fallback": True, "has_hits": True, "broad_match": True}
        else:
            return "Project semantic model:\nMetadata retrieval: no matching project metadata was found for this question.", [], hits
    selected_models = hits["models"]
    selected_relations = hits["relations"]
    retrieved_tables = [m["name"] for m in selected_models]
    semantic_context = _reformat_schema_context(
        selected_models,
        selected_relations,
        retrieved_tables,
        question,
        hits.get("has_hits"),
    )
    return semantic_context, retrieved_tables, hits


def _validate_sql_alias_scope(sql: str) -> list[str]:
    if sqlglot is None or exp is None:
        return []
    try:
        from sqlglot.optimizer.scope import traverse_scope

        parsed = sqlglot.parse_one(_normalize_sql_text(sql), read="duckdb")
    except Exception:
        return []

    issues: list[str] = []
    seen: set[str] = set()

    def _scope_sources(scope_obj: Any) -> set[str]:
        try:
            selected = scope_obj.selected_sources or {}
        except Exception as exc:
            duplicate_alias = _extract_duplicate_alias_name(exc)
            if duplicate_alias:
                key = f"duplicate_alias::{duplicate_alias.lower()}"
                if key not in seen:
                    seen.add(key)
                    issues.append(f"{duplicate_alias} (duplicate table alias in the same SELECT scope)")
            return set()
        return {
            str(name or "").strip().lower()
            for name in selected.keys()
            if str(name or "").strip()
        }

    for scope in traverse_scope(parsed):
        visible_sources = _scope_sources(scope)
        parent = getattr(scope, "parent", None)
        while parent is not None:
            visible_sources.update(_scope_sources(parent))
            parent = getattr(parent, "parent", None)
        if not visible_sources:
            continue
        for col in scope.columns:
            table = str(getattr(col, "table", "") or "").strip().lower()
            col_name = str(getattr(col, "name", "") or "").strip().lower()
            if not table or not col_name:
                continue
            if table in visible_sources:
                continue
            key = f"{table}.{col_name}"
            if key in seen:
                continue
            seen.add(key)
            available = ", ".join(sorted(visible_sources))
            issues.append(
                f"{table}.{col_name} (table/alias not visible in current SELECT scope; available: {available})"
            )
    return issues


def _knowledge_context(project_id: int, question: str, limit: int = 5) -> tuple[str, dict[str, Any]]:
    question_tokens = _tokenize(question)
    if not question_tokens:
        return "", {"instructions": [], "sql_pairs": []}
    with connection_lock():
        con = get_connection()
        instruction_rows = con.execute(
            """
            SELECT id, instruction, category, scope, priority, questions
            FROM metadata.instructions
            WHERE project_id = ? OR scope = 'system'
            ORDER BY COALESCE(priority, 0) DESC, updated_at DESC, id DESC
            """,
            [project_id],
        ).fetchall()
        sql_pair_rows = con.execute(
            """
            SELECT id, question, sql, description, category, scope
            FROM metadata.sql_pairs
            WHERE project_id = ? OR scope = 'system'
            ORDER BY updated_at DESC, id DESC
            """,
            [project_id],
        ).fetchall()

    scored_instructions = []
    for row in instruction_rows:
        related_questions = _safe_json_loads(row[5], [])
        score = int(row[4] or 0)
        related_questions_text = " ".join(_normalize_analysis_string_list(related_questions))
        score += _score_metadata_text(question_tokens, row[1], row[2], row[3], related_questions_text)
        if score > 0:
            scored_instructions.append((score, row))

    scored_pairs = []
    for row in sql_pair_rows:
        score = _score_metadata_text(question_tokens, row[1], row[3], row[4], row[5])
        if score > 0:
            scored_pairs.append((score, row))

    scored_instructions.sort(key=lambda item: item[0], reverse=True)
    scored_pairs.sort(key=lambda item: item[0], reverse=True)
    instructions = [row for _, row in scored_instructions[:limit]]
    sql_pairs = [row for _, row in scored_pairs[:limit]]
    if not instructions and not sql_pairs and _contains_cjk(question):
        # Cross-language fallback: CJK question had no token overlap with
        # English/other-language knowledge items. Return most recent items.
        instructions = [row for row in instruction_rows[:limit]]
        sql_pairs = [row for row in sql_pair_rows[:limit]]
    if not instructions and not sql_pairs:
        return "", {"instructions": [], "sql_pairs": []}

    lines = ["Project knowledge context:"]
    if instructions:
        lines.append("Instructions:")
        for row in instructions:
            category = f" [{row[2]}]" if row[2] else ""
            lines.append(f"- #{row[0]}{category}: {row[1]}")
    if sql_pairs:
        lines.append("Verified question-SQL examples:")
        for row in sql_pairs:
            description = f" - {row[3]}" if row[3] else ""
            lines.append(f"- #{row[0]} question: {row[1]}{description}\n  sql: {row[2]}")
    hits = {
        "instructions": [{"id": row[0], "category": row[2], "scope": row[3]} for row in instructions],
        "sql_pairs": [{"id": row[0], "category": row[4], "scope": row[5]} for row in sql_pairs],
    }
    return "\n".join(lines), hits


def _augment_context_with_knowledge(semantic_context: str, knowledge_context: str) -> str:
    return "\n\n".join(part for part in [semantic_context, knowledge_context] if part)


def _extract_sql_examples_from_knowledge(knowledge_context: str) -> str:
    if not knowledge_context:
        return ""
    examples = []
    for line in knowledge_context.split("\n"):
        stripped = line.strip()
        if stripped.startswith("sql:"):
            examples.append(stripped[4:].strip())
    if not examples:
        return "No verified examples available for this question."
    return "\n".join(examples[:5])


def _column_lookup(model: dict[str, Any]) -> dict[str, str]:
    return {str(column.get("name") or "").lower(): str(column.get("name")) for column in model.get("columns", []) if column.get("name")}


def _find_model_with_columns(models: list[dict[str, Any]], required: set[str], preferred_name: str | None = None) -> tuple[dict[str, Any], dict[str, str]] | tuple[None, None]:
    candidates = []
    for model in models:
        columns = _column_lookup(model)
        if required.issubset(set(columns.keys())):
            score = 1
            if preferred_name and preferred_name.lower() in str(model.get("name") or "").lower():
                score += 5
            candidates.append((score, model, columns))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, model, columns = candidates[0]
    return model, columns


def _classify_question_route(question: str, project_id: int, previous_questions: Optional[list[str]] = None, analysis: Optional[dict] = None) -> dict[str, Any]:
    normalized_analysis = _normalize_question_analysis(analysis)
    try:
        models = _models_for_project(project_id)
    except Exception:
        models = []
    try:
        relations = _relations_for_project(project_id)
    except Exception:
        relations = []
    clause_routing = _classify_clause_routing(
        question,
        models=models,
        relations=relations,
        analysis=normalized_analysis,
    )
    clause_routing_prompt = _format_clause_routing_for_prompt(clause_routing)
    semantic_context, retrieved_tables, hits = _semantic_prompt(project_id, question, require_hits=False, analysis=normalized_analysis)
    knowledge_context, knowledge_hits = _knowledge_context(project_id, question)
    combined_context = _augment_context_with_knowledge(semantic_context, knowledge_context)
    meta_summary = _build_metadata_summary(project_id)
    strict_json = _strict_json_capability()
    prompt_selection = _prompt_profile_selection(
        "question_categorization",
        strict_json_mode=strict_json.get("mode", "none"),
    )
    route_v2_enabled = _is_sql_route_v2_enabled(project_id)
    shadow_mode = bool(ROUTER_CONFIG.get("sql_route_shadow_mode", False))
    use_profile = route_v2_enabled or shadow_mode

    def _emit_question_route(result_payload: dict[str, Any]) -> None:
        if not use_profile:
            return
        clause_summary = _event_clause_routing_summary(result_payload.get("clause_routing"))
        _emit_route_event(
            "question_route_decision",
            {
                "requires_sql": bool(result_payload.get("requires_sql")),
                "reasoning": str(result_payload.get("reasoning") or ""),
                "metadata_question_part": str(result_payload.get("metadata_question_part") or ""),
                "non_metadata_question_part": str(result_payload.get("non_metadata_question_part") or ""),
                "analysis_tier": str(normalized_analysis.get("tier") or "simple"),
                "semantic_hits": {
                    "has_hits": bool((result_payload.get("semantic_hits") or {}).get("has_hits")),
                    "models": len((result_payload.get("semantic_hits") or {}).get("models") or []),
                    "relations": len((result_payload.get("semantic_hits") or {}).get("relations") or []),
                },
                "knowledge_hits": {
                    "instructions": len((result_payload.get("knowledge_hits") or {}).get("instructions") or []),
                    "sql_pairs": len((result_payload.get("knowledge_hits") or {}).get("sql_pairs") or []),
                },
                "clause_routing": clause_summary,
                "clause_mixed": bool(clause_summary.get("mixed")),
                "metadata_clause_count": int(clause_summary.get("metadata_clause_count") or 0),
                "non_metadata_clause_count": int(clause_summary.get("non_metadata_clause_count") or 0),
                "prompt_profile_id": prompt_selection.profile_id,
                "prompt_profile_version": prompt_selection.profile_version,
                "strict_json_mode": strict_json.get("mode", "none"),
            },
            project_id=project_id,
        )

    has_clause_metadata = bool(str(clause_routing.get("metadata_question_part") or "").strip())
    no_hits_result = {
        "requires_sql": False,
        "metadata_question_part": "",
        "non_metadata_question_part": question,
        "reasoning": "No project model, field, or relation metadata matched the question.",
        "retrieved_tables": [],
        "semantic_context": semantic_context,
        "knowledge_context": knowledge_context,
        "combined_context": combined_context,
        "semantic_hits": hits,
        "knowledge_hits": knowledge_hits,
        "configured": True,
        "metadata_summary": meta_summary,
        "analysis": normalized_analysis,
        "prompt_profile_id": prompt_selection.profile_id,
        "prompt_profile_version": prompt_selection.profile_version,
        "strict_json_mode": strict_json.get("mode", "none"),
        "clause_routing": clause_routing,
    }
    if not hits.get("has_hits") and not has_clause_metadata:
        _emit_question_route(no_hits_result)
        return no_hits_result

    llm = LLMService()
    if not llm.is_configured():
        metadata_part = str(clause_routing.get("metadata_question_part") or "").strip() or question
        non_metadata_part = str(clause_routing.get("non_metadata_question_part") or "").strip() if clause_routing.get("mixed") else ""
        requires_sql = bool(metadata_part)
        route_semantic_context = semantic_context
        route_retrieved_tables = retrieved_tables
        route_hits = hits
        route_knowledge_context = knowledge_context
        route_knowledge_hits = knowledge_hits
        route_combined_context = combined_context
        if requires_sql:
            if metadata_part.strip() != question.strip() or not hits.get("has_hits"):
                route_semantic_context, route_retrieved_tables, route_hits = _semantic_prompt(
                    project_id,
                    metadata_part,
                    require_hits=False,
                    analysis=normalized_analysis,
                )
            if not route_hits.get("has_hits"):
                route_semantic_context, route_retrieved_tables, route_hits = _semantic_prompt(
                    project_id,
                    metadata_part,
                    require_hits=True,
                    analysis=normalized_analysis,
                )
            if metadata_part.strip() != question.strip() or clause_routing.get("mixed"):
                route_knowledge_context, route_knowledge_hits = _knowledge_context(project_id, metadata_part)
            route_combined_context = _augment_context_with_knowledge(route_semantic_context, route_knowledge_context)
        result = {
            "requires_sql": requires_sql,
            "metadata_question_part": metadata_part if requires_sql else "",
            "non_metadata_question_part": non_metadata_part if requires_sql else question,
            "reasoning": "Matched project metadata; LLM routing was skipped because the provider is not configured.",
            "retrieved_tables": route_retrieved_tables,
            "semantic_context": route_semantic_context,
            "knowledge_context": route_knowledge_context,
            "combined_context": route_combined_context,
            "semantic_hits": route_hits,
            "knowledge_hits": route_knowledge_hits,
            "configured": False,
            "metadata_summary": meta_summary,
            "analysis": normalized_analysis,
            "prompt_profile_id": prompt_selection.profile_id,
            "prompt_profile_version": prompt_selection.profile_version,
            "strict_json_mode": strict_json.get("mode", "none"),
            "clause_routing": clause_routing,
        }
        _emit_question_route(result)
        return result

    analysis_str = ""
    if normalized_analysis:
        filters_text = _format_analysis_filters(normalized_analysis.get("filters") or [])
        analysis_str = (
            f"Question tier: {normalized_analysis.get('tier', 'unknown')}\n"
            f"Entities: {', '.join(normalized_analysis.get('entities') or [])}\n"
            f"Metrics: {', '.join(normalized_analysis.get('metrics') or [])}\n"
            f"Dimensions: {', '.join(normalized_analysis.get('dimensions') or [])}\n"
            f"Filters: {filters_text}\n"
        )
    meta = _project_meta(project_id) or {}
    system_suffix = f"\n{prompt_selection.system_suffix}" if use_profile and prompt_selection.system_suffix else ""
    response_format = prompt_selection.response_format if (use_profile and prompt_selection.response_format) else "json"
    messages = [
        {"role": "system", "content": f"{_render_system_prompt()}\n\n{QUESTION_ROUTING_CONTRACT}{system_suffix}"},
        {
            "role": "user",
            "content": (
                f"Project: {meta.get('display_name') or meta.get('name') or project_id}\n"
                f"Project description: {meta.get('description') or ''}\n"
                f"Project schema:\n{meta_summary['summary']}\n"
                + (f"Question analysis:\n{analysis_str}\n" if analysis_str else "")
                + (f"Clause routing pre-analysis:\n{clause_routing_prompt}\n" if clause_routing_prompt else "")
                + f"Matched metadata and knowledge:\n{combined_context}"
            ),
        },
        {"role": "user", "content": f"Previous questions: {previous_questions or []}\nQuestion: {question}"},
    ]
    try:
        result = _llm_chat_with_response_format_fallback(
            llm,
            messages,
            response_format=response_format,
            stage="question_categorization",
        )
        raw_content = str(result.get("content") or "").strip()
        if not raw_content:
            raise ValueError("LLM returned empty content in question route classification")
        parsed = parse_json_object(raw_content)
    except Exception as exc:
        LOGGER.warning(
            "Question route classification failed (%s); defaulting to SQL route when metadata is matched",
            exc,
        )
        parsed = {
            "requires_sql": True,
            "metadata_question_part": question,
            "non_metadata_question_part": "",
            "reasoning": f"Route classification failed ({type(exc).__name__}); defaulted to SQL path because project metadata was matched.",
        }

    requires_sql = _normalize_bool(parsed.get("requires_sql", False))
    metadata_part = str(parsed.get("metadata_question_part") or "").strip()
    non_metadata_part = str(parsed.get("non_metadata_question_part") or "").strip()

    clause_metadata_part = str(clause_routing.get("metadata_question_part") or "").strip()
    clause_non_metadata_part = str(clause_routing.get("non_metadata_question_part") or "").strip()
    if clause_metadata_part and clause_non_metadata_part:
        requires_sql = True
        metadata_part = clause_metadata_part
        non_metadata_part = clause_non_metadata_part
    elif clause_metadata_part:
        if not requires_sql:
            requires_sql = True
        if not metadata_part:
            metadata_part = clause_metadata_part
        if clause_non_metadata_part and not non_metadata_part and metadata_part.strip() != question.strip():
            non_metadata_part = clause_non_metadata_part

    if requires_sql and not metadata_part:
        metadata_part = clause_metadata_part or question
    if not requires_sql:
        metadata_part = ""
        non_metadata_part = non_metadata_part or clause_non_metadata_part or question
    if requires_sql and non_metadata_part and not clause_routing.get("mixed"):
        non_lower = non_metadata_part.lower()
        if any(indicator in non_lower for indicator in _DATA_ROUTE_INDICATORS):
            metadata_part = question
            non_metadata_part = ""

    route_semantic_context = semantic_context
    route_retrieved_tables = retrieved_tables
    route_hits = hits
    route_knowledge_context = knowledge_context
    route_knowledge_hits = knowledge_hits
    route_combined_context = combined_context
    if requires_sql and metadata_part:
        if metadata_part.strip() != question.strip() or not hits.get("has_hits") or clause_routing.get("mixed"):
            route_semantic_context, route_retrieved_tables, route_hits = _semantic_prompt(
                project_id,
                metadata_part,
                require_hits=False,
                analysis=normalized_analysis,
            )
        if not route_hits.get("has_hits"):
            route_semantic_context, route_retrieved_tables, route_hits = _semantic_prompt(
                project_id,
                metadata_part,
                require_hits=True,
                analysis=normalized_analysis,
            )
        if metadata_part.strip() != question.strip() or clause_routing.get("mixed"):
            route_knowledge_context, route_knowledge_hits = _knowledge_context(project_id, metadata_part)
        route_combined_context = _augment_context_with_knowledge(route_semantic_context, route_knowledge_context)

    reasoning = parsed.get("reasoning") or "Matched project metadata and routed the answer path."
    if clause_routing.get("mixed"):
        reasoning = f"{reasoning} Clause-level routing separated SQL-focused and general clauses."

    final_result = {
        "requires_sql": requires_sql,
        "metadata_question_part": metadata_part,
        "non_metadata_question_part": non_metadata_part,
        "reasoning": reasoning,
        "retrieved_tables": route_retrieved_tables,
        "semantic_context": route_semantic_context,
        "knowledge_context": route_knowledge_context,
        "combined_context": route_combined_context,
        "semantic_hits": route_hits,
        "knowledge_hits": route_knowledge_hits,
        "configured": True,
        "metadata_summary": meta_summary,
        "analysis": normalized_analysis,
        "prompt_profile_id": prompt_selection.profile_id,
        "prompt_profile_version": prompt_selection.profile_version,
        "strict_json_mode": strict_json.get("mode", "none"),
        "clause_routing": clause_routing,
    }
    _emit_question_route(final_result)
    return final_result


def _validate_sql_columns(sql: str, models: list[dict[str, Any]]) -> list[str]:
    """Check that SQL table-prefixed columns exist in the given models.

    Resolves SQL aliases (e.g., FROM orders AS o) and CTE aliases (e.g., WITH t1 AS (SELECT ... FROM orders))
    to model names so that alias-prefixed column references are validated
    against the correct model's column list.

    For each bad column, searches ALL models for a match and suggests the
    correct owner. Returns a list of human-readable diagnostic strings
    (empty = all valid). Returns [] if validation itself fails (defensive)."""
    try:
        import sqlglot
        normalized_sql = _normalize_sql_text(sql)
        alias_scope_issues = _validate_sql_alias_scope(normalized_sql)
        parsed = sqlglot.parse_one(normalized_sql, read="duckdb")
        alias_map = _build_alias_map(normalized_sql)
        cte_projection_map: dict[str, set[str]] = {}

        def _collect_select_projection_names(select_expr: sqlglot.exp.Expression) -> set[str]:
            projection: set[str] = set()
            if not isinstance(select_expr, sqlglot.exp.Select):
                return projection
            for expr in select_expr.expressions or []:
                if isinstance(expr, sqlglot.exp.Star):
                    projection.add("*")
                    continue
                alias_name = (expr.alias or "").lower()
                if alias_name:
                    projection.add(alias_name)
                    continue
                if isinstance(expr, sqlglot.exp.Column):
                    col_name = (expr.name or "").lower()
                    if col_name:
                        projection.add(col_name)
            return projection

        for table_expr in parsed.find_all(sqlglot.exp.Table):
            name = table_expr.name
            if name:
                alias_map[name.lower()] = name.lower()
            if table_expr.alias:
                alias_map[table_expr.alias.lower()] = name.lower() if name else table_expr.alias.lower()
        for cte in parsed.find_all(sqlglot.exp.CTE):
            cte_alias = (cte.alias or "").lower()
            inner = cte.this
            projection = _collect_select_projection_names(inner)
            if cte_alias and projection:
                cte_projection_map[cte_alias] = projection
            if inner and hasattr(inner, "find_all"):
                for tbl in inner.find_all(sqlglot.exp.Table):
                    inner_name = (tbl.name or "").lower()
                    if inner_name and not inner_name.startswith("select"):
                        alias_map[cte_alias] = inner_name
                        break
        model_columns: dict[str, set[str]] = {}
        column_owners: dict[str, list[str]] = {}
        for model in models:
            mname = model.get("table_reference", model.get("name", "")).lower()
            mname_alt = model.get("name", "").lower()
            cols = set()
            for col in model.get("columns", []):
                cn = col.get("name", "").lower()
                if cn:
                    cols.add(cn)
                    column_owners.setdefault(cn, []).append(mname)
                    if mname_alt != mname:
                        column_owners[cn].append(mname_alt)
                dn = (col.get("display_name") or "").lower()
                if dn:
                    cols.add(dn)
                    column_owners.setdefault(dn, []).append(mname)
            for col in model.get("matched_columns") or []:
                cn = col.get("name", "").lower()
                if cn and cn not in cols:
                    cols.add(cn)
                    column_owners.setdefault(cn, []).append(mname)
                    if mname_alt != mname and mname_alt not in column_owners.get(cn, []):
                        column_owners[cn].append(mname_alt)
                dn = (col.get("display_name") or "").lower()
                if dn and dn not in cols:
                    cols.add(dn)
                    column_owners.setdefault(dn, []).append(mname)
            model_columns[mname] = cols
            if mname_alt != mname:
                model_columns[mname_alt] = cols
        model_names = set(model_columns.keys())
        bad = []
        seen = set()
        for col in parsed.find_all(sqlglot.exp.Column):
            table = col.table
            col_name = col.name.lower()
            if not table or not col_name:
                continue
            table_lower = table.lower()
            cte_projection = cte_projection_map.get(table_lower)
            if cte_projection is not None and "*" not in cte_projection and col_name not in cte_projection:
                key = f"{table_lower}.{col_name}"
                if key not in seen:
                    seen.add(key)
                    suggested = _fuzzy_column_match(col_name, cte_projection)
                    if suggested:
                        bad.append(f"{table}.{col_name} (not projected by CTE {table}; did you mean {suggested}?)")
                    else:
                        bad.append(f"{table}.{col_name} (not projected by CTE {table})")
                continue
            resolved = alias_map.get(table_lower, table_lower)
            is_bad = False
            if resolved in model_names:
                if col_name not in model_columns[resolved]:
                    is_bad = True
            elif table_lower in model_names:
                if col_name not in model_columns[table_lower]:
                    is_bad = True
            else:
                continue
            if is_bad:
                key = f"{table_lower}.{col_name}"
                if key not in seen:
                    seen.add(key)
                    owners = column_owners.get(col_name, [])
                    if owners:
                        bad.append(f"{table}.{col_name} (belongs on: {', '.join(dict.fromkeys(owners))})")
                    else:
                        all_col_names = set(column_owners.keys())
                        fuzzy = _fuzzy_column_match(col_name, all_col_names)
                        if fuzzy:
                            fuzzy_owners = column_owners.get(fuzzy, [])
                            bad.append(f"{table}.{col_name} (not found; did you mean {fuzzy} on {', '.join(dict.fromkeys(fuzzy_owners))}?)")
                        else:
                            bad.append(f"{table}.{col_name} (not found in any model)")
        alias_refs: dict[str, str] = {}
        for col in parsed.find_all(sqlglot.exp.Column):
            t = col.table
            if t:
                alias_refs.setdefault(t, []).append(col.name.lower())
        for alias, alias_table_name in alias_map.items():
            if alias == alias_table_name:
                continue
            if alias_table_name not in model_columns:
                continue
            valid_cols = model_columns[alias_table_name]
            for col_name in alias_refs.get(alias, []):
                key = f"{alias}.{col_name}"
                if col_name not in valid_cols and key not in seen:
                    seen.add(key)
                    owners = column_owners.get(col_name, [])
                    if owners:
                        bad.append(f"{alias}.{col_name} (belongs on: {', '.join(dict.fromkeys(owners))})")
                    else:
                        all_col_names = set(column_owners.keys())
                        fuzzy = _fuzzy_column_match(col_name, all_col_names)
                        if fuzzy:
                            fuzzy_owners = column_owners.get(fuzzy, [])
                            bad.append(f"{alias}.{col_name} (not found; did you mean {fuzzy} on {', '.join(dict.fromkeys(fuzzy_owners))}?)")
                        else:
                            bad.append(f"{alias}.{col_name} (not found in any model)")

        try:
            from sqlglot.optimizer.scope import traverse_scope

            def _normalize_projection_names(values: set[str]) -> set[str]:
                normalized: set[str] = set()
                for value in values:
                    text = str(value or "").strip().lower()
                    if text:
                        normalized.add(text)
                return normalized

            for scope in traverse_scope(parsed):
                source_projection_map: dict[str, set[str]] = {}
                selected_sources = getattr(scope, "selected_sources", {}) or {}
                for source_alias, source_payload in selected_sources.items():
                    alias_lower = str(source_alias or "").strip().lower()
                    if not alias_lower:
                        continue
                    source_expr = source_payload[0] if isinstance(source_payload, tuple) and source_payload else source_payload
                    projection: set[str] = set()
                    if isinstance(source_expr, sqlglot.exp.Table):
                        table_name = str(source_expr.name or "").strip().lower()
                        projection = set(
                            cte_projection_map.get(alias_lower)
                            or cte_projection_map.get(table_name)
                            or model_columns.get(table_name)
                            or model_columns.get(alias_lower)
                            or []
                        )
                    elif isinstance(source_expr, sqlglot.exp.Subquery):
                        projection = _collect_select_projection_names(source_expr.this)
                    elif isinstance(source_expr, sqlglot.exp.Select):
                        projection = _collect_select_projection_names(source_expr)
                    if projection:
                        source_projection_map[alias_lower] = _normalize_projection_names(projection)

                if not source_projection_map:
                    continue

                scope_expression = getattr(scope, "expression", None)
                select_aliases: set[str] = set()
                if isinstance(scope_expression, sqlglot.exp.Select):
                    for select_expr in scope_expression.expressions or []:
                        alias_name = str(select_expr.alias or "").strip().lower()
                        if alias_name:
                            select_aliases.add(alias_name)

                for col in scope.columns:
                    table = str(getattr(col, "table", "") or "").strip().lower()
                    col_name = str(getattr(col, "name", "") or "").strip().lower()
                    if table or not col_name:
                        continue
                    if col_name in select_aliases:
                        continue
                    matching_sources = [
                        alias
                        for alias, projected in source_projection_map.items()
                        if "*" in projected or col_name in projected
                    ]
                    scope_key = ",".join(sorted(source_projection_map.keys()))
                    issue_key = f"__unqualified__::{scope_key}::{col_name}"
                    if issue_key in seen:
                        continue
                    if len(matching_sources) > 1:
                        seen.add(issue_key)
                        bad.append(
                            f"{col_name} (not visible in current SELECT scope; ambiguous unqualified column, candidates: {', '.join(sorted(matching_sources))})"
                        )
                        continue
                    if len(matching_sources) == 1:
                        continue

                    available_columns = sorted(
                        {
                            projected_col
                            for projected in source_projection_map.values()
                            for projected_col in projected
                            if projected_col != "*"
                        }
                    )
                    preview = ", ".join(available_columns[:12]) if available_columns else ", ".join(sorted(source_projection_map.keys()))
                    seen.add(issue_key)
                    bad.append(
                        f"{col_name} (not visible in current SELECT scope; available columns: {preview})"
                    )
        except Exception:
            pass

        merged = list(dict.fromkeys((bad or []) + (alias_scope_issues or [])))
        return merged or []
    except Exception as exc:
        duplicate_alias = _extract_duplicate_alias_name(exc)
        if duplicate_alias:
            return [f"{duplicate_alias} (duplicate table alias in the same SELECT scope)"]
        return []


def _fuzzy_column_match(name: str, candidates: set[str]) -> str | None:
    if not name or not candidates:
        return None
    import difflib

    name_lower = name.lower().replace(" ", "_")
    if name_lower in candidates:
        return name_lower
    for c in candidates:
        c_lower = c.lower()
        if c_lower == name_lower:
            return c_lower
        if c_lower.startswith(name_lower):
            return c_lower
    name_parts = set(re.split(r"[\s_]+", name_lower))
    best = None
    best_score = 0.0
    best_overlap = 0
    best_ratio = 0.0
    for c in candidates:
        c_parts = set(re.split(r"[\s_]+", c.lower()))
        overlap = len(name_parts & c_parts)
        ratio = difflib.SequenceMatcher(None, name_lower, c.lower()).ratio()
        score = overlap * 2.0 + ratio * 4.0
        if score > best_score:
            best_score = score
            best = c.lower()
            best_overlap = overlap
            best_ratio = ratio
    if best:
        high_confidence_typo = best_ratio >= 0.74
        related_tokens = best_overlap > 0 and best_score >= 2.2
        if high_confidence_typo or related_tokens:
            return best
    return None


def _classify_unknown_column_issue(issue: str) -> str:
    text = (issue or "").lower()
    if "duplicate table alias" in text or "duplicate alias" in text or "alias already used" in text:
        return "duplicate_alias"
    if "not visible in current select scope" in text:
        return "alias_scope_leak"
    if "not projected by cte" in text:
        return "cte_projection_missing"
    if "belongs on:" in text:
        owners_part = text.split("belongs on:", 1)[-1]
        owners = [p.strip() for p in owners_part.split(",") if p.strip()]
        if len(owners) > 1:
            return "ambiguous_owner"
        return "wrong_alias_owner"
    if "did you mean" in text:
        return "fuzzy_miss"
    if "not found in any model" in text:
        return "hallucinated_column"
    return "other_unknown_column_issue"


def _summarize_unknown_column_issues(issues: list[str]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for issue in issues or []:
        bucket = _classify_unknown_column_issue(issue)
        summary[bucket] = summary.get(bucket, 0) + 1
    return summary


def _dominant_issue_bucket(issue_buckets: Optional[dict[str, int]]) -> str:
    if not issue_buckets:
        return ""
    candidate = sorted(
        (
            (str(bucket or ""), int(count or 0))
            for bucket, count in issue_buckets.items()
            if str(bucket or "").strip() and int(count or 0) > 0
        ),
        key=lambda item: (-item[1], item[0]),
    )
    return candidate[0][0] if candidate else ""


def _parse_alias_scope_issue(issue: str) -> tuple[str, str, list[str]] | None:
    match = _ALIAS_SCOPE_ISSUE_RE.match(str(issue or "").strip())
    if not match:
        return None
    table = str(match.group("table") or "").strip().lower()
    column = str(match.group("column") or "").strip().lower()
    available = [
        part.strip().lower()
        for part in str(match.group("available") or "").split(",")
        if part.strip()
    ]
    if not table or not column or not available:
        return None
    return table, column, available


def _parse_unqualified_alias_scope_issue(issue: str) -> tuple[str, list[str]] | None:
    match = _UNQUALIFIED_ALIAS_SCOPE_ISSUE_RE.match(str(issue or "").strip())
    if not match:
        return None
    column = str(match.group("column") or "").strip().lower()
    candidates = [
        part.strip().lower()
        for part in str(match.group("candidates") or "").split(",")
        if part.strip()
    ]
    if not column or not candidates:
        return None
    return column, candidates


def _parse_hallucinated_column_issue(issue: str) -> tuple[str, str] | None:
    match = _HALLUCINATED_COLUMN_ISSUE_RE.match(str(issue or "").strip())
    if not match:
        return None
    table = str(match.group("table") or "").strip().lower()
    column = str(match.group("column") or "").strip().lower()
    if not table or not column:
        return None
    return table, column


def _pick_hallucinated_column_replacement(column_name: str, available_columns: set[str]) -> str | None:
    normalized = {str(col or "").strip().lower() for col in (available_columns or set()) if str(col or "").strip()}
    if not normalized:
        return None
    target = str(column_name or "").strip().lower()
    if not target:
        return None
    if target in normalized:
        return target
    fuzzy = _fuzzy_column_match(target, normalized)
    if fuzzy:
        return fuzzy
    if target in _HALLUCINATED_QUANTITY_TOKENS:
        priority = [
            "quantity",
            "qty",
            "item_quantity",
            "item_qty",
            "order_quantity",
            "order_qty",
            "order_item_id",
            "line_item_id",
            "item_id",
            "order_id",
            "product_id",
            "sku_id",
            "id",
        ]
        for candidate in priority:
            if candidate in normalized:
                return candidate
        id_like = sorted(col for col in normalized if col.endswith("_id"))
        if id_like:
            return id_like[0]
    return None


def _apply_hallucinated_column_rewrite_rules(
    sql: str,
    issues: Optional[list[str]],
    hit_models: Optional[list[dict[str, Any]]] = None,
) -> str:
    parsed_issues = {
        parsed
        for parsed in (_parse_hallucinated_column_issue(issue) for issue in (issues or []))
        if parsed is not None
    }
    if not parsed_issues or sqlglot is None or exp is None:
        return sql
    try:
        from sqlglot.optimizer.scope import traverse_scope

        parsed_sql = sqlglot.parse_one(_normalize_sql_text(sql), read="duckdb")
    except Exception:
        return sql

    model_columns: dict[str, set[str]] = {}
    for model in hit_models or []:
        keys = {
            str(model.get("name") or "").strip().lower(),
            str(model.get("table_reference") or "").strip().lower(),
        }
        col_names = {
            str(col.get("name") or "").strip().lower()
            for col in (
                model.get("matched_columns")
                if model.get("matched_columns") is not None
                else model.get("columns")
                or []
            )
            if str(col.get("name") or "").strip()
        }
        for key in keys:
            if key:
                model_columns[key] = col_names

    if not model_columns:
        return sql

    def _scope_alias_to_table(scope_obj: Any) -> dict[str, str]:
        alias_to_table: dict[str, str] = {}
        cursor = scope_obj
        while cursor is not None:
            try:
                selected = cursor.selected_sources or {}
            except Exception:
                selected = {}
            for alias_name, source_payload in selected.items():
                alias = str(alias_name or "").strip().lower()
                if not alias or alias in alias_to_table:
                    continue
                source_expr = source_payload[0] if isinstance(source_payload, tuple) and source_payload else source_payload
                table_name = alias
                if isinstance(source_expr, exp.Table):
                    table_name = str(source_expr.name or source_expr.alias_or_name or alias).strip().lower() or alias
                else:
                    fallback_name = str(getattr(source_expr, "name", "") or "").strip().lower()
                    if fallback_name:
                        table_name = fallback_name
                alias_to_table[alias] = table_name
            cursor = getattr(cursor, "parent", None)
        return alias_to_table

    changed = False
    for scope in traverse_scope(parsed_sql):
        alias_to_table = _scope_alias_to_table(scope)
        if not alias_to_table:
            continue
        for column in scope.columns:
            table = str(getattr(column, "table", "") or "").strip().lower()
            col_name = str(getattr(column, "name", "") or "").strip().lower()
            if not table or not col_name:
                continue
            resolved_table = alias_to_table.get(table, table)
            if (table, col_name) not in parsed_issues and (resolved_table, col_name) not in parsed_issues:
                continue
            available_columns = model_columns.get(resolved_table) or model_columns.get(table) or set()
            replacement = _pick_hallucinated_column_replacement(col_name, available_columns)
            if not replacement or replacement == col_name:
                continue
            if col_name in _HALLUCINATED_QUANTITY_TOKENS:
                parent = getattr(column, "parent", None)
                if isinstance(parent, exp.Sum) and getattr(parent, "this", None) is column:
                    count_target = exp.Column(this=sqlglot.to_identifier(replacement))
                    if table:
                        count_target.set("table", sqlglot.to_identifier(table))
                    parent.replace(exp.Count(this=count_target))
                    changed = True
                    continue
                if isinstance(parent, exp.Mul):
                    column.replace(exp.Literal(this="1", is_string=False))
                    changed = True
                    continue
            column.set("this", sqlglot.to_identifier(replacement))
            changed = True

    if not changed:
        return sql
    try:
        rewritten = parsed_sql.sql(dialect="duckdb")
        sqlglot.parse_one(rewritten, read="duckdb")
        return rewritten
    except Exception:
        return sql


def _apply_alias_scope_rewrite_rules(
    sql: str,
    issues: Optional[list[str]],
    hit_models: Optional[list[dict[str, Any]]] = None,
    schema_link_plan: Optional[dict[str, Any]] = None,
) -> str:
    parsed_issues = [
        parsed_issue
        for parsed_issue in (_parse_alias_scope_issue(issue) for issue in (issues or []))
        if parsed_issue is not None
    ]
    parsed_unqualified_issues = [
        parsed_issue
        for parsed_issue in (_parse_unqualified_alias_scope_issue(issue) for issue in (issues or []))
        if parsed_issue is not None
    ]
    if (not parsed_issues and not parsed_unqualified_issues) or sqlglot is None or exp is None:
        return sql
    try:
        from sqlglot.optimizer.scope import traverse_scope

        parsed_sql = sqlglot.parse_one(_normalize_sql_text(sql), read="duckdb")
    except Exception:
        return sql

    model_columns: dict[str, set[str]] = {}
    for model in hit_models or []:
        keys = {
            str(model.get("name") or "").strip().lower(),
            str(model.get("table_reference") or "").strip().lower(),
        }
        col_names = {
            str(col.get("name") or "").strip().lower()
            for col in (
                model.get("matched_columns")
                if model.get("matched_columns") is not None
                else model.get("columns")
                or []
            )
            if str(col.get("name") or "").strip()
        }
        for key in keys:
            if key:
                model_columns[key] = col_names

    model_rank = _model_rank_map(hit_models or [])
    selected_owner_map = {
        str(key).strip().lower(): str(value).strip().lower()
        for key, value in ((schema_link_plan or {}).get("selected_owner_map") or {}).items()
        if str(key).strip() and str(value).strip()
    }

    def _owner_key(owner: str) -> tuple[int, str]:
        normalized_owner = str(owner or "").strip().lower()
        return (model_rank.get(normalized_owner, 10_000), normalized_owner)

    def _scope_alias_to_table(scope_obj: Any) -> dict[str, str]:
        alias_to_table: dict[str, str] = {}
        cursor = scope_obj
        while cursor is not None:
            try:
                selected = cursor.selected_sources or {}
            except Exception:
                selected = {}
            for alias_name, source_payload in selected.items():
                alias = str(alias_name or "").strip().lower()
                if not alias or alias in alias_to_table:
                    continue
                source_expr = source_payload[0] if isinstance(source_payload, tuple) and source_payload else source_payload
                table_name = alias
                if isinstance(source_expr, exp.Table):
                    table_name = str(source_expr.name or source_expr.alias_or_name or alias).strip().lower() or alias
                else:
                    fallback_name = str(getattr(source_expr, "name", "") or "").strip().lower()
                    if fallback_name:
                        table_name = fallback_name
                alias_to_table[alias] = table_name
            cursor = getattr(cursor, "parent", None)
        return alias_to_table

    changed = False
    for scope in traverse_scope(parsed_sql):
        alias_to_table = _scope_alias_to_table(scope)
        if not alias_to_table:
            continue
        visible_aliases = set(alias_to_table.keys())
        scope_expression = getattr(scope, "expression", None)
        select_aliases: set[str] = set()
        if isinstance(scope_expression, exp.Select):
            for select_expr in scope_expression.expressions or []:
                alias_name = str(select_expr.alias or "").strip().lower()
                if alias_name:
                    select_aliases.add(alias_name)
        for table_name, column_name, available_aliases in parsed_issues:
            candidate_aliases = [alias for alias in available_aliases if alias in visible_aliases]
            if not candidate_aliases:
                continue
            target_alias = next(
                (alias for alias in candidate_aliases if alias_to_table.get(alias) == table_name),
                None,
            )
            if not target_alias and model_columns:
                owning_aliases = [
                    alias
                    for alias in candidate_aliases
                    if column_name in model_columns.get(alias_to_table.get(alias, ""), set())
                ]
                if len(owning_aliases) == 1:
                    target_alias = owning_aliases[0]
                elif len(owning_aliases) > 1:
                    preferred_owner = selected_owner_map.get(column_name)
                    if preferred_owner:
                        target_alias = next(
                            (alias for alias in owning_aliases if alias_to_table.get(alias, "") == preferred_owner),
                            None,
                        )
                    if not target_alias:
                        target_alias = sorted(
                            owning_aliases,
                            key=lambda alias: (_owner_key(alias_to_table.get(alias, "")), alias),
                        )[0]
            if not target_alias:
                continue
            for column in scope.columns:
                current_table = str(getattr(column, "table", "") or "").strip().lower()
                current_name = str(getattr(column, "name", "") or "").strip().lower()
                if current_table == table_name and current_name == column_name and current_table != target_alias:
                    column.set("table", target_alias)
                    changed = True

        for column_name, available_aliases in parsed_unqualified_issues:
            candidate_aliases = [alias for alias in available_aliases if alias in visible_aliases]
            if not candidate_aliases:
                continue
            target_alias: str | None = None
            preferred_owner = selected_owner_map.get(column_name)
            if preferred_owner:
                target_alias = next(
                    (alias for alias in candidate_aliases if alias_to_table.get(alias, "") == preferred_owner),
                    None,
                )
            if not target_alias and model_columns:
                owning_aliases = [
                    alias
                    for alias in candidate_aliases
                    if column_name in model_columns.get(alias_to_table.get(alias, ""), set())
                ]
                if len(owning_aliases) == 1:
                    target_alias = owning_aliases[0]
                elif len(owning_aliases) > 1:
                    target_alias = sorted(
                        owning_aliases,
                        key=lambda alias: (_owner_key(alias_to_table.get(alias, "")), alias),
                    )[0]
            if not target_alias:
                target_alias = sorted(
                    candidate_aliases,
                    key=lambda alias: (_owner_key(alias_to_table.get(alias, "")), alias),
                )[0]
            if not target_alias:
                continue
            for column in scope.columns:
                current_table = str(getattr(column, "table", "") or "").strip().lower()
                current_name = str(getattr(column, "name", "") or "").strip().lower()
                if current_table or current_name != column_name:
                    continue
                if current_name in select_aliases:
                    continue
                column.set("table", target_alias)
                changed = True

    if not changed:
        return sql
    try:
        rewritten = parsed_sql.sql(dialect="duckdb")
        sqlglot.parse_one(rewritten, read="duckdb")
        return rewritten
    except Exception:
        return sql


def _record_sql_generation_failure(
    *,
    project_id: int,
    question: str,
    failed_sql: Optional[str],
    error_text: str,
    stage: str,
    sql_engine: Optional[str] = None,
    attempt: Optional[int] = None,
    issue_buckets: Optional[dict[str, int]] = None,
    repaired_sql: Optional[str] = None,
    resolved: Optional[bool] = None,
    schema_link_snapshot: Optional[dict[str, Any]] = None,
    sql_plan_snapshot: Optional[dict[str, Any]] = None,
) -> None:
    try:
        with connection_lock():
            con = get_connection()
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata.sql_generation_failures (
                    id INTEGER PRIMARY KEY,
                    project_id INTEGER,
                    question_hash VARCHAR,
                    question TEXT,
                    failed_sql TEXT,
                    error_text TEXT,
                    stage VARCHAR,
                    sql_engine VARCHAR,
                    attempt INTEGER,
                    issue_buckets JSON,
                    schema_link_snapshot JSON,
                    sql_plan_snapshot JSON,
                    repaired_sql TEXT,
                    resolved BOOLEAN,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            con.execute("ALTER TABLE metadata.sql_generation_failures ADD COLUMN IF NOT EXISTS schema_link_snapshot JSON")
            con.execute("ALTER TABLE metadata.sql_generation_failures ADD COLUMN IF NOT EXISTS sql_plan_snapshot JSON")
            next_id = _max_id(con, "metadata.sql_generation_failures")
            question_hash = hashlib.sha256((question or "").encode("utf-8", errors="ignore")).hexdigest()
            con.execute(
                """
                INSERT INTO metadata.sql_generation_failures
                (id, project_id, question_hash, question, failed_sql, error_text, stage, sql_engine, attempt, issue_buckets, schema_link_snapshot, sql_plan_snapshot, repaired_sql, resolved)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    next_id,
                    int(project_id),
                    question_hash,
                    question or "",
                    failed_sql,
                    error_text or "",
                    stage or "unknown",
                    sql_engine,
                    attempt,
                    _json_dumps(issue_buckets or {}),
                    _json_dumps(schema_link_snapshot or {}),
                    _json_dumps(sql_plan_snapshot or {}),
                    repaired_sql,
                    resolved,
                ],
            )
    except Exception:
        LOGGER.debug("Failed to persist sql_generation_failure sample", exc_info=True)


def _build_ambiguous_owner_hint(
    failed_sql: str,
    error: str,
    hit_models: Optional[list[dict[str, Any]]] = None,
    schema_link_plan: Optional[dict[str, Any]] = None,
) -> str:
    ambiguous_parts = [p.strip() for p in str(error or "").split(";") if "belongs on:" in p and "," in p]
    if not ambiguous_parts:
        return ""

    query_tables: set[str] = set()
    try:
        import sqlglot as _sqlglot

        parsed = _sqlglot.parse_one(failed_sql)
        for table_expr in parsed.find_all(_sqlglot.exp.Table):
            table_name = (table_expr.name or "").strip().lower()
            if table_name:
                query_tables.add(table_name)
    except Exception:
        pass

    model_rank = _model_rank_map(hit_models or [])
    selected_owner_map = (schema_link_plan or {}).get("selected_owner_map") or {}

    def _rank(owner: str) -> int:
        return model_rank.get(owner.lower(), 10_000)

    lines: list[str] = []
    for part in ambiguous_parts:
        left, right = part.split("belongs on:", 1)
        col_ref = left.split("(", 1)[0].strip()
        owners = [o.strip() for o in right.strip(" )").split(",") if o.strip()]
        if not owners:
            continue
        owners_lower = [o.lower() for o in owners]
        col_name = col_ref.split(".")[-1].lower()
        preselected = str(selected_owner_map.get(col_name) or "").lower()
        if preselected and preselected in owners_lower:
            chosen = preselected
            reason = "pre-selected by schema linking plan"
            lines.append(f"- {col_ref}: prefer owner '{chosen}' ({reason})")
            continue
        in_query = [o for o in owners_lower if o in query_tables]
        chosen = None
        reason = ""
        if len(in_query) == 1:
            chosen = in_query[0]
            reason = "only owner already referenced in query tables"
        elif len(in_query) > 1:
            chosen = sorted(in_query, key=_rank)[0]
            reason = "multiple owners referenced; pick the highest-ranked semantic hit"
        else:
            chosen = sorted(owners_lower, key=_rank)[0]
            reason = "owner chosen by semantic-hit ranking"
        if chosen:
            lines.append(f"- {col_ref}: prefer owner '{chosen}' ({reason})")

    if not lines:
        return ""
    return "\nAmbiguous owner resolution hints:\n" + "\n".join(lines)


def _rehint_columns(
    sql: str,
    models: list[dict[str, Any]],
    bad_columns: list[str] | None = None,
    owner_preferences: Optional[dict[str, str]] = None,
) -> str:
    try:
        import sqlglot
        parsed = sqlglot.parse_one(sql)
        alias_map = _build_alias_map(sql)
        for table_expr in parsed.find_all(sqlglot.exp.Table):
            name = table_expr.name
            if name:
                alias_map[name.lower()] = name.lower()
            if table_expr.alias:
                alias_map[table_expr.alias.lower()] = name.lower() if name else table_expr.alias.lower()
        cte_source_map: dict[str, str] = {}
        for cte in parsed.find_all(sqlglot.exp.CTE):
            cte_alias = (cte.alias or "").lower()
            inner = cte.this
            if inner and hasattr(inner, "find_all"):
                for tbl in inner.find_all(sqlglot.exp.Table):
                    inner_name = (tbl.name or "").lower()
                    if inner_name:
                        cte_source_map[cte_alias] = inner_name
                        if cte_alias not in alias_map:
                            alias_map[cte_alias] = inner_name
                        break
        model_columns: dict[str, set[str]] = {}
        model_by_col: dict[str, list[str]] = {}
        for model in models:
            mname = model.get("table_reference", model.get("name", "")).lower()
            mname_alt = model.get("name", "").lower()
            cols = set()
            for col in model.get("columns", []):
                cn = col.get("name", "").lower()
                if cn:
                    cols.add(cn)
                    for mn in (mname, mname_alt):
                        if mn and mn not in model_by_col.setdefault(cn, []):
                            model_by_col[cn].append(mn)
            for col in model.get("matched_columns") or []:
                cn = col.get("name", "").lower()
                if cn and cn not in cols:
                    cols.add(cn)
                    for mn in (mname, mname_alt):
                        if mn and mn not in model_by_col.setdefault(cn, []):
                            model_by_col[cn].append(mn)
            model_columns[mname] = cols
            if mname_alt != mname:
                model_columns[mname_alt] = cols
        model_names = set(model_columns.keys())
        all_column_names = set(model_by_col.keys())
        cte_names = set()
        try:
            for cte in parsed.find_all(sqlglot.exp.CTE):
                alias = (cte.alias or "").lower()
                if alias:
                    cte_names.add(alias)
        except Exception:
            pass
        owner_preferences = {str(k).lower(): str(v).lower() for k, v in (owner_preferences or {}).items()}
        changed = False

        def _preferred_owner(col_name: str, owners: list[str]) -> str | None:
            preferred = owner_preferences.get(col_name.lower())
            if preferred and preferred in owners:
                return preferred
            return None

        for col_node in list(parsed.find_all(sqlglot.exp.Column)):
            table = col_node.table
            col_name = col_node.name.lower()
            if not table or not col_name:
                continue
            table_lower = table.lower()
            resolved_table = alias_map.get(table_lower, table_lower)
            is_known_table = resolved_table in model_names or table_lower in model_names
            is_cte_or_subquery = table_lower in cte_names
            cte_source = cte_source_map.get(table_lower)
            if is_cte_or_subquery:
                if cte_source and cte_source in model_columns:
                    if col_name not in model_columns[cte_source]:
                        fuzzy_match = _fuzzy_column_match(col_name, all_column_names)
                        if fuzzy_match:
                            col_node.set("this", sqlglot.to_identifier(fuzzy_match))
                            changed = True
                continue
            if not is_known_table:
                owners = model_by_col.get(col_name, [])
                single_owners = [o for o in owners if o in model_names]
                preferred_owner = _preferred_owner(col_name, single_owners)
                if preferred_owner:
                    single_owners = [preferred_owner]
                if len(single_owners) != 1:
                    fuzzy_match = _fuzzy_column_match(col_name, all_column_names)
                    if fuzzy_match:
                        fuzzy_owners = model_by_col.get(fuzzy_match, [])
                        single_owners = [o for o in fuzzy_owners if o in model_names]
                        if len(single_owners) == 1:
                            col_node.set("this", sqlglot.to_identifier(fuzzy_match))
                            correct_model = single_owners[0]
                            new_alias = None
                            for tbl in parsed.find_all(sqlglot.exp.Table):
                                tbl_name = tbl.name.lower()
                                tbl_alias = (tbl.alias or "").lower()
                                if tbl_name == correct_model or tbl_alias == correct_model:
                                    new_alias = tbl.alias if tbl.alias else tbl.name
                                    break
                            if new_alias:
                                col_node.set("table", new_alias)
                            changed = True
                if len(single_owners) == 1:
                    correct_model = single_owners[0]
                    new_alias = None
                    for tbl in parsed.find_all(sqlglot.exp.Table):
                        tbl_name = tbl.name.lower()
                        tbl_alias = (tbl.alias or "").lower()
                        if tbl_name == correct_model or tbl_alias == correct_model:
                            new_alias = tbl.alias if tbl.alias else tbl.name
                            break
                    if new_alias and new_alias.lower() != table_lower:
                        col_node.set("table", new_alias)
                        changed = True
                continue
            model_key = resolved_table if resolved_table in model_names else table_lower
            if col_name in model_columns.get(model_key, set()):
                continue
            owners = model_by_col.get(col_name, [])
            single_owners = [o for o in owners if o in model_names]
            preferred_owner = _preferred_owner(col_name, single_owners)
            if preferred_owner:
                single_owners = [preferred_owner]
            if len(single_owners) == 1:
                correct_model = single_owners[0]
                new_alias = None
                for tbl in parsed.find_all(sqlglot.exp.Table):
                    tbl_name = tbl.name.lower()
                    tbl_alias = (tbl.alias or "").lower()
                    if tbl_name == correct_model or tbl_alias == correct_model:
                        new_alias = tbl.alias if tbl.alias else tbl.name
                        break
                if new_alias and new_alias != table:
                    col_node.set("table", new_alias)
                    changed = True
                continue
            if owners:
                continue
            fuzzy_match = _fuzzy_column_match(col_name, all_column_names)
            if fuzzy_match:
                fuzzy_owners = model_by_col.get(fuzzy_match, [])
                fuzzy_single = [o for o in fuzzy_owners if o in model_names]
                if len(fuzzy_single) == 1:
                    correct_model = fuzzy_single[0]
                    col_node.set("this", sqlglot.to_identifier(fuzzy_match))
                    new_alias = None
                    for tbl in parsed.find_all(sqlglot.exp.Table):
                        tbl_name = tbl.name.lower()
                        tbl_alias = (tbl.alias or "").lower()
                        if tbl_name == correct_model or tbl_alias == correct_model:
                            new_alias = tbl.alias if tbl.alias else tbl.name
                            break
                    if new_alias:
                        col_node.set("table", new_alias)
                    changed = True
        if not changed:
            return sql
        result = parsed.sql(dialect="duckdb")
        return result if result.strip() else sql
    except Exception:
        return sql


def _validate_sql_group_by(sql: str, dimensions: list[str], hit_models: Optional[list[dict[str, Any]]] = None, resolved: Optional[dict] = None) -> list[str]:
    if not dimensions:
        return []
    if sqlglot is None or exp is None:
        return []
    try:
        parsed = sqlglot.parse_one(_normalize_sql_text(sql), read="duckdb")
        select_exprs = list(parsed.args.get("expressions") or [])
        group = parsed.args.get("group")

        def _marker_sets_overlap(required_markers: set[str], candidate_markers: set[str]) -> bool:
            if not required_markers or not candidate_markers:
                return False
            if any(marker in candidate_markers for marker in required_markers):
                return True
            for required in required_markers:
                for candidate in candidate_markers:
                    if not required or not candidate:
                        continue
                    if required in candidate or candidate in required:
                        return True
                    required_parts = {part for part in re.split(r"[\s_]+", required) if part}
                    candidate_parts = {part for part in re.split(r"[\s_]+", candidate) if part}
                    if required_parts and candidate_parts and required_parts & candidate_parts:
                        return True
            return False

        def _collect_expr_markers(expr_node: exp.Expression) -> set[str]:
            markers: set[str] = set()
            try:
                markers.update(_identifier_markers(expr_node.sql(dialect="duckdb")))
            except Exception:
                pass
            if isinstance(expr_node, exp.Column):
                if expr_node.name:
                    markers.update(_identifier_markers(expr_node.name))
                if expr_node.table and expr_node.name:
                    markers.update(_identifier_markers(f"{expr_node.table}.{expr_node.name}"))
            for col in expr_node.find_all(exp.Column):
                if col.name:
                    markers.update(_identifier_markers(col.name))
                if col.table and col.name:
                    markers.update(_identifier_markers(f"{col.table}.{col.name}"))
            return markers

        group_markers: set[str] = set()
        if group:
            for group_expr in group.expressions or []:
                if isinstance(group_expr, exp.Literal):
                    ordinal = str(group_expr.this or "").strip()
                    if ordinal.isdigit():
                        idx = int(ordinal) - 1
                        if 0 <= idx < len(select_exprs):
                            mapped_expr = select_exprs[idx]
                            if isinstance(mapped_expr, exp.Alias):
                                mapped_expr = mapped_expr.this
                            group_markers.update(_collect_expr_markers(mapped_expr))
                    continue
                group_markers.update(_collect_expr_markers(group_expr))

        name_to_aliases: dict[str, set[str]] = {}
        column_alias_lookup: dict[tuple[str, str], set[str]] = {}
        if hit_models:
            for model in hit_models:
                columns = model.get("matched_columns") if model.get("matched_columns") is not None else model.get("columns", [])
                model_name = str(model.get("name") or "").strip()
                table_reference = str(model.get("table_reference") or "").strip()
                model_keys = {
                    value.lower()
                    for value in (model_name, table_reference)
                    if str(value or "").strip()
                }
                for col in columns:
                    cn = str(col.get("name") or "").strip()
                    dn = str(col.get("display_name") or "").strip()
                    if not cn:
                        continue
                    aliases: set[str] = set()
                    aliases.update(_identifier_markers(cn))
                    if dn:
                        aliases.update(_identifier_markers(dn))
                    for alias in _column_alias_strings(col):
                        aliases.update(_identifier_markers(alias))
                    if cn and model_name:
                        aliases.update(_identifier_markers(f"{model_name}.{cn}"))
                    if cn and table_reference:
                        aliases.update(_identifier_markers(f"{table_reference}.{cn}"))
                    keys = _identifier_markers(cn) | _identifier_markers(dn)
                    if not keys and cn:
                        keys = {cn.lower()}
                    for key in keys:
                        name_to_aliases.setdefault(key, set()).update(aliases)
                    col_key = cn.lower()
                    for model_key in model_keys:
                        column_alias_lookup.setdefault((model_key, col_key), set()).update(aliases)
                    column_alias_lookup.setdefault(("", col_key), set()).update(aliases)

        def _matches_group(marker_candidates: set[str]) -> bool:
            return _marker_sets_overlap(marker_candidates, group_markers)

        def _is_join_key_like(column_name: str) -> bool:
            normalized = str(column_name or "").strip().lower()
            if not normalized:
                return False
            if normalized in {"id", "key"}:
                return True
            return normalized.endswith(("_id", "_no", "_key", "_code"))

        resolved_entries = list((resolved or {}).get("dimensions_resolved") or [])
        resolved_targets: list[dict[str, Any]] = []
        resolved_column_counts: dict[str, int] = {}
        mapped_dimension_indexes: set[int] = set()
        for entry in resolved_entries:
            col_name = str(entry.get("column") or "").strip().lower()
            model_name = str(entry.get("model") or "").strip().lower()
            if not col_name:
                continue
            markers = set(_identifier_markers(col_name))
            if model_name:
                markers.update(_identifier_markers(f"{model_name}.{col_name}"))
                markers.update(column_alias_lookup.get((model_name, col_name), set()))
            markers.update(column_alias_lookup.get(("", col_name), set()))
            if not markers:
                continue
            label = f"{model_name}.{col_name}" if model_name else col_name
            resolved_targets.append({"label": label, "markers": markers, "column": col_name})
            resolved_column_counts[col_name] = int(resolved_column_counts.get(col_name) or 0) + 1

            for idx, dim_value in enumerate(dimensions):
                dim_markers = _identifier_markers(dim_value)
                if not dim_markers:
                    raw_dim = str(dim_value or "").strip().lower()
                    if raw_dim:
                        dim_markers = {raw_dim}
                if _marker_sets_overlap(dim_markers, markers):
                    mapped_dimension_indexes.add(idx)

        missing: list[str] = []
        seen_missing: set[str] = set()
        matched_join_key_columns: set[str] = set()
        resolved_target_matches: list[tuple[dict[str, Any], bool]] = []
        for target in resolved_targets:
            is_matched = _matches_group(set(target.get("markers") or set()))
            resolved_target_matches.append((target, is_matched))
            if is_matched and _is_join_key_like(str(target.get("column") or "")):
                matched_join_key_columns.add(str(target.get("column") or "").strip().lower())

        for target, is_matched in resolved_target_matches:
            if is_matched:
                continue
            target_column = str(target.get("column") or "").strip().lower()
            if (
                target_column
                and _is_join_key_like(target_column)
                and target_column in matched_join_key_columns
                and int(resolved_column_counts.get(target_column) or 0) > 1
            ):
                continue
            label = str(target.get("label") or "").strip()
            if not label or label in seen_missing:
                continue
            seen_missing.add(label)
            missing.append(label)

        downgraded_cjk_dimensions: list[str] = []
        for idx, d in enumerate(dimensions):
            if idx in mapped_dimension_indexes:
                continue
            dim_text = str(d or "").strip()
            dim_markers = _identifier_markers(dim_text)
            if not dim_markers:
                raw_dim = dim_text.lower()
                if raw_dim:
                    dim_markers = {raw_dim}

            if _matches_group(dim_markers):
                continue

            aliases: set[str] = set()
            for marker in dim_markers:
                aliases.update(name_to_aliases.get(marker) or set())

            if aliases and _matches_group(aliases):
                continue

            if _contains_cjk(dim_text) and not aliases:
                downgraded_cjk_dimensions.append(dim_text)
                continue

            if dim_text and dim_text not in seen_missing:
                seen_missing.add(dim_text)
                missing.append(dim_text)

        if downgraded_cjk_dimensions:
            LOGGER.warning(
                "GROUP BY validation downgraded unresolved CJK dimension(s) to warning: %s",
                downgraded_cjk_dimensions,
            )

        if missing:
            if not group:
                return [f"SQL has no GROUP BY but {len(missing)} dimension(s) required: {', '.join(missing)}"]
            return [f"Dimension(s) missing from GROUP BY: {', '.join(missing)}"]
        return []
    except Exception:
        return []


def _marker_sets_overlap(required_markers: set[str], candidate_markers: set[str]) -> bool:
    if not required_markers or not candidate_markers:
        return False
    if any(marker in candidate_markers for marker in required_markers):
        return True
    for required in required_markers:
        for candidate in candidate_markers:
            if not required or not candidate:
                continue
            if required in candidate or candidate in required:
                return True
            required_parts = {part for part in re.split(r"[\s_]+", required) if part}
            candidate_parts = {part for part in re.split(r"[\s_]+", candidate) if part}
            if required_parts and candidate_parts and required_parts & candidate_parts:
                return True
    return False


def _collect_expr_markers(expr_node: exp.Expression) -> set[str]:
    markers: set[str] = set()
    try:
        markers.update(_identifier_markers(expr_node.sql(dialect="duckdb")))
    except Exception:
        pass
    if isinstance(expr_node, exp.Column):
        if expr_node.name:
            markers.update(_identifier_markers(expr_node.name))
        if expr_node.table and expr_node.name:
            markers.update(_identifier_markers(f"{expr_node.table}.{expr_node.name}"))
    for col in expr_node.find_all(exp.Column):
        if col.name:
            markers.update(_identifier_markers(col.name))
        if col.table and col.name:
            markers.update(_identifier_markers(f"{col.table}.{col.name}"))
    return markers


def _collect_group_markers(select_expr: exp.Select) -> set[str]:
    group_markers: set[str] = set()
    group = select_expr.args.get("group")
    if not group:
        return group_markers
    select_exprs = list(select_expr.args.get("expressions") or [])
    for group_expr in group.expressions or []:
        if isinstance(group_expr, exp.Literal):
            ordinal = str(group_expr.this or "").strip()
            if ordinal.isdigit():
                idx = int(ordinal) - 1
                if 0 <= idx < len(select_exprs):
                    mapped_expr = select_exprs[idx]
                    if isinstance(mapped_expr, exp.Alias):
                        mapped_expr = mapped_expr.this
                    group_markers.update(_collect_expr_markers(mapped_expr))
            continue
        group_markers.update(_collect_expr_markers(group_expr))
    return group_markers


def _select_aggregate_aliases(select_expr: exp.Select) -> set[str]:
    aliases: set[str] = set()
    for expr_node in select_expr.args.get("expressions") or []:
        if not isinstance(expr_node, exp.Alias):
            continue
        alias_name = str(expr_node.alias or "").strip().lower()
        if not alias_name:
            continue
        if any(isinstance(node, exp.AggFunc) for node in expr_node.this.find_all(exp.AggFunc)):
            aliases.add(alias_name)
    return aliases


def _column_in_aggregate_scope(column_node: exp.Column, root_expr: exp.Expression) -> bool:
    parent = column_node.parent
    while parent is not None:
        if isinstance(parent, exp.AggFunc):
            return True
        if parent is root_expr:
            break
        if isinstance(parent, exp.Select):
            break
        parent = parent.parent
    return False


def _column_matches_group_context(
    column_node: exp.Column,
    group_markers: set[str],
    aggregate_aliases: set[str],
) -> bool:
    column_name = str(column_node.name or "").strip().lower()
    table_name = str(column_node.table or "").strip()
    if not table_name and column_name and column_name in aggregate_aliases:
        return True
    markers = _collect_expr_markers(column_node)
    return _marker_sets_overlap(markers, group_markers)


def _collect_non_aggregated_columns(expr_node: exp.Expression) -> list[exp.Column]:
    columns: list[exp.Column] = []
    seen: set[str] = set()
    for column_node in expr_node.find_all(exp.Column):
        if _column_in_aggregate_scope(column_node, expr_node):
            continue
        key = str(column_node.sql(dialect="duckdb") or "").strip().lower()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        columns.append(column_node)
    return columns


def _apply_group_by_aggregation_rewrite_rules(sql: str) -> tuple[str, list[str]]:
    if sqlglot is None or exp is None:
        return sql, []
    normalized_sql = _normalize_sql_text(sql)
    try:
        parsed = sqlglot.parse_one(normalized_sql, read="duckdb")
    except Exception:
        return sql, []

    changed = False
    rewrite_notes: list[str] = []
    for select_expr in list(parsed.find_all(exp.Select)):
        has_aggregate = any(isinstance(node, exp.AggFunc) for node in select_expr.find_all(exp.AggFunc))
        if not has_aggregate and not select_expr.args.get("group"):
            continue

        group_markers = _collect_group_markers(select_expr)
        aggregate_aliases = _select_aggregate_aliases(select_expr)

        order_expr = select_expr.args.get("order")
        if isinstance(order_expr, exp.Order):
            for order_item in order_expr.expressions or []:
                candidate_expr = order_item.this
                if isinstance(candidate_expr, exp.Literal):
                    ordinal = str(candidate_expr.this or "").strip()
                    if ordinal.isdigit():
                        continue
                if any(isinstance(node, exp.AggFunc) for node in candidate_expr.find_all(exp.AggFunc)):
                    continue
                candidate_columns = _collect_non_aggregated_columns(candidate_expr)
                if not candidate_columns:
                    continue
                needs_wrap = any(
                    not _column_matches_group_context(column_node, group_markers, aggregate_aliases)
                    for column_node in candidate_columns
                )
                if not needs_wrap:
                    continue
                order_item.set("this", exp.Max(this=candidate_expr.copy()))
                changed = True
                rewrite_notes.append(
                    f"ORDER BY {candidate_expr.sql(dialect='duckdb')} -> MAX({candidate_expr.sql(dialect='duckdb')})"
                )

        having_expr = select_expr.args.get("having")
        if isinstance(having_expr, exp.Having) and having_expr.this is not None:
            having_changed = False

            def _rewrite_having(node: exp.Expression) -> exp.Expression:
                nonlocal having_changed
                if not isinstance(node, exp.Column):
                    return node
                if _column_in_aggregate_scope(node, having_expr.this):
                    return node
                if _column_matches_group_context(node, group_markers, aggregate_aliases):
                    return node
                having_changed = True
                return exp.Max(this=node.copy())

            rewritten_having = having_expr.this.transform(_rewrite_having)
            if having_changed:
                having_expr.set("this", rewritten_having)
                changed = True
                rewrite_notes.append("HAVING non-grouped column(s) wrapped with MAX()")

    if not changed:
        return sql, []
    try:
        rewritten_sql = parsed.sql(dialect="duckdb")
        sqlglot.parse_one(rewritten_sql, read="duckdb")
        return rewritten_sql, rewrite_notes
    except Exception:
        return sql, []


def _auto_repair_aggregation_issues(
    sql: str,
    *,
    dimensions: Optional[list[str]],
    hit_models: Optional[list[dict[str, Any]]],
    resolved: Optional[dict[str, Any]],
) -> tuple[str, list[str]]:
    agg_issues = _validate_sql_aggregation(sql)
    if not agg_issues:
        return sql, []

    rewritten_sql, rewrite_notes = _apply_group_by_aggregation_rewrite_rules(sql)
    if rewritten_sql == sql:
        return sql, agg_issues

    rewritten_columns = _validate_sql_columns(rewritten_sql, hit_models or [])
    rewritten_group_issues = _validate_sql_group_by(
        rewritten_sql,
        dimensions or [],
        hit_models=hit_models,
        resolved=resolved,
    )
    rewritten_agg_issues = _validate_sql_aggregation(rewritten_sql)
    if (rewritten_columns is None or not rewritten_columns) and not rewritten_group_issues and not rewritten_agg_issues:
        LOGGER.info(
            "Auto-repaired aggregation clause issues with local rules: %s",
            rewrite_notes or ["aggregation clause rewrite"],
        )
        return rewritten_sql, []

    LOGGER.warning(
        "Aggregation clause auto-rewrite incomplete: columns=%s group=%s agg=%s notes=%s",
        rewritten_columns,
        rewritten_group_issues,
        rewritten_agg_issues,
        rewrite_notes,
    )
    return sql, agg_issues


def _is_group_by_aggregate_binder_error(message: str) -> bool:
    lowered = str(message or "").lower()
    if not lowered:
        return False
    return "must appear in the group by clause" in lowered and "aggregate function" in lowered


def _validate_sql_aggregation(sql: str) -> list[str]:
    if not sql:
        return []
    if sqlglot is None or exp is None:
        return []
    try:
        parsed = sqlglot.parse_one(_normalize_sql_text(sql), read="duckdb")
        issues = []
        seen: set[str] = set()

        for select_expr in list(parsed.find_all(exp.Select)):
            select_columns = list(select_expr.args.get("expressions") or [])
            if not select_columns:
                continue
            has_aggregate = any(isinstance(node, exp.AggFunc) for node in select_expr.find_all(exp.AggFunc))
            if not has_aggregate and not select_expr.args.get("group"):
                continue

            group_markers = _collect_group_markers(select_expr)
            aggregate_aliases = _select_aggregate_aliases(select_expr)

            for select_item in select_columns:
                candidate_expr = select_item.this if isinstance(select_item, exp.Alias) else select_item
                for column_node in _collect_non_aggregated_columns(candidate_expr):
                    if _column_matches_group_context(column_node, group_markers, aggregate_aliases):
                        continue
                    issue = f"Column '{column_node.sql(dialect='duckdb')}' in SELECT is not aggregated and not in GROUP BY"
                    if issue in seen:
                        continue
                    seen.add(issue)
                    issues.append(issue)

            having_expr = select_expr.args.get("having")
            if isinstance(having_expr, exp.Having) and having_expr.this is not None:
                for column_node in _collect_non_aggregated_columns(having_expr.this):
                    if _column_matches_group_context(column_node, group_markers, aggregate_aliases):
                        continue
                    issue = f"Column '{column_node.sql(dialect='duckdb')}' in HAVING is not aggregated and not in GROUP BY"
                    if issue in seen:
                        continue
                    seen.add(issue)
                    issues.append(issue)

            order_expr = select_expr.args.get("order")
            if isinstance(order_expr, exp.Order):
                for order_item in order_expr.expressions or []:
                    candidate_expr = order_item.this
                    if isinstance(candidate_expr, exp.Literal):
                        ordinal = str(candidate_expr.this or "").strip()
                        if ordinal.isdigit():
                            continue
                    if any(isinstance(node, exp.AggFunc) for node in candidate_expr.find_all(exp.AggFunc)):
                        continue
                    for column_node in _collect_non_aggregated_columns(candidate_expr):
                        if _column_matches_group_context(column_node, group_markers, aggregate_aliases):
                            continue
                        issue = f"Column '{column_node.sql(dialect='duckdb')}' in ORDER BY is not aggregated and not in GROUP BY"
                        if issue in seen:
                            continue
                        seen.add(issue)
                        issues.append(issue)
        return issues
    except Exception:
        return []


def _validate_duckdb_sql_syntax(sql: str) -> list[str]:
    normalized_sql = _normalize_sql_candidate(sql)
    if not normalized_sql:
        return ["SQL is empty"]
    conn = duckdb.connect(":memory:")
    try:
        try:
            conn.execute(f"EXPLAIN {normalized_sql}")
            return []
        except Exception as exc:
            if "[" in normalized_sql and "]" in normalized_sql:
                bracket_rewritten = _rewrite_bracket_identifiers_for_duckdb(normalized_sql)
                if bracket_rewritten != normalized_sql:
                    try:
                        conn.execute(f"EXPLAIN {bracket_rewritten}")
                        return []
                    except Exception as rewritten_exc:
                        exc = rewritten_exc
            message = _sanitize_error_message(exc, max_length=600)
            lower = message.lower()
            if "duplicate alias" in lower or "alias already used" in lower:
                return [message]
            parser_exception_cls = getattr(duckdb, "ParserException", None)
            if parser_exception_cls and isinstance(exc, parser_exception_cls):
                return [message]
            parser_markers = (
                "parser error",
                "syntax error",
                "unexpected token",
                "unterminated",
                "expecting",
            )
            if any(marker in lower for marker in parser_markers):
                return [message]
            # Binder/runtime errors are ignored; this guard is parser-focused.
            return []
    finally:
        conn.close()


def _validate_sql_syntax_for_project(sql: str, project_id: int) -> list[str]:
    normalized_sql = _normalize_sql_candidate(sql)
    if not normalized_sql:
        return ["SQL is empty"]
    ds_type = _primary_datasource_type(project_id)
    if ds_type in {"duckdb", "sample"}:
        return _validate_duckdb_sql_syntax(normalized_sql)
    if sqlglot is None:
        return []
    try:
        sqlglot.parse_one(_normalize_sql_text(normalized_sql), read=_dialect_for_ds(ds_type))
        return []
    except Exception as exc:
        return [_sanitize_error_message(exc, max_length=600)]


_STRING_TYPES = frozenset({"VARCHAR", "TEXT", "CHAR", "BPCHAR", "STRING"})
_NUMERIC_TYPES = frozenset({"INTEGER", "BIGINT", "SMALLINT", "TINYINT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL", "INT", "INT2", "INT4", "INT8", "FLOAT4", "FLOAT8"})


def _model_column_types(models: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for model in models:
        mname = model.get("table_reference", model.get("name", "")).lower()
        mname_alt = model.get("name", "").lower()
        cols: dict[str, str] = {}
        for col in model.get("columns", []):
            cn = col.get("name", "").lower()
            ct = col.get("type", "").upper()
            if cn:
                cols[cn] = ct
        for col in model.get("matched_columns") or []:
            cn = col.get("name", "").lower()
            ct = col.get("type", "").upper()
            if cn and cn not in cols:
                cols[cn] = ct
        result[mname] = cols
        if mname_alt != mname:
            result[mname_alt] = cols
    return result


def _fix_type_mismatch_multiply(sql: str, models: list[dict[str, Any]]) -> str:
    try:
        import sqlglot
        from sqlglot import exp
        parsed = sqlglot.parse_one(sql)
        if not isinstance(parsed, exp.Select):
            return sql
        col_types = _model_column_types(models)
        alias_map = _build_alias_map(sql)
        for table_expr in parsed.find_all(exp.Table):
            name = table_expr.name
            if name:
                alias_map[name.lower()] = name.lower()
            if table_expr.alias:
                alias_map[table_expr.alias.lower()] = name.lower() if name else table_expr.alias.lower()
        for cte in parsed.find_all(exp.CTE):
            cte_alias = (cte.alias or "").lower()
            inner = cte.this
            if inner and hasattr(inner, "find_all"):
                for tbl in inner.find_all(sqlglot.exp.Table):
                    inner_name = (tbl.name or "").lower()
                    if inner_name:
                        alias_map[cte_alias] = inner_name
                        break

        def _col_type(col_node: exp.Column) -> str | None:
            tbl = (col_node.table or "").lower()
            col_name = col_node.name.lower()
            if tbl:
                resolved = alias_map.get(tbl, tbl)
                for key in (resolved, tbl):
                    if key and key in col_types:
                        result = col_types[key].get(col_name)
                        if result:
                            return result
            for model_types in col_types.values():
                if col_name in model_types and model_types[col_name]:
                    return model_types[col_name]
            return None

        def _is_inside_aggregate(node: exp.Expression) -> bool:
            parent = node.parent
            while parent:
                if isinstance(parent, (exp.Sum, exp.Avg, exp.Count, exp.Max, exp.Min, exp.AggFunc)):
                    return True
                parent = parent.parent
            return False

        def _has_aggregate_descendant(node: exp.Expression) -> bool:
            return any(isinstance(n, (exp.Sum, exp.Avg, exp.Count, exp.Max, exp.Min)) for n in node.walk())

        select_exprs = list(parsed.args.get("expressions") or [])
        replacements: list[tuple[int, exp.Expression, exp.Expression]] = []
        for sel_expr in select_exprs:
            for mul_node in list(sel_expr.find_all(exp.Mul)):
                if _is_inside_aggregate(mul_node):
                    continue
                left = mul_node.left
                right = mul_node.right
                left_has_agg = _has_aggregate_descendant(left)
                right_has_agg = _has_aggregate_descendant(right)
                string_col_side = None
                if isinstance(left, exp.Column) and not left_has_agg and right_has_agg:
                    ct = _col_type(left)
                    if ct and ct in _STRING_TYPES:
                        string_col_side = "left"
                elif isinstance(right, exp.Column) and not right_has_agg and left_has_agg:
                    ct = _col_type(right)
                    if ct and ct in _STRING_TYPES:
                        string_col_side = "right"
                elif isinstance(left, exp.Column) and isinstance(right, exp.Column):
                    if not (left_has_agg or right_has_agg):
                        left_ct = _col_type(left) or ""
                        right_ct = _col_type(right) or ""
                        if left_ct in _STRING_TYPES or right_ct in _STRING_TYPES:
                            string_col_side = "left" if left_ct in _STRING_TYPES else "right"
                if string_col_side:
                    if isinstance(sel_expr, exp.Alias):
                        alias_name = sel_expr.alias
                    else:
                        alias_name = None
                    if string_col_side == "left":
                        dim_expr = left
                        agg_expr = right
                    else:
                        dim_expr = right
                        agg_expr = left
                    dim_select = dim_expr
                    agg_select = exp.Alias(this=agg_expr, alias=sqlglot.to_identifier(alias_name)) if alias_name else agg_expr
                    idx = parsed.args["expressions"].index(sel_expr)
                    replacements.append((idx, dim_select, agg_select))
                    break

        if not replacements:
            return sql

        for idx, dim_select, agg_select in sorted(replacements, key=lambda r: r[0], reverse=True):
            parsed.args["expressions"][idx] = dim_select
            parsed.args["expressions"].insert(idx + 1, agg_select)

        result = parsed.sql(dialect="duckdb")
        try:
            sqlglot.parse_one(result)
            return result
        except Exception:
            return sql
    except Exception:
        return sql


def _estimate_sql_generation_complexity(analysis: Optional[dict[str, Any]], semantic_hits: Optional[dict[str, Any]]) -> int:
    a = _normalize_question_analysis(analysis)
    hits = semantic_hits or {}
    score = 0
    tier = str(a.get("tier") or "").lower()
    if tier == "compound":
        score += 3
    elif tier == "multi_dimension":
        score += 2
    if len(a.get("sub_questions") or []) >= 2:
        score += 2
    if len(a.get("dimensions") or []) >= 2:
        score += 1
    if len((hits.get("models") or [])) >= 4:
        score += 1
    if hits.get("broad_match"):
        score += 1
    return score


def _auto_complete_single_cte_main_select(sql: str) -> str:
    normalized = _normalize_sql_candidate(sql)
    if not normalized or not normalized.strip().upper().startswith("WITH"):
        return normalized
    if sqlglot is None:
        return normalized
    try:
        sqlglot.parse_one(normalized, read="duckdb")
        return normalized
    except Exception:
        pass
    header = re.match(
        r"^\s*WITH\s+(?:RECURSIVE\s+)?([A-Za-z_][\w$]*)\s+AS\s*\(",
        normalized,
        flags=re.IGNORECASE,
    )
    if not header:
        return normalized
    cte_name = str(header.group(1) or "").strip()
    if not cte_name:
        return normalized
    index = header.end()
    depth = 1
    in_single = False
    in_double = False
    in_backtick = False
    while index < len(normalized):
        ch = normalized[index]
        if ch == "'" and not in_double and not in_backtick:
            if in_single and index + 1 < len(normalized) and normalized[index + 1] == "'":
                index += 2
                continue
            in_single = not in_single
        elif ch == '"' and not in_single and not in_backtick:
            if in_double and index + 1 < len(normalized) and normalized[index + 1] == '"':
                index += 2
                continue
            in_double = not in_double
        elif ch == "`" and not in_single and not in_double:
            in_backtick = not in_backtick
        elif not in_single and not in_double and not in_backtick:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    index += 1
                    break
        index += 1
    if depth != 0:
        return normalized
    trailing = normalized[index:].strip()
    if trailing.endswith(";"):
        trailing = trailing[:-1].strip()
    if trailing:
        return normalized
    candidate = f"{normalized}\nSELECT * FROM {_quote_identifier(cte_name)}"
    try:
        sqlglot.parse_one(candidate, read="duckdb")
        return candidate
    except Exception:
        return normalized


def _validate_no_orphaned_cte(sql: str) -> list[str]:
    if not sql or not sql.strip().upper().startswith("WITH"):
        return []
    try:
        import sqlglot
        parsed = sqlglot.parse_one(sql)
        with_ = parsed.args.get("with_")
        if not with_:
            return []
        cte_defs = {cte.alias.lower(): cte for cte in with_.expressions}
        cte_reference_names = set()
        for table in parsed.find_all(sqlglot.exp.Table):
            cte_reference_names.add(table.name.lower())
        for subq in parsed.find_all(sqlglot.exp.Subquery):
            if subq.alias:
                cte_reference_names.add(subq.alias.lower())
        orphans = []
        for cte_name in cte_defs:
            if cte_name not in cte_reference_names:
                own_cte = cte_defs[cte_name]
                referenced_in_body = False
                for inner_table in own_cte.find_all(sqlglot.exp.Table):
                    if inner_table.name.lower() == cte_name:
                        referenced_in_body = True
                        break
                if referenced_in_body:
                    continue
                body_text = str(own_cte.sql()) if hasattr(own_cte, 'sql') else ''
                sql_rest = sql.lower().split(body_text.lower(), 1)[-1] if body_text else ''
                if cte_name in sql_rest:
                    continue
                orphans.append(cte_name)
        if orphans:
            return [f"CTE(s) defined but never referenced: {', '.join(sorted(orphans))}"]
        return []
    except Exception as exc:
        return [f"CTE SQL syntax is invalid: {_sanitize_error_message(exc, max_length=220)}"]


def _syntax_failure_result(
    sql_engine: str,
    last_errors: list[str],
    retrieved_tables: list[str],
) -> dict[str, Any]:
    return {
        "sql": None,
        "summary": "LLM returned SQL with syntax errors after retries.",
        "reasoning": "; ".join(last_errors),
        "retrieved_tables": retrieved_tables,
        "configured": True,
        "sql_engine": f"{sql_engine}_failed",
    }


def _decompose_merge_sql(
    question: str,
    project_id: int,
    analysis: dict,
    semantic_context: str,
    retrieved_tables: list[str],
    semantic_hits: dict,
    previous_questions: Optional[list[str]] = None,
    language: Optional[str] = None,
    knowledge_context: Optional[str] = None,
    resolved: Optional[dict] = None,
    schema_link_plan: Optional[dict[str, Any]] = None,
    cancel_check: Optional[Callable[[], None]] = None,
    failure_meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any] | None:
    if cancel_check:
        cancel_check()
    normalized_analysis = _normalize_question_analysis(analysis)
    sub_questions = _normalize_analysis_string_list(normalized_analysis.get("sub_questions"))
    max_sub = ROUTER_CONFIG.get("max_sub_questions", 5)
    if len(sub_questions) > max_sub:
        LOGGER.warning("Truncating sub_questions from %d to %d for decompose-merge", len(sub_questions), max_sub)
        sub_questions = sub_questions[:max_sub]
    if not sub_questions:
        if isinstance(failure_meta, dict):
            failure_meta["reason"] = "returned_none"
        return None
    sql_examples = _extract_sql_examples_from_knowledge(knowledge_context or "")
    project_prompt = _render_project_prompt(project_id, semantic_context, sql_examples)
    dialect_hint = _dialect_hint_for_project(project_id)
    strict_json = _strict_json_capability()
    prompt_selection = _prompt_profile_selection(
        "sql_generation",
        strict_json_mode=strict_json.get("mode", "none"),
    )
    use_profile = _is_sql_route_v2_enabled(project_id) or bool(ROUTER_CONFIG.get("sql_route_shadow_mode", False))
    system_suffix = f"\n{prompt_selection.system_suffix}" if use_profile and prompt_selection.system_suffix else ""
    response_format = prompt_selection.response_format if (use_profile and prompt_selection.response_format) else "json"
    system = (
        f"{_render_system_prompt()}\n\n"
        f"{SQL_RESPONSE_CONTRACT.format(dialect_hint=dialect_hint)}\n"
        f"{_language_instruction(language)}{system_suffix}"
    )
    llm = LLMService()
    if not llm.is_configured():
        return None
    hit_models = semantic_hits.get("models", [])
    guard = _candidate_guard()
    owner_lock_hint = _format_owner_lock_constraints_hint(resolved, schema_link_plan)
    subquery_failure_counts: dict[str, int] = {}
    merge_failure_counts: dict[str, int] = {}

    def _record_subquery_failure(reason: str, sq: str, detail: str | None = None) -> None:
        normalized_reason = str(reason or "returned_none").strip().lower() or "returned_none"
        subquery_failure_counts[normalized_reason] = int(subquery_failure_counts.get(normalized_reason) or 0) + 1
        if detail:
            LOGGER.info(
                "Decompose-merge sub-query failed reason=%s detail=%s sub_question=%s",
                normalized_reason,
                detail,
                sq,
            )
        else:
            LOGGER.info(
                "Decompose-merge sub-query failed reason=%s sub_question=%s",
                normalized_reason,
                sq,
            )

    def _set_failure(reason: str, detail: str | None = None) -> None:
        if not isinstance(failure_meta, dict):
            return
        normalized_reason = str(reason or "returned_none").strip().lower() or "returned_none"
        failure_meta["reason"] = normalized_reason
        if detail:
            failure_meta["detail"] = detail
        if subquery_failure_counts:
            failure_meta["reason_counts"] = dict(subquery_failure_counts)
        if merge_failure_counts:
            failure_meta["merge_reason_counts"] = dict(merge_failure_counts)

    def _record_merge_failure(reason: str, detail: str | None = None) -> None:
        normalized_reason = str(reason or "returned_none").strip().lower() or "returned_none"
        merge_failure_counts[normalized_reason] = int(merge_failure_counts.get(normalized_reason) or 0) + 1
        if detail:
            LOGGER.info(
                "Decompose-merge merge candidate failed reason=%s detail=%s",
                normalized_reason,
                detail,
            )
        else:
            LOGGER.info("Decompose-merge merge candidate failed reason=%s", normalized_reason)

    def _stabilize_decompose_candidate(candidate_sql: str, label: str) -> tuple[str | None, list[str], list[str], str | None]:
        if _contains_sql_placeholder_markers(candidate_sql):
            LOGGER.warning(
                "Decompose-merge %s contains placeholder SQL identifiers; rejecting candidate",
                label,
            )
            return None, [], [], "placeholder"
        stabilized = _fix_type_mismatch_multiply(candidate_sql, hit_models) if hit_models else candidate_sql
        inspected = guard.inspect(
            stabilized,
            dimensions=normalized_analysis.get("dimensions") or [],
            hit_models=hit_models,
            resolved=resolved,
            project_id=project_id,
        )
        group_issues = list(inspected.group_issues)
        if group_issues:
            group_candidate = _apply_group_by_completion_rules(stabilized)
            if group_candidate != stabilized:
                group_columns = _validate_sql_columns(group_candidate, hit_models)
                group_candidate_issues = _validate_sql_group_by(
                    group_candidate,
                    normalized_analysis.get("dimensions") or [],
                    hit_models=hit_models,
                    resolved=resolved,
                )
                if (group_columns is None or not group_columns) and not group_candidate_issues:
                    stabilized = group_candidate
                    group_issues = []
            if group_issues and resolved and resolved.get("dimensions_resolved"):
                resolved_candidate = _apply_resolved_dimension_group_by_rules(
                    stabilized,
                    resolved,
                    hit_models=hit_models,
                )
                if resolved_candidate != stabilized:
                    resolved_columns = _validate_sql_columns(resolved_candidate, hit_models)
                    resolved_issues = _validate_sql_group_by(
                        resolved_candidate,
                        normalized_analysis.get("dimensions") or [],
                        hit_models=hit_models,
                        resolved=resolved,
                    )
                    if (resolved_columns is None or not resolved_columns) and not resolved_issues:
                        stabilized = resolved_candidate
                        group_issues = []
        inspected = guard.inspect(
            stabilized,
            dimensions=normalized_analysis.get("dimensions") or [],
            hit_models=hit_models,
            resolved=resolved,
            project_id=project_id,
        )
        agg_issues = list(inspected.aggregation_issues)
        syntax_issues = list(inspected.syntax_issues)
        if syntax_issues:
            LOGGER.warning(
                "Decompose-merge %s syntax issues: %s",
                label,
                syntax_issues,
            )
            return None, group_issues, agg_issues, "syntax"
        if group_issues:
            LOGGER.warning(
                "Decompose-merge %s GROUP BY issues remain after local repair: %s",
                label,
                group_issues,
            )
            return None, group_issues, agg_issues, "group_by"
        return stabilized, group_issues, agg_issues, None

    def _subquery_guard_reason(candidate_sql: str) -> str | None:
        inspected = guard.inspect(
            candidate_sql,
            dimensions=[],
            hit_models=hit_models,
            resolved=resolved,
            project_id=project_id,
        )
        if inspected.syntax_issues:
            return "syntax"
        if inspected.aggregation_issues:
            return "aggregation"
        return None

    def _sub_sql(sq: str) -> tuple[str | None, str | None, str]:
        try:
            if cancel_check:
                cancel_check()
            sub_question_prompt = (
                f"Previous questions: {previous_questions or []}\n"
                f"Sub-question: {sq}\n"
                "Generate a SQL query that answers this sub-question. Use only the schema context provided."
                f"{owner_lock_hint}"
            )
            result = _llm_chat_with_response_format_fallback(
                llm,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Project prompt:\n{project_prompt}"},
                    {"role": "user", "content": sub_question_prompt},
                ],
                response_format=response_format,
                stage="sql_generation_subquery",
            )
            parsed = parse_json_object(result.get("content", ""))
            sq_sql = _normalize_sql_candidate(parsed.get("sql"))
            if not sq_sql:
                return None, None, "empty"
            validated = _validate_sql_columns(sq_sql, hit_models)
            if validated is None:
                return None, None, "returned_none"
            if validated is not None and not validated:
                guard_reason = _subquery_guard_reason(sq_sql)
                if guard_reason:
                    return None, None, guard_reason
                return sq_sql, parsed.get("summary", ""), "ok"
            if validated:
                rehinted = _rehint_columns(sq_sql, hit_models)
                if rehinted != sq_sql:
                    rehinted_validated = _validate_sql_columns(rehinted, hit_models)
                    if rehinted_validated is not None and not rehinted_validated:
                        guard_reason = _subquery_guard_reason(rehinted)
                        if guard_reason:
                            return None, None, guard_reason
                        return rehinted, parsed.get("summary", ""), "ok"
            return None, None, "bad_columns"
        except Exception:
            return None, None, "returned_none"

    sql_results = [_sub_sql(sq) for sq in sub_questions]
    if cancel_check:
        cancel_check()
    valid_results: list[tuple[str, str, str | None]] = []
    for sq, (sql, summary, reason) in zip(sub_questions, sql_results):
        if sql:
            valid_results.append((sq, sql, summary))
            continue
        _record_subquery_failure(reason, sq)
    if not valid_results:
        ranked_failures = sorted(subquery_failure_counts.items(), key=lambda item: (-item[1], item[0]))
        primary_reason = ranked_failures[0][0] if ranked_failures else "returned_none"
        _set_failure(primary_reason, detail=f"sub_query_failures={dict(subquery_failure_counts)}")
        LOGGER.warning(
            "Decompose-merge: all sub-queries failed (reasons=%s) — falling back to direct generation",
            subquery_failure_counts or {primary_reason: 1},
        )
        return None

    if len(valid_results) == 1:
        _, only_sql, only_summary = valid_results[0]
        orphan = _validate_no_orphaned_cte(only_sql)
        if orphan:
            only_sql = _repair_sql(
                question,
                only_sql,
                "; ".join(orphan),
                project_id,
                semantic_context,
                language,
                cancel_check=cancel_check,
            ).get("sql") or only_sql
        stabilized_sql, _group_issues, agg_issues, stabilize_reason = _stabilize_decompose_candidate(only_sql, "single sub-query")
        if not stabilized_sql:
            _set_failure(stabilize_reason or "returned_none", detail="single_subquery_unstable")
            return None
        if agg_issues:
            LOGGER.warning("Decompose-merge single sub-query aggregation issues: %s", agg_issues)
        return {
            "sql": stabilized_sql,
            "summary": only_summary or "Generated SQL for your question.",
            "reasoning": "Single sub-question generated directly via decompose-merge.",
            "retrieved_tables": retrieved_tables,
            "configured": True,
            "sql_engine": "decompose_merge",
        }

    merge_system = (
        f"{_render_system_prompt()}\n\n"
        "When combining SQL queries, return only JSON with keys sql, summary, reasoning. "
        "Only generate SELECT or WITH queries. "
        "Use CTEs (WITH name AS (SELECT ...)) if sub-queries share tables. "
        "Keep ALL dimensions and metrics from every sub-query. "
        "Prefix every column with its table alias. "
        "For compound questions, generate a single SQL where the main query has one GROUP BY covering all dimensions. "
        "Prefer one flat query over multiple CTEs. "
        f"{_dialect_hint_for_project(project_id)}\n"
        f"{_language_instruction(language)}{system_suffix}"
    )
    merge_parts = [f"Original question: {question}\nSchema context:\n{semantic_context}{owner_lock_hint}\n\nSub-queries to combine:"]
    for i, (sq, sq_sql, sq_summary) in enumerate(valid_results):
        merge_parts.append(f"\n--- Sub-query {i+1}: {sq} ---\nSQL: {sq_sql}\nSummary: {sq_summary}")
    merge_parts.append("\nCombine these into a single SQL query that answers the original question.")
    merge_user = "\n".join(merge_parts)

    merge_candidate_budget = max(1, min(3, int(ROUTER_CONFIG.get("tier3_max_retries", 3) or 3)))
    for merge_attempt in range(merge_candidate_budget):
        attempt_label = f"candidate {merge_attempt + 1}/{merge_candidate_budget}"
        try:
            if cancel_check:
                cancel_check()
            attempt_prompt = merge_user
            if merge_candidate_budget > 1:
                attempt_prompt = (
                    f"{merge_user}\n"
                    f"Generate merge SQL {attempt_label}. Keep semantics identical while improving validity and stability."
                )
            result = _llm_chat_with_response_format_fallback(
                llm,
                [
                    {"role": "system", "content": merge_system},
                    {"role": "user", "content": f"Project prompt:\n{project_prompt}"},
                    {"role": "user", "content": attempt_prompt},
                ],
                response_format=response_format,
                stage="sql_generation_merge",
            )
            raw_merge_content = result.get("content", "")
            try:
                parsed = parse_json_object(raw_merge_content)
            except Exception as parse_exc:
                fallback_sql = _extract_sql_from_llm_text(raw_merge_content)
                if not fallback_sql:
                    _record_merge_failure("parse_error", detail=f"{attempt_label}:{type(parse_exc).__name__}")
                    continue
                LOGGER.warning(
                    "Decompose-merge merge response JSON parse failed (%s); using plain-text SQL fallback",
                    parse_exc,
                )
                parsed = {
                    "sql": fallback_sql,
                    "summary": "Merged SQL for your compound question.",
                    "reasoning": f"Merge JSON parse failed ({type(parse_exc).__name__}); extracted SQL from plain-text fallback.",
                }
            merged_sql = _normalize_sql_candidate(parsed.get("sql"))
            if merged_sql and (validated := _validate_sql_columns(merged_sql, hit_models)) is not None and not validated:
                orphan = _validate_no_orphaned_cte(merged_sql)
                if orphan:
                    merged_sql = _repair_sql(
                        question,
                        merged_sql,
                        "; ".join(orphan),
                        project_id,
                        semantic_context,
                        language,
                        cancel_check=cancel_check,
                    ).get("sql") or merged_sql
                stabilized_sql, _group_issues, agg_issues, stabilize_reason = _stabilize_decompose_candidate(
                    merged_sql,
                    f"merge result ({attempt_label})",
                )
                if not stabilized_sql:
                    LOGGER.warning("Decompose-merge merge result %s is unstable", attempt_label)
                    _record_merge_failure(stabilize_reason or "returned_none", detail=f"{attempt_label}:merge_result_unstable")
                    continue
                if agg_issues:
                    LOGGER.warning("Decompose-merge SQL aggregation issues: %s", agg_issues)
                return {
                    "sql": stabilized_sql,
                    "summary": parsed.get("summary") or "Merged SQL for your compound question.",
                    "reasoning": parsed.get("reasoning") or f"Merged {len(valid_results)} sub-queries into one SQL.",
                    "retrieved_tables": retrieved_tables,
                    "configured": True,
                    "sql_engine": "decompose_merge",
                }
            if not merged_sql:
                LOGGER.warning("Decompose-merge merge step returned empty SQL (%s)", attempt_label)
                _record_merge_failure("empty", detail=f"{attempt_label}:merge_sql_empty")
                continue
            rehinted = _rehint_columns(merged_sql, hit_models)
            if rehinted != merged_sql:
                rehinted_validated = _validate_sql_columns(rehinted, hit_models)
                if rehinted_validated is not None and not rehinted_validated:
                    orphan = _validate_no_orphaned_cte(rehinted)
                    if orphan:
                        rehinted = _repair_sql(
                            question,
                            rehinted,
                            "; ".join(orphan),
                            project_id,
                            semantic_context,
                            language,
                            cancel_check=cancel_check,
                        ).get("sql") or rehinted
                    stabilized_rehinted, _rehint_group_issues, rehint_agg_issues, rehint_reason = _stabilize_decompose_candidate(
                        rehinted,
                        f"rehinted merge result ({attempt_label})",
                    )
                    if not stabilized_rehinted:
                        LOGGER.warning("Decompose-merge rehinted SQL %s is unstable", attempt_label)
                        _record_merge_failure(rehint_reason or "returned_none", detail=f"{attempt_label}:rehinted_merge_unstable")
                        continue
                    if rehint_agg_issues:
                        LOGGER.warning("Decompose-merge rehinted SQL aggregation issues: %s", rehint_agg_issues)
                    return {
                        "sql": stabilized_rehinted,
                        "summary": parsed.get("summary") or "Merged SQL for your compound question (auto-corrected columns).",
                        "reasoning": parsed.get("reasoning") or f"Merged {len(valid_results)} sub-queries into one SQL.",
                        "retrieved_tables": retrieved_tables,
                        "configured": True,
                        "sql_engine": "decompose_merge_rehint",
                    }
            LOGGER.warning("Decompose-merge merged SQL has bad columns (%s)", attempt_label)
            _record_merge_failure("merged_bad_columns", detail=attempt_label)
        except Exception as e:
            LOGGER.warning(
                "Decompose-merge merge %s failed: %s",
                attempt_label,
                _sanitize_error_message(e),
            )
            _record_merge_failure("returned_none", detail=f"{attempt_label}:{type(e).__name__}")

    ranked_merge_failures = sorted(merge_failure_counts.items(), key=lambda item: (-item[1], item[0]))
    primary_reason = ranked_merge_failures[0][0] if ranked_merge_failures else "returned_none"
    _set_failure(primary_reason, detail=f"merge_candidate_failures={dict(merge_failure_counts)}")
    LOGGER.warning(
        "Decompose-merge merge step exhausted %d candidate(s) (reasons=%s) — falling back to direct generation",
        merge_candidate_budget,
        merge_failure_counts or {primary_reason: 1},
    )
    return None


def _format_sql_clause_focus_hint(analysis: Optional[dict[str, Any]]) -> str:
    if not isinstance(analysis, dict):
        return ""
    metadata_part = str(analysis.get("metadata_question_part") or "").strip()
    non_metadata_part = str(analysis.get("non_metadata_question_part") or "").strip()
    lines: list[str] = []
    if metadata_part:
        lines.append(f"SQL-focused question part: {metadata_part}")
    if non_metadata_part:
        lines.append(f"Non-SQL question part (do not use as SQL requirements): {non_metadata_part}")
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
        return {
            "sql": None,
            "summary": "Failed to prepare SQL generation context.",
            "reasoning": "Generation context is unavailable.",
            "retrieved_tables": [],
            "configured": True,
            "sql_engine": "llm_fallback_prepare_error",
        }

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

    had_compound_fallback = False
    # Decompose & Merge for compound questions
    normalized_sub_questions = _normalize_analysis_string_list((analysis or {}).get("sub_questions"))
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
            decompose_failure_meta: dict[str, Any] = {}
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
    system = (
        f"{_render_system_prompt()}\n\n"
        f"{SQL_RESPONSE_CONTRACT.format(dialect_hint=_dialect_hint_for_project(project_id))}\n"
        f"{_language_instruction(language)}{system_suffix}"
    )
    use_examples = strategy.get("use_examples", True)
    sql_examples = _extract_sql_examples_from_knowledge(knowledge_context or "") if use_examples else ""
    project_prompt = _render_project_prompt(project_id, semantic_context, sql_examples)
    composite_hint = ""
    has_multi_dimension_markers = "," in question or "、" in question or " and " in question.lower() or "并且" in question or "以及" in question or "不同" in question or " each " in question.lower() or " by " in question.lower() or "怎么" in question or "如何" in question or "表现" in question or " per " in question.lower()
    if has_multi_dimension_markers or engine_label in ("fewshot_cot", "decompose_merge") or had_compound_fallback:
        composite_hint = (
            "\nIMPORTANT: This question may have multiple dimensions or sub-questions. "
            "Identify ALL dimensions the user is asking about and include ALL of them in GROUP BY and SELECT. "
            "For example, if asking about products 'and' their performance 'by' city, include BOTH the product column AND the city column in GROUP BY and SELECT — do not drop any dimension."
        )
    strategy_hint = ""
    if engine_label == "decompose_merge" or had_compound_fallback:
        strategy_hint = (
            "\nThis question has multiple sub-questions. Generate a SINGLE SQL query that answers ALL parts. "
            "Do not create multiple queries. Do not define a CTE unless the main SELECT references it. "
            "Prefer a flat query with all necessary joins and one GROUP BY that covers all dimensions. "
            "If you use a CTE, use syntax: WITH name AS (SELECT ...) and ensure the final SELECT references name."
        )
    elif engine_label == "fewshot_cot":
        if sql_examples and use_examples:
            strategy_hint = (
                "\nThis question has multiple dimensions. Follow the verified SQL examples above as reference patterns. "
                "Generate one SQL with all dimensions in GROUP BY. "
                "Think step by step: identify tables, join conditions, GROUP BY columns, then write the query."
            )
        else:
            strategy_hint = (
                "\nThis question has multiple dimensions. Generate one SQL with all dimensions in GROUP BY. "
                "Think step by step: identify tables, join conditions, GROUP BY columns, then write the query."
            )
    user = (
        f"Previous questions: {previous_questions or []}\n"
        f"Question: {question}{composite_hint}{strategy_hint}{clause_focus_hint}{dimension_mapping}{schema_link_hint}{owner_lock_hint}{sql_plan_hint}{fallback_constraints_hint}"
    )
    llm = LLMService()
    if not llm.is_configured():
        return {
            "sql": None,
            "summary": "LLM provider is not configured. Please configure it in Settings > LLM.",
            "reasoning": None,
            "retrieved_tables": retrieved_tables,
            "configured": False,
            "sql_engine": "llm_fallback",
        }
    max_retries = int(prepared.max_retries)
    last_errors = []
    last_repair_result = None
    last_unknown_issue_bucket = ""
    unknown_issue_bucket_streak = 0
    unknown_issue_bucket_circuit_threshold = 2
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
        active_engine_label = engine_label
        try:
            context_messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Project prompt:\n{project_prompt}"},
                {"role": "user", "content": user},
            ]
            if last_errors:
                error_feedback = "\nPrevious attempt errors:\n" + "\n".join(last_errors)
                context_messages.append({"role": "user", "content": f"Fix these errors:{error_feedback}"})
            result = _llm_chat_with_response_format_fallback(
                llm,
                context_messages,
                response_format=response_format,
                stage="sql_generation",
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
                if attempt < max_retries - 1:
                    continue
                return {
                    "sql": None,
                    "summary": "LLM did not return valid SQL after retries.",
                    "reasoning": "; ".join(last_errors),
                    "retrieved_tables": retrieved_tables,
                    "configured": True,
                    "sql_engine": f"{engine_label}_failed",
                }
            parsed = parse_json_object(raw_content)
            sql = _normalize_sql_candidate(parsed.get("sql"))
            if not sql:
                last_errors.append(f"Attempt {attempt+1}: LLM returned empty SQL")
                if attempt < max_retries - 1:
                    continue
                return {
                    "sql": None,
                    "summary": "LLM did not return valid SQL after retries.",
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
                repair = _repair_sql(
                    question,
                    sql,
                    "; ".join(orphan_cte_issues),
                    project_id,
                    semantic_context,
                    language,
                    analysis=analysis,
                    schema_link_plan=schema_link_plan,
                    cancel_check=cancel_check,
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
                        "summary": repair.get("summary") or parsed.get("summary") or "Repaired SQL: removed orphaned CTE.",
                        "reasoning": repair.get("reasoning") or parsed.get("reasoning"),
                    }
                    active_engine_label = f"{engine_label}_repair"
                else:
                    last_repair_result = {
                        "sql": repair.get("sql"),
                        "summary": repair.get("summary") or "Repaired SQL after orphan CTE (best effort).",
                        "reasoning": repair.get("reasoning"),
                        "retrieved_tables": retrieved_tables,
                        "configured": True,
                        "sql_engine": f"{engine_label}_repair",
                    }
                    last_errors.append(f"Attempt {attempt+1}: orphan CTE ({orphan_cte_issues[0] if orphan_cte_issues else 'unknown'})")
                    if attempt < max_retries - 1:
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
                            "summary": repair.get("summary") or parsed.get("summary") or "Repaired SQL after syntax validation.",
                            "reasoning": repair.get("reasoning") or parsed.get("reasoning"),
                        }
                        active_engine_label = f"{engine_label}_repair"
                        inspected = repaired_inspected
                    else:
                        last_errors.append(
                            f"Attempt {attempt+1}: syntax repair failed ({repaired_inspected.syntax_issues[0]})"
                        )
                        if attempt < max_retries - 1:
                            continue
                        return _syntax_failure_result(engine_label, last_errors, retrieved_tables)
                else:
                    last_errors.append(f"Attempt {attempt+1}: syntax repair returned empty SQL")
                    if attempt < max_retries - 1:
                        continue
                    return _syntax_failure_result(engine_label, last_errors, retrieved_tables)
            bad_columns = None if inspected.columns_inconclusive else list(inspected.bad_columns)
            if bad_columns:
                issue_buckets = _summarize_unknown_column_issues(bad_columns)
                dominant_issue_bucket = _dominant_issue_bucket(issue_buckets)
                if dominant_issue_bucket:
                    if dominant_issue_bucket == last_unknown_issue_bucket:
                        unknown_issue_bucket_streak += 1
                    else:
                        last_unknown_issue_bucket = dominant_issue_bucket
                        unknown_issue_bucket_streak = 1
                else:
                    last_unknown_issue_bucket = ""
                    unknown_issue_bucket_streak = 0
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
                            local_summary = "SQL auto-corrected column references."
                            if local_rewrite_stages == ["alias_scope"]:
                                local_summary = "SQL auto-corrected alias scope references."
                            elif "hallucinated_column" in local_rewrite_stages:
                                local_summary = "SQL auto-corrected unresolved metric columns from available schema columns."
                            return {
                                "sql": sql,
                                "summary": parsed.get("summary") or local_summary,
                                "reasoning": parsed.get("reasoning"),
                                "retrieved_tables": retrieved_tables,
                                "configured": True,
                                "sql_engine": f"{engine_label}_rehint",
                            }
                bucket_circuit_open = (
                    bool(dominant_issue_bucket)
                    and dominant_issue_bucket in circuitable_issue_buckets
                    and unknown_issue_bucket_streak >= unknown_issue_bucket_circuit_threshold
                )
                if bucket_circuit_open:
                    LOGGER.warning(
                        "Unknown column issue bucket '%s' repeated %d time(s); opening local repair circuit",
                        dominant_issue_bucket,
                        unknown_issue_bucket_streak,
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
                            dominant_issue_bucket,
                        )
                        if emit_route_events:
                            _emit_route_event(
                                "sql_repair_short_circuit",
                                {
                                    "reason": "bucket_circuit_local_fix",
                                    "issue_bucket": dominant_issue_bucket,
                                    "attempt": attempt + 1,
                                    "max_retries": max_retries,
                                    "generation_engine": engine_label,
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
                            "summary": parsed.get("summary") or "SQL auto-corrected after repeated validation failures.",
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
                                "issue_bucket": dominant_issue_bucket,
                                "attempt": attempt + 1,
                                "max_retries": max_retries,
                                "generation_engine": engine_label,
                            },
                            project_id=project_id,
                        )
                    circuit_reasoning = (
                        f"Stopped LLM repair retries after repeated unknown-column bucket '{dominant_issue_bucket}'"
                    )
                    if bad_columns:
                        circuit_reasoning += f" (example: {bad_columns[0]})."
                    if local_group_issues:
                        circuit_reasoning += f" GROUP BY issues: {'; '.join(local_group_issues)}."
                    return {
                        "sql": None,
                        "summary": "SQL validation failed repeatedly; repair circuit opened.",
                        "reasoning": circuit_reasoning,
                        "retrieved_tables": retrieved_tables,
                        "configured": True,
                        "sql_engine": f"{engine_label}_validation_circuit_open",
                    }
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
                        "summary": repair.get("summary") or parsed.get("summary") or "Repaired SQL after column validation.",
                        "reasoning": repair.get("reasoning") or parsed.get("reasoning"),
                    }
                    active_engine_label = f"{engine_label}_repair"
                else:
                    last_repair_result = {
                        "sql": repair.get("sql"),
                        "summary": repair.get("summary") or "Repaired SQL after column validation (best effort).",
                        "reasoning": repair.get("reasoning"),
                        "retrieved_tables": retrieved_tables,
                        "configured": True,
                        "sql_engine": f"{engine_label}_repair",
                    }
                    last_errors.append(
                        f"Attempt {attempt+1}: bad columns ({bad_columns[0] if bad_columns else 'unknown'}) buckets={issue_buckets}"
                    )
                    if attempt < max_retries - 1:
                        continue
                    if repair.get("sql") and repair_validated is None:
                        LOGGER.warning("Column validation inconclusive for repaired SQL — using as-is")
                        return {
                            "sql": repair["sql"],
                            "summary": repair.get("summary") or "Repaired SQL after column validation (best effort).",
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
                if attempt < max_retries - 1:
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
        except (json.JSONDecodeError, KeyError, AttributeError, TypeError, ValueError) as e:
            LOGGER.warning("Failed to parse LLM response (attempt %d/%d): %s", attempt + 1, max_retries, e)
            last_errors.append(f"Attempt {attempt+1}: parse error ({e})")
            if attempt < max_retries - 1:
                continue
            return {
                "sql": None,
                "summary": "LLM response could not be parsed as valid JSON after retries.",
                "reasoning": "; ".join(last_errors),
                "retrieved_tables": retrieved_tables,
                "configured": True,
                "sql_engine": "llm_fallback_parse_error",
            }
    return {
        "sql": None,
        "summary": "Failed to generate valid SQL after retries.",
        "reasoning": "; ".join(last_errors),
        "retrieved_tables": retrieved_tables,
        "configured": True,
        "sql_engine": "llm_fallback_retry_exhausted",
    }


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
) -> dict[str, Any]:
    if cancel_check:
        cancel_check()
    if semantic_context is None:
        semantic_context, _, _ = _semantic_prompt(project_id, question, require_hits=True, analysis=analysis)
    strict_json = _strict_json_capability()
    prompt_selection = _prompt_profile_selection(
        "sql_repair",
        strict_json_mode=strict_json.get("mode", "none"),
    )
    use_profile = _is_sql_route_v2_enabled(project_id) or bool(ROUTER_CONFIG.get("sql_route_shadow_mode", False))
    response_format = prompt_selection.response_format if (use_profile and prompt_selection.response_format) else "json"
    system_suffix = f"\n{prompt_selection.system_suffix}" if use_profile and prompt_selection.system_suffix else ""
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
        misowned = [p for p in error.split(";") if "belongs on:" in p or "not found" in p]
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
    messages = [
        {
            "role": "system",
            "content": (
                f"{_render_system_prompt()}\n\n{SQL_RESPONSE_CONTRACT.format(dialect_hint=_dialect_hint_for_project(project_id))}\n{_language_instruction(language)}\n"
                "Repair the failed SQL using only the listed semantic model columns. Return only JSON with keys sql, summary, reasoning."
                f"{system_suffix}"
            ),
        },
        {"role": "user", "content": f"Semantic model:\n{semantic_context}"},
        {
            "role": "user",
            "content": (
                f"Question: {question}\nFailed SQL:\n{failed_sql}\nError:\n{error}\n"
                "Fix the SQL so it can execute. CRITICAL: Every column must be prefixed with the alias of the model that OWNS that column. "
                "If a column does not exist on its prefixed model, move it to the correct model alias shown in the ownership map. "
                "CRITICAL: Every table alias and CTE alias must be unique within the same SELECT scope; do not reuse aliases such as T1 for multiple joined sources. "
                "CRITICAL: In grouped/aggregated queries, every ORDER BY and HAVING column must either be a GROUP BY key or be wrapped in an aggregate function (for example MAX(...) or ANY_VALUE(...)). "
                "Do not guess — look up each column in the semantic model above and use the alias that matches the model owning that column."
                f"{column_map_hint}{ambiguous_owner_hint}"
            ),
        },
    ]
    if cancel_check:
        cancel_check()
    result = _llm_chat_with_response_format_fallback(
        llm,
        messages,
        response_format=response_format,
        stage="sql_repair",
    )
    raw_content = _llm_content_text(result)
    if not raw_content.strip():
        LOGGER.warning("LLM returned empty SQL repair payload; keeping local validation fallback")
        return {
            "sql": None,
            "summary": None,
            "reasoning": "Repair failed: LLM returned empty content.",
            "configured": True,
        }
    try:
        parsed = parse_json_object(raw_content)
        sql = _normalize_sql_candidate(parsed.get("sql"))
        return {
            "sql": sql,
            "summary": parsed.get("summary") or "Repaired SQL for your question.",
            "reasoning": parsed.get("reasoning"),
            "configured": True,
        }
    except (json.JSONDecodeError, KeyError, AttributeError, TypeError, ValueError) as e:
        fallback_sql = _extract_sql_from_llm_text(raw_content)
        if fallback_sql:
            LOGGER.warning("Failed to parse LLM response in _repair_sql: %s; using plain-text SQL fallback", e)
            return {
                "sql": fallback_sql,
                "summary": "Repaired SQL for your question.",
                "reasoning": f"Repair JSON parse failed: {e}; extracted SQL from plain-text fallback.",
                "configured": True,
            }
        LOGGER.warning("Failed to parse LLM response in _repair_sql: %s", e)
        return {
            "sql": None,
            "summary": None,
            "reasoning": f"Repair failed: LLM response could not be parsed as valid JSON.",
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
    use_chinese = _in_chinese(language) or _contains_cjk(question)
    if use_chinese:
        if project_scoped:
            return (
                "我是 PrismBI 助手。"
                "我可以回答通用问题，也可以基于当前项目数据帮你生成 SQL、执行查询并解释结果。"
                "你可以继续问我：例如“按城市统计销售额”。"
            )
        return (
            "我是 PrismBI 助手。"
            "我可以进行通用问答；在你选择项目并连接数据后，"
            "还可以帮你生成 SQL、执行查询并解释结果。"
        )
    if project_scoped:
        return (
            "I am PrismBI assistant. I can answer general questions and also help with SQL generation, "
            "query execution, and result explanations for the current project data."
        )
    return (
        "I am PrismBI assistant. I can answer general questions, and once a project with data is selected "
        "I can generate SQL, run queries, and explain results."
    )


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
        return {
            "content": "LLM provider is not configured. Please configure it in Settings > LLM.",
            "configured": False,
            "latency_ms": None,
        }
    meta = _project_meta(project_id) or {}
    messages = [
        {"role": "system", "content": f"{_render_system_prompt()}\n{_language_instruction(language)}"},
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
            "Answer only the part of the user's request that is not covered by matched project metadata. "
            "Do not invent live project data or query results. If the user's question requires live project data that was not matched to metadata, "
            "explain what data is available in the project and suggest a concrete question they could ask instead. "
            "Respond in the same language as the user's question.\n"
            f"Project: {meta.get('display_name') or meta.get('name') or project_id}\n"
            f"Project description: {meta.get('description') or ''}\n"
            f"Full question: {full_question}\n"
            f"Unmatched part: {question_part}"
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


def _general_chat(question: str, previous_questions: Optional[list[str]] = None, previous_answers: Optional[list[str]] = None, language: Optional[str] = None) -> dict[str, Any]:
    llm = LLMService()
    if not llm.is_configured():
        return {
            "content": "LLM provider is not configured. Please configure it in Settings > LLM.",
            "configured": False,
            "latency_ms": None,
        }
    messages = [
        {"role": "system", "content": f"{_render_system_prompt()}\n{_language_instruction(language)}"},
    ]
    if previous_questions and previous_answers:
        history_limit = min(len(previous_questions), len(previous_answers), 5)
        for i in range(max(0, len(previous_questions) - history_limit), len(previous_questions)):
            ans_idx = i if i < len(previous_answers) else len(previous_answers) - 1
            messages.append({"role": "user", "content": previous_questions[i]})
            messages.append({"role": "assistant", "content": previous_answers[ans_idx][:500] if previous_answers[ans_idx] else ""})
    messages.append({"role": "user", "content": question})
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
    "postgresql": "\nIMPORTANT: The target database is PostgreSQL. Use PostgreSQL-compatible SQL syntax: use DOUBLE PRECISION instead of DOUBLE, TIMESTAMP instead of DATETIME, STRING_AGG instead of LIST, and standard ANSI SQL identifiers (double-quoted). Avoid DuckDB-specific functions like TRY_CAST, LIST, ARBITRARY, QUANTILE.",
    "postgres": "\nIMPORTANT: The target database is PostgreSQL. Use PostgreSQL-compatible SQL syntax: use DOUBLE PRECISION instead of DOUBLE, TIMESTAMP instead of DATETIME, STRING_AGG instead of LIST, and standard ANSI SQL identifiers (double-quoted). Avoid DuckDB-specific functions like TRY_CAST, LIST, ARBITRARY, QUANTILE.",
    "redshift": "\nIMPORTANT: The target database is Amazon Redshift. Use Redshift-compatible SQL syntax: use DOUBLE PRECISION, LISTAGG instead of STRING_AGG, and avoid DuckDB-specific functions.",
    "mysql": "\nIMPORTANT: The target database is MySQL. Use MySQL-compatible SQL syntax: use backtick quotes for identifiers, GROUP_CONCAT instead of STRING_AGG or LIST, DATE_FORMAT instead of DATE_TRUNC, LIMIT without OFFSET syntax, and avoid TRY_CAST.",
    "mariadb": "\nIMPORTANT: The target database is MariaDB. Use MySQL-compatible SQL syntax: use backtick quotes for identifiers, GROUP_CONCAT instead of STRING_AGG or LIST, DATE_FORMAT instead of DATE_TRUNC.",
    "mssql": "\nIMPORTANT: The target database is Microsoft SQL Server. Use T-SQL syntax: use square brackets for identifiers, STRING_AGG instead of LIST, DATEFROMPARTS instead of MAKE_DATE, TOP N instead of LIMIT, and avoid DuckDB-specific functions.",
    "sqlserver": "\nIMPORTANT: The target database is Microsoft SQL Server. Use T-SQL syntax: use square brackets for identifiers, STRING_AGG instead of LIST, DATEFROMPARTS instead of MAKE_DATE, TOP N instead of LIMIT, and avoid DuckDB-specific functions.",
    "clickhouse": "\nIMPORTANT: The target database is ClickHouse. Use ClickHouse-compatible SQL syntax: use backtick quotes for identifiers, groupArray instead of LIST/ARRAY_AGG, toDateTime instead of CAST, and avoid DuckDB-specific functions.",
    "trino": "\nIMPORTANT: The target database is Trino. Use Trino-compatible SQL syntax: use DOUBLE instead of DOUBLE PRECISION, ARRAY_AGG instead of LIST, DATE_TRUNC is supported, and use double-quoted identifiers.",
    "athena": "\nIMPORTANT: The target database is Amazon Athena (Trino-compatible). Use Trino-compatible SQL syntax: use ARRAY_AGG instead of LIST, DATE_TRUNC is supported, and double-quote identifiers.",
    "bigquery": "\nIMPORTANT: The target database is BigQuery. Use BigQuery-compatible SQL syntax: use backtick quotes for identifiers, TIMESTAMP_TRUNC/DATE_TRUNC for time bucketing, SAFE_CAST when conversion can fail, and avoid DuckDB-only functions.",
    "snowflake": "\nIMPORTANT: The target database is Snowflake. Use Snowflake-compatible SQL syntax: use DOUBLE/NUMBER types, DATE_TRUNC for time bucketing, IFF/CASE for conditionals, and avoid DuckDB-specific functions.",
    "oracle": "\nIMPORTANT: The target database is Oracle. Use Oracle-compatible SQL syntax: use FETCH FIRST N ROWS ONLY instead of LIMIT, TO_CHAR/TO_DATE as needed, and avoid DuckDB-specific functions.",
    "databricks": "\nIMPORTANT: The target database is Databricks SQL (Spark). Use Spark SQL-compatible syntax: use backtick quotes for identifiers, date_trunc for time bucketing, and avoid DuckDB-specific functions.",
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
            connect_timeout=10,
        )

    conn = None
    cursor = None
    keep_connection = False
    try:
        conn = _acquire_pooled_connection(pool_key, _connect, _is_postgres_connection_healthy)
        cursor = conn.cursor()
        cursor.execute("SET statement_timeout = 30000")
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
            connection_timeout=10,
            ssl_disabled=not _normalize_bool(props.get("ssl")),
        )

    def _connect_pymysql():
        return pymysql.connect(
            host=props.get("host"),
            port=int(props.get("port") or 3306),
            user=props.get("user") or props.get("username"),
            password=props.get("password"),
            database=database,
            connect_timeout=10,
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
        cursor.execute("SET SESSION max_execution_time = 30000")
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
        connect_timeout=10,
        send_receive_timeout=60,
    )
    try:
        client.command("SET max_execution_time = 60")
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
            timeout=10,
        )

    def _connect_pymssql():
        return pymssql.connect(
            server=props.get("host"),
            port=int(props.get("port") or 1433),
            user=props.get("user") or props.get("username"),
            password=props.get("password"),
            database=props.get("database"),
            timeout=10,
            login_timeout=10,
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
        cursor.execute("SET LOCK_TIMEOUT 30000")
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
            conn.call_timeout = 30000
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
            login_timeout=10,
            network_timeout=60,
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


def _transpile_sql_for_dialect(sql: str, target_ds_type: str) -> tuple[str, str | None]:
    target_dialect = _dialect_for_ds(target_ds_type)
    if target_dialect == "duckdb":
        return sql, None
    if sqlglot is None:
        return (
            sql,
            f"SQL transpilation to {target_dialect} is unavailable because sqlglot is not installed; using DuckDB SQL as fallback.",
        )
    try:
        transpiled = sqlglot.transpile(sql, read="duckdb", write=target_dialect)
        if transpiled and transpiled[0]:
            return transpiled[0], None
    except Exception as exc:
        safe_error = _sanitize_error_message(exc)
        LOGGER.warning("SQL transpilation to %s failed: %s; using DuckDB SQL as fallback", target_dialect, safe_error)
        return sql, f"SQL transpilation to {target_dialect} failed; query may contain incompatible syntax. Error: {safe_error}"
    return sql, f"SQL transpilation to {target_dialect} produced no output; using DuckDB SQL as fallback."


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
        model_name = str(model["name"])
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
    conn = duckdb.connect(path)
    try:
        duckdb_binding_ids = {binding_id for binding_id, _ in duckdb_bindings}
        attached_binding_ids: set[int] = {primary_binding}
        for binding_id, props in duckdb_bindings[1:]:
            attach_path = _resolve_duckdb_path(props, project_id, binding_id)
            if not os.path.exists(attach_path):
                LOGGER.warning("Attached DuckDB file not found: %s (binding_id=%d)", attach_path, binding_id)
                continue
            schema_name = f"ds_{binding_id}"
            conn.execute(f"ATTACH {_quote_sql_literal(attach_path)} AS {_quote_identifier(schema_name)}")
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
        executable_sql = planned_sql
        LOGGER.debug("Executing SQL: %s", executable_sql)

        scalar_subquery_retry = False
        aggregation_rewrite_retry = False
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
        return {
            "content": "LLM provider is not configured. Please configure it in Settings > LLM.",
            "configured": False,
            "latency_ms": None,
        }
    if semantic_context is None:
        semantic_context, _, _ = _semantic_prompt(project_id, question)
    meta = _project_meta(project_id) or {}
    project_capabilities = _build_project_capabilities(project_id)
    messages = [
        {"role": "system", "content": f"{_render_system_prompt()}\n{_language_instruction(language)}"},
        {"role": "user", "content": f"Project context:\n{_render_project_general_context(project_id, semantic_context)}\n\nProject capabilities: {project_capabilities}"},
    ]
    if previous_questions and previous_answers:
        history_limit = min(len(previous_questions), len(previous_answers), 5)
        for i in range(max(0, len(previous_questions) - history_limit), len(previous_questions)):
            ans_idx = i if i < len(previous_answers) else len(previous_answers) - 1
            messages.append({"role": "user", "content": previous_questions[i]})
            messages.append({"role": "assistant", "content": previous_answers[ans_idx][:500] if previous_answers[ans_idx] else ""})
    guidance = ""
    if metadata_summary and metadata_summary.get("models_count", 0) > 0:
        if ROUTER_CONFIG.get("guidance_llm_available", True):
            guidance = GUIDANCE_PROMPT.format(
                model_summary=metadata_summary["summary"],
                suggested_questions="\n".join(f"- {q}" for q in (metadata_summary.get("suggested_questions") or [])),
            )
        else:
            guidance = (
                f"\nAvailable project models:\n{metadata_summary['summary']}\n"
                f"Example questions you could ask:\n"
                + "\n".join(f"- {q}" for q in (metadata_summary.get("suggested_questions") or []))
            )
    messages.append({
        "role": "user",
        "content": (
            "Answer as a helpful assistant. Do not return SQL, JSON, code fences, or query plans. "
            "If the user asks what you can do, describe the project capabilities listed above. "
            "If the user asks for live project data that was not matched to metadata, explain what data is available and suggest a concrete question they could ask. "
            "Use the conversation history above to understand context and follow-up questions.\n"
            f"{guidance}\n"
            f"Question: {question}"
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
    strict_json = _strict_json_capability()
    prompt_selection = _prompt_profile_selection(
        "final_answer",
        strict_json_mode=strict_json.get("mode", "none"),
    )
    use_profile = bool(
        (project_id is not None and _is_sql_route_v2_enabled(project_id))
        or ROUTER_CONFIG.get("sql_route_shadow_mode", False)
    )
    system_suffix = f"\n{prompt_selection.system_suffix}" if use_profile and prompt_selection.system_suffix else ""
    llm = LLMService()
    if not llm.is_configured():
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
    sql_data = {
        "columns": query_result.get("columns", []),
        "rows": preview_rows,
        "total_rows": query_result.get("total_rows", 0),
    }
    sub_q_hint = ""
    if sub_questions:
        sub_q_hint = f"\nThe original question has {len(sub_questions)} sub-questions. Address ALL of them: {'; '.join(sub_questions)}\n"
    route_focus_hint = ""
    if metadata_focus or non_metadata_focus or clause_routing_prompt:
        lines: list[str] = []
        if metadata_focus:
            lines.append(f"SQL-focused question part: {metadata_focus}")
        if non_metadata_focus:
            lines.append(f"Non-SQL question part (already answered separately): {non_metadata_focus}")
        if clause_routing_prompt:
            lines.append("Clause routing details:")
            lines.extend(clause_routing_prompt.split("\n"))
        route_focus_hint = "\n" + "\n".join(lines) + "\n"
    user_content = (
        f"{FINAL_ANSWER_CONTRACT}\n\n"
        "Create a text-based answer from executed SQL preview data, following this exact evidence order: user question, SQL, SQL data.\n"
        "Use only SQL data columns and rows for facts, rankings, comparisons, totals, and examples.\n"
        "The SQL text is context for how the data was produced, not a source for factual conclusions.\n"
        "Ignore any SQL-generation summary if it conflicts with or is not proven by SQL data.\n"
        f"{sub_q_hint}"
        f"{route_focus_hint}"
    )
    if previous_questions:
        user_content += f"Conversation context (previous questions): {previous_questions}\n"
    user_content += f"\nQuestion: {question}\nSQL: {sql}\nSQL data: {json.dumps(sql_data, ensure_ascii=False, default=str)}"
    messages = [
        {"role": "system", "content": f"{_render_system_prompt()}\n{_language_instruction(language)}{system_suffix}"},
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
        return _generic_result_answer(query_result, generated_summary, language, limit, sub_questions=sub_questions)


def _basic_result_summary(query_result: dict[str, Any], fallback: str = "", language: Optional[str] = None) -> str:
    columns = query_result.get("columns", [])
    rows = query_result.get("rows", [])[:5]
    total = query_result.get("total_rows", 0)
    warning = query_result.get("warning")
    use_chinese = not language or str(language).lower().replace("_", "-").startswith("zh")
    returned_text = "查询已完成，返回" if use_chinese else "Query completed and returned"
    empty_columns_text = "查询已完成，但没有返回可展示的列。" if use_chinese else "Query completed, but no displayable columns were returned."
    empty_rows_text = "查询已完成，但结果为空。" if use_chinese else "Query completed, but the result is empty."
    warning_label = "提示" if use_chinese else "Warning"
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
    if use_chinese:
        parts = [f"{returned_text} {total} 行结果。前 {len(rows)} 行如下:", *preview]
    else:
        parts = [f"{returned_text} {total} rows. First {len(rows)} rows:", *preview]
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


def _result_detail_hint(use_chinese: bool) -> str:
    if use_chinese:
        return "更多数据明细和可视化信息可在 Result 数据视图和图表中查看。"
    return "More row-level details and visual information are available in the Result data view and charts."


def _generate_data_insights(columns: list[str], rows: list[dict[str, Any]], numeric_columns: list[str], use_chinese: bool) -> list[str]:
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
            if use_chinese:
                insights.append(f"**{col}** 的最大值 ({_format_metric(max_val)}) 约是最小值 ({_format_metric(min_val)}) 的 **{_format_metric(ratio, 1)}x**，差异显著。")
            else:
                insights.append(f"**{col}** has a wide spread: max ({_format_metric(max_val)}) is **{_format_metric(ratio, 1)}x** the min ({_format_metric(min_val)}).")
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
                if use_chinese:
                    insights.append(f"**{top_group}** 的 {primary_num_col} 合计 ({_format_metric(group_sums[top_group])}) 占总量的 **{top_pct:.1f}%**。")
                else:
                    insights.append(f"**{top_group}** accounts for **{top_pct:.1f}%** of total {primary_num_col} ({_format_metric(group_sums[top_group])}).")
            bottom_group = min(group_sums, key=group_sums.get)
            if len(group_sums) >= 3 and group_sums[top_group] > 0 and group_sums[bottom_group] > 0:
                diff_ratio = group_sums[top_group] / group_sums[bottom_group]
                if diff_ratio >= 2:
                    if use_chinese:
                        insights.append(f"{primary_text_col} 中，**{top_group}** ({_format_metric(group_sums[top_group])}) 与 **{bottom_group}** ({_format_metric(group_sums[bottom_group])}) 的 {primary_num_col} 差异达 **{_format_metric(diff_ratio, 1)}x**。")
                    else:
                        insights.append(f"The gap between **{top_group}** ({_format_metric(group_sums[top_group])}) and **{bottom_group}** ({_format_metric(group_sums[bottom_group])}) in {primary_num_col} is **{_format_metric(diff_ratio, 1)}x**.")
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
                    if use_chinese:
                        insights.append(f"**{col_a}** 与 **{col_b}** 呈强正相关 (r={correlation:.2f})，一方升高时另一方也倾向于升高。")
                    else:
                        insights.append(f"**{col_a}** and **{col_b}** are strongly correlated (r={correlation:.2f}) — when one rises, the other tends to rise as well.")
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

    use_chinese = not language or str(language).lower().replace("_", "-").startswith("zh")
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
    if use_chinese:
        lines = [
            "### 结论",
            f"预览结果包含 **{total_rows}** 行、**{len(columns)}** 个字段。",
        ]
        if numeric_columns:
            lines.extend(["", "### 关键指标", "| 字段 | 合计 | 平均 | 最大值 |", "|---|---:|---:|---:|"])
            for column in numeric_columns[:4]:
                values = [_to_number(row.get(column)) for row in rows if row.get(column) is not None]
                if values:
                    lines.append(f"| {column} | {_format_metric(sum(values))} | {_format_metric(sum(values) / len(values))} | {_format_metric(max(values))} |")
        insights = _generate_data_insights(columns, rows, numeric_columns, True)
        if insights:
            lines.extend(["", "### 数据洞察"])
            for insight in insights:
                lines.append(f"- {insight}")
        lines.extend(["", f"### {'全部结果' if show_all else '代表性预览'}", "| # | " + " | ".join(display_columns) + " |", "|---:|" + "|".join("---" for _ in display_columns) + "|"])
        for index, row in enumerate(preview_rows, start=1):
            values = " | ".join(str(row.get(column) if row.get(column) is not None else "-") for column in display_columns)
            lines.append(f"| {index} | {values} |")
        if len(columns) > len(display_columns):
            lines.append(f"\n每行仅展示前 {len(display_columns)} 个字段，其余字段可在 Result 视图查看。")
        if total_rows > len(rows):
            lines.append(_result_detail_hint(True))
        if warning:
            lines.append(f"\n提示: {warning}")
        if sub_questions:
            lines.append(f"\n**注意**: 原问题包含 {len(sub_questions)} 个子问题，以上数据可能仅覆盖部分子问题。")
        return "\n".join(lines)

    lines = [
        "### Takeaway",
        f"The preview contains **{total_rows}** rows and **{len(columns)}** columns.",
    ]
    if numeric_columns:
        lines.extend(["", "### Key Metrics", "| Field | Total | Average | Max |", "|---|---:|---:|---:|"])
        for column in numeric_columns[:4]:
            values = [_to_number(row.get(column)) for row in rows if row.get(column) is not None]
            if values:
                lines.append(f"| {column} | {_format_metric(sum(values))} | {_format_metric(sum(values) / len(values))} | {_format_metric(max(values))} |")
    insights = _generate_data_insights(columns, rows, numeric_columns, False)
    if insights:
        lines.extend(["", "### Data Insights"])
        for insight in insights:
            lines.append(f"- {insight}")
    lines.extend(["", f"### {'All Rows' if show_all else 'Representative Preview'}", "| # | " + " | ".join(display_columns) + " |", "|---:|" + "|".join("---" for _ in display_columns) + "|"])
    for index, row in enumerate(preview_rows, start=1):
        values = " | ".join(str(row.get(column) if row.get(column) is not None else "-") for column in display_columns)
        lines.append(f"| {index} | {values} |")
    if len(columns) > len(display_columns):
        lines.append(f"\nEach row shows only the first {len(display_columns)} columns; the rest are available in the Result view.")
    if total_rows > len(rows):
        lines.append(_result_detail_hint(False))
    if warning:
        lines.append(f"\nWarning: {warning}")
    if sub_questions:
        lines.append(f"\n**Note**: The original question has {len(sub_questions)} sub-questions; the above data may only cover some of them.")
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
    use_chinese = not language or str(language).lower().replace("_", "-").startswith("zh")
    if sql_summary:
        parts = [part for part in [sql_summary, supplemental_answer] if part]
        if query_result.get("warning"):
            warning = f"提示: {query_result['warning']}" if use_chinese else f"Warning: {query_result['warning']}"
            parts.append(warning)
        return "\n\n".join(parts) if parts else "Returned query results."
    parts = [part for part in [_generic_result_answer(query_result, "", language, limit), supplemental_answer] if part]
    if query_result.get("warning"):
        warning = f"提示: {query_result['warning']}" if use_chinese else f"Warning: {query_result['warning']}"
        parts.append(warning)
    return "\n\n".join(parts) if parts else "Returned query results."


def _fallback_answer_after_sql_failure(question: str, project_id: int, error: str, previous_questions: Optional[list[str]] = None, previous_answers: Optional[list[str]] = None, language: Optional[str] = None) -> dict[str, Any]:
    llm = LLMService()
    if not llm.is_configured():
        return {
            "content": None,
            "configured": False,
            "error": error,
        }
    semantic_context, _, _ = _semantic_prompt(project_id, question)
    messages = [
        {"role": "system", "content": f"{_render_system_prompt()}\n{_language_instruction(language)}"},
        {"role": "user", "content": f"Project context:\n{_render_project_general_context(project_id, semantic_context)}"},
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
            "The project metadata-to-SQL path failed. Answer the user as a normal assistant without inventing query results. "
            "Do not return SQL, JSON, code fences, or query plans. If the answer requires live data, say that I could not run the query and explain what metadata may be needed.\n"
            f"Failure: {error}\nQuestion: {question}"
        ),
    })
    result = llm.chat(messages)
    result["content"] = _strip_sql_json_leak(result.get("content") or "")
    return result


def execute_project_sql(sql: str, project_id: int, user_id: int, limit: Optional[int] = None) -> dict[str, Any]:
    return _execute_project_sql_routed(sql, project_id, user_id, limit)


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
        update_auto_thread_summary(thread_id, user_id, question)
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
            "intentReasoning": "Answered with system and user prompts only because no non-empty project context is active.",
            "sqlGenerationReasoning": None,
            "error": answer_detail.get("error"),
            "processSteps": [
                {"key": "understand", "title": _step_title("understand", language), "status": "FINISHED", "detail": _step_detail_text("empty_project_or_no_project", language)},
                {"key": "answer", "title": _step_title("answer", language), "status": answer_detail["status"], "detail": answer_detail.get("content")},
            ],
        }
        breakdown_detail = {
            "status": answer_detail["status"],
            "description": "PrismBI used the system prompt and user prompt only because no non-empty project context is active.",
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
            "intentReasoning": "Classified as project-scoped general chat; no SQL execution was attempted.",
            "sqlGenerationReasoning": None,
            "error": answer_detail.get("error"),
            "processSteps": [
                {"key": "understand", "title": _step_title("understand", language), "status": "FINISHED", "detail": _step_detail_text("in_project_general", language)},
                {"key": "answer", "title": _step_title("answer", language), "status": answer_detail["status"], "detail": answer_detail.get("content")},
            ],
        }
        breakdown_detail = {
            "status": answer_detail["status"],
            "description": "Classified as project-scoped general chat; no SQL execution was attempted.",
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
        analysis = _analyze_question(question, project_id, previous_questions)
        _switch_stage("retrieve")
        route = _classify_question_route(question, project_id, previous_questions, analysis)
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

        def _emit_generation_decision(generated_payload: dict[str, Any] | None = None, *, requires_sql: bool) -> None:
            if not emit_route_events:
                return
            strict_json = _strict_json_capability()
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
            decision = _GENERATION_ROUTER.build_decision(
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
                "intentReasoning": route.get("reasoning") or "No matching project metadata was found; answered without SQL.",
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
                "description": "No project metadata matched this question, so PrismBI answered with the LLM and skipped SQL generation.",
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
            generation_reason = generated.get("reasoning") or generated.get("summary") or "Failed to generate SQL."
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
                    "intentReasoning": route.get("reasoning") or "Matched project metadata but SQL generation failed; answered with LLM.",
                    "sqlGenerationReasoning": generation_reason,
                    "error": generation_reason,
                    "processSteps": [
                        {"key": "understand", "title": _step_title("understand", language), "status": "FINISHED", "detail": route.get("reasoning")},
                        {"key": "retrieve", "title": _step_title("retrieve", language), "status": "FINISHED", "detail": _retrieve_detail(generated, route, language) if not _in_chinese(language) else f"命中表: {', '.join(generated.get('retrieved_tables', [])) or '无'}; 知识命中: {len((route.get('knowledge_hits') or {}).get('instructions') or [])} instructions, {len((route.get('knowledge_hits') or {}).get('sql_pairs') or [])} sql pairs"},
                        {"key": "organize", "title": _step_title("organize", language), "status": "FAILED", "detail": generation_reason},
                        {"key": "answer", "title": _step_title("answer", language), "status": "FINISHED", "detail": fallback["content"][:200]},
                    ],
                }
                breakdown_detail = {
                    "status": "FAILED",
                    "description": "SQL generation failed after retries; PrismBI fell back to a general LLM answer.",
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
            content = _in_chinese(language) and "无法生成有效的 SQL 查询，请尝试重新描述您的问题。" or "Unable to generate a valid SQL query. Please try rephrasing your question."
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
                "intentReasoning": route.get("reasoning") or "Matched project metadata but SQL generation failed.",
                "sqlGenerationReasoning": generation_reason,
                "error": generation_reason,
                "processSteps": [
                    {"key": "understand", "title": _step_title("understand", language), "status": "FINISHED", "detail": route.get("reasoning")},
                    {"key": "retrieve", "title": _step_title("retrieve", language), "status": "FINISHED", "detail": _retrieve_detail(generated, route, language) if not _in_chinese(language) else f"命中表: {', '.join(generated.get('retrieved_tables', [])) or '无'}; 知识命中: {len((route.get('knowledge_hits') or {}).get('instructions') or [])} instructions, {len((route.get('knowledge_hits') or {}).get('sql_pairs') or [])} sql pairs"},
                    {"key": "organize", "title": _step_title("organize", language), "status": "FAILED", "detail": generation_reason},
                ],
            }
            breakdown_detail = {
                "status": "FAILED",
                "description": "SQL generation failed after retries.",
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
            raise ValueError(supplemental.get("content") or "LLM provider is not configured for supplemental answer.")
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
            "intentReasoning": route.get("reasoning") or "Matched project metadata and routed the data-backed part to SQL.",
            "sqlGenerationReasoning": generated.get("reasoning"),
            "error": None,
            "metadataQuestionPart": metadata_part,
            "nonMetadataQuestionPart": non_metadata_part,
            "sqlEngine": generated.get("sql_engine"),
            "knowledgeHits": route.get("knowledge_hits"),
            "processSteps": [
                {"key": "understand", "title": _step_title("understand", language), "status": "FINISHED", "detail": route.get("reasoning")},
                {"key": "retrieve", "title": _step_title("retrieve", language), "status": "FINISHED", "detail": _retrieve_detail(generated, route, language) if not _in_chinese(language) else f"命中表: {', '.join(generated.get('retrieved_tables', [])) or '无'}; 知识命中: {len((route.get('knowledge_hits') or {}).get('instructions') or [])} instructions, {len((route.get('knowledge_hits') or {}).get('sql_pairs') or [])} sql pairs"},
                {"key": "organize", "title": _step_title("organize", language), "status": "FINISHED", "detail": repair_reasoning or generated.get("reasoning") or generated.get("summary")},
                {"key": "execute", "title": _step_title("execute", language), "status": "FINISHED", "detail": _execution_detail(query_result, language) if not _in_chinese(language) else f"返回 {query_result['total_rows']} 行, 耗时 {query_result['execution_time_ms']} ms。"},
                {"key": "answer", "title": _step_title("answer", language), "status": "FINISHED", "detail": content},
            ],
        }
        steps = ["interpret_question", "retrieve_metadata", "route_metadata_and_llm_parts", "generate_sql", "security_plan", "execute_query"]
        if non_metadata_part:
            steps.append("complete_non_metadata_part")
        steps.append("compose_final_answer")
        breakdown_detail = {
            "status": "FINISHED",
            "description": "Matched metadata was answered through SQL execution; unmatched question text was completed by the LLM before composing the final answer." if non_metadata_part else "Matched metadata, generated SQL, applied security policy planning, executed the query, and summarized the result.",
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
            content = "SQL 执行失败，请在 SQL 视图中调整后重新运行。" if _language_name(language).lower().startswith("chinese") or str(language or "").lower().startswith("zh") else "SQL execution failed. Adjust it in SQL view and run it again."
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
                "intentReasoning": route.get("reasoning") or "Matched project metadata and attempted SQL execution.",
                "sqlGenerationReasoning": generated.get("reasoning"),
                "error": error_message,
                "metadataQuestionPart": metadata_part,
                "nonMetadataQuestionPart": non_metadata_part,
                "sqlEngine": generated.get("sql_engine"),
                "knowledgeHits": route.get("knowledge_hits"),
                "processSteps": [
                    {"key": "understand", "title": _step_title("understand", language), "status": "FINISHED", "detail": route.get("reasoning")},
                    {"key": "retrieve", "title": _step_title("retrieve", language), "status": "FINISHED", "detail": _retrieve_detail(generated, route, language) if not _in_chinese(language) else f"命中表: {', '.join(generated.get('retrieved_tables', [])) or '无'}; 知识命中: {len((route.get('knowledge_hits') or {}).get('instructions') or [])} instructions, {len((route.get('knowledge_hits') or {}).get('sql_pairs') or [])} sql pairs"},
                    {"key": "organize", "title": _step_title("organize", language), "status": "FINISHED", "detail": generated.get("reasoning") or generated.get("summary")},
                    {"key": "execute", "title": _step_title("execute", language), "status": "FAILED", "detail": error_message},
                ],
            }
            breakdown_detail = {
                "status": "FAILED",
                "description": "SQL generation succeeded but execution failed. The response remains in SQL view so the user can adjust and rerun it.",
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
            fallback = _fallback_answer_after_sql_failure(question, project_id, safe_exc, previous_questions, previous_answers, language)
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
                "intentReasoning": "Metadata-to-SQL failed; answered with project context as a normal LLM response.",
                "sqlGenerationReasoning": None,
                "error": safe_exc,
                "processSteps": [
                    {"key": "retrieve", "title": _step_title("retrieve", language), "status": "FAILED", "detail": safe_exc},
                    {"key": "answer", "title": _step_title("answer", language), "status": "FINISHED", "detail": fallback_content},
                ],
            }
            breakdown_detail = {
                "status": "FAILED",
                "description": "SQL generation or execution failed, then PrismBI fell back to a project-context LLM answer.",
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
