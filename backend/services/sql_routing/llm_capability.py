from __future__ import annotations

import json
import os
import threading
import time
from typing import Any


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
