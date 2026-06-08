from __future__ import annotations

import copy
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from db import connection_lock, get_connection

LOGGER = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 120.0
_DB_CACHE_TTL_SECONDS = 600.0
_IN_FLIGHT_STALE_SECONDS = 180.0
_MAX_TRACKED_KEYS = 2048
_LOCK = threading.Lock()
_NOOP_EVENT = threading.Event()


@dataclass
class _AskEntry:
    key: str
    created_at: float
    watchers: int = 0
    done: threading.Event = field(default_factory=threading.Event)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    result: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    error_is_value_error: bool = False
    finished_at: Optional[float] = None


_ENTRIES: dict[str, _AskEntry] = {}


def _db_delete_key(key: str) -> None:
    try:
        with connection_lock():
            con = get_connection()
            con.execute("DELETE FROM metadata.idempotency_keys WHERE key = ?", [key])
    except Exception:
        LOGGER.debug("Failed to delete ask idempotency key=%s", key, exc_info=True)


def _db_upsert_result(key: str, result: dict[str, Any]) -> None:
    try:
        payload = json.dumps(result, ensure_ascii=False, default=str)
        with connection_lock():
            con = get_connection()
            con.execute(
                "INSERT OR REPLACE INTO metadata.idempotency_keys (key, response, created_at) VALUES (?, ?::JSON, CURRENT_TIMESTAMP)",
                [key, payload],
            )
    except Exception:
        LOGGER.debug("Failed to persist ask idempotency key=%s", key, exc_info=True)


def _created_at_is_stale(created_at: Any) -> bool:
    if not isinstance(created_at, datetime):
        return True
    try:
        return (time.time() - created_at.timestamp()) > _DB_CACHE_TTL_SECONDS
    except Exception:
        return True


def _db_load_cached_result(key: str) -> Optional[dict[str, Any]]:
    try:
        with connection_lock():
            con = get_connection()
            row = con.execute(
                "SELECT response, created_at FROM metadata.idempotency_keys WHERE key = ?",
                [key],
            ).fetchone()
            if row is None:
                return None
            cached = row[0]
            created_at = row[1] if len(row) > 1 else None
            if _created_at_is_stale(created_at):
                con.execute("DELETE FROM metadata.idempotency_keys WHERE key = ?", [key])
                return None
    except Exception:
        LOGGER.debug("Failed to load ask idempotency key=%s", key, exc_info=True)
        return None

    if isinstance(cached, str):
        try:
            cached = json.loads(cached)
        except Exception:
            _db_delete_key(key)
            return None
    if isinstance(cached, dict):
        return cached
    _db_delete_key(key)
    return None


class AskIdempotencyHandle:
    def __init__(self, key: Optional[str], entry: Optional[_AskEntry], is_owner: bool):
        self.key = key
        self.is_owner = is_owner
        self._entry = entry
        self._released = False

    @property
    def enabled(self) -> bool:
        return bool(self.key and self._entry is not None)

    @property
    def cancel_event(self) -> threading.Event:
        if self._entry is None:
            return _NOOP_EVENT
        return self._entry.cancel_event

    def wait_result(self) -> dict[str, Any]:
        if self._entry is None:
            raise RuntimeError("Idempotency entry is unavailable")
        if self.key:
            LOGGER.info("Ask idempotency waiting key=%s", self.key)
        self._entry.done.wait()
        if self._entry.error_message:
            if self._entry.error_is_value_error:
                raise ValueError(self._entry.error_message)
            raise RuntimeError(self._entry.error_message)
        if self.key:
            LOGGER.info("Ask idempotency wait completed key=%s", self.key)
        return copy.deepcopy(self._entry.result or {})

    def complete_success(self, result: dict[str, Any]) -> None:
        if self._entry is None:
            return
        with _LOCK:
            if self._entry.done.is_set():
                return
            self._entry.result = copy.deepcopy(result)
            self._entry.error_message = None
            self._entry.error_is_value_error = False
            self._entry.finished_at = time.monotonic()
            self._entry.done.set()
        if self.key:
            LOGGER.info("Ask idempotency complete success key=%s", self.key)
        if self.key:
            _db_upsert_result(self.key, result)

    def complete_error(self, exc: Exception) -> None:
        if self._entry is None:
            return
        with _LOCK:
            if self._entry.done.is_set():
                return
            self._entry.result = None
            self._entry.error_message = str(exc) or exc.__class__.__name__
            self._entry.error_is_value_error = isinstance(exc, ValueError)
            self._entry.finished_at = time.monotonic()
            self._entry.done.set()
        if self.key:
            LOGGER.warning(
                "Ask idempotency complete error key=%s error=%s",
                self.key,
                str(exc) or exc.__class__.__name__,
            )

    def release(self, disconnected: bool = False) -> None:
        if self._released or self._entry is None or self.key is None:
            self._released = True
            return
        self._released = True
        now = time.monotonic()
        with _LOCK:
            entry = _ENTRIES.get(self.key)
            if entry is not self._entry:
                return
            if entry.watchers > 0:
                entry.watchers -= 1
            if disconnected and not entry.done.is_set() and entry.watchers <= 0:
                entry.cancel_event.set()
                LOGGER.warning("Ask idempotency release cancelled in-flight key=%s", self.key)
            if entry.done.is_set() and entry.watchers <= 0:
                if entry.error_message:
                    LOGGER.info("Ask idempotency release evicting error entry key=%s", self.key)
                    _db_delete_key(self.key)
                    _ENTRIES.pop(self.key, None)
                    return
                if entry.finished_at is None:
                    LOGGER.info("Ask idempotency release evicting incomplete entry key=%s", self.key)
                    _ENTRIES.pop(self.key, None)
                    return
                if now - entry.finished_at > _CACHE_TTL_SECONDS:
                    LOGGER.info("Ask idempotency release evicting expired entry key=%s", self.key)
                    _ENTRIES.pop(self.key, None)


def _normalize_key(thread_id: Optional[int], client_request_id: Optional[str]) -> Optional[str]:
    if thread_id is None:
        return None
    token = str(client_request_id or "").strip()
    if not token:
        return None
    if len(token) > 128:
        token = token[:128]
    try:
        thread_num = int(thread_id)
    except (TypeError, ValueError):
        return None
    return f"ask:{thread_num}:{token}"


def _cleanup_entries(now: Optional[float] = None) -> None:
    current = now if now is not None else time.monotonic()
    stale_keys = [
        key
        for key, entry in _ENTRIES.items()
        if entry.done.is_set()
        and entry.watchers <= 0
        and entry.finished_at is not None
        and current - entry.finished_at > _CACHE_TTL_SECONDS
    ]
    for key in stale_keys:
        _ENTRIES.pop(key, None)
    if len(_ENTRIES) <= _MAX_TRACKED_KEYS:
        return
    sortable = sorted(
        _ENTRIES.items(),
        key=lambda item: item[1].finished_at or item[1].created_at,
    )
    trim = max(0, len(sortable) - _MAX_TRACKED_KEYS)
    for key, entry in sortable[:trim]:
        if entry.watchers <= 0:
            _ENTRIES.pop(key, None)


def acquire_ask_idempotency(thread_id: Optional[int], client_request_id: Optional[str]) -> AskIdempotencyHandle:
    key = _normalize_key(thread_id, client_request_id)
    if key is None:
        return AskIdempotencyHandle(None, None, True)

    now = time.monotonic()
    with _LOCK:
        LOGGER.info(
            "Ask idempotency acquire thread_id=%s client_request_id=%s key=%s",
            thread_id,
            client_request_id,
            key,
        )
        _cleanup_entries(now)
        entry = _ENTRIES.get(key)
        if (
            entry is not None
            and not entry.done.is_set()
            and entry.watchers <= 0
            and (now - entry.created_at) > _IN_FLIGHT_STALE_SECONDS
        ):
            LOGGER.warning("Ask idempotency evicting stale in-flight key=%s", key)
            entry.cancel_event.set()
            _ENTRIES.pop(key, None)
            entry = None
        if entry is not None and entry.done.is_set() and entry.error_message and entry.watchers <= 0:
            _ENTRIES.pop(key, None)
            entry = None
        if entry is None:
            cached_result = _db_load_cached_result(key)
            if cached_result is not None:
                entry = _AskEntry(key=key, created_at=now, watchers=0)
                entry.result = cached_result
                entry.finished_at = now
                entry.done.set()
                _ENTRIES[key] = entry
                LOGGER.info("Ask idempotency loaded database replay key=%s", key)
        if entry is None:
            entry = _AskEntry(key=key, created_at=now, watchers=1)
            _ENTRIES[key] = entry
            LOGGER.info("Ask idempotency created owner entry key=%s", key)
            return AskIdempotencyHandle(key, entry, True)
        entry.watchers += 1
        if entry.done.is_set():
            LOGGER.info("Ask idempotency cache replay for key=%s", key)
        else:
            LOGGER.info("Ask idempotency joined in-flight key=%s", key)
        return AskIdempotencyHandle(key, entry, False)
