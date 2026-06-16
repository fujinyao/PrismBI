from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import sqlglot
from sqlglot import exp


LOGGER = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Existing strict-json capability (backward compatible)
# ═══════════════════════════════════════════════════════════════

_CACHE_LOCK = threading.Lock()
_CACHE_PAYLOAD: dict[str, Any] = {
    "loaded_at": 0.0,
    "source_path": "",
    "value": {
        "supported": False,
        "mode": "none",
        "detail": "Strict JSON capability report not found.",
    },
}
_CACHE_TTL_SECONDS = 15.0


def _default_report_path() -> str:
    current_dir = os.path.dirname(__file__)
    backend_dir = os.path.abspath(os.path.join(current_dir, "..", ".."))
    return os.path.join(backend_dir, "data", "llm_capability_report.json")


def _normalize_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"json_object", "json_schema", "partial", "none"}:
        return mode
    if str(value or "").strip():
        return "partial"
    return "none"


def _load_report(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {
            "supported": False,
            "mode": "none",
            "detail": f"Strict JSON capability report does not exist: {path}",
        }
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        return {
            "supported": False,
            "mode": "partial",
            "detail": f"Failed to parse capability report ({type(exc).__name__}).",
        }

    summary = data.get("summary") if isinstance(data, dict) else None
    strict = summary.get("strict_json") if isinstance(summary, dict) else None
    if not isinstance(strict, dict):
        return {
            "supported": False,
            "mode": "partial",
            "detail": "Capability report exists but strict_json summary is missing.",
        }
    supported = bool(strict.get("supported"))
    mode = _normalize_mode(strict.get("mode"))
    detail = str(strict.get("detail") or "")
    if supported and mode == "none":
        mode = "json_object"
    return {
        "supported": supported,
        "mode": mode,
        "detail": detail,
    }


def get_strict_json_capability(force_refresh: bool = False) -> dict[str, Any]:
    report_path = str(
        os.getenv("PRISMBI_LLM_CAPABILITY_REPORT_PATH")
        or os.getenv("LLM_CAPABILITY_REPORT_PATH")
        or _default_report_path()
    ).strip()
    now = time.monotonic()
    with _CACHE_LOCK:
        cached_path = str(_CACHE_PAYLOAD.get("source_path") or "")
        loaded_at = float(_CACHE_PAYLOAD.get("loaded_at") or 0.0)
        if (
            (not force_refresh)
            and cached_path == report_path
            and now - loaded_at <= _CACHE_TTL_SECONDS
        ):
            value = _CACHE_PAYLOAD.get("value")
            return dict(value) if isinstance(value, dict) else {
                "supported": False,
                "mode": "none",
                "detail": "Capability cache is invalid.",
            }

        loaded = _load_report(report_path)
        _CACHE_PAYLOAD["source_path"] = report_path
        _CACHE_PAYLOAD["loaded_at"] = now
        _CACHE_PAYLOAD["value"] = loaded
        return dict(loaded)


# ═══════════════════════════════════════════════════════════════
# Phase 1: Model capability data classes
# ═══════════════════════════════════════════════════════════════

@dataclass
class ModelCapabilities:
    model_family: str = "unknown"
    model_size_b: float = 0.0
    provider: str = "unknown"
    context_window: int = 4096
    max_output_tokens: int = 2048
    quantization: str = ""

    supports_json_schema: bool = False
    supports_json_object: bool = True
    json_mode_reliable: bool = False
    json_output_leak_markdown: bool = True
    json_output_field_accuracy: float = 0.5

    sql_accuracy_tier: str = "low"
    sql_safety_compliant: bool = False
    sql_column_hallucination_rate: float = 0.5
    sql_table_hallucination_rate: float = 0.2
    sql_join_accuracy: float = 0.3
    sql_group_by_compliance: float = 0.3
    sql_aggregate_placement: float = 0.4
    sql_syntax_validity: float = 0.5
    sql_readonly_compliance: float = 0.5

    system_prompt_adherence: str = "weak"
    instruction_following_score: float = 0.4
    format_compliance: float = 0.4
    reasoning_leak: bool = False
    empty_output_rate: float = 0.2

    repair_capability: bool = False
    repair_success_rate: float = 0.0
    error_feedback_utilization: float = 0.1
    max_useful_repair_attempts: int = 0

    recommended_temperature: float = 0.3
    recommended_max_tokens: int = 4096
    supports_streaming: bool = True
    supports_vision: bool = False
    supports_tool_calling: bool = False
    avg_response_latency_ms: float = 2000.0
    latency_p50_ms: float = 1800.0
    latency_p95_ms: float = 4000.0
    token_generation_speed: float = 30.0

    model_key: str = ""
    probe_version: int = 2
    probe_count: int = 0
    last_error: str = ""
    probed_at: str = ""
    probe_duration_ms: float = 0.0
    probe_level: str = "keyword_only"


@dataclass
class ModelTierProfile:
    tier: str = "weak"
    response_format_strategy: str = "text_with_instruction"
    temperature: float = 0.1
    max_tokens: int = 4096
    extra_params: dict = None
    extra_system_suffix: str = ""
    sql_constraint_level: str = "strict"
    max_repair_attempts: int = 0
    skip_repair_if_json_empty: bool = True
    json_parse_retries: int = 2
    enable_fast_precheck: bool = True
    precheck_check_paren: bool = True
    precheck_check_keywords: bool = True
    precheck_check_readonly: bool = True

    def __post_init__(self):
        if self.extra_params is None:
            self.extra_params = {}


def build_tier_profile(tier: str) -> ModelTierProfile:
    params = _TIER_PARAMS.get(tier, _TIER_PARAMS["weak"])
    repair = _REPAIR_TIER_CONFIG.get(tier, _REPAIR_TIER_CONFIG["weak"])
    suffix = _TIER_SYSTEM_SUFFIXES.get(tier, _TIER_SYSTEM_SUFFIXES["weak"])

    if tier == "strong":
        strat = "json_schema"
    elif tier == "medium":
        strat = "json_object"
    else:
        strat = "text_with_instruction"

    return ModelTierProfile(
        tier=tier,
        response_format_strategy=strat,
        temperature=params.get("temperature", 0.1),
        max_tokens=params.get("max_tokens", 4096),
        extra_params=params.get("extra_params", {}),
        extra_system_suffix=suffix,
        sql_constraint_level="none" if tier == "strong" else ("normal" if tier == "medium" else "strict"),
        max_repair_attempts=repair.get("max_repair_attempts", 0),
        skip_repair_if_json_empty=repair.get("skip_repair_if_json_empty", True),
        json_parse_retries=repair.get("json_parse_retries", 2),
        enable_fast_precheck=(tier == "weak"),
        precheck_check_paren=(tier == "weak"),
        precheck_check_keywords=(tier == "weak"),
        precheck_check_readonly=(tier == "weak"),
    )


# ═══════════════════════════════════════════════════════════════
# Phase 1: Keyword heuristics
# ═══════════════════════════════════════════════════════════════

# Small model size patterns: models with these parameter counts (1B-8B) are downgraded one tier
_SMALL_MODEL_RE = re.compile(r'(?<![a-zA-Z0-9])(\d+)b(?![a-zA-Z0-9])', re.IGNORECASE)

_MODEL_FAMILY_KEYWORDS: dict[str, dict[str, Any]] = {
    "gpt-4":     {"family": "openai",    "tier": "strong"},
    "gpt-5":     {"family": "openai",    "tier": "strong"},
    "o1":        {"family": "openai",    "tier": "strong"},
    "o3":        {"family": "openai",    "tier": "strong"},
    "claude-3":  {"family": "anthropic", "tier": "strong"},
    "claude-4":  {"family": "anthropic", "tier": "strong"},
    "claude":    {"family": "anthropic", "tier": "strong"},
    "gemini-2.5-pro":   {"family": "google", "tier": "strong"},
    "gemini-2.5-flash": {"family": "google", "tier": "strong"},
    "gemini-2.0-flash": {"family": "google", "tier": "strong"},
    "gemini-2.0":       {"family": "google", "tier": "strong"},
    "gemini-1.5-pro":   {"family": "google", "tier": "strong"},
    "gemini-1.5-flash": {"family": "google", "tier": "medium"},
    "gemini":           {"family": "google", "tier": "medium"},
    "qwen2.5":   {"family": "qwen",      "tier": "medium"},
    "qwen3":     {"family": "qwen",      "tier": "medium"},
    "qwen":      {"family": "qwen",      "tier": "medium"},
    "deepseek":  {"family": "deepseek",  "tier": "strong"},
    "llama-4":   {"family": "llama",     "tier": "medium"},
    "llama-3":   {"family": "llama",     "tier": "medium"},
    "llama":     {"family": "llama",     "tier": "medium"},
    "mistral":   {"family": "mistral",   "tier": "medium"},
    "mixtral":   {"family": "mistral",   "tier": "medium"},
    "gemma-4":   {"family": "gemma",     "tier": "weak"},
    "gemma4":    {"family": "gemma",     "tier": "weak"},
    "gemma-3":   {"family": "gemma",     "tier": "weak"},
    "gemma-2":   {"family": "gemma",     "tier": "weak"},
    "gemma":     {"family": "gemma",     "tier": "weak"},
    "phi-4":     {"family": "phi",       "tier": "weak"},
    "phi-3":     {"family": "phi",       "tier": "weak"},
    "phi":       {"family": "phi",       "tier": "weak"},
    "tinyllama": {"family": "llama",     "tier": "weak"},
}


_TIER_DEFAULTS: dict[str, dict[str, Any]] = {
    "strong": {
        "supports_json_schema": True,
        "supports_json_object": True,
        "json_mode_reliable": True,
        "json_output_leak_markdown": False,
        "json_output_field_accuracy": 0.98,
        "sql_accuracy_tier": "high",
        "sql_safety_compliant": True,
        "sql_column_hallucination_rate": 0.05,
        "sql_table_hallucination_rate": 0.02,
        "sql_hallucination_risk": "low",
        "sql_join_accuracy": 0.95,
        "sql_group_by_compliance": 0.95,
        "sql_aggregate_placement": 0.95,
        "sql_syntax_validity": 0.98,
        "sql_readonly_compliance": 0.98,
        "system_prompt_adherence": "strict",
        "instruction_following_score": 0.95,
        "format_compliance": 0.95,
        "empty_output_rate": 0.01,
        "repair_capability": True,
        "repair_success_rate": 0.9,
        "error_feedback_utilization": 0.8,
        "max_useful_repair_attempts": 2,
        "recommended_temperature": 0.3,
    },
    "medium": {
        "supports_json_schema": True,
        "supports_json_object": True,
        "json_mode_reliable": True,
        "json_output_leak_markdown": False,
        "json_output_field_accuracy": 0.85,
        "sql_accuracy_tier": "medium",
        "sql_safety_compliant": True,
        "sql_column_hallucination_rate": 0.15,
        "sql_table_hallucination_rate": 0.05,
        "sql_hallucination_risk": "medium",
        "sql_join_accuracy": 0.75,
        "sql_group_by_compliance": 0.8,
        "sql_aggregate_placement": 0.8,
        "sql_syntax_validity": 0.85,
        "sql_readonly_compliance": 0.85,
        "system_prompt_adherence": "normal",
        "instruction_following_score": 0.8,
        "format_compliance": 0.8,
        "empty_output_rate": 0.05,
        "repair_capability": True,
        "repair_success_rate": 0.6,
        "error_feedback_utilization": 0.5,
        "max_useful_repair_attempts": 1,
        "recommended_temperature": 0.2,
    },
    "weak": {
        "supports_json_schema": False,
        "supports_json_object": True,
        "json_mode_reliable": False,
        "json_output_leak_markdown": True,
        "json_output_field_accuracy": 0.5,
        "sql_accuracy_tier": "low",
        "sql_safety_compliant": False,
        "sql_column_hallucination_rate": 0.35,
        "sql_table_hallucination_rate": 0.1,
        "sql_hallucination_risk": "high",
        "sql_join_accuracy": 0.4,
        "sql_group_by_compliance": 0.3,
        "sql_aggregate_placement": 0.4,
        "sql_syntax_validity": 0.5,
        "sql_readonly_compliance": 0.5,
        "system_prompt_adherence": "weak",
        "instruction_following_score": 0.5,
        "format_compliance": 0.5,
        "empty_output_rate": 0.15,
        "repair_capability": False,
        "repair_success_rate": 0.0,
        "error_feedback_utilization": 0.2,
        "max_useful_repair_attempts": 0,
        "recommended_temperature": 0.1,
    },
}

# Per-model-keyword overrides that override tier defaults for specific models.
# Applied in _keyword_fallback after _apply_tier_defaults.
# Key must match a keyword in _MODEL_FAMILY_KEYWORDS.
_MODEL_OVERLAYS: dict[str, dict[str, Any]] = {
    "tinyllama": {
        "supports_json_object": False,
    },
    "deepseek": {
        "sql_hallucination_risk": "medium",
    },
}


def _default_capabilities(provider: str, endpoint: str, model: str) -> dict:
    return {
        "model_meta": {
            "model_family": "unknown",
            "model_size_b": 0.0,
            "provider": provider,
            "context_window": 4096,
            "max_output_tokens": 2048,
            "quantization": "",
        },
        "structured_output": {
            "supports_json_schema": False,
            "supports_json_object": True,
            "json_mode_reliable": False,
            "json_output_leak_markdown": True,
            "json_output_field_accuracy": 0.5,
        },
        "sql_quality": {
            "sql_accuracy_tier": "low",
            "sql_safety_compliant": False,
            "sql_column_hallucination_rate": 0.5,
            "sql_table_hallucination_rate": 0.2,
            "sql_hallucination_risk": "high",
            "sql_join_accuracy": 0.3,
            "sql_group_by_compliance": 0.3,
            "sql_aggregate_placement": 0.4,
            "sql_syntax_validity": 0.5,
            "sql_readonly_compliance": 0.5,
        },
        "instruction": {
            "system_prompt_adherence": "weak",
            "instruction_following_score": 0.4,
            "format_compliance": 0.4,
            "reasoning_leak": False,
            "empty_output_rate": 0.2,
        },
        "repair": {
            "repair_capability": False,
            "repair_success_rate": 0.0,
            "error_feedback_utilization": 0.1,
            "max_useful_repair_attempts": 0,
        },
        "performance": {
            "recommended_temperature": 0.3,
            "recommended_max_tokens": 4096,
            "supports_streaming": True,
            "supports_vision": False,
            "supports_tool_calling": False,
            "avg_response_latency_ms": 2000.0,
            "latency_p50_ms": 1800.0,
            "latency_p95_ms": 4000.0,
            "token_generation_speed": 30.0,
        },
        "probe_meta": {
            "model_key": f"{provider}:{endpoint}:{model}",
            "probe_version": 2,
            "probe_count": 0,
            "last_error": "",
            "probed_at": "",
            "probe_duration_ms": 0.0,
            "probe_level": "keyword_only",
        },
    }


# Full category map used by both _apply_tier_defaults and _apply_overlay
_CAPABILITY_CATEGORY_MAP: dict[str, tuple[str, ...]] = {
    "supports_json_schema": ("structured_output",),
    "supports_json_object": ("structured_output",),
    "json_mode_reliable": ("structured_output",),
    "json_output_leak_markdown": ("structured_output",),
    "json_output_field_accuracy": ("structured_output",),
    "sql_accuracy_tier": ("sql_quality",),
    "sql_safety_compliant": ("sql_quality",),
    "sql_column_hallucination_rate": ("sql_quality",),
    "sql_table_hallucination_rate": ("sql_quality",),
    "sql_hallucination_risk": ("sql_quality",),
    "sql_join_accuracy": ("sql_quality",),
    "sql_group_by_compliance": ("sql_quality",),
    "sql_aggregate_placement": ("sql_quality",),
    "sql_syntax_validity": ("sql_quality",),
    "sql_readonly_compliance": ("sql_quality",),
    "system_prompt_adherence": ("instruction",),
    "instruction_following_score": ("instruction",),
    "format_compliance": ("instruction",),
    "empty_output_rate": ("instruction",),
    "repair_capability": ("repair",),
    "repair_success_rate": ("repair",),
    "error_feedback_utilization": ("repair",),
    "max_useful_repair_attempts": ("repair",),
    "recommended_temperature": ("performance",),
    "recommended_max_tokens": ("performance",),
    "supports_streaming": ("performance",),
    "supports_vision": ("performance",),
    "supports_tool_calling": ("performance",),
    "avg_response_latency_ms": ("performance",),
    "latency_p50_ms": ("performance",),
    "latency_p95_ms": ("performance",),
    "token_generation_speed": ("performance",),
}


def _apply_overlay(caps: dict, overlay: dict[str, Any]) -> None:
    for key, value in overlay.items():
        categories = _CAPABILITY_CATEGORY_MAP.get(key)
        if categories:
            for cat in categories:
                if cat in caps:
                    caps[cat][key] = value


def _keyword_fallback(provider: str, endpoint: str, model: str) -> dict:
    model_lower = model.lower()
    matched = None
    matched_keyword = None
    matched_len = 0
    for keyword, config in _MODEL_FAMILY_KEYWORDS.items():
        if keyword in model_lower and len(keyword) > matched_len:
            matched = config
            matched_keyword = keyword
            matched_len = len(keyword)

    caps = _default_capabilities(provider, endpoint, model)
    if matched is not None:
        effective_tier = matched["tier"]
        if _SMALL_MODEL_RE.search(model_lower):
            downgraded = {"strong": "medium", "medium": "weak"}.get(effective_tier)
            if downgraded:
                LOGGER.debug("Downgrading model %s from %s to %s due to small size", model, effective_tier, downgraded)
                effective_tier = downgraded
        caps["model_meta"]["model_family"] = matched["family"]
        _apply_tier_defaults(caps, effective_tier)
        if matched_keyword in _MODEL_OVERLAYS:
            _apply_overlay(caps, _MODEL_OVERLAYS[matched_keyword])
    else:
        caps["model_meta"]["model_family"] = "unknown"
        _apply_tier_defaults(caps, "weak")
    caps["probe_meta"]["probe_level"] = "keyword_only"
    return caps


def _apply_tier_defaults(caps: dict, tier: str) -> None:
    defaults = _TIER_DEFAULTS.get(tier, _TIER_DEFAULTS["weak"])
    for key, value in defaults.items():
        categories = _CAPABILITY_CATEGORY_MAP.get(key)
        if categories:
            for cat in categories:
                if cat in caps:
                    caps[cat][key] = value


# ═══════════════════════════════════════════════════════════════
# Phase 1: Capability → tier mapping
# ═══════════════════════════════════════════════════════════════

def _capabilities_to_tier(capabilities: dict) -> str:
    if not isinstance(capabilities, dict):
        return "weak"
    sql_tier = (
        capabilities
        .get("sql_quality", {})
        .get("sql_accuracy_tier", "low")
    )
    structured = capabilities.get("structured_output", {})
    supports_json = structured.get("supports_json_schema", False) or structured.get("supports_json_object", False)
    repair_cap = (
        capabilities
        .get("repair", {})
        .get("repair_capability", False)
    )
    sql_safe = (
        capabilities
        .get("sql_quality", {})
        .get("sql_safety_compliant", False)
    )
    if sql_tier == "high" and supports_json and repair_cap and sql_safe:
        return "strong"
    if sql_tier == "medium" and supports_json:
        return "medium"
    return "weak"


# ═══════════════════════════════════════════════════════════════
# Phase 1: Adaptation configurations
# ═══════════════════════════════════════════════════════════════

_TIER_PARAMS: dict[str, dict[str, Any]] = {
    "strong": {
        "temperature": 0.3,
        "max_tokens": 4096,
        "extra_params": {},
    },
    "medium": {
        "temperature": 0.2,
        "max_tokens": 4096,
        "extra_params": {},
    },
    "weak": {
        "temperature": 0.1,
        "max_tokens": 4096,
        "extra_params": {},
    },
}

_REPAIR_TIER_CONFIG: dict[str, dict[str, Any]] = {
    "strong": {
        "max_repair_attempts": 2,
        "json_parse_retries": 1,
        "repair_timeout_s": 60,
        "skip_repair_if_json_empty": False,
        "retry_on_binder_error": True,
    },
    "medium": {
        "max_repair_attempts": 2,
        "json_parse_retries": 2,
        "repair_timeout_s": 90,
        "skip_repair_if_json_empty": False,
        "retry_on_binder_error": True,
    },
    "weak": {
        "max_repair_attempts": 0,
        "json_parse_retries": 2,
        "repair_timeout_s": 30,
        "skip_repair_if_json_empty": True,
        "retry_on_binder_error": False,
    },
}

_TIER_SYSTEM_SUFFIXES: dict[str, str] = {
    "strong": "",
    "medium": (
        "\n- Output ONLY valid JSON. No markdown fences, no extra text."
        "\n- Every non-aggregated column in SELECT must appear in GROUP BY."
        "\n- Use column names EXACTLY as they appear in the schema."
        "\n- Never wrap the entire query in parentheses."
    ),
    "weak": (
        "\nCRITICAL - Output ONLY valid JSON. No markdown, no code fences, no extra text."
        "\nCRITICAL - Only use SELECT or WITH...SELECT. No INSERT/UPDATE/DELETE."
        "\nCRITICAL - Confirm every column name exists in the schema before using it."
        "\nCRITICAL - Never put aggregate functions (COUNT, SUM, AVG, MIN, MAX) in GROUP BY."
        "\nCRITICAL - Never wrap expressions or the full query in extra parentheses."
        "\nCRITICAL - All non-aggregated SELECT columns must be in GROUP BY."
    ),
}


def _adapt_response_format(requested_format: Any, capabilities: dict) -> Any:
    if requested_format is None:
        return None
    if not isinstance(capabilities, dict):
        return requested_format
    structured = capabilities.get("structured_output", {})
    supports_json_schema = structured.get("supports_json_schema", False)
    supports_json_object = structured.get("supports_json_object", True)
    if isinstance(requested_format, dict):
        fmt_type = requested_format.get("type", "")
        if fmt_type == "json_schema" and not supports_json_schema:
            if supports_json_object:
                return "json"
            return None
        return requested_format

    if requested_format == "json":
        if not supports_json_object:
            return None
        return "json"

    return requested_format


def _get_response_format_strategy(capabilities: dict) -> dict:
    structured = capabilities.get("structured_output", {})
    supports_json_schema = structured.get("supports_json_schema", False)
    supports_json_object = structured.get("supports_json_object", True)
    hallucination_risk = capabilities.get("sql_quality", {}).get("sql_hallucination_risk", "high")

    if supports_json_schema:
        strategy = "json_schema"
        response_format = None
        retry_on_failure = "json_object"
    elif supports_json_object:
        strategy = "json_object"
        response_format = "json"
        retry_on_failure = "text_with_instruction"
    else:
        strategy = "text_with_instruction"
        response_format = None
        retry_on_failure = "none"

    return {
        "strategy": strategy,
        "response_format": response_format,
        "retry_on_failure": retry_on_failure,
        "hallucination_risk": hallucination_risk,
    }


def _get_tier_system_suffix(tier: str) -> str:
    profile = build_tier_profile(tier)
    return profile.extra_system_suffix


def _get_repair_config(tier: str) -> dict:
    profile = build_tier_profile(tier)
    raw = _REPAIR_TIER_CONFIG.get(tier, _REPAIR_TIER_CONFIG["weak"])
    return {
        "max_repair_attempts": profile.max_repair_attempts,
        "json_parse_retries": profile.json_parse_retries,
        "skip_repair_if_json_empty": profile.skip_repair_if_json_empty,
        "repair_timeout_s": raw.get("repair_timeout_s", 60),
        "retry_on_binder_error": raw.get("retry_on_binder_error", True),
    }


def _get_tier_params(tier: str) -> dict:
    raw = _TIER_PARAMS.get(tier, _TIER_PARAMS["weak"])
    return {
        "temperature": raw.get("temperature", 0.1),
        "max_tokens": raw.get("max_tokens", 4096),
        "extra_params": dict(raw.get("extra_params", {})),
    }


def _fast_precheck_bad_sql(sql: str, tier: str, capabilities: dict) -> Optional[str]:
    if not sql or not isinstance(sql, str):
        return "Empty or invalid SQL"

    errors = []
    sql_upper = sql.upper()

    forbidden = ["INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "CREATE ", "TRUNCATE "]
    for kw in forbidden:
        if kw in sql_upper:
            errors.append(f"Contains forbidden keyword: {kw.strip()}")

    if sql.count("(") != sql.count(")"):
        errors.append("Unmatched parentheses")

    if re.search(r'[&|`]|(?<!\w)\$(?!\w)', sql):
        errors.append("Contains shell control characters")

    hallucination_risk = capabilities.get("sql_quality", {}).get("sql_hallucination_risk", "high")
    if hallucination_risk != "low":
        try:
            parsed = sqlglot.parse_one(sql, read="duckdb")
            dangerous_nodes = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command)
            if any(isinstance(node, dangerous_nodes) for node in parsed.walk()):
                errors.append("AST contains non-READ-ONLY expressions")
        except Exception:
            pass

    return "; ".join(errors) if errors else None


# ═══════════════════════════════════════════════════════════════
# Phase 1: Memory cache
# ═══════════════════════════════════════════════════════════════

_CAPABILITY_MEMORY_CACHE: dict[str, dict[str, Any]] = {}
_CAPABILITY_MEMORY_LOCK = threading.Lock()
_CAPABILITY_MEMORY_TTL = 300.0
_CAPABILITY_KEYWORD_TTL = 3600.0


def _memory_cache_key(provider: str, endpoint: str, model: str) -> str:
    ep_hash = hashlib.sha256((endpoint or "").encode()).hexdigest()[:8]
    model_hash = hashlib.sha256((model or "").encode()).hexdigest()[:8]
    return f"{provider}:{ep_hash}:{model_hash}"


def _memory_cache_get(key: str) -> Optional[dict]:
    with _CAPABILITY_MEMORY_LOCK:
        entry = _CAPABILITY_MEMORY_CACHE.get(key)
        if entry is None:
            return None
        loaded_at = entry.get("loaded_at", 0.0)
        ttl = entry.get("ttl", _CAPABILITY_MEMORY_TTL)
        if time.monotonic() - loaded_at > ttl:
            _CAPABILITY_MEMORY_CACHE.pop(key, None)
            return None
        return entry.get("data")


def _memory_cache_set(key: str, data: dict, ttl: Optional[float] = None) -> None:
    with _CAPABILITY_MEMORY_LOCK:
        _CAPABILITY_MEMORY_CACHE[key] = {
            "data": data,
            "loaded_at": time.monotonic(),
            "ttl": ttl if ttl is not None else _CAPABILITY_MEMORY_TTL,
        }


def _memory_cache_clear() -> None:
    with _CAPABILITY_MEMORY_LOCK:
        _CAPABILITY_MEMORY_CACHE.clear()


# ═══════════════════════════════════════════════════════════════
# Phase 1/2: Database helpers
# ═══════════════════════════════════════════════════════════════

_CAPABILITY_KEY_PREFIX = "llm_capability_"
_CAPABILITY_TTL_SECONDS = 86400
_CAPABILITY_TTL_PROBE_FAILED = 300


def _model_capability_key(provider: str, endpoint: str, model: str) -> str:
    endpoint_hash = hashlib.sha256((endpoint or "").encode()).hexdigest()[:8]
    model_hash = hashlib.sha256((model or "").encode()).hexdigest()[:8]
    return f"{_CAPABILITY_KEY_PREFIX}{provider}:{endpoint_hash}:{model_hash}"


def _is_capability_expired(updated_at, ttl_seconds: Optional[float] = None) -> bool:
    if updated_at is None:
        return True
    if ttl_seconds is None:
        ttl_seconds = _CAPABILITY_TTL_SECONDS
    if isinstance(updated_at, str):
        try:
            updated_at = datetime.fromisoformat(updated_at)
        except Exception:
            return True
    if isinstance(updated_at, datetime):
        elapsed = (datetime.now() - updated_at).total_seconds()
        return elapsed > ttl_seconds
    return True


def _load_capability_from_db(provider: str, endpoint: str, model: str) -> Optional[dict]:
    try:
        from db import connection_lock, get_connection
        with connection_lock():
            con = get_connection()
            row = con.execute(
                """SELECT provider, endpoint, model, model_family, model_tier,
                          structured_output, sql_quality, instruction, repair, performance,
                          probe_level, probe_count, probed_at, updated_at
                   FROM metadata.llm_capabilities
                   WHERE provider = ? AND endpoint = ? AND model = ?""",
                [provider, endpoint, model],
            ).fetchone()
        if row is None:
            return _try_migrate_from_settings(provider, endpoint, model)
        (db_provider, db_endpoint, db_model, model_family, model_tier,
         structured_output, sql_quality, instruction, repair, performance,
         probe_level, probe_count, probed_at, updated_at) = row

        data = {
            "structured_output": _parse_json_col(structured_output),
            "sql_quality": _parse_json_col(sql_quality),
            "instruction": _parse_json_col(instruction),
            "repair": _parse_json_col(repair),
            "performance": _parse_json_col(performance),
            "probe_meta": {
                "model_key": f"{db_provider}:{db_endpoint}:{db_model}",
                "model_family": model_family,
                "model_tier": model_tier,
                "probe_version": 2,
                "probe_count": probe_count or 0,
                "last_error": "",
                "probed_at": str(probed_at) if probed_at else "",
                "probe_duration_ms": 0.0,
                "probe_level": probe_level or "keyword_only",
            },
        }

        if probe_level == "keyword_only":
            ttl = _CAPABILITY_KEYWORD_TTL
        elif probe_level == "full" and (probe_count or 0) == 0:
            ttl = _CAPABILITY_TTL_PROBE_FAILED
        else:
            ttl = _CAPABILITY_TTL_SECONDS

        if _is_capability_expired(updated_at, ttl_seconds=ttl):
            return None
        return data
    except Exception as exc:
        LOGGER.debug("Failed to load capability from DB: %s", exc)
        return None


def _parse_json_col(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {}
    return {}


def _parse_probe_model_key(model_key: Any) -> tuple[str, str, str] | None:
    key = str(model_key or "").strip()
    if not key or ":" not in key:
        return None
    provider, rest = key.split(":", 1)
    if not provider or ":" not in rest:
        return None
    endpoint, model = rest.rsplit(":", 1)
    if not endpoint or not model:
        return None
    return provider, endpoint, model


def _migrate_all_capabilities_from_settings() -> int:
    moved = 0
    try:
        from db import connection_lock, get_connection
        with connection_lock():
            con = get_connection()
            rows = con.execute(
                "SELECT key, value FROM metadata.settings WHERE key LIKE ?",
                [_CAPABILITY_KEY_PREFIX + "%"],
            ).fetchall()
        delete_keys: list[str] = []
        for key, value in rows:
            data: Any
            if isinstance(value, str):
                try:
                    data = json.loads(value)
                except Exception:
                    continue
            else:
                data = value
            if not isinstance(data, dict):
                continue
            parsed = _parse_probe_model_key(data.get("probe_meta", {}).get("model_key"))
            if parsed is None:
                continue
            provider, endpoint, model = parsed
            _save_capability_to_db(provider, endpoint, model, data)
            delete_keys.append(str(key))
            moved += 1
        if delete_keys:
            from db import connection_lock, get_connection
            with connection_lock():
                con = get_connection()
                con.executemany(
                    "DELETE FROM metadata.settings WHERE key = ?",
                    [[k] for k in delete_keys],
                )
        if moved:
            LOGGER.info(
                "Migrated %d capability rows from metadata.settings to metadata.llm_capabilities",
                moved,
            )
    except Exception as exc:
        LOGGER.debug("Failed to migrate capability rows from settings: %s", exc)
    return moved


def _try_migrate_from_settings(provider: str, endpoint: str, model: str) -> Optional[dict]:
    key = _model_capability_key(provider, endpoint, model)
    try:
        from db import connection_lock, get_connection
        with connection_lock():
            con = get_connection()
            row = con.execute(
                "SELECT value, updated_at FROM metadata.settings WHERE key = ?",
                [key],
            ).fetchone()
        if row is None:
            return None
        value, updated_at = row
        if isinstance(value, str):
            try:
                data = json.loads(value)
            except Exception:
                return None
        else:
            data = value
        if not isinstance(data, dict):
            return None
        _save_capability_to_db(provider, endpoint, model, data)
        with connection_lock():
            con = get_connection()
            con.execute("DELETE FROM metadata.settings WHERE key = ?", [key])
        LOGGER.info("Migrated capability data from metadata.settings to metadata.llm_capabilities for %s", key)
        return data
    except Exception as exc:
        LOGGER.debug("Failed to migrate capability from settings: %s", exc)
        return None


def _save_capability_to_db(provider: str, endpoint: str, model: str, data: dict) -> None:
    try:
        from db import connection_lock, get_connection
        probe_meta = data.get("probe_meta", {})
        model_family = probe_meta.get("model_family") or data.get("model_meta", {}).get("model_family", "")
        model_tier = probe_meta.get("model_tier") or _capabilities_to_tier(data)
        probe_level = probe_meta.get("probe_level", "keyword_only")
        probe_count = probe_meta.get("probe_count", 0)
        probed_at = probe_meta.get("probed_at") or None

        with connection_lock():
            con = get_connection()
            con.execute(
                """INSERT OR REPLACE INTO metadata.llm_capabilities
                   (provider, endpoint, model, model_family, model_tier,
                    structured_output, sql_quality, instruction, repair, performance,
                    probe_level, probe_count, probed_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?::JSON, ?::JSON, ?::JSON, ?::JSON, ?::JSON, ?, ?, ?, CURRENT_TIMESTAMP)""",
                [
                    provider, endpoint, model, model_family, model_tier,
                    json.dumps(data.get("structured_output", {})),
                    json.dumps(data.get("sql_quality", {})),
                    json.dumps(data.get("instruction", {})),
                    json.dumps(data.get("repair", {})),
                    json.dumps(data.get("performance", {})),
                    probe_level, probe_count, probed_at,
                ],
            )
    except Exception as exc:
        LOGGER.debug("Failed to save capability to DB: %s", exc)


def _delete_stale_capability(provider: str, endpoint: str, model: str) -> None:
    try:
        from db import connection_lock, get_connection
        with connection_lock():
            con = get_connection()
            con.execute(
                "DELETE FROM metadata.llm_capabilities WHERE provider = ? AND endpoint = ? AND model = ?",
                [provider, endpoint, model],
            )
    except Exception as exc:
        LOGGER.debug("Failed to delete capability from DB: %s", exc)


def _list_all_capabilities() -> list[dict]:
    results = []
    try:
        _migrate_all_capabilities_from_settings()
        from db import connection_lock, get_connection
        with connection_lock():
            con = get_connection()
            rows = con.execute(
                """SELECT provider, endpoint, model, model_family, model_tier,
                          probe_level, probe_count, probed_at, updated_at
                   FROM metadata.llm_capabilities""",
            ).fetchall()
        for row in rows:
            (provider, endpoint, model, model_family, model_tier,
             probe_level, probe_count, probed_at, updated_at) = row
            results.append({
                "provider": provider,
                "endpoint": endpoint,
                "model": model,
                "model_key": f"{provider}:{endpoint}:{model}",
                "model_family": model_family,
                "model_tier": model_tier,
                "probe_level": probe_level,
                "probe_count": probe_count,
                "probed_at": str(probed_at) if probed_at else None,
                "updated_at": updated_at,
            })
    except Exception as exc:
        LOGGER.debug("Failed to list capabilities from DB: %s", exc)
    return results


# ═══════════════════════════════════════════════════════════════
# Phase 1: Main entry points
# ═══════════════════════════════════════════════════════════════

def get_model_capabilities(
    provider: str,
    endpoint: str,
    model: str,
    force_refresh: bool = False,
) -> dict:
    cache_key = _memory_cache_key(provider, endpoint, model)

    if not force_refresh:
        cached = _memory_cache_get(cache_key)
        if cached is not None:
            return cached

    if not force_refresh:
        db_data = _load_capability_from_db(provider, endpoint, model)
        if db_data is not None:
            _memory_cache_set(cache_key, db_data)
            return db_data

    keyword_data = _keyword_fallback(provider, endpoint, model)
    _save_capability_to_db(provider, endpoint, model, keyword_data)
    _memory_cache_set(cache_key, keyword_data, ttl=_CAPABILITY_KEYWORD_TTL)

    trigger_async_probe(provider, endpoint, model)

    return keyword_data


def probe_and_save(
    provider: str,
    endpoint: str,
    model: str,
) -> dict:
    try:
        from services.sql_routing.llm_probe_suite import probe_sync
        caps = probe_sync(provider, endpoint, model)
    except Exception as exc:
        LOGGER.warning("Probe sync failed for %s/%s: %s; falling back to keyword", provider, model, exc)
        caps = _keyword_fallback(provider, endpoint, model)
    _save_capability_to_db(provider, endpoint, model, caps)
    cache_key = _memory_cache_key(provider, endpoint, model)
    _memory_cache_set(cache_key, caps)
    return caps


def trigger_async_probe(provider: str, endpoint: str, model: str) -> None:
    try:
        from services.sql_routing.llm_probe_suite import trigger_async_probe as _trigger_async
        _trigger_async(provider, endpoint, model)
    except Exception as exc:
        LOGGER.warning("Failed to trigger async probe for %s/%s: %s", provider, model, exc)
