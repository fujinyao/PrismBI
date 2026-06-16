from __future__ import annotations

import hashlib
import json
import logging
import socket
import threading
import time
from typing import Any

LOGGER = logging.getLogger(__name__)


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
            LOGGER.debug("Ignored error closing pooled connection", exc_info=True)


def _external_pool_key(ds_type: str, props: dict[str, Any], driver_tag: str = "") -> str:
    from services.ask_service import _normalize_bool

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


def _probe_connection(conn: Any) -> bool:
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        return True
    except Exception:
        return False


def _acquire_pooled_connection(
    pool_key: str,
    connector: Any,
    health_check: Any,
) -> Any:
    from services.ask_service import ROUTER_CONFIG

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
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(5.0)
            try:
                healthy = health_check(candidate)
            finally:
                socket.setdefaulttimeout(old_timeout)
            if healthy:
                if _probe_connection(candidate):
                    return candidate
        except Exception:
            pass
        _close_connection_quietly(candidate)
    return connector()


def _release_pooled_connection(pool_key: str, conn: Any, healthy: bool = True) -> None:
    from services.ask_service import ROUTER_CONFIG

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
