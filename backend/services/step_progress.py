from __future__ import annotations

import asyncio

_STEP_KEYS = ["understand", "retrieve", "organize", "execute", "answer"]


class StepProgress:
    def __init__(self, loop: asyncio.AbstractEventLoop, step_queue: asyncio.Queue):
        self._loop = loop
        self._queue = step_queue
        self._current_step = -1

    def __call__(self, step_key: str, detail: str | None = None) -> None:
        idx = _STEP_KEYS.index(step_key) if step_key in _STEP_KEYS else len(_STEP_KEYS)
        if idx <= self._current_step:
            return
        self._current_step = idx
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, {"key": step_key, "detail": detail})
        except RuntimeError:
            pass