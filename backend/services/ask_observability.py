from __future__ import annotations

import hashlib
import importlib
import json
import logging
import math
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

import duckdb
import sqlglot
from sqlglot import exp

from db import connection_lock, get_connection
from services.prompt_templates import (
    localized_contract,
)
from services.security_policy_service import plan_secured_sql

LOGGER = logging.getLogger(__name__)

# ── External bindings (resolved from ask_service at import time) ─────
# These are defined in ask_service.py and imported here to allow bare-name
# resolution inside functions. Python function objects use their original
# __globals__ (ask_service), so call-chains resolve correctly.
# Note: importlib is already imported above.
_ask = importlib.import_module('services.ask_service')

# constants
MAX_SQL_ROWS = _ask.MAX_SQL_ROWS
DEFAULT_PREVIEW_ROW_LIMIT = _ask.DEFAULT_PREVIEW_ROW_LIMIT
MIN_PREVIEW_ROW_LIMIT = _ask.MIN_PREVIEW_ROW_LIMIT
MAX_PREVIEW_ROW_LIMIT = _ask.MAX_PREVIEW_ROW_LIMIT
MIN_EXECUTION_ROW_LIMIT = _ask.MIN_EXECUTION_ROW_LIMIT
MAX_EXECUTION_ROW_LIMIT = _ask.MAX_EXECUTION_ROW_LIMIT
# ROUTER_CONFIG must stay a shared reference (mutable dict)
ROUTER_CONFIG = _ask.ROUTER_CONFIG

# preamble functions used by obs code
_build_metadata_summary = _ask._build_metadata_summary
_coerce_float_setting = _ask._coerce_float_setting
_coerce_int_setting = _ask._coerce_int_setting
_collect_aliases = _ask._collect_aliases
_column_alias_strings = _ask._column_alias_strings
_contains_cjk = _ask._contains_cjk
_current_llm_model_tier = _ask._current_llm_model_tier
_emit_route_event = _ask._emit_route_event
_extract_duplicate_alias_name = _ask._extract_duplicate_alias_name
_fallback_column_aliases = _ask._fallback_column_aliases
_get_execution_router = _ask._get_execution_router
_get_generation_router = _ask._get_generation_router
_identifier_markers = _ask._identifier_markers
_is_sql_route_v2_enabled = _ask._is_sql_route_v2_enabled
_json_dumps = _ask._json_dumps
_language_instruction = _ask._language_instruction
_llm_chat_with_response_format_fallback = _ask._llm_chat_with_response_format_fallback
_llm_content_text = _ask._llm_content_text
_max_id = _ask._max_id
_normalize_bool = _ask._normalize_bool
_normalize_sql_candidate = _ask._normalize_sql_candidate
_normalize_sql_text = _ask._normalize_sql_text
_project_meta = _ask._project_meta
_prompt_profile_selection = _ask._prompt_profile_selection
_render_system_prompt = _ask._render_system_prompt
_rewrite_bracket_identifiers_for_duckdb = _ask._rewrite_bracket_identifiers_for_duckdb
_safe_json_loads = _ask._safe_json_loads
_sanitize_error_message = _ask._sanitize_error_message
_strict_json_capability = _ask._strict_json_capability
_tokenize_cached = _ask._tokenize_cached
refresh_runtime_router_settings = _ask.refresh_runtime_router_settings
CandidateGuard = _ask.CandidateGuard
ExecutionPipeline = _ask.ExecutionPipeline
GenerationPipeline = _ask.GenerationPipeline
LLMService = _ask.LLMService
parse_json_object = _ask.parse_json_object
get_strict_json_capability = _ask.get_strict_json_capability

# additional ask_service functions and constants used by obs code
_primary_datasource_type = _ask._primary_datasource_type
_build_alias_map = _ask._build_alias_map
_binding_rows = _ask._binding_rows
_models_by_binding = _ask._models_by_binding
_apply_limit = _ask._apply_limit
_dialect_for_ds = _ask._dialect_for_ds
_resolve_table_alias = _ask._resolve_table_alias
_quote_identifier = _ask._quote_identifier
_ALIAS_SCOPE_ISSUE_RE = _ask._ALIAS_SCOPE_ISSUE_RE
_UNQUALIFIED_ALIAS_SCOPE_ISSUE_RE = _ask._UNQUALIFIED_ALIAS_SCOPE_ISSUE_RE
_HALLUCINATED_COLUMN_ISSUE_RE = _ask._HALLUCINATED_COLUMN_ISSUE_RE
_HALLUCINATED_QUANTITY_TOKENS = _ask._HALLUCINATED_QUANTITY_TOKENS
_CLAUSE_SPLIT_RE = _ask._CLAUSE_SPLIT_RE
_DATA_ROUTE_INDICATORS = _ask._DATA_ROUTE_INDICATORS
_CACHE_TTL_SECONDS = _ask._CACHE_TTL_SECONDS
_analysis_cache = _ask._analysis_cache
_analysis_cache_max = _ask._analysis_cache_max
_analysis_cache_lock = _ask._analysis_cache_lock
_analysis_cache_computing = _ask._analysis_cache_computing


# ── Observable subsystem ────────────────────────────────────────────
# TODO: Extract to ask_observability.py
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


def _percentile_from_sorted(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    try:
        normalized_percentile = float(percentile)
    except Exception:
        normalized_percentile = 0.0
    normalized_percentile = min(1.0, max(0.0, normalized_percentile))
    if normalized_percentile <= 0.0:
        return float(values[0])
    index = max(0, math.ceil(len(values) * normalized_percentile) - 1)
    return float(values[index])


def _execution_metric_summary(bucket: dict[str, Any]) -> dict[str, Any]:
    latencies = list(bucket.get("latencies_ms") or [])
    sorted_latencies = sorted(float(item) for item in latencies if isinstance(item, (int, float)))
    p95_ms = 0.0
    avg_ms = 0.0
    if sorted_latencies:
        p95_ms = round(_percentile_from_sorted(sorted_latencies, 0.95), 2)
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
        "generation_fallback_chain_step": {},
        "generation_fallback_chain_pattern": {},
        "repair_used": 0,
        "generation_retry_reason": {},
        "validation_issue_bucket": {},
        "validation_issue_bucket_transition": {},
        "llm_empty_response_retry": 0,
        "repair_guard_blocked": 0,
        "repair_short_circuit": 0,
        "repair_short_circuit_reason": {},
        "repair_short_circuit_issue_bucket": {},
        "repair_short_circuit_dominant_issue_bucket": {},
        "repair_short_circuit_issue_bucket_streak_max": 0,
        "repair_short_circuit_circuitable_issue_bucket_streak_max": 0,
        "schema_link_fallback_total": 0,
        "schema_link_fallback_reason": {},
        "sql_generation_fallback_total": 0,
        "sql_generation_fallback_reason": {},
        "final_answer_fallback_total": 0,
        "final_answer_fallback_reason": {},
        "decompose_stage_total": 0,
        "decompose_stage_status": {},
        "decompose_stage_reason": {},
        "decompose_stage_elapsed_ms_total": 0.0,
        "decompose_stage_elapsed_ms_max": 0.0,
        "decompose_stage_budget_exceeded": 0,
        "duckdb_did_you_mean_fix_total": 0,
        "duckdb_did_you_mean_fix_status": {},
        "duckdb_did_you_mean_fix_applied": 0,
        "last_updated": 0.0,
    }


def _route_observability_window_seconds() -> int:
    return _coerce_int_setting(
        ROUTER_CONFIG.get("route_observability_window_seconds"),
        1800,
        300,
        86400,
    )


def _route_alert_threshold_snapshot() -> dict[str, Any]:
    repair_timeout_warning_rate = _coerce_float_setting(
        ROUTER_CONFIG.get("route_alert_repair_timeout_short_circuit_warning_rate"),
        0.25,
        0.01,
        1.0,
    )
    repair_timeout_critical_rate = _coerce_float_setting(
        ROUTER_CONFIG.get("route_alert_repair_timeout_short_circuit_critical_rate"),
        max(repair_timeout_warning_rate, 0.45),
        0.01,
        1.0,
    )
    if repair_timeout_critical_rate < repair_timeout_warning_rate:
        repair_timeout_critical_rate = repair_timeout_warning_rate
    repair_timeout_min_warning_events = _coerce_int_setting(
        ROUTER_CONFIG.get("route_alert_repair_timeout_short_circuit_min_warning_events"),
        6,
        1,
        10000,
    )
    repair_timeout_min_critical_events = _coerce_int_setting(
        ROUTER_CONFIG.get("route_alert_repair_timeout_short_circuit_min_critical_events"),
        max(repair_timeout_min_warning_events, 12),
        1,
        10000,
    )
    if repair_timeout_min_critical_events < repair_timeout_min_warning_events:
        repair_timeout_min_critical_events = repair_timeout_min_warning_events

    repair_budget_warning_rate = _coerce_float_setting(
        ROUTER_CONFIG.get("route_alert_repair_budget_low_short_circuit_warning_rate"),
        0.20,
        0.01,
        1.0,
    )
    repair_budget_critical_rate = _coerce_float_setting(
        ROUTER_CONFIG.get("route_alert_repair_budget_low_short_circuit_critical_rate"),
        max(repair_budget_warning_rate, 0.35),
        0.01,
        1.0,
    )
    if repair_budget_critical_rate < repair_budget_warning_rate:
        repair_budget_critical_rate = repair_budget_warning_rate
    repair_budget_min_warning_events = _coerce_int_setting(
        ROUTER_CONFIG.get("route_alert_repair_budget_low_short_circuit_min_warning_events"),
        6,
        1,
        10000,
    )
    repair_budget_min_critical_events = _coerce_int_setting(
        ROUTER_CONFIG.get("route_alert_repair_budget_low_short_circuit_min_critical_events"),
        max(repair_budget_min_warning_events, 12),
        1,
        10000,
    )
    if repair_budget_min_critical_events < repair_budget_min_warning_events:
        repair_budget_min_critical_events = repair_budget_min_warning_events

    json_reask_warning_rate = _coerce_float_setting(
        ROUTER_CONFIG.get("route_alert_json_reask_warning_rate"),
        0.20,
        0.01,
        1.0,
    )
    json_reask_critical_rate = _coerce_float_setting(
        ROUTER_CONFIG.get("route_alert_json_reask_critical_rate"),
        max(json_reask_warning_rate, 0.40),
        0.01,
        1.0,
    )
    if json_reask_critical_rate < json_reask_warning_rate:
        json_reask_critical_rate = json_reask_warning_rate
    json_reask_min_warning_decisions = _coerce_int_setting(
        ROUTER_CONFIG.get("route_alert_json_reask_min_warning_decisions"),
        10,
        1,
        10000,
    )
    json_reask_min_critical_decisions = _coerce_int_setting(
        ROUTER_CONFIG.get("route_alert_json_reask_min_critical_decisions"),
        max(json_reask_min_warning_decisions, 20),
        1,
        10000,
    )
    if json_reask_min_critical_decisions < json_reask_min_warning_decisions:
        json_reask_min_critical_decisions = json_reask_min_warning_decisions

    decompose_cancelled_warning_rate = _coerce_float_setting(
        ROUTER_CONFIG.get("route_alert_decompose_cancelled_warning_rate"),
        0.15,
        0.01,
        1.0,
    )
    decompose_cancelled_critical_rate = _coerce_float_setting(
        ROUTER_CONFIG.get("route_alert_decompose_cancelled_critical_rate"),
        max(decompose_cancelled_warning_rate, 0.30),
        0.01,
        1.0,
    )
    if decompose_cancelled_critical_rate < decompose_cancelled_warning_rate:
        decompose_cancelled_critical_rate = decompose_cancelled_warning_rate
    decompose_cancelled_min_warning_events = _coerce_int_setting(
        ROUTER_CONFIG.get("route_alert_decompose_cancelled_min_warning_events"),
        6,
        1,
        10000,
    )
    decompose_cancelled_min_critical_events = _coerce_int_setting(
        ROUTER_CONFIG.get("route_alert_decompose_cancelled_min_critical_events"),
        max(decompose_cancelled_min_warning_events, 12),
        1,
        10000,
    )
    if decompose_cancelled_min_critical_events < decompose_cancelled_min_warning_events:
        decompose_cancelled_min_critical_events = decompose_cancelled_min_warning_events

    return {
        "route_alert_repair_timeout_short_circuit_warning_rate": float(repair_timeout_warning_rate),
        "route_alert_repair_timeout_short_circuit_critical_rate": float(repair_timeout_critical_rate),
        "route_alert_repair_timeout_short_circuit_min_warning_events": int(repair_timeout_min_warning_events),
        "route_alert_repair_timeout_short_circuit_min_critical_events": int(repair_timeout_min_critical_events),
        "route_alert_repair_budget_low_short_circuit_warning_rate": float(repair_budget_warning_rate),
        "route_alert_repair_budget_low_short_circuit_critical_rate": float(repair_budget_critical_rate),
        "route_alert_repair_budget_low_short_circuit_min_warning_events": int(repair_budget_min_warning_events),
        "route_alert_repair_budget_low_short_circuit_min_critical_events": int(repair_budget_min_critical_events),
        "route_alert_json_reask_warning_rate": float(json_reask_warning_rate),
        "route_alert_json_reask_critical_rate": float(json_reask_critical_rate),
        "route_alert_json_reask_min_warning_decisions": int(json_reask_min_warning_decisions),
        "route_alert_json_reask_min_critical_decisions": int(json_reask_min_critical_decisions),
        "route_alert_decompose_cancelled_warning_rate": float(decompose_cancelled_warning_rate),
        "route_alert_decompose_cancelled_critical_rate": float(decompose_cancelled_critical_rate),
        "route_alert_decompose_cancelled_min_warning_events": int(decompose_cancelled_min_warning_events),
        "route_alert_decompose_cancelled_min_critical_events": int(decompose_cancelled_min_critical_events),
    }


def _inject_route_alert_threshold_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    merged = dict(snapshot or {})
    merged.update(_route_alert_threshold_snapshot())
    return merged


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
        "generation_fallback_chain_step": {},
        "generation_fallback_chain_pattern": {},
        "schema_link_fallback_total": 0,
        "schema_link_fallback_reason": {},
        "schema_link_fallback_rate": 0.0,
        "sql_generation_fallback_total": 0,
        "sql_generation_fallback_reason": {},
        "sql_generation_fallback_rate": 0.0,
        "final_answer_fallback_total": 0,
        "final_answer_fallback_reason": {},
        "final_answer_fallback_rate": 0.0,
        "decompose_stage_total": 0,
        "decompose_stage_status": {},
        "decompose_stage_reason": {},
        "decompose_stage_elapsed_ms_avg": 0.0,
        "decompose_stage_elapsed_ms_p50": 0.0,
        "decompose_stage_elapsed_ms_p95": 0.0,
        "decompose_stage_elapsed_ms_max": 0.0,
        "decompose_stage_budget_exceeded": 0,
        "duckdb_did_you_mean_fix_total": 0,
        "duckdb_did_you_mean_fix_status": {},
        "duckdb_did_you_mean_fix_applied": 0,
        "repair_used": 0,
        "generation_retry_reason": {},
        "validation_issue_bucket": {},
        "validation_issue_bucket_transition": {},
        "llm_empty_response_retry": 0,
        "repair_guard_blocked": 0,
        "repair_short_circuit": 0,
        "repair_short_circuit_reason": {},
        "repair_short_circuit_issue_bucket": {},
        "repair_short_circuit_dominant_issue_bucket": {},
        "repair_short_circuit_issue_bucket_streak_max": 0,
        "repair_short_circuit_circuitable_issue_bucket_streak_max": 0,
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
    decompose_stage_total = int(bucket.get("decompose_stage_total") or 0)
    try:
        decompose_stage_elapsed_total = max(0.0, float(bucket.get("decompose_stage_elapsed_ms_total") or 0.0))
    except Exception:
        decompose_stage_elapsed_total = 0.0
    try:
        decompose_stage_elapsed_max = max(0.0, float(bucket.get("decompose_stage_elapsed_ms_max") or 0.0))
    except Exception:
        decompose_stage_elapsed_max = 0.0
    elapsed_samples = bucket.get("decompose_stage_elapsed_ms_samples")
    if isinstance(elapsed_samples, list):
        sorted_elapsed_samples = sorted(
            max(0.0, float(item))
            for item in elapsed_samples
            if isinstance(item, (int, float))
            and math.isfinite(float(item))
        )
    else:
        sorted_elapsed_samples = []
    if sorted_elapsed_samples:
        decompose_stage_elapsed_p50 = _percentile_from_sorted(sorted_elapsed_samples, 0.50)
        decompose_stage_elapsed_p95 = _percentile_from_sorted(sorted_elapsed_samples, 0.95)
    else:
        decompose_stage_elapsed_p50 = (
            decompose_stage_elapsed_total / decompose_stage_total
            if decompose_stage_total > 0
            else 0.0
        )
        decompose_stage_elapsed_p95 = max(decompose_stage_elapsed_p50, decompose_stage_elapsed_max)
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
        "generation_fallback_chain_step": dict(bucket.get("generation_fallback_chain_step") or {}),
        "generation_fallback_chain_pattern": dict(bucket.get("generation_fallback_chain_pattern") or {}),
        "schema_link_fallback_total": schema_link_fallback_total,
        "schema_link_fallback_reason": dict(bucket.get("schema_link_fallback_reason") or {}),
        "schema_link_fallback_rate": round(schema_link_fallback_total / generation_total, 4) if generation_total > 0 else 0.0,
        "sql_generation_fallback_total": sql_generation_fallback_total,
        "sql_generation_fallback_reason": dict(bucket.get("sql_generation_fallback_reason") or {}),
        "sql_generation_fallback_rate": round(sql_generation_fallback_total / generation_total, 4) if generation_total > 0 else 0.0,
        "final_answer_fallback_total": final_answer_fallback_total,
        "final_answer_fallback_reason": dict(bucket.get("final_answer_fallback_reason") or {}),
        "final_answer_fallback_rate": round(final_answer_fallback_total / generation_total, 4) if generation_total > 0 else 0.0,
        "decompose_stage_total": decompose_stage_total,
        "decompose_stage_status": dict(bucket.get("decompose_stage_status") or {}),
        "decompose_stage_reason": dict(bucket.get("decompose_stage_reason") or {}),
        "decompose_stage_elapsed_ms_avg": round(decompose_stage_elapsed_total / decompose_stage_total, 3)
        if decompose_stage_total > 0
        else 0.0,
        "decompose_stage_elapsed_ms_p50": round(max(0.0, decompose_stage_elapsed_p50), 3),
        "decompose_stage_elapsed_ms_p95": round(max(0.0, decompose_stage_elapsed_p95), 3),
        "decompose_stage_elapsed_ms_max": round(decompose_stage_elapsed_max, 3),
        "decompose_stage_budget_exceeded": int(bucket.get("decompose_stage_budget_exceeded") or 0),
        "duckdb_did_you_mean_fix_total": int(bucket.get("duckdb_did_you_mean_fix_total") or 0),
        "duckdb_did_you_mean_fix_status": dict(bucket.get("duckdb_did_you_mean_fix_status") or {}),
        "duckdb_did_you_mean_fix_applied": int(bucket.get("duckdb_did_you_mean_fix_applied") or 0),
        "repair_used": int(bucket.get("repair_used") or 0),
        "generation_retry_reason": dict(bucket.get("generation_retry_reason") or {}),
        "validation_issue_bucket": dict(bucket.get("validation_issue_bucket") or {}),
        "validation_issue_bucket_transition": dict(bucket.get("validation_issue_bucket_transition") or {}),
        "llm_empty_response_retry": int(bucket.get("llm_empty_response_retry") or 0),
        "repair_guard_blocked": int(bucket.get("repair_guard_blocked") or 0),
        "repair_short_circuit": int(bucket.get("repair_short_circuit") or 0),
        "repair_short_circuit_reason": dict(bucket.get("repair_short_circuit_reason") or {}),
        "repair_short_circuit_issue_bucket": dict(bucket.get("repair_short_circuit_issue_bucket") or {}),
        "repair_short_circuit_dominant_issue_bucket": dict(bucket.get("repair_short_circuit_dominant_issue_bucket") or {}),
        "repair_short_circuit_issue_bucket_streak_max": int(bucket.get("repair_short_circuit_issue_bucket_streak_max") or 0),
        "repair_short_circuit_circuitable_issue_bucket_streak_max": int(bucket.get("repair_short_circuit_circuitable_issue_bucket_streak_max") or 0),
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
    decompose_stage_elapsed_samples: list[float] = []
    for event_ts, marker, event_payload in history:
        if event_ts < window_start:
            continue
        _apply_route_dimension_event_to_bucket(window_bucket, marker, event_payload, event_ts)
        if marker == "sql_generation_decompose_stage":
            try:
                elapsed_ms = max(0.0, float(event_payload.get("elapsed_ms") or 0.0))
            except Exception:
                elapsed_ms = 0.0
            decompose_stage_elapsed_samples.append(elapsed_ms)
    window_bucket["decompose_stage_elapsed_ms_samples"] = decompose_stage_elapsed_samples
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

    decompose_stage_elapsed_avg = _coerce_non_negative_float(parsed.get("decompose_stage_elapsed_ms_avg"), 0.0)
    decompose_stage_elapsed_max = _coerce_non_negative_float(parsed.get("decompose_stage_elapsed_ms_max"), 0.0)

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
        "generation_fallback_chain_step": _coerce_counter_map(parsed.get("generation_fallback_chain_step")),
        "generation_fallback_chain_pattern": _coerce_counter_map(parsed.get("generation_fallback_chain_pattern")),
        "schema_link_fallback_total": _coerce_non_negative_int(parsed.get("schema_link_fallback_total"), 0),
        "schema_link_fallback_reason": _coerce_counter_map(parsed.get("schema_link_fallback_reason")),
        "schema_link_fallback_rate": _coerce_non_negative_float(parsed.get("schema_link_fallback_rate"), 0.0),
        "sql_generation_fallback_total": _coerce_non_negative_int(parsed.get("sql_generation_fallback_total"), 0),
        "sql_generation_fallback_reason": _coerce_counter_map(parsed.get("sql_generation_fallback_reason")),
        "sql_generation_fallback_rate": _coerce_non_negative_float(parsed.get("sql_generation_fallback_rate"), 0.0),
        "final_answer_fallback_total": _coerce_non_negative_int(parsed.get("final_answer_fallback_total"), 0),
        "final_answer_fallback_reason": _coerce_counter_map(parsed.get("final_answer_fallback_reason")),
        "final_answer_fallback_rate": _coerce_non_negative_float(parsed.get("final_answer_fallback_rate"), 0.0),
        "decompose_stage_total": _coerce_non_negative_int(parsed.get("decompose_stage_total"), 0),
        "decompose_stage_status": _coerce_counter_map(parsed.get("decompose_stage_status")),
        "decompose_stage_reason": _coerce_counter_map(parsed.get("decompose_stage_reason")),
        "decompose_stage_elapsed_ms_avg": decompose_stage_elapsed_avg,
        "decompose_stage_elapsed_ms_p50": _coerce_non_negative_float(
            parsed.get("decompose_stage_elapsed_ms_p50"),
            decompose_stage_elapsed_avg,
        ),
        "decompose_stage_elapsed_ms_p95": _coerce_non_negative_float(
            parsed.get("decompose_stage_elapsed_ms_p95"),
            max(decompose_stage_elapsed_avg, decompose_stage_elapsed_max),
        ),
        "decompose_stage_elapsed_ms_max": decompose_stage_elapsed_max,
        "decompose_stage_budget_exceeded": _coerce_non_negative_int(parsed.get("decompose_stage_budget_exceeded"), 0),
        "duckdb_did_you_mean_fix_total": _coerce_non_negative_int(parsed.get("duckdb_did_you_mean_fix_total"), 0),
        "duckdb_did_you_mean_fix_status": _coerce_counter_map(parsed.get("duckdb_did_you_mean_fix_status")),
        "duckdb_did_you_mean_fix_applied": _coerce_non_negative_int(parsed.get("duckdb_did_you_mean_fix_applied"), 0),
        "repair_used": _coerce_non_negative_int(parsed.get("repair_used"), 0),
        "generation_retry_reason": _coerce_counter_map(parsed.get("generation_retry_reason")),
        "validation_issue_bucket": _coerce_counter_map(parsed.get("validation_issue_bucket")),
        "validation_issue_bucket_transition": _coerce_counter_map(parsed.get("validation_issue_bucket_transition")),
        "llm_empty_response_retry": _coerce_non_negative_int(parsed.get("llm_empty_response_retry"), 0),
        "repair_guard_blocked": _coerce_non_negative_int(parsed.get("repair_guard_blocked"), 0),
        "repair_short_circuit": _coerce_non_negative_int(parsed.get("repair_short_circuit"), 0),
        "repair_short_circuit_reason": _coerce_counter_map(parsed.get("repair_short_circuit_reason")),
        "repair_short_circuit_issue_bucket": _coerce_counter_map(parsed.get("repair_short_circuit_issue_bucket")),
        "repair_short_circuit_dominant_issue_bucket": _coerce_counter_map(parsed.get("repair_short_circuit_dominant_issue_bucket")),
        "repair_short_circuit_issue_bucket_streak_max": _coerce_non_negative_int(parsed.get("repair_short_circuit_issue_bucket_streak_max"), 0),
        "repair_short_circuit_circuitable_issue_bucket_streak_max": _coerce_non_negative_int(parsed.get("repair_short_circuit_circuitable_issue_bucket_streak_max"), 0),
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
        issue_bucket = str(payload.get("issue_bucket") or "").strip()
        if issue_bucket:
            compact["issue_bucket"] = issue_bucket
        dominant_issue_bucket = str(payload.get("dominant_issue_bucket") or "").strip()
        if dominant_issue_bucket:
            compact["dominant_issue_bucket"] = dominant_issue_bucket
        try:
            issue_bucket_streak = max(0, int(payload.get("issue_bucket_streak") or 0))
        except Exception:
            issue_bucket_streak = 0
        if issue_bucket_streak > 0:
            compact["issue_bucket_streak"] = issue_bucket_streak
        try:
            circuitable_issue_bucket_streak = max(0, int(payload.get("circuitable_issue_bucket_streak") or 0))
        except Exception:
            circuitable_issue_bucket_streak = 0
        if circuitable_issue_bucket_streak > 0:
            compact["circuitable_issue_bucket_streak"] = circuitable_issue_bucket_streak
        return compact
    if marker in {"schema_link_fallback", "sql_generation_fallback", "final_answer_fallback"}:
        compact["reason"] = str(payload.get("reason") or "unknown")
        return compact
    if marker == "sql_generation_decompose_stage":
        compact["status"] = str(payload.get("status") or "unknown")
        compact["reason"] = str(payload.get("reason") or "unknown")
        compact["elapsed_ms"] = _coerce_non_negative_float(payload.get("elapsed_ms"), 0.0)
        return compact
    if marker == "duckdb_did_you_mean_fix":
        compact["status"] = str(payload.get("status") or "unknown")
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
        normalized_chain: list[str] = []
        for item in fallback_chain[:8]:
            step = str(item or "").strip().lower()
            step = re.sub(r"[^a-z0-9_.-]+", "_", step)
            step = step.strip("_")
            if step:
                normalized_chain.append(step)
        if normalized_chain:
            compact["fallback_chain"] = normalized_chain
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
        issue_bucket = str(payload.get("issue_bucket") or "").strip()
        if issue_bucket:
            _increment_counter(bucket.setdefault("repair_short_circuit_issue_bucket", {}), issue_bucket)
        dominant_issue_bucket = str(payload.get("dominant_issue_bucket") or "").strip()
        if dominant_issue_bucket:
            _increment_counter(
                bucket.setdefault("repair_short_circuit_dominant_issue_bucket", {}),
                dominant_issue_bucket,
            )
        try:
            issue_bucket_streak = max(0, int(payload.get("issue_bucket_streak") or 0))
        except Exception:
            issue_bucket_streak = 0
        bucket["repair_short_circuit_issue_bucket_streak_max"] = max(
            int(bucket.get("repair_short_circuit_issue_bucket_streak_max") or 0),
            issue_bucket_streak,
        )
        try:
            circuitable_issue_bucket_streak = max(0, int(payload.get("circuitable_issue_bucket_streak") or 0))
        except Exception:
            circuitable_issue_bucket_streak = 0
        bucket["repair_short_circuit_circuitable_issue_bucket_streak_max"] = max(
            int(bucket.get("repair_short_circuit_circuitable_issue_bucket_streak_max") or 0),
            circuitable_issue_bucket_streak,
        )
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
    if marker == "sql_generation_decompose_stage":
        bucket["decompose_stage_total"] = int(bucket.get("decompose_stage_total") or 0) + 1
        status = str(payload.get("status") or "unknown")
        reason = str(payload.get("reason") or "unknown")
        _increment_counter(bucket.setdefault("decompose_stage_status", {}), status)
        _increment_counter(bucket.setdefault("decompose_stage_reason", {}), reason)
        if reason == "budget_exceeded":
            bucket["decompose_stage_budget_exceeded"] = int(bucket.get("decompose_stage_budget_exceeded") or 0) + 1
        try:
            elapsed_ms = max(0.0, float(payload.get("elapsed_ms") or 0.0))
        except Exception:
            elapsed_ms = 0.0
        bucket["decompose_stage_elapsed_ms_total"] = float(bucket.get("decompose_stage_elapsed_ms_total") or 0.0) + elapsed_ms
        bucket["decompose_stage_elapsed_ms_max"] = max(float(bucket.get("decompose_stage_elapsed_ms_max") or 0.0), elapsed_ms)
        return
    if marker == "duckdb_did_you_mean_fix":
        bucket["duckdb_did_you_mean_fix_total"] = int(bucket.get("duckdb_did_you_mean_fix_total") or 0) + 1
        status = str(payload.get("status") or "unknown")
        _increment_counter(bucket.setdefault("duckdb_did_you_mean_fix_status", {}), status)
        if status == "applied":
            bucket["duckdb_did_you_mean_fix_applied"] = int(bucket.get("duckdb_did_you_mean_fix_applied") or 0) + 1
        return
    if marker in {"sql_validation_issue", "repair_guard_blocked"}:
        if marker == "repair_guard_blocked":
            bucket["repair_guard_blocked"] = int(bucket.get("repair_guard_blocked") or 0) + 1
        normalized_issue_buckets: dict[str, int] = {}
        issue_buckets = payload.get("issue_buckets")
        if isinstance(issue_buckets, dict):
            for issue, amount in issue_buckets.items():
                try:
                    normalized_amount = int(amount or 0)
                except Exception:
                    normalized_amount = 0
                if normalized_amount > 0:
                    normalized_issue_buckets[str(issue or "unknown")] = normalized_amount
                _increment_counter_by(
                    bucket.setdefault("validation_issue_bucket", {}),
                    str(issue or "unknown"),
                    normalized_amount,
                )
        if marker == "sql_validation_issue" and normalized_issue_buckets:
            dominant_issue_bucket = _counter_top_key(normalized_issue_buckets)
            if dominant_issue_bucket:
                last_issue_bucket = str(bucket.get("_validation_issue_last_bucket") or "").strip()
                if last_issue_bucket and last_issue_bucket != dominant_issue_bucket:
                    _increment_counter(
                        bucket.setdefault("validation_issue_bucket_transition", {}),
                        f"{last_issue_bucket}->{dominant_issue_bucket}",
                    )
                bucket["_validation_issue_last_bucket"] = dominant_issue_bucket
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
        fallback_steps = [str(item or "").strip() for item in fallback_chain if str(item or "").strip()]
        has_repair = has_repair or any("repair" in step.lower() for step in fallback_steps)
        for step in fallback_steps:
            _increment_counter(bucket.setdefault("generation_fallback_chain_step", {}), step)
        if fallback_steps:
            chain_pattern = ">".join(fallback_steps[:4])
            _increment_counter(bucket.setdefault("generation_fallback_chain_pattern", {}), chain_pattern)
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
        return _inject_route_alert_threshold_snapshot(snapshot)

    persisted_snapshot = _load_route_observability_snapshot(pid, window_seconds)
    if persisted_snapshot is not None:
        return _inject_route_alert_threshold_snapshot(persisted_snapshot)
    return _inject_route_alert_threshold_snapshot(_route_dimension_zero_snapshot(window_seconds))


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

def _analysis_cache_key(question: str, project_id: int, previous_questions: Optional[list[str]] = None, language: Optional[str] = None) -> str:
    question_hash = hashlib.sha256(question.encode()).hexdigest()[:32]
    if not previous_questions:
        prev_hash = ""
    else:
        prev_hash = hashlib.sha256(json.dumps(previous_questions, sort_keys=True).encode()).hexdigest()[:16]
    lang_part = f"::{language}" if language else ""
    return f"{project_id}::{question_hash}::{prev_hash}{lang_part}"

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


def _normalize_question_analysis(analysis: Optional[dict[str, Any]], language: Optional[str] = None) -> dict[str, Any]:
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
        "reasoning": reasoning or _ask._sql_msg("llm_analyzer_fallback", language),
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

def _analyze_question(question: str, project_id: int, previous_questions: Optional[list[str]] = None, language: Optional[str] = None) -> dict:
    cache_key = _analysis_cache_key(question, project_id, previous_questions, language)
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
                _analysis_cache.move_to_end(cache_key)
                return normalized_cached
        if cache_key in _analysis_cache_computing:
            return _normalize_question_analysis({})
        _analysis_cache_computing.add(cache_key)
    meta = _project_meta(project_id) or {}
    default = _normalize_question_analysis({})
    llm = LLMService()
    if not llm.is_configured():
        with _analysis_cache_lock:
            _analysis_cache_computing.discard(cache_key)
        return default
    try:
        if _ask._in_chinese(language):
            few_shot_examples = (
                _ask._sql_msg("few_shot_header", language)
                + '输入："按城市和类别显示收入"\n'
                '输出：{"tier": "multi_dimension", "sub_questions": [], "entities": ["products", "orders"], "metrics": ["revenue"], "dimensions": ["city", "category"], "filters": [], "reasoning": "一个指标按两个维度细分"}\n'
                '输入："上个月总销售额是多少？另外跟我说说天气。"\n'
                '输出：{"tier": "compound", "sub_questions": ["上个月总销售额"], "entities": ["sales"], "metrics": ["total_sales"], "dimensions": ["month"], "filters": [{"field": "date", "operator": "last_month"}], "reasoning": "只有第一部分需要 SQL；天气与数据无关"}\n'
            )
        else:
            few_shot_examples = (
                _ask._sql_msg("few_shot_header", language)
                + 'Input: "Show me revenue by city and category"\n'
                'Output: {"tier": "multi_dimension", "sub_questions": [], "entities": ["products", "orders"], "metrics": ["revenue"], "dimensions": ["city", "category"], "filters": [], "reasoning": "One metric broken down by two dimensions"}\n'
                'Input: "What were total sales last month? Also tell me about the weather."\n'
                'Output: {"tier": "compound", "sub_questions": ["Total sales last month"], "entities": ["sales"], "metrics": ["total_sales"], "dimensions": ["month"], "filters": [{"field": "date", "operator": "last_month"}], "reasoning": "Only the first part requires SQL; weather is non-data"}\n'
            )
        model_tier, _ = _current_llm_model_tier(llm)
        strict_json = _strict_json_capability()
        prompt_selection = _prompt_profile_selection(
            "question_analysis",
            strict_json_mode=strict_json.get("mode", "none"),
            model_tier=model_tier,
        )
        use_profile = _is_sql_route_v2_enabled(project_id) or bool(ROUTER_CONFIG.get("sql_route_shadow_mode", False))
        system_suffix = f"\n<PROFILE>{prompt_selection.system_suffix}</PROFILE>" if use_profile and prompt_selection.system_suffix else ""
        response_format = prompt_selection.response_format if (use_profile and prompt_selection.response_format) else "json"
        messages = [
            {"role": "system", "content": f"{_render_system_prompt()}{system_suffix}\n\n{localized_contract('question_analysis', language)}{few_shot_examples}\n{_language_instruction(language)}"},
            {"role": "user", "content": f"Project: {meta.get('display_name') or meta.get('name') or project_id}\nPrevious questions: {previous_questions or []}\nQuestion: {question}"},
        ]
        result = llm.chat(messages, response_format=response_format)
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
    failed_sql: Optional[str] = None,
) -> dict[str, str]:
    selected_owner_map = {
        str(key).strip().lower(): str(value).strip().lower()
        for key, value in ((schema_link_plan or {}).get("selected_owner_map") or {}).items()
        if str(key).strip() and str(value).strip()
    }
    preferences = dict(selected_owner_map)
    model_rank = _model_rank_map(hit_models or [])
    query_tables: set[str] = set()

    if failed_sql:
        try:
            parsed = sqlglot.parse_one(_normalize_sql_text(failed_sql), read="duckdb")
            for table_expr in parsed.find_all(sqlglot.exp.Table):
                table_name = str(table_expr.name or "").strip().lower()
                if table_name:
                    query_tables.add(table_name)
        except Exception:
            query_tables = set()

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
        owners_in_query = [owner for owner in unique_owners if owner in query_tables]
        owner_pool = owners_in_query or unique_owners
        chosen_owner = sorted(owner_pool, key=_owner_key)[0]
        preferences[col_name] = chosen_owner
    return preferences


def _apply_owner_selector_rules(
    sql: str,
    hit_models: list[dict[str, Any]],
    bad_columns: Optional[list[str]] = None,
    schema_link_plan: Optional[dict[str, Any]] = None,
) -> str:
    preferences = _owner_preferences_from_issues(
        bad_columns,
        hit_models=hit_models,
        schema_link_plan=schema_link_plan,
        failed_sql=sql,
    )
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
    strategy = _get_generation_router().select_strategy(_normalize_question_analysis(analysis), bool(has_knowledge))
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
        execution_router=_get_execution_router(),
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
        resolve_model_tier=lambda: _current_llm_model_tier()[0],
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
        col_to_models: dict[str, list[str]] = {}
        for model in models:
            model_name_lower = model.get("name", "").lower()
            model_cols = model.get("matched_columns") if model.get("matched_columns") is not None else model.get("columns", [])
            for c in model_cols:
                cn = (c.get("name") or "").lower()
                if cn:
                    col_to_models.setdefault(cn, [])
                    if model_name_lower not in col_to_models[cn]:
                        col_to_models[cn].append(model_name_lower)
        shared_cols = {cn: models_list for cn, models_list in col_to_models.items() if len(models_list) > 1}
        if shared_cols:
            lines.append("AMBIGUOUS COLUMNS (appear in multiple models — you MUST pick the right owner model):")
            for cn in sorted(shared_cols):
                lines.append(f"  • {cn}: appears in {', '.join(shared_cols[cn])} — prefix with the aliased model that should own it in your query")
    # Compact column checklist for easy verification
    lines.append("AVAILABLE COLUMNS (use ONLY these — do not invent columns):")
    for model in models:
        tbl = model.get("table_reference", model.get("name", ""))
        columns = model.get("matched_columns") if model.get("matched_columns") is not None else model.get("columns", [])
        col_names = [c["name"] for c in columns if c.get("name")]
        lines.append(f"  {tbl}: {', '.join(col_names)}")
    return "\n".join(lines)


def _normalize_preview_row_limit(value: Optional[int]) -> int:
    if value is None:
        return DEFAULT_PREVIEW_ROW_LIMIT
    try:
        return max(MIN_PREVIEW_ROW_LIMIT, min(MAX_PREVIEW_ROW_LIMIT, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_PREVIEW_ROW_LIMIT


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


def _auto_thread_title(question: str, language: Optional[str] = None) -> str:
    text = re.sub(r"\s+", " ", (question or "").strip())
    text = re.sub(r"[?？。.!！]+$", "", text).strip()
    if not text:
        return _ask._sql_msg("new_session", language)
    return text[:24] if len(text) <= 24 else f"{text[:24]}..."


def update_auto_thread_summary(thread_id: int, user_id: int, question: str, language: Optional[str] = None) -> None:
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
            [_auto_thread_title(question, language), thread_id, user_id],
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




def _metadata_catalog_text(meta: Optional[dict[str, Any]], models: list[dict[str, Any]], relations: list[dict[str, Any]]) -> str:
    lines = []
    if meta:
        lines.append(f"Project: {meta.get('display_name') or meta.get('name')}")
        if meta.get("description"):
            lines.append(f"Description: {meta['description']}")
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


def _llm_semantic_matching(question: str, project_id: int, *, models: Optional[list[dict]] = None, relations: Optional[list[dict]] = None, llm: Optional[LLMService] = None, language: Optional[str] = None) -> Optional[dict[str, Any]]:
    return _llm_semantic_link(question, project_id, models=models, relations=relations, llm=llm, language=language)




def _llm_semantic_link(question: str, project_id: int, *, models: Optional[list[dict]] = None, relations: Optional[list[dict]] = None, llm: Optional[LLMService] = None, language: Optional[str] = None) -> Optional[dict[str, Any]]:
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
    from services.sql_routing.llm_capability import _adapt_response_format

    model_tier, caps = _current_llm_model_tier(llm)
    route_v2_enabled = _is_sql_route_v2_enabled(project_id)
    shadow_mode = bool(ROUTER_CONFIG.get("sql_route_shadow_mode", False))
    use_profile = route_v2_enabled or shadow_mode
    strict_json = _strict_json_capability()
    prompt_selection = _prompt_profile_selection("semantic_link", strict_json_mode=strict_json.get("mode", "none"), model_tier=model_tier)
    system_suffix = f"\n<PROFILE>{prompt_selection.system_suffix}</PROFILE>" if use_profile and prompt_selection.system_suffix else ""
    response_format = prompt_selection.response_format if (use_profile and prompt_selection.response_format) else "json"
    response_format = _adapt_response_format(response_format, caps)
    messages = [
        {"role": "system", "content": f"{_render_system_prompt()}{system_suffix}\n\n{localized_contract('schema_link', language)}\n{_language_instruction(language)}"},
        {"role": "user", "content": f"Metadata catalog:\n{catalog}\n\nUser question: {question}"},
    ]
    try:
        result = llm.chat(messages, response_format=response_format)
        content = _llm_content_text(result)
        if not content.strip():
            LOGGER.warning("LLM semantic link returned empty content; using token-based semantic matching")
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
    except json.JSONDecodeError:
        LOGGER.warning("LLM semantic link returned malformed JSON; retrying once with strict instruction")
        messages[0]["content"] += "\n\nCRITICAL: You MUST output ONLY valid JSON. No markdown, no code fences, no extra text."
        try:
            result = llm.chat(messages, response_format=response_format)
            content = _llm_content_text(result)
            if not content.strip():
                LOGGER.warning("LLM semantic link retry returned empty content; falling back")
                _emit_route_event(
                    "schema_link_fallback",
                    {"reason": "empty_content_retry", "fallback": "token_semantic_matching"},
                    project_id=project_id,
                )
                return None
            parsed = parse_json_object(content)
        except Exception as exc_retry:
            LOGGER.exception("LLM semantic link retry also failed")
            _emit_route_event(
                "schema_link_fallback",
                {
                    "reason": "exception_retry",
                    "error_type": type(exc_retry).__name__,
                    "fallback": "token_semantic_matching",
                },
                project_id=project_id,
            )
            return None
    except Exception as exc:
        LOGGER.exception("LLM semantic link failed")
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


def _llm_schema_link(question: str, project_id: int, *, models: Optional[list[dict]] = None, relations: Optional[list[dict]] = None, llm: Optional[LLMService] = None, language: Optional[str] = None) -> Optional[dict[str, Any]]:
    return _llm_semantic_link(question, project_id, models=models, relations=relations, llm=llm, language=language)


def _semantic_prompt(project_id: int, question: Optional[str] = None, *, require_hits: bool = False, analysis: Optional[dict] = None, language: Optional[str] = None) -> tuple[str, list[str], dict[str, Any]]:
    models = _models_for_project(project_id)
    relations = _relations_for_project(project_id)
    token_hits = _semantic_hits(question or "", models, relations) if question else {"models": models, "relations": relations, "fallback": True, "has_hits": bool(models)}
    llm = LLMService()
    llm_configured = llm.is_configured()
    hits = token_hits
    if question and llm_configured:
        llm_hits = _llm_semantic_link(question, project_id, models=models, relations=relations, llm=llm, language=language)
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


def _classify_question_route(question: str, project_id: int, previous_questions: Optional[list[str]] = None, analysis: Optional[dict] = None, language: Optional[str] = None) -> dict[str, Any]:
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
    semantic_context, retrieved_tables, hits = _semantic_prompt(project_id, question, require_hits=False, analysis=normalized_analysis, language=language)
    knowledge_context, knowledge_hits = _knowledge_context(project_id, question)
    combined_context = _augment_context_with_knowledge(semantic_context, knowledge_context)
    meta_summary = _build_metadata_summary(project_id)
    llm = LLMService()
    llm_configured = llm.is_configured()
    model_tier = None
    if llm_configured:
        model_tier, _ = _current_llm_model_tier(llm)
    strict_json = _strict_json_capability()
    prompt_selection = _prompt_profile_selection(
        "question_categorization",
        strict_json_mode=strict_json.get("mode", "none"),
        model_tier=model_tier,
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
        "reasoning": _ask._sql_msg("route_no_metadata_match", language),
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

    if not llm_configured:
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
            if metadata_part.strip() != question.strip():
                route_semantic_context, route_retrieved_tables, route_hits = _semantic_prompt(
                    project_id,
                    metadata_part,
                    require_hits=False,
                    analysis=normalized_analysis,
                    language=language,
                )
            if not route_hits.get("has_hits"):
                route_semantic_context, route_retrieved_tables, route_hits = _semantic_prompt(
                    project_id,
                    metadata_part,
                    require_hits=True,
                    analysis=normalized_analysis,
                    language=language,
                )
            if metadata_part.strip() != question.strip() or clause_routing.get("mixed"):
                route_knowledge_context, route_knowledge_hits = _knowledge_context(project_id, metadata_part)
            route_combined_context = _augment_context_with_knowledge(route_semantic_context, route_knowledge_context)
        result = {
            "requires_sql": requires_sql,
            "metadata_question_part": metadata_part if requires_sql else "",
            "non_metadata_question_part": non_metadata_part if requires_sql else question,
            "reasoning": _ask._sql_msg("route_llm_not_configured", language),
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
    system_suffix = f"\n<PROFILE>{prompt_selection.system_suffix}</PROFILE>" if use_profile and prompt_selection.system_suffix else ""
    response_format = prompt_selection.response_format if (use_profile and prompt_selection.response_format) else "json"
    messages = [
        {"role": "system", "content": f"{_render_system_prompt()}{system_suffix}\n\n{localized_contract('question_routing', language)}\n{_language_instruction(language)}"},
        {"role": "user", "content": (
            f"Project: {meta.get('display_name') or meta.get('name') or project_id}\n"
            f"Description: {meta.get('description') or ''}\n"
            f"Schema:\n{meta_summary['summary']}\n"
            + (f"Question analysis:\n{analysis_str}\n" if analysis_str else "")
            + (f"Clause routing pre-analysis:\n{clause_routing_prompt}\n" if clause_routing_prompt else "")
            + f"Matched metadata and knowledge:\n{combined_context}\n"
            + f"Previous questions: {previous_questions or []}\nQuestion: {question}"
        )},
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
            "reasoning": _ask._sql_msg("route_classification_failed", language).format(type(exc).__name__),
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
        if metadata_part.strip() != question.strip() or clause_routing.get("mixed"):
            route_semantic_context, route_retrieved_tables, route_hits = _semantic_prompt(
                project_id,
                metadata_part,
                require_hits=False,
                analysis=normalized_analysis,
                language=language,
            )
        if not route_hits.get("has_hits"):
            route_semantic_context, route_retrieved_tables, route_hits = _semantic_prompt(
                project_id,
                metadata_part,
                require_hits=True,
                analysis=normalized_analysis,
                language=language,
            )
        if metadata_part.strip() != question.strip() or clause_routing.get("mixed"):
            route_knowledge_context, route_knowledge_hits = _knowledge_context(project_id, metadata_part)
        route_combined_context = _augment_context_with_knowledge(route_semantic_context, route_knowledge_context)

    reasoning = parsed.get("reasoning") or _ask._sql_msg("route_default_answer_path", language)
    if clause_routing.get("mixed"):
        reasoning = f"{reasoning} {_ask._sql_msg('route_clause_separation', language)}"

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
        for subq in parsed.find_all(sqlglot.exp.Subquery):
            subq_alias = (subq.alias or "").lower()
            if subq_alias and subq_alias not in alias_map:
                inner = subq.this
                if inner and hasattr(inner, "find_all"):
                    for tbl in inner.find_all(sqlglot.exp.Table):
                        inner_name = (tbl.name or "").lower()
                        if inner_name:
                            alias_map[subq_alias] = inner_name
                            break
        for alias_tbl in parsed.find_all(sqlglot.exp.Table):
            tbl_alias = (alias_tbl.alias or "").lower()
            if tbl_alias and tbl_alias not in alias_map:
                tbl_this = getattr(alias_tbl, "this", None)
                if tbl_this and hasattr(tbl_this, "find_all"):
                    for inner_tbl in tbl_this.find_all(sqlglot.exp.Table):
                        inner_name = (inner_tbl.name or "").lower()
                        if inner_name:
                            alias_map[tbl_alias] = inner_name
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
                            if new_alias and new_alias.lower() != table_lower:
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
                    if new_alias and new_alias.lower() != table_lower:
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

    def _group_expr_references_aggregate_aliases(group_expr: exp.Expression, aggregate_aliases: set[str]) -> set[str]:
        referenced_aliases: set[str] = set()
        if not aggregate_aliases:
            return referenced_aliases

        direct_name = str(getattr(group_expr, "name", "") or "").strip().lower()
        direct_table = str(getattr(group_expr, "table", "") or "").strip()
        if direct_name and not direct_table and direct_name in aggregate_aliases:
            referenced_aliases.add(direct_name)

        for column_node in group_expr.find_all(exp.Column):
            column_name = str(column_node.name or "").strip().lower()
            table_name = str(column_node.table or "").strip()
            if column_name and not table_name and column_name in aggregate_aliases:
                referenced_aliases.add(column_name)
        return referenced_aliases

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

        group = select_expr.args.get("group")
        if group:
            removed_aggregate_expr_count = 0
            removed_aggregate_aliases: set[str] = set()
            cleaned_expressions = [
                gexpr
                for gexpr in (group.expressions or [])
                if not (
                    any(isinstance(node, exp.AggFunc) for node in gexpr.find_all(exp.AggFunc))
                    or _group_expr_references_aggregate_aliases(gexpr, aggregate_aliases)
                )
            ]

            for gexpr in (group.expressions or []):
                if any(isinstance(node, exp.AggFunc) for node in gexpr.find_all(exp.AggFunc)):
                    removed_aggregate_expr_count += 1
                removed_aggregate_aliases.update(
                    _group_expr_references_aggregate_aliases(gexpr, aggregate_aliases)
                )

            if len(cleaned_expressions) < len(group.expressions or []):
                removed_count = len(group.expressions or []) - len(cleaned_expressions)
                if cleaned_expressions:
                    group.set("expressions", cleaned_expressions)
                else:
                    select_expr.args.pop("group", None)
                changed = True
                if removed_aggregate_expr_count > 0:
                    rewrite_notes.append(
                        f"removed {removed_aggregate_expr_count} aggregate expression(s) from GROUP BY"
                    )
                alias_count = len(removed_aggregate_aliases)
                if alias_count > 0:
                    rewrite_notes.append(
                        "removed aggregate alias reference(s) from GROUP BY: "
                        + ", ".join(sorted(removed_aggregate_aliases))
                    )
                if removed_aggregate_expr_count <= 0 and alias_count <= 0:
                    rewrite_notes.append(
                        f"removed {removed_count} invalid expression(s) from GROUP BY"
                    )

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
    if "must appear in the group by clause" in lowered and "aggregate function" in lowered:
        return True
    if "group by clause cannot contain aggregates" in lowered:
        return True
    return False


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
    language: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "sql": None,
        "summary": _ask._sql_msg("syntax_failure_summary", language),
        "reasoning": "; ".join(last_errors),
        "retrieved_tables": retrieved_tables,
        "configured": True,
        "sql_engine": f"{sql_engine}_failed",
    }
