from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from db import connection_lock, get_connection
from models.schemas import (
    AskSettingsUpdate,
    BrandingUpdate,
    GeneralUpdate,
    LLMAdvancedUpdate,
    LLMEndpointWhitelistUpdate,
    LLMModelsRequest,
    LLMTestRequest,
    LLMUpdate,
    RecommenderSettingsUpdate,
    RouterSettingsUpdate,
    SecuritySettingsUpdate,
    ThemeUpdate,
)
from routers.auth import (
    consume_ws_ticket,
    get_current_user,
    get_payload_from_token,
    log_audit,
    payload_has_permission,
    require_permission,
)
from services.crypto_service import decrypt_json, encrypt_json, is_encrypted_value
from services.llm_service import (
    DEFAULT_ENDPOINTS,
    DEFAULT_MODELS,
    LLMService,
    refresh_llm_http_resilience_settings,
)

router = APIRouter()

LOGGER = logging.getLogger(__name__)

RECOMMENDER_MAP = {
    "max_results": "recommender_max_results",
    "schema_weight": "recommender_schema_weight",
    "session_weight": "recommender_session_weight",
    "user_weight": "recommender_user_weight",
    "project_weight": "recommender_project_weight",
    "global_weight": "recommender_global_weight",
    "llm_weight": "recommender_llm_weight",
    "novelty_weight": "recommender_novelty_weight",
    "score_weight": "recommender_score_weight",
    "score_learning_rate": "recommender_score_learning_rate",
    "score_half_life": "recommender_score_half_life_days",
    "low_score_threshold": "recommender_low_score_threshold",
    "consecutive_low_alert": "recommender_consecutive_low_alert",
    "auto_recover": "recommender_weight_auto_recover",
}

from services.sensitive_keys import is_sensitive_key as _is_sensitive_key

MASKED_SECRET = "********"


def _refresh_runtime_router_settings() -> None:
    try:
        from services.ask_service import refresh_runtime_router_settings

        refresh_runtime_router_settings(force=True)
    except Exception:
        LOGGER.warning("Failed to refresh ask/router runtime settings", exc_info=True)


def _capabilities_to_tier(capabilities: dict) -> str:
    try:
        from services.sql_routing.llm_capability import _capabilities_to_tier as _tier
        return _tier(capabilities)
    except Exception:
        return "weak"


_SETTINGS_SSE_BEARER = HTTPBearer(auto_error=False)
_LLM_PROBE_SESSION_TTL_SECONDS = 3600.0
_LLM_PROBE_SESSION_STREAM_POLL_SECONDS = 0.35
_LLM_PROBE_SESSION_STREAM_HEARTBEAT_SECONDS = 10.0
_LLM_PROBE_SESSION_MAX = 256


@dataclass
class _LLMProbeSession:
    session_id: str
    user_id: int
    provider: str
    endpoint: str
    model: str
    probe_level: str
    status: str = "queued"
    tier: str = "weak"
    capabilities: dict[str, Any] = field(default_factory=dict)
    capability_saved: bool = False
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    events: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancel_event: threading.Event = field(default_factory=threading.Event)


_llm_probe_sessions_lock = threading.Lock()
_llm_probe_sessions: dict[str, _LLMProbeSession] = {}
_llm_probe_active_by_target: dict[str, str] = {}


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def _probe_sse_frame(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _llm_probe_target_key(*, user_id: int, provider: str, endpoint: str, model: str) -> str:
    return f"{int(user_id)}:{str(provider or '').strip()}:{str(endpoint or '').strip()}:{str(model or '').strip()}"


def _cleanup_llm_probe_sessions() -> None:
    now_ts = time.time()
    with _llm_probe_sessions_lock:
        stale_session_ids = [
            session_id
            for session_id, session in _llm_probe_sessions.items()
            if (now_ts - float(session.updated_at or session.created_at)) > _LLM_PROBE_SESSION_TTL_SECONDS
        ]
        for session_id in stale_session_ids:
            _llm_probe_sessions.pop(session_id, None)
        if stale_session_ids:
            stale_set = set(stale_session_ids)
            stale_active_keys = [
                target_key
                for target_key, active_session_id in _llm_probe_active_by_target.items()
                if active_session_id in stale_set
            ]
            for target_key in stale_active_keys:
                _llm_probe_active_by_target.pop(target_key, None)
        if len(_llm_probe_sessions) > _LLM_PROBE_SESSION_MAX:
            ordered = sorted(_llm_probe_sessions.values(), key=lambda item: float(item.updated_at or item.created_at))
            drop_count = len(_llm_probe_sessions) - _LLM_PROBE_SESSION_MAX
            for stale in ordered[:drop_count]:
                _llm_probe_sessions.pop(stale.session_id, None)
                stale_target_key = _llm_probe_target_key(
                    user_id=stale.user_id,
                    provider=stale.provider,
                    endpoint=stale.endpoint,
                    model=stale.model,
                )
                if _llm_probe_active_by_target.get(stale_target_key) == stale.session_id:
                    _llm_probe_active_by_target.pop(stale_target_key, None)


def _append_llm_probe_event(session: _LLMProbeSession, event: str, payload: Optional[dict[str, Any]] = None) -> None:
    details = dict(payload or {})
    details.setdefault("event", event)
    details.setdefault("session_id", session.session_id)
    details.setdefault("provider", session.provider)
    details.setdefault("endpoint", session.endpoint)
    details.setdefault("model", session.model)
    details.setdefault("probe_level", session.probe_level)
    details.setdefault("timestamp", _now_iso_utc())
    with session.lock:
        details.setdefault("status", session.status)
        details.setdefault("tier", session.tier)
        details.setdefault("capability_saved", session.capability_saved)
        session.events.append(details)
        session.updated_at = time.time()


def _llm_probe_session_snapshot(session: _LLMProbeSession) -> dict[str, Any]:
    with session.lock:
        return {
            "session_id": session.session_id,
            "provider": session.provider,
            "endpoint": session.endpoint,
            "model": session.model,
            "probe_level": session.probe_level,
            "status": session.status,
            "tier": session.tier,
            "capability_saved": session.capability_saved,
            "error": session.error,
            "capabilities": dict(session.capabilities) if isinstance(session.capabilities, dict) else {},
            "created_at": datetime.fromtimestamp(session.created_at, timezone.utc).isoformat(),
            "updated_at": datetime.fromtimestamp(session.updated_at, timezone.utc).isoformat(),
            "event_count": len(session.events),
        }


def _cancel_llm_probe_session(session: _LLMProbeSession, reason: str = "cancelled_by_new_request") -> None:
    with session.lock:
        already_terminal = session.status in {"completed", "failed", "cancelled"}
        if already_terminal:
            return
        session.status = "cancelled"
        session.error = reason
        session.updated_at = time.time()
        session.cancel_event.set()
    _append_llm_probe_event(
        session,
        "probe_cancelled",
        {
            "status": "cancelled",
            "reason": reason,
            "capabilities": dict(session.capabilities) if isinstance(session.capabilities, dict) else {},
            "capability_saved": session.capability_saved,
            "tier": session.tier,
        },
    )


def _create_llm_probe_session(
    *,
    user_id: int,
    provider: str,
    endpoint: str,
    model: str,
    probe_level: str,
    initial_capabilities: Optional[dict[str, Any]] = None,
) -> _LLMProbeSession:
    _cleanup_llm_probe_sessions()
    target_key = _llm_probe_target_key(
        user_id=int(user_id),
        provider=provider,
        endpoint=endpoint,
        model=model,
    )
    previous_active: Optional[_LLMProbeSession] = None
    with _llm_probe_sessions_lock:
        previous_id = _llm_probe_active_by_target.get(target_key)
        if previous_id:
            previous_active = _llm_probe_sessions.get(previous_id)

    if previous_active is not None:
        _cancel_llm_probe_session(previous_active, reason="replaced_by_new_probe")

    session = _LLMProbeSession(
        session_id=uuid.uuid4().hex,
        user_id=int(user_id),
        provider=str(provider or ""),
        endpoint=str(endpoint or ""),
        model=str(model or ""),
        probe_level=str(probe_level or "fast"),
    )
    if isinstance(initial_capabilities, dict):
        session.capabilities = dict(initial_capabilities)
        session.tier = _capabilities_to_tier(session.capabilities)
    session.capability_saved = False
    with _llm_probe_sessions_lock:
        _llm_probe_sessions[session.session_id] = session
        _llm_probe_active_by_target[target_key] = session.session_id
    _append_llm_probe_event(
        session,
        "probe_queued",
        {
            "status": "queued",
            "capabilities": session.capabilities,
            "tier": session.tier,
            "capability_saved": False,
        },
    )
    return session


def _get_llm_probe_session_for_user(session_id: str, user_id: int) -> _LLMProbeSession:
    _cleanup_llm_probe_sessions()
    with _llm_probe_sessions_lock:
        session = _llm_probe_sessions.get(str(session_id or "").strip())
    if session is None:
        raise HTTPException(status_code=404, detail="Probe session not found")
    if int(session.user_id) != int(user_id):
        raise HTTPException(status_code=404, detail="Probe session not found")
    return session


def _persist_llm_probe_capabilities(session: _LLMProbeSession, capabilities: dict[str, Any]) -> bool:
    caps_to_save = dict(capabilities) if isinstance(capabilities, dict) else {}
    if not caps_to_save:
        return False
    try:
        from services.sql_routing.llm_capability import (
            _memory_cache_key,
            _memory_cache_set,
            _save_capability_to_db,
        )

        _save_capability_to_db(session.provider, session.endpoint, session.model, caps_to_save)
        _memory_cache_set(_memory_cache_key(session.provider, session.endpoint, session.model), caps_to_save)
    except Exception as exc:
        LOGGER.warning("Failed to persist async LLM probe capabilities: %s", exc)
        return False

    with session.lock:
        session.capabilities = caps_to_save
        session.tier = _capabilities_to_tier(caps_to_save)
        session.capability_saved = True
        session.updated_at = time.time()
    return True


def _normalize_capabilities_payload(
    capabilities: dict[str, Any],
    *,
    provider: str,
    endpoint: str,
    model: str,
    default_probe_level: str = "full",
) -> dict[str, Any]:
    normalized = dict(capabilities or {})
    for section in ("structured_output", "sql_quality", "instruction", "repair", "performance"):
        if not isinstance(normalized.get(section), dict):
            normalized[section] = {}
    probe_meta = normalized.get("probe_meta")
    if not isinstance(probe_meta, dict):
        probe_meta = {}
    probe_meta["model_key"] = f"{provider}:{endpoint}:{model}"
    probe_meta.setdefault("probe_level", default_probe_level or "full")
    probe_meta.setdefault("probe_count", 1)
    probe_meta.setdefault("probe_version", 2)
    probe_meta.setdefault("last_error", "")
    probe_meta.setdefault("probe_duration_ms", 0.0)
    probe_meta.setdefault(
        "probed_at",
        datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    )
    normalized["probe_meta"] = probe_meta
    return normalized


def _start_llm_probe_async(session: _LLMProbeSession, *, api_key: str, schema_hint: str = "") -> None:
    target_key = _llm_probe_target_key(
        user_id=session.user_id,
        provider=session.provider,
        endpoint=session.endpoint,
        model=session.model,
    )

    def _worker() -> None:
        from services.sql_routing.llm_probe_suite import probe_sync

        with session.lock:
            session.status = "running"
            session.error = ""
            session.capability_saved = False
            session.cancel_event.clear()
            session.updated_at = time.time()
        _append_llm_probe_event(session, "probe_started", {"status": "running", "capability_saved": False})

        def _progress_callback(event: dict[str, Any]) -> None:
            if session.cancel_event.is_set():
                return
            event_name = str(event.get("event") or "progress").strip() or "progress"
            event_payload = dict(event)
            incoming_caps = event_payload.get("capabilities") if isinstance(event_payload.get("capabilities"), dict) else None
            if incoming_caps is not None:
                _persist_llm_probe_capabilities(session, incoming_caps)

            with session.lock:
                if event_name == "probe_completed":
                    session.status = "completed"
                    session.error = ""
                elif event_name == "probe_failed":
                    session.status = "failed"
                    session.error = str(event_payload.get("error") or "Probe failed")
                else:
                    session.status = "running"
                session.updated_at = time.time()
                event_payload["status"] = session.status
                event_payload["capability_saved"] = session.capability_saved
                event_payload["tier"] = session.tier

            _append_llm_probe_event(session, event_name, event_payload)

        try:
            final_caps = probe_sync(
                session.provider,
                session.endpoint,
                session.model,
                schema_hint=schema_hint,
                api_key=api_key,
                probe_level=session.probe_level,
                progress_cb=_progress_callback,
                cancel_event=session.cancel_event,
            )
            if session.cancel_event.is_set():
                _cancel_llm_probe_session(session, reason="cancelled")
                return
            if isinstance(final_caps, dict):
                _persist_llm_probe_capabilities(session, final_caps)
            with session.lock:
                session.status = "completed"
                session.error = ""
                session.updated_at = time.time()
                done_payload = {
                    "status": session.status,
                    "capability_saved": session.capability_saved,
                    "tier": session.tier,
                    "capabilities": dict(session.capabilities) if isinstance(session.capabilities, dict) else {},
                }
            _append_llm_probe_event(session, "probe_done", done_payload)
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {str(exc)[:300]}"
            with session.lock:
                session.status = "failed"
                session.error = error_text
                session.updated_at = time.time()
            _append_llm_probe_event(
                session,
                "probe_failed",
                {
                    "status": "failed",
                    "error": error_text,
                    "capability_saved": session.capability_saved,
                    "capabilities": dict(session.capabilities) if isinstance(session.capabilities, dict) else {},
                    "tier": session.tier,
                },
            )
        finally:
            with _llm_probe_sessions_lock:
                if _llm_probe_active_by_target.get(target_key) == session.session_id:
                    _llm_probe_active_by_target.pop(target_key, None)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


async def _get_settings_sse_payload(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_SETTINGS_SSE_BEARER),
    ticket: Optional[str] = Query(None),
    token: Optional[str] = Query(None),
) -> dict[str, Any]:
    if ticket:
        payload = consume_ws_ticket(str(ticket).strip())
        if not isinstance(payload, dict):
            raise HTTPException(status_code=401, detail="Invalid or expired ticket")
        if not payload_has_permission(payload, "settings", "update"):
            raise HTTPException(status_code=403, detail="Permission denied")
        return payload

    if credentials:
        try:
            payload = get_payload_from_token(credentials.credentials)
            if not payload_has_permission(payload, "settings", "update"):
                raise HTTPException(status_code=403, detail="Permission denied")
            return payload
        except HTTPException as exc:
            if exc.status_code == 403:
                raise
    if token:
        payload = get_payload_from_token(token)
        if not payload_has_permission(payload, "settings", "update"):
            raise HTTPException(status_code=403, detail="Permission denied")
        return payload
    raise HTTPException(status_code=401, detail="Authentication required")


def _force_refresh_runtime_router_settings() -> dict[str, object]:
    from services.ask_service import refresh_runtime_router_settings

    snapshot = refresh_runtime_router_settings(force=True)
    router_config = snapshot.get("router_config") if isinstance(snapshot.get("router_config"), dict) else {}
    result: dict[str, object] = {
        "max_sql_rows": snapshot.get("MAX_SQL_ROWS"),
        "default_preview_row_limit": snapshot.get("DEFAULT_PREVIEW_ROW_LIMIT"),
        "min_preview_row_limit": snapshot.get("MIN_PREVIEW_ROW_LIMIT"),
        "max_preview_row_limit": snapshot.get("MAX_PREVIEW_ROW_LIMIT"),
        "max_source_materialization_rows": snapshot.get("MAX_SOURCE_MATERIALIZATION_ROWS"),
        "analysis_cache_max": snapshot.get("analysis_cache_max"),
        "analysis_cache_ttl_s": snapshot.get("analysis_cache_ttl_s"),
    }
    result.update(router_config)
    return result


def _payload_user_id(payload: dict | None) -> int | None:
    if not isinstance(payload, dict):
        return None
    raw = payload.get("sub")
    if raw is None:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def _audit_settings_update(payload: dict, scope: str, changed_fields: list[str], *, action: str = "update") -> None:
    normalized_fields = sorted({str(field or "").strip() for field in changed_fields if str(field or "").strip()})
    if not normalized_fields:
        return
    log_audit(
        _payload_user_id(payload),
        f"SETTINGS_{scope.upper()}_UPDATE",
        "settings",
        scope,
        action,
        {
            "scope": scope,
            "changed_fields": normalized_fields,
        },
    )


def _parse_audit_detail(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _settings_scope_from_event(event_type: object, detail: dict[str, object]) -> str:
    raw_scope = detail.get("scope")
    if isinstance(raw_scope, str) and raw_scope.strip():
        return raw_scope.strip().lower()
    marker = str(event_type or "").strip().upper()
    if marker == "SETTINGS_ROUTER_RELOAD":
        return "router"
    prefix = "SETTINGS_"
    suffix = "_UPDATE"
    if marker.startswith(prefix) and marker.endswith(suffix):
        return marker[len(prefix):-len(suffix)].strip().lower() or "unknown"
    return "unknown"


def _normalize_changed_fields(detail: dict[str, object]) -> list[str]:
    raw = detail.get("changed_fields")
    if not isinstance(raw, list):
        return []
    normalized: set[str] = set()
    for item in raw:
        field_name = str(item or "").strip()
        if not field_name:
            continue
        normalized.add(field_name)
    return sorted(normalized)


def _parse_timestamp_filter(raw: str | None, *, param_name: str) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid '{param_name}' timestamp") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


@router.get("/audit-summary", response_model=dict)
def get_settings_audit_summary(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    scope: str | None = Query(None),
    max_events: int = Query(2000, ge=100, le=20000),
    latest_limit: int = Query(20, ge=1, le=200),
    latest_offset: int = Query(0, ge=0, le=5000),
    payload: dict = Depends(require_permission("settings", "read")),
):
    capped_max_events = max(100, min(20000, int(max_events or 2000)))
    capped_latest_limit = max(1, min(200, int(latest_limit or 20)))
    capped_latest_offset = max(0, min(5000, int(latest_offset or 0)))
    normalized_scope = str(scope or "").strip().lower() or None
    parsed_from = _parse_timestamp_filter(from_, param_name="from")
    parsed_to = _parse_timestamp_filter(to, param_name="to")
    if parsed_from is not None and parsed_to is not None and parsed_from > parsed_to:
        raise HTTPException(status_code=422, detail="'from' must be less than or equal to 'to'")
    with connection_lock():
        con = get_connection()
        conditions = ["event_type LIKE 'SETTINGS_%'"]
        params: list[object] = []
        if parsed_from is not None:
            conditions.append("created_at >= ?::TIMESTAMP")
            params.append(parsed_from)
        if parsed_to is not None:
            conditions.append("created_at <= ?::TIMESTAMP")
            params.append(parsed_to)
        where = " WHERE " + " AND ".join(conditions)
        rows = con.execute(
            f"SELECT id, event_type, user_id, resource_id, action, detail, created_at FROM metadata.audit_logs{where} ORDER BY created_at DESC, id DESC LIMIT ?",
            params + [capped_max_events],
        ).fetchall()

    by_scope: dict[str, dict[str, object]] = {}
    latest: list[dict[str, object]] = []
    matched_events = 0
    for row in rows:
        event_type = str(row[1] or "")
        user_id = row[2]
        resource_id = str(row[3] or "")
        action = str(row[4] or "")
        detail = _parse_audit_detail(row[5])
        created_at = str(row[6]) if row[6] else None
        scope = _settings_scope_from_event(event_type, detail)
        if normalized_scope is not None and scope != normalized_scope:
            continue
        matched_events += 1
        changed_fields = _normalize_changed_fields(detail)

        scope_bucket = by_scope.setdefault(
            scope,
            {
                "events": 0,
                "last_updated": None,
                "changed_fields": {},
                "actions": {},
            },
        )
        scope_bucket["events"] = int(scope_bucket.get("events") or 0) + 1
        if scope_bucket.get("last_updated") is None and created_at is not None:
            scope_bucket["last_updated"] = created_at

        actions_bucket = scope_bucket.setdefault("actions", {})
        if action:
            actions_bucket[action] = int(actions_bucket.get(action) or 0) + 1

        changed_fields_bucket = scope_bucket.setdefault("changed_fields", {})
        for field_name in changed_fields:
            changed_fields_bucket[field_name] = int(changed_fields_bucket.get(field_name) or 0) + 1

        latest_index = matched_events - 1
        if capped_latest_offset <= latest_index < (capped_latest_offset + capped_latest_limit):
            latest.append(
                {
                    "event_type": event_type,
                    "scope": scope,
                    "user_id": user_id,
                    "resource_id": resource_id,
                    "action": action,
                    "created_at": created_at,
                    "changed_fields": changed_fields,
                }
            )

    return {
        "data": {
            "scanned_events": len(rows),
            "matched_events": matched_events,
            "scope": normalized_scope,
            "latest_offset": capped_latest_offset,
            "latest_limit": capped_latest_limit,
            "by_scope": by_scope,
            "latest": latest,
        }
    }


def _redact_settings(settings: dict[str, object]) -> dict[str, object]:
    return {key: _redact_setting_value(key, value) for key, value in settings.items()}


def _redact_setting_value(key: str, value):
    if _is_sensitive_key(key) and value not in (None, "", "null"):
        return MASKED_SECRET
    if isinstance(value, dict):
        return {child_key: _redact_setting_value(str(child_key), child_value) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_redact_setting_value(key, item) for item in value]
    return value


def _setting_value(con, key: str):
    row = con.execute("SELECT value FROM metadata.settings WHERE key = ?", [key]).fetchone()
    return _json_value(key, row[0]) if row else None


def _resolve_llm_api_key(con, value: str | None) -> str:
    if value == MASKED_SECRET or value is None:
        return str(_setting_value(con, "llm_api_key") or "")
    return value


def _upsert_settings(con, mapping: dict[str, object]) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    statements: list[tuple[str, str, bool]] = []
    for key, value in mapping.items():
        if value is None:
            continue
        sensitive = _is_sensitive_key(key)
        stored_value = encrypt_json(value) if sensitive else json.dumps(value)
        statements.append((key, stored_value, sensitive))
    if not statements:
        return
    try:
        con.execute("BEGIN TRANSACTION")
        for key, stored_value, sensitive in statements:
            con.execute(
                "INSERT OR REPLACE INTO metadata.settings (key, value, updated_at) VALUES (?, ?::JSON, ?)",
                [key, json.dumps(stored_value) if sensitive else stored_value, now],
            )
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise


def _json_value(key: str, value):
    if _is_sensitive_key(key):
        return decrypt_json(value, None)
    if is_encrypted_value(value):
        return decrypt_json(value, None)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


@router.get("", response_model=dict)
def get_settings(
    payload: dict = Depends(require_permission("settings", "read")),
):
    with connection_lock():
        con = get_connection()
        rows = con.execute("SELECT key, value FROM metadata.settings").fetchall()
        result = {}
        for r in rows:
            result[r[0]] = _json_value(r[0], r[1])
    safe_result = _redact_settings(result)
    return {"data": {"settings": safe_result}}


@router.get("/public", response_model=dict)
def get_public_settings():
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            "SELECT key, value FROM metadata.settings WHERE key IN ('app_name', 'app_description', 'app_logo', 'app_icon', 'language', 'timeout_request_ms')"
        ).fetchall()
        result = {}
        for r in rows:
            result[r[0]] = _json_value(r[0], r[1])
        sso_row = con.execute("SELECT value FROM metadata.settings WHERE key = 'sso_config'").fetchone()
    if not str(result.get("language") or "").strip():
        result["language"] = "en"
    sso_enabled = False
    sso_provider = None
    if sso_row:
        try:
            sso_cfg = decrypt_json(sso_row[0], {})
            if isinstance(sso_cfg, dict):
                sso_enabled = bool(sso_cfg.get("enabled", False))
                sso_provider = sso_cfg.get("provider") or "oidc"
                del sso_cfg
        except Exception:
            pass
    result["sso_enabled"] = sso_enabled
    result["sso_provider"] = sso_provider
    return {"data": {"settings": result}}


@router.put("/branding", response_model=dict)
def update_branding(
    body: BrandingUpdate,
    payload: dict = Depends(require_permission("settings", "update")),
):
    updated_fields: list[str] = []
    if body.app_name is not None:
        updated_fields.append("app_name")
    if body.app_description is not None:
        updated_fields.append("app_description")
    if body.logo is not None:
        updated_fields.append("logo")
    if body.icon is not None:
        updated_fields.append("icon")
    with connection_lock():
        _upsert_settings(get_connection(), {
            "app_name": body.app_name,
            "app_description": body.app_description,
            "app_logo": body.logo,
            "app_icon": body.icon,
        })
    _audit_settings_update(payload, "branding", updated_fields)
    return {"data": {"success": True}}


@router.put("/theme", response_model=dict)
def update_theme(
    body: ThemeUpdate,
    payload: dict = Depends(require_permission("settings", "update")),
):
    updated_fields: list[str] = []
    if body.mode is not None:
        updated_fields.append("mode")
    if body.primary_color is not None:
        updated_fields.append("primary_color")
    if body.border_radius is not None:
        updated_fields.append("border_radius")
    if body.font is not None:
        updated_fields.append("font")
    with connection_lock():
        _upsert_settings(get_connection(), {
            "theme_mode": body.mode,
            "theme_primary_color": body.primary_color,
            "theme_border_radius": body.border_radius,
            "theme_font": body.font,
        })
    _audit_settings_update(payload, "theme", updated_fields)
    return {"data": {"success": True}}


@router.put("/llm", response_model=dict)
def update_llm(
    body: LLMUpdate,
    payload: dict = Depends(require_permission("settings", "update")),
):
    updated_fields: list[str] = []
    standard_fields = (
        "provider", "api_key", "model", "endpoint",
        "max_tokens", "temperature", "extra_params", "system_prompt",
    )
    has_standard_update = False
    for field_name in standard_fields:
        if getattr(body, field_name, None) is not None:
            updated_fields.append(field_name)
            has_standard_update = True
    if getattr(body, "probed_capabilities", None) is not None or getattr(body, "probe_session_id", None) is not None:
        updated_fields.append("probe")
    if body.endpoint:
        try:
            from services.llm_service import _validate_llm_endpoint
            _validate_llm_endpoint(str(body.endpoint))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    with connection_lock():
        con = get_connection()
        api_key = body.api_key
        if api_key == MASKED_SECRET:
            api_key = None
        _upsert_settings(con, {
            "llm_provider": body.provider,
            "llm_api_key": api_key,
            "llm_model": body.model,
            "llm_endpoint": body.endpoint,
            "llm_max_tokens": body.max_tokens,
            "llm_temperature": body.temperature,
            "llm_extra_params": body.extra_params,
            "llm_system_prompt": body.system_prompt,
        })
    _audit_settings_update(payload, "llm", updated_fields)
    from services.llm_service import get_llm_config

    cfg = get_llm_config()
    llm_model = str(cfg.get("model") or "")
    llm_endpoint = str(cfg.get("endpoint") or "")
    llm_provider = str(cfg.get("provider") or "")
    request_probe_session_id = str(body.probe_session_id or "").strip()
    user_id = _payload_user_id(payload)

    probe_session: Optional[_LLMProbeSession] = None
    if request_probe_session_id and user_id is not None:
        try:
            probe_session = _get_llm_probe_session_for_user(request_probe_session_id, int(user_id))
        except HTTPException:
            probe_session = None

    result_caps: dict = {}
    capability_saved = False

    if llm_model and llm_endpoint:
        session_matches_model = (
            probe_session is not None
            and probe_session.provider == llm_provider
            and probe_session.endpoint == llm_endpoint
            and probe_session.model == llm_model
        )

        if session_matches_model and probe_session is not None:
            with probe_session.lock:
                session_saved = bool(probe_session.capability_saved)
                session_caps = dict(probe_session.capabilities) if isinstance(probe_session.capabilities, dict) else {}

            if session_saved and session_caps:
                result_caps = session_caps
                capability_saved = True
            else:
                source_caps: dict[str, Any] = {}
                if isinstance(body.probed_capabilities, dict):
                    source_caps = dict(body.probed_capabilities)
                elif session_caps:
                    source_caps = session_caps
                if source_caps:
                    normalized = _normalize_capabilities_payload(
                        source_caps,
                        provider=llm_provider,
                        endpoint=llm_endpoint,
                        model=llm_model,
                        default_probe_level=probe_session.probe_level,
                    )
                    capability_saved = _persist_llm_probe_capabilities(probe_session, normalized)
                    result_caps = normalized if capability_saved else source_caps
        elif isinstance(body.probed_capabilities, dict):
            try:
                from services.sql_routing.llm_capability import (
                    _memory_cache_key,
                    _memory_cache_set,
                    _save_capability_to_db,
                )

                normalized = _normalize_capabilities_payload(
                    dict(body.probed_capabilities),
                    provider=llm_provider,
                    endpoint=llm_endpoint,
                    model=llm_model,
                    default_probe_level="full",
                )
                _save_capability_to_db(llm_provider, llm_endpoint, llm_model, normalized)
                _memory_cache_set(_memory_cache_key(llm_provider, llm_endpoint, llm_model), normalized)
                result_caps = normalized
                capability_saved = True
            except Exception as exc:
                LOGGER.warning("Failed to persist provided LLM capabilities on save: %s", exc)
                result_caps = {}
                capability_saved = False

    tier = _capabilities_to_tier(result_caps) if result_caps else "weak"
    return {
        "data": {
            "success": True,
            "capabilities": result_caps or {},
            "tier": tier,
            "capability_saved": capability_saved,
            "probe_session_id": request_probe_session_id or None,
        }
    }


@router.post("/llm/test", response_model=dict)
def test_llm(
    body: LLMTestRequest,
    payload: dict = Depends(require_permission("settings", "update")),
):
    try:
        requested_probe_level = str(body.probe_level or "fast").strip().lower()
        if requested_probe_level not in {"fast", "full"}:
            requested_probe_level = "fast"
        with connection_lock():
            con = get_connection()
            api_key = _resolve_llm_api_key(con, body.api_key)
        endpoint = body.endpoint or DEFAULT_ENDPOINTS.get(body.provider, "")
        model_name = body.model or DEFAULT_MODELS.get(body.provider, "")
        if endpoint:
            try:
                from services.llm_service import _validate_llm_endpoint
                endpoint = _validate_llm_endpoint(str(endpoint))
            except ValueError as e:
                return {"data": {"success": False, "latency_ms": None, "error": str(e)}}
        config = {
            "provider": body.provider,
            "api_key": api_key,
            "model": model_name,
            "endpoint": endpoint,
            "max_tokens": 128,
            "temperature": 0,
            "extra_params": {},
        }
        result = LLMService(config).chat([
            {"role": "system", "content": "Reply with pong."},
            {"role": "user", "content": "ping"},
        ])
        if not bool(result.get("configured")):
            return {
                "data": {
                    "success": False,
                    "latency_ms": result.get("latency_ms"),
                    "error": result.get("content"),
                }
            }

        from services.sql_routing.llm_capability import _keyword_fallback

        initial_caps = _keyword_fallback(body.provider, endpoint, model_name)
        user_id = _payload_user_id(payload)
        if user_id is None:
            return {
                "data": {
                    "success": False,
                    "latency_ms": result.get("latency_ms"),
                    "error": "Authentication required",
                }
            }
        session = _create_llm_probe_session(
            user_id=int(user_id),
            provider=body.provider,
            endpoint=endpoint,
            model=model_name,
            probe_level=requested_probe_level,
            initial_capabilities=initial_caps,
        )
        _start_llm_probe_async(session, api_key=api_key)
        session_state = _llm_probe_session_snapshot(session)
        caps = session_state.get("capabilities") if isinstance(session_state.get("capabilities"), dict) else {}

        return {
            "data": {
                "success": True,
                "latency_ms": result.get("latency_ms"),
                "error": None,
                "capabilities": caps,
                "tier": str(session_state.get("tier") or (_capabilities_to_tier(caps) if caps else "weak")),
                "model_key": caps.get("probe_meta", {}).get("model_key") if isinstance(caps, dict) else None,
                "probe_level": requested_probe_level,
                "probed_at": caps.get("probe_meta", {}).get("probed_at") if isinstance(caps, dict) else None,
                "probe_status": session_state.get("status"),
                "probe_session_id": session.session_id,
                "capability_saved": bool(session_state.get("capability_saved")),
                "async": True,
            }
        }
    except Exception as exc:
        LOGGER.warning("LLM test failed: %s", exc)
        return {"data": {"success": False, "latency_ms": None, "error": f"LLM test failed: {type(exc).__name__}: {str(exc)[:200]}"}}


@router.get("/llm/test/stream")
async def stream_llm_test(
    request: Request,
    session_id: str = Query(..., min_length=8),
    payload: dict = Depends(_get_settings_sse_payload),
):
    user_id = _payload_user_id(payload)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    session = _get_llm_probe_session_for_user(session_id, int(user_id))

    async def _event_stream():
        cursor = 0
        last_heartbeat = time.monotonic()
        snapshot = _llm_probe_session_snapshot(session)
        yield _probe_sse_frame("session_state", snapshot)
        while True:
            with session.lock:
                total = len(session.events)
                pending = list(session.events[cursor:total])
                status = session.status
            for event_payload in pending:
                cursor += 1
                event_name = str(event_payload.get("event") or "message").strip() or "message"
                yield _probe_sse_frame(event_name, event_payload)

            if status in {"completed", "failed", "cancelled"} and cursor >= total:
                break
            if await request.is_disconnected():
                break
            now_monotonic = time.monotonic()
            if (now_monotonic - last_heartbeat) >= _LLM_PROBE_SESSION_STREAM_HEARTBEAT_SECONDS:
                yield ": keepalive\n\n"
                last_heartbeat = now_monotonic
            await asyncio.sleep(_LLM_PROBE_SESSION_STREAM_POLL_SECONDS)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/llm/probe", response_model=dict)
def trigger_llm_probe(
    payload: dict = Depends(require_permission("settings", "update")),
):
    from services.llm_service import get_llm_config
    from services.sql_routing.llm_capability import probe_and_save
    cfg = get_llm_config()
    try:
        caps = probe_and_save(
            cfg.get("provider", ""),
            cfg.get("endpoint", ""),
            cfg.get("model", ""),
        )
        return {
            "data": {
                "status": "completed",
                "model_key": caps.get("probe_meta", {}).get("model_key"),
                "probe_level": caps.get("probe_meta", {}).get("probe_level"),
                "probed_at": caps.get("probe_meta", {}).get("probed_at"),
                "tier": _capabilities_to_tier(caps),
            }
        }
    except Exception as exc:
        LOGGER.warning("LLM probe failed: %s", exc)
        return {"data": {"status": "error", "error": str(exc)}}


@router.get("/llm/probe", response_model=dict)
def get_llm_probe_status(
    payload: dict = Depends(require_permission("settings", "read")),
):
    from services.llm_service import get_llm_config
    from services.sql_routing.llm_capability import get_model_capabilities
    cfg = get_llm_config()
    caps = get_model_capabilities(
        cfg.get("provider", ""),
        cfg.get("endpoint", ""),
        cfg.get("model", ""),
    )
    return {
        "data": {
            "model_key": caps.get("probe_meta", {}).get("model_key"),
            "probe_level": caps.get("probe_meta", {}).get("probe_level"),
            "probed_at": caps.get("probe_meta", {}).get("probed_at"),
            "tier": _capabilities_to_tier(caps),
            "capabilities": caps,
        }
    }


@router.get("/llm/probe/history", response_model=dict)
def list_llm_probe_history(
    payload: dict = Depends(require_permission("settings", "read")),
):
    from services.sql_routing.llm_capability import _list_all_capabilities
    return {"data": {"models": _list_all_capabilities()}}


ANTHROPIC_MODELS = [
    "claude-3-5-sonnet-latest",
    "claude-3-5-haiku-latest",
    "claude-3-opus-latest",
    "claude-3-sonnet-20240229",
    "claude-3-haiku-20240307",
]


@router.get("/llm/whitelist", response_model=dict)
def get_llm_whitelist(
    payload: dict = Depends(require_permission("settings", "read")),
):
    from services.llm_service import DEFAULT_ENDPOINT_WHITELIST
    with connection_lock():
        con = get_connection()
        enabled_row = con.execute("SELECT value FROM metadata.settings WHERE key = 'llm_endpoint_whitelist_enabled'").fetchone()
        wl_row = con.execute("SELECT value FROM metadata.settings WHERE key = 'llm_endpoint_whitelist'").fetchone()
    enabled = True
    if enabled_row:
        val = enabled_row[0]
        if isinstance(val, str):
            val = val.strip('"')
        if str(val).lower() in ("false", "0"):
            enabled = False
    import json as _json
    prefixes = list(DEFAULT_ENDPOINT_WHITELIST)
    if wl_row:
        try:
            val = wl_row[0]
            if isinstance(val, str):
                val = _json.loads(val)
            if isinstance(val, list):
                prefixes = val
        except Exception:
            pass
    return {"data": {"enabled": enabled, "prefixes": prefixes, "defaults": list(DEFAULT_ENDPOINT_WHITELIST)}}


@router.put("/llm/whitelist", response_model=dict)
def update_llm_whitelist(
    body: LLMEndpointWhitelistUpdate,
    payload: dict = Depends(require_permission("settings", "update")),
):
    updated_fields: list[str] = []
    with connection_lock():
        con = get_connection()
        if body.enabled is not None:
            _upsert_settings(con, {"llm_endpoint_whitelist_enabled": body.enabled})
            updated_fields.append("enabled")
        if body.prefixes is not None:
            import json as _json
            _upsert_settings(con, {"llm_endpoint_whitelist": body.prefixes})
            updated_fields.append("prefixes")
    _audit_settings_update(payload, "llm_whitelist", updated_fields)
    return {"data": {"success": True}}


def _is_private_host(hostname: str) -> bool:
    hostname = hostname.strip().strip("[]")
    known_private = {"localhost", "localhost.localdomain", "127.0.0.1", "::1", "0.0.0.0"}
    if hostname.lower() in known_private:
        return True
    try:
        import ipaddress
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return True
    except ValueError:
        pass
    try:
        import socket
        addr = socket.getaddrinfo(hostname, None)
        for family, type_, proto, canonname, sockaddr in addr:
            try:
                ip = ipaddress.ip_address(sockaddr[0])
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    return True
            except ValueError:
                continue
    except Exception:
        pass
    return False


def _validate_llm_endpoint_url(endpoint: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(endpoint)
    if not parsed.scheme:
        parsed = urlparse(f"https://{endpoint}")
    hostname = parsed.hostname or ""
    if _is_private_host(hostname):
        import logging
        logging.getLogger(__name__).warning("Blocked SSRF attempt to private host: %s", hostname)
        raise ValueError(f"LLM endpoint cannot point to a private or loopback address: {hostname}")
    return endpoint


def _get_model_list_timeout() -> float:
    with connection_lock():
        con = get_connection()
        row = con.execute("SELECT value FROM metadata.settings WHERE key = 'timeout_model_list_s'").fetchone()
    if row:
        try:
            return float(json.loads(row[0]) if isinstance(row[0], str) else row[0])
        except Exception:
            pass
    return 15.0


def _parse_openai_models_endpoint(endpoint: str, api_key: str, *, allow_private: bool = False) -> list[str]:
    import httpx
    if not allow_private:
        _validate_llm_endpoint_url(endpoint)
    base = endpoint.rstrip("/")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    with httpx.Client(timeout=_get_model_list_timeout()) as client:
        resp = client.get(f"{base}/models", headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return [m["id"] for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]

def _parse_ollama_models(endpoint: str) -> list[str]:
    import httpx
    import re
    base = endpoint.rstrip("/")
    ollama_base = re.sub(r"/v1/?$", "", base)
    with httpx.Client(timeout=_get_model_list_timeout()) as client:
        resp = client.get(f"{ollama_base}/api/tags")
        resp.raise_for_status()
        data = resp.json()
    return [m["name"] for m in (data.get("models") or []) if isinstance(m, dict) and m.get("name")]


@router.post("/llm/models", response_model=dict)
def list_llm_models(
    body: LLMModelsRequest,
    payload: dict = Depends(get_current_user),
):
    provider = body.provider
    endpoint = body.endpoint or DEFAULT_ENDPOINTS.get(provider, "")
    is_admin = payload_has_permission(payload, "settings", "update") or payload_has_permission(payload, "admin", "manage")
    with connection_lock():
        api_key = _resolve_llm_api_key(get_connection(), body.api_key)
    try:
        if provider == "anthropic":
            models = ANTHROPIC_MODELS
        elif provider == "github_copilot":
            models = []
        elif provider == "ollama":
            if not is_admin:
                _validate_llm_endpoint_url(endpoint)
            models = _parse_ollama_models(endpoint)
        else:
            models = _parse_openai_models_endpoint(endpoint, api_key, allow_private=is_admin)
        return {"data": {"models": models, "error": None}}
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Failed to list LLM models for provider=%s: %s", provider, exc)
        return {"data": {"models": [], "error": f"Failed to fetch models: {exc}"}}


@router.put("/general", response_model=dict)
def update_general(
    body: GeneralUpdate,
    payload: dict = Depends(require_permission("settings", "update")),
):
    updated_fields: list[str] = []
    for field_name in (
        "language",
        "default_page",
        "telemetry",
        "timezone",
        "date_format",
        "session_timeout",
    ):
        if getattr(body, field_name, None) is not None:
            updated_fields.append(field_name)
    mapping: dict[str, object] = {
        "language": body.language,
        "default_page": body.default_page,
        "telemetry_enabled": body.telemetry,
        "timezone": body.timezone,
        "date_format": body.date_format,
        "session_timeout": body.session_timeout,
    }
    if not any(value is not None for value in mapping.values()):
        raise HTTPException(status_code=400, detail="No fields to update")
    with connection_lock():
        _upsert_settings(get_connection(), mapping)
    _audit_settings_update(payload, "general", updated_fields)
    return {"data": {"success": True}}


@router.get("/app-info", response_model=dict)
def get_app_info(
    payload: dict = Depends(require_permission("settings", "read")),
):
    return {"data": {"version": "1.0.0", "platforms": ["web"]}}


@router.get("/recommendations", response_model=dict)
def get_recommender_settings(
    payload: dict = Depends(require_permission("settings", "read")),
):
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            "SELECT key, value FROM metadata.settings WHERE key LIKE 'recommender_%'"
        ).fetchall()
        result = {}
        for r in rows:
            result[r[0]] = _json_value(r[0], r[1])
    return {"data": result}


@router.put("/recommendations", response_model=dict)
def update_recommender_settings(
    body: RecommenderSettingsUpdate,
    payload: dict = Depends(require_permission("settings", "update")),
):
    mapping = {}
    updated_fields: list[str] = []
    for field_name, setting_key in RECOMMENDER_MAP.items():
        value = getattr(body, field_name, None)
        if value is not None:
            mapping[setting_key] = value
            updated_fields.append(field_name)
    if not mapping:
        raise HTTPException(status_code=400, detail="No fields to update")
    with connection_lock():
        _upsert_settings(get_connection(), mapping)
    _audit_settings_update(payload, "recommendations", updated_fields)
    return {"data": {"success": True}}






LLM_ADVANCED_MAP = {
    "max_retries": "llm_max_retries",
    "retry_base_delay_s": "llm_retry_base_delay_s",
    "retry_max_delay_s": "llm_retry_max_delay_s",
    "http_circuit_enabled": "llm_http_circuit_enabled",
    "http_circuit_failure_threshold": "llm_http_circuit_failure_threshold",
    "http_circuit_open_seconds": "llm_http_circuit_open_seconds",
    "chat_history_limit": "llm_chat_history_limit",
    "general_chat_history_limit": "llm_general_chat_history_limit",
}


@router.get("/llm/advanced", response_model=dict)
def get_llm_advanced(
    payload: dict = Depends(require_permission("settings", "read")),
):
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            "SELECT key, value FROM metadata.settings "
            "WHERE key LIKE 'llm_max_retries' "
            "OR key LIKE 'llm_retry_%' "
            "OR key LIKE 'llm_http_circuit_%' "
            "OR key LIKE 'llm_%chat_history%'"
        ).fetchall()
        db_vals = {r[0]: _json_value(r[0], r[1]) for r in rows}
    result = {}
    for field_name, setting_key in LLM_ADVANCED_MAP.items():
        result[field_name] = db_vals.get(setting_key)
    return {"data": result}


@router.put("/llm/advanced", response_model=dict)
def update_llm_advanced(
    body: LLMAdvancedUpdate,
    payload: dict = Depends(require_permission("settings", "update")),
):
    mapping = {}
    updated_fields: list[str] = []
    for field_name, setting_key in LLM_ADVANCED_MAP.items():
        value = getattr(body, field_name, None)
        if value is not None:
            mapping[setting_key] = value
            updated_fields.append(field_name)
    if not mapping:
        raise HTTPException(status_code=400, detail="No fields to update")
    with connection_lock():
        _upsert_settings(get_connection(), mapping)
    try:
        refresh_llm_http_resilience_settings(force_refresh=True)
    except Exception:
        LOGGER.warning("Failed to refresh LLM HTTP resilience settings", exc_info=True)
    _audit_settings_update(payload, "llm_advanced", updated_fields)
    return {"data": {"success": True}}


ASK_SETTINGS_MAP = {
    "max_sql_rows": "ask_max_sql_rows",
    "default_preview_row_limit": "ask_default_preview_row_limit",
    "min_preview_row_limit": "ask_min_preview_row_limit",
    "max_preview_row_limit": "ask_max_preview_row_limit",
    "max_source_materialization_rows": "ask_max_source_materialization_rows",
    "analysis_cache_max": "ask_analysis_cache_max",
    "analysis_cache_ttl_s": "ask_analysis_cache_ttl_s",
}


@router.get("/ask", response_model=dict)
def get_ask_settings(
    payload: dict = Depends(require_permission("settings", "read")),
):
    with connection_lock():
        con = get_connection()
        rows = con.execute("SELECT key, value FROM metadata.settings WHERE key LIKE 'ask_%' OR key LIKE 'router_%' OR key LIKE 'timeout_%'").fetchall()
        db_vals = {r[0]: _json_value(r[0], r[1]) for r in rows}
    runtime_snapshot: dict[str, object] = {}
    try:
        from services.ask_service import refresh_runtime_router_settings

        runtime_snapshot = refresh_runtime_router_settings(force=False)
    except Exception:
        runtime_snapshot = {}
    result = {}
    ask_fallback = {
        "max_sql_rows": runtime_snapshot.get("MAX_SQL_ROWS"),
        "default_preview_row_limit": runtime_snapshot.get("DEFAULT_PREVIEW_ROW_LIMIT"),
        "min_preview_row_limit": runtime_snapshot.get("MIN_PREVIEW_ROW_LIMIT"),
        "max_preview_row_limit": runtime_snapshot.get("MAX_PREVIEW_ROW_LIMIT"),
        "max_source_materialization_rows": runtime_snapshot.get("MAX_SOURCE_MATERIALIZATION_ROWS"),
        "analysis_cache_max": runtime_snapshot.get("analysis_cache_max"),
        "analysis_cache_ttl_s": runtime_snapshot.get("analysis_cache_ttl_s"),
    }
    router_fallback = runtime_snapshot.get("router_config") if isinstance(runtime_snapshot.get("router_config"), dict) else {}
    for field_name, setting_key in ASK_SETTINGS_MAP.items():
        value = db_vals.get(setting_key)
        if value is None:
            value = ask_fallback.get(field_name)
        result[field_name] = value
    for field_name, setting_key in ROUTER_SETTINGS_MAP.items():
        value = db_vals.get(setting_key)
        if value is None and isinstance(router_fallback, dict):
            value = router_fallback.get(field_name)
        result[field_name] = value
    return {"data": result}


@router.put("/ask", response_model=dict)
def update_ask_settings(
    body: AskSettingsUpdate,
    payload: dict = Depends(require_permission("settings", "update")),
):
    mapping = {}
    updated_fields: list[str] = []
    for field_name, setting_key in ASK_SETTINGS_MAP.items():
        value = getattr(body, field_name, None)
        if value is not None:
            mapping[setting_key] = value
            updated_fields.append(field_name)
    if not mapping:
        raise HTTPException(status_code=400, detail="No fields to update")
    with connection_lock():
        _upsert_settings(get_connection(), mapping)
    _refresh_runtime_router_settings()
    _audit_settings_update(payload, "ask", updated_fields)
    return {"data": {"success": True}}


ROUTER_SETTINGS_MAP = {
    "tier1_max_retries": "router_tier1_max_retries",
    "tier2_max_retries": "router_tier2_max_retries",
    "tier3_max_retries": "router_tier3_max_retries",
    "adaptive_strategy_enabled": "router_adaptive_strategy_enabled",
    "adaptive_strategy_consensus_risk_threshold": "router_adaptive_strategy_consensus_risk_threshold",
    "adaptive_strategy_decompose_risk_threshold": "router_adaptive_strategy_decompose_risk_threshold",
    "adaptive_strategy_min_subquestions_for_decompose": "router_adaptive_strategy_min_subquestions_for_decompose",
    "tier1_max_columns_per_model": "router_tier1_max_columns_per_model",
    "tier2_max_columns_per_model": "router_tier2_max_columns_per_model",
    "tier3_max_columns_per_model": "router_tier3_max_columns_per_model",
    "max_sub_questions": "router_max_sub_questions",
    "max_suggested_questions": "router_max_suggested_questions",
    "metadata_summary_max_models": "router_metadata_summary_max_models",
    "guidance_llm_available": "router_guidance_llm_available",
    "schema_pruning_enabled": "router_schema_pruning_enabled",
    "cross_source_max_workers": "router_cross_source_max_workers",
    "decompose_merge_enabled": "router_decompose_merge_enabled",
    "decompose_merge_circuit_enabled": "router_decompose_merge_circuit_enabled",
    "decompose_merge_failure_threshold": "router_decompose_merge_failure_threshold",
    "decompose_merge_disable_seconds": "router_decompose_merge_disable_seconds",
    "decompose_merge_stage_budget_s": "router_decompose_merge_stage_budget_s",
    "sql_generation_total_budget_s": "router_sql_generation_total_budget_s",
    "sql_generation_timeout_cap_s": "router_sql_generation_timeout_cap_s",
    "sql_generation_timeout_min_s": "router_sql_generation_timeout_min_s",
    "json_reask_timeout_cap_s": "router_json_reask_timeout_cap_s",
    "json_reask_timeout_min_s": "router_json_reask_timeout_min_s",
    "llm_sub_query_timeout_s": "router_llm_sub_query_timeout_s",
    "llm_merge_timeout_s": "router_llm_merge_timeout_s",
    "duckdb_did_you_mean_fix_enabled": "router_duckdb_did_you_mean_fix_enabled",
    "duckdb_did_you_mean_allow_internal_tables": "router_duckdb_did_you_mean_allow_internal_tables",
    "duckdb_did_you_mean_max_retries": "router_duckdb_did_you_mean_max_retries",
    "external_connection_pool_enabled": "router_external_connection_pool_enabled",
    "external_connection_pool_max_per_key": "router_external_connection_pool_max_per_key",
    "external_connection_pool_idle_seconds": "router_external_connection_pool_idle_seconds",
    "execution_metrics_log_every": "router_execution_metrics_log_every",
    "execution_metrics_log_interval_seconds": "router_execution_metrics_log_interval_seconds",
    "execution_metrics_max_samples": "router_execution_metrics_max_samples",
    "route_observability_window_seconds": "router_route_observability_window_seconds",
    "route_observability_max_events_per_project": "router_route_observability_max_events_per_project",
    "route_observability_persist_enabled": "router_route_observability_persist_enabled",
    "route_observability_persist_interval_seconds": "router_route_observability_persist_interval_seconds",
    "route_observability_persist_event_delta": "router_route_observability_persist_event_delta",
    "route_observability_strategy_trend_max_points": "router_route_observability_strategy_trend_max_points",
    "route_observability_strategy_trend_persist_interval_seconds": "router_route_observability_strategy_trend_persist_interval_seconds",
    "route_observability_strategy_trend_persist_decision_delta": "router_route_observability_strategy_trend_persist_decision_delta",
    "route_alert_repair_timeout_short_circuit_warning_rate": "router_route_alert_repair_timeout_short_circuit_warning_rate",
    "route_alert_repair_timeout_short_circuit_critical_rate": "router_route_alert_repair_timeout_short_circuit_critical_rate",
    "route_alert_repair_timeout_short_circuit_min_warning_events": "router_route_alert_repair_timeout_short_circuit_min_warning_events",
    "route_alert_repair_timeout_short_circuit_min_critical_events": "router_route_alert_repair_timeout_short_circuit_min_critical_events",
    "route_alert_repair_budget_low_short_circuit_warning_rate": "router_route_alert_repair_budget_low_short_circuit_warning_rate",
    "route_alert_repair_budget_low_short_circuit_critical_rate": "router_route_alert_repair_budget_low_short_circuit_critical_rate",
    "route_alert_repair_budget_low_short_circuit_min_warning_events": "router_route_alert_repair_budget_low_short_circuit_min_warning_events",
    "route_alert_repair_budget_low_short_circuit_min_critical_events": "router_route_alert_repair_budget_low_short_circuit_min_critical_events",
    "route_alert_json_reask_warning_rate": "router_route_alert_json_reask_warning_rate",
    "route_alert_json_reask_critical_rate": "router_route_alert_json_reask_critical_rate",
    "route_alert_json_reask_min_warning_decisions": "router_route_alert_json_reask_min_warning_decisions",
    "route_alert_json_reask_min_critical_decisions": "router_route_alert_json_reask_min_critical_decisions",
    "route_alert_decompose_cancelled_warning_rate": "router_route_alert_decompose_cancelled_warning_rate",
    "route_alert_decompose_cancelled_critical_rate": "router_route_alert_decompose_cancelled_critical_rate",
    "route_alert_decompose_cancelled_min_warning_events": "router_route_alert_decompose_cancelled_min_warning_events",
    "route_alert_decompose_cancelled_min_critical_events": "router_route_alert_decompose_cancelled_min_critical_events",
    "sql_route_v2_enabled": "router_sql_route_v2_enabled",
    "sql_route_allowlist_projects": "router_sql_route_allowlist_projects",
    "sql_route_shadow_mode": "router_sql_route_shadow_mode",
    "sql_route_event_persist_enabled": "router_sql_route_event_persist_enabled",
    "model_ref_case_sensitive": "router_model_ref_case_sensitive",
    "sql_route_profile_id": "router_sql_route_profile_id",
    "sql_route_profile_version": "router_sql_route_profile_version",
    "sql_route_strict_json_probe_enabled": "router_sql_route_strict_json_probe_enabled",
    "request_timeout_ms": "timeout_request_ms",
    "llm_connect_timeout_s": "timeout_llm_connect_s",
    "llm_read_timeout_s": "timeout_llm_read_s",
    "llm_write_timeout_s": "timeout_llm_write_s",
    "llm_pool_timeout_s": "timeout_llm_pool_s",
    "db_connect_timeout_s": "timeout_db_connect_s",
    "model_list_timeout_s": "timeout_model_list_s",
}


@router.get("/router", response_model=dict)
def get_router_settings(
    payload: dict = Depends(require_permission("settings", "read")),
):
    with connection_lock():
        con = get_connection()
        rows = con.execute("SELECT key, value FROM metadata.settings WHERE key LIKE 'router_%' OR key LIKE 'timeout_%'").fetchall()
        db_vals = {r[0]: _json_value(r[0], r[1]) for r in rows}
    runtime_router_config: dict[str, object] = {}
    try:
        from services.ask_service import refresh_runtime_router_settings

        runtime_snapshot = refresh_runtime_router_settings(force=False)
        if isinstance(runtime_snapshot.get("router_config"), dict):
            runtime_router_config = runtime_snapshot["router_config"]
    except Exception:
        runtime_router_config = {}
    result = {}
    for field_name, setting_key in ROUTER_SETTINGS_MAP.items():
        value = db_vals.get(setting_key)
        if value is None:
            value = runtime_router_config.get(field_name)
        result[field_name] = value
    return {"data": result}


@router.put("/router", response_model=dict)
def update_router_settings(
    body: RouterSettingsUpdate,
    payload: dict = Depends(require_permission("settings", "update")),
):
    mapping = {}
    updated_fields: list[str] = []
    for field_name, setting_key in ROUTER_SETTINGS_MAP.items():
        value = getattr(body, field_name, None)
        if value is not None:
            mapping[setting_key] = value
            updated_fields.append(field_name)
    if not mapping:
        raise HTTPException(status_code=400, detail="No fields to update")
    with connection_lock():
        _upsert_settings(get_connection(), mapping)
    _refresh_runtime_router_settings()
    _audit_settings_update(payload, "router", updated_fields)
    return {"data": {"success": True}}


@router.post("/router/reload", response_model=dict)
def reload_router_runtime_settings(
    payload: dict = Depends(require_permission("settings", "update")),
):
    try:
        snapshot = _force_refresh_runtime_router_settings()
    except Exception as exc:
        LOGGER.warning("Manual runtime settings refresh failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reload runtime router settings")
    log_audit(
        _payload_user_id(payload),
        "SETTINGS_ROUTER_RELOAD",
        "settings",
        "router",
        "reload",
        {"runtime_keys": sorted(snapshot.keys())},
    )
    return {"data": {"success": True, "runtime": snapshot}}


SECURITY_MAP = {
    "sql_forbidden_keywords": "security_sql_forbidden_keywords",
    "forbidden_duckdb_functions": "security_forbidden_duckdb_functions",
    "allowed_operators": "security_allowed_operators",
    "allowed_access_types": "security_allowed_access_types",
    "rate_limit_window_s": "security_rate_limit_window_s",
    "rate_limit_max": "security_rate_limit_max",
    "rate_limit_max_entries": "security_rate_limit_max_entries",
    "ws_ticket_ttl_s": "security_ws_ticket_ttl_s",
    "jwt_expiry_hours": "security_jwt_expiry_hours",
    "sso_state_ttl_s": "security_sso_state_ttl_s",
    "oidc_cache_ttl_s": "security_oidc_cache_ttl_s",
    "max_session_days": "security_max_session_days",
}


@router.get("/security", response_model=dict)
def get_security_settings(
    payload: dict = Depends(require_permission("settings", "read")),
):
    with connection_lock():
        con = get_connection()
        rows = con.execute("SELECT key, value FROM metadata.settings WHERE key LIKE 'security_%'").fetchall()
        db_vals = {r[0]: _json_value(r[0], r[1]) for r in rows}
    _LIST_KEYS = {"security_sql_forbidden_keywords", "security_forbidden_duckdb_functions", "security_allowed_operators", "security_allowed_access_types"}
    result = {}
    for field_name, setting_key in SECURITY_MAP.items():
        val = db_vals.get(setting_key)
        if setting_key in _LIST_KEYS:
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except Exception:
                    val = [val]
            elif val is None:
                val = []
            result[field_name] = val
        else:
            result[field_name] = val
    return {"data": result}


@router.put("/security", response_model=dict)
def update_security_settings(
    body: SecuritySettingsUpdate,
    payload: dict = Depends(require_permission("settings", "update")),
):
    mapping = {}
    updated_fields: list[str] = []
    for field_name, setting_key in SECURITY_MAP.items():
        value = getattr(body, field_name, None)
        if value is not None:
            mapping[setting_key] = value
            updated_fields.append(field_name)
    if not mapping:
        raise HTTPException(status_code=400, detail="No fields to update")
    with connection_lock():
        _upsert_settings(get_connection(), mapping)
    _audit_settings_update(payload, "security", updated_fields)
    return {"data": {"success": True}}
