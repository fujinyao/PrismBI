from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from db import connection_lock, get_connection


LOGGER = logging.getLogger(__name__)
_ROUTE_EVENT_LOCK = threading.Lock()
_ROUTE_EVENT_TABLE_READY = False
_SENSITIVE_KEYS = {
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "api_key",
    "access_key",
    "client_secret",
    "private_key",
    "authorization",
}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key or "").lower()
            if any(marker in key_text for marker in _SENSITIVE_KEYS):
                sanitized[str(key)] = "[REDACTED]"
            else:
                sanitized[str(key)] = _redact(child)
        return sanitized
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


def _ensure_table() -> None:
    global _ROUTE_EVENT_TABLE_READY
    with _ROUTE_EVENT_LOCK:
        if _ROUTE_EVENT_TABLE_READY:
            return
        with connection_lock():
            con = get_connection()
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata.sql_route_events (
                    id INTEGER PRIMARY KEY,
                    event_type VARCHAR,
                    project_id INTEGER,
                    payload JSON,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        _ROUTE_EVENT_TABLE_READY = True


def emit_sql_route_event(
    event_type: str,
    payload: dict[str, Any],
    *,
    project_id: int | None = None,
    persist: bool = True,
) -> None:
    cleaned_payload = _redact(payload)
    event = {
        "event": str(event_type or "unknown_event"),
        "project_id": int(project_id) if project_id is not None else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": cleaned_payload,
    }
    LOGGER.info("sql_route_event=%s", json.dumps(event, ensure_ascii=False, default=str))
    if not persist:
        return
    try:
        _ensure_table()
        with connection_lock():
            con = get_connection()
            next_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.sql_route_events").fetchone()[0]
            con.execute(
                "INSERT INTO metadata.sql_route_events (id, event_type, project_id, payload) VALUES (?, ?, ?, ?::JSON)",
                [
                    int(next_id),
                    str(event["event"]),
                    event["project_id"],
                    json.dumps(event["payload"], ensure_ascii=False, default=str),
                ],
            )
    except Exception:
        LOGGER.debug("Failed to persist sql_route_event", exc_info=True)
