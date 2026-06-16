from __future__ import annotations

import logging
from typing import Any


LOGGER = logging.getLogger(__name__)

# ── Re-export from ask_config to avoid duplication ──
from services.ask_config import (  # noqa: E402, F401
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
    _RUNTIME_SETTINGS_CACHE_TTL,
    _RUNTIME_ASK_DEFAULTS,
    _CONNECTION_TIMEOUTS,
)

# ── Common types used across ask_* modules ──

NormalizedAnalysis = dict[str, Any]
SemanticHit = dict[str, Any]
RouteResult = dict[str, Any]
SqlResult = dict[str, Any]

# ── Pure utility functions ──

def safe_json_loads(value: Any, fallback: Any = None) -> Any:
    import json
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def coerce_int_setting(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(int(minimum), min(int(maximum), parsed))


def coerce_float_setting(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return max(float(minimum), min(float(maximum), parsed))
