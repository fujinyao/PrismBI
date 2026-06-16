from __future__ import annotations

import asyncio
import threading

import pytest

from services.step_progress import StepProgress, _normalize_step_detail


def test_normalize_step_detail_truncates_when_limit_exceeded():
    detail = "x" * 20

    normalized = _normalize_step_detail(detail, max_chars=10)

    assert normalized == "xxxxxxx..."
    assert len(normalized) == 10


def test_normalize_step_detail_handles_empty_and_unlimited_values():
    assert _normalize_step_detail("  short detail  ", max_chars=20) == "short detail"
    assert _normalize_step_detail("y" * 30, max_chars=0) == "y" * 30
    assert _normalize_step_detail("", max_chars=20) is None
    assert _normalize_step_detail(None, max_chars=20) is None


@pytest.mark.asyncio
async def test_step_progress_flush_async_waits_for_pending_callbacks():
    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    progress = StepProgress(asyncio.get_running_loop(), queue)

    def emit_steps() -> None:
        progress("understand", "understand")
        progress("organize", "organize")

    thread = threading.Thread(target=emit_steps)
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive()

    await progress.flush_async(timeout=0.5)

    first = await asyncio.wait_for(queue.get(), timeout=0.2)
    second = await asyncio.wait_for(queue.get(), timeout=0.2)
    assert first["key"] == "understand"
    assert second["key"] == "organize"
