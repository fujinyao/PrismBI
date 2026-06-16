from __future__ import annotations

import asyncio
import os
import threading
import time

_STEP_KEYS = ["understand", "retrieve", "organize", "execute", "answer"]


def _coerce_step_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else int(default)
    except Exception:
        value = int(default)
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


_STEP_DETAIL_MAX_CHARS = _coerce_step_int_env(
    "PRISMBI_ASK_STREAM_STEP_DETAIL_MAX_CHARS",
    180,
    0,
    4000,
)


def _normalize_step_detail(detail: str | None, max_chars: int = _STEP_DETAIL_MAX_CHARS) -> str | None:
    """Truncate detail to max_chars. Returns None for empty/None detail (step skipped)."""
    text = str(detail or "").strip()
    if not text:
        return None
    if int(max_chars) <= 0:
        return text
    limit = max(4, int(max_chars))
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _try_put_nowait(queue: asyncio.Queue, item: dict) -> None:
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        import logging
        logging.getLogger(__name__).warning("Step progress queue full (%d), event dropped", queue.maxsize)


class StepProgress:
    def __init__(self, loop: asyncio.AbstractEventLoop, step_queue: asyncio.Queue):
        self._loop = loop
        self._queue = step_queue
        self._current_step = -1
        self._pending_callbacks = 0
        self._pending_lock = threading.Lock()

    def _mark_pending(self) -> None:
        with self._pending_lock:
            self._pending_callbacks += 1

    def _mark_completed(self) -> None:
        with self._pending_lock:
            self._pending_callbacks = max(0, self._pending_callbacks - 1)

    def _enqueue_step(self, payload: dict) -> None:
        try:
            _try_put_nowait(self._queue, payload)
        finally:
            self._mark_completed()

    def __call__(self, step_key: str, detail: str | None = None) -> None:
        idx = _STEP_KEYS.index(step_key) if step_key in _STEP_KEYS else len(_STEP_KEYS)
        if idx <= self._current_step:
            return
        self._current_step = idx
        normalized_detail = _normalize_step_detail(detail)
        if normalized_detail is None:
            return
        payload = {"key": step_key, "detail": normalized_detail}
        self._mark_pending()
        try:
            self._loop.call_soon_threadsafe(
                self._enqueue_step,
                payload,
            )
        except RuntimeError:
            self._mark_completed()

    async def flush_async(self, timeout: float = 0.2) -> None:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            with self._pending_lock:
                pending = self._pending_callbacks
            if pending <= 0:
                return
            if time.monotonic() >= deadline:
                return
            await asyncio.sleep(0)
