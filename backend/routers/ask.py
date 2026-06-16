from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from models.schemas import AskRequest
from routers.auth import get_current_user, payload_has_permission, get_payload_from_token
from services.ask_idempotency import acquire_ask_idempotency
from services.ask_service import AskCancelledError, ask_question as run_ask_question, get_thread_project_id, get_user_default_project_id
from services.step_progress import StepProgress

LOGGER = logging.getLogger(__name__)


def _coerce_stream_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
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


def _stream_env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


_STREAM_MIN_CHARS = _coerce_stream_int_env("PRISMBI_ASK_STREAM_MIN_CHARS", 48, 20, 400)
_STREAM_MAX_CHARS = _coerce_stream_int_env("PRISMBI_ASK_STREAM_MAX_CHARS", 80, 20, 800)
if _STREAM_MAX_CHARS < _STREAM_MIN_CHARS:
    _STREAM_MAX_CHARS = _STREAM_MIN_CHARS
_STREAM_FLUSH_MS = _coerce_stream_int_env("PRISMBI_ASK_STREAM_FLUSH_MS", 40, 0, 2000)
_STREAM_FLUSH_SECONDS = float(_STREAM_FLUSH_MS) / 1000.0
_STREAM_RUNNING_HEARTBEAT_MS = _coerce_stream_int_env(
    "PRISMBI_ASK_STREAM_RUNNING_HEARTBEAT_MS",
    5000,
    0,
    60000,
)
_STREAM_RUNNING_HEARTBEAT_SECONDS = float(_STREAM_RUNNING_HEARTBEAT_MS) / 1000.0
_STREAM_RUNNING_HEARTBEAT_LONG_MS = _coerce_stream_int_env(
    "PRISMBI_ASK_STREAM_RUNNING_HEARTBEAT_LONG_MS",
    15000,
    5000,
    120000,
)
_STREAM_RUNNING_HEARTBEAT_LONG_SECONDS = float(_STREAM_RUNNING_HEARTBEAT_LONG_MS) / 1000.0
_STREAM_RUNNING_HEARTBEAT_LONG_AFTER_MS = _coerce_stream_int_env(
    "PRISMBI_ASK_STREAM_RUNNING_HEARTBEAT_LONG_AFTER_MS",
    120000,
    30000,
    600000,
)
_STREAM_RUNNING_HEARTBEAT_LONG_AFTER_SECONDS = float(_STREAM_RUNNING_HEARTBEAT_LONG_AFTER_MS) / 1000.0

def _stream_heartbeat_interval(elapsed: float) -> float:
    if _STREAM_RUNNING_HEARTBEAT_LONG_AFTER_SECONDS > 0:
        if elapsed > _STREAM_RUNNING_HEARTBEAT_LONG_AFTER_SECONDS + 270:
            return _STREAM_RUNNING_HEARTBEAT_LONG_SECONDS * 3  # 45s after 390s
        if elapsed > _STREAM_RUNNING_HEARTBEAT_LONG_AFTER_SECONDS + 150:
            return _STREAM_RUNNING_HEARTBEAT_LONG_SECONDS * 2  # 30s after 270s
        if elapsed > _STREAM_RUNNING_HEARTBEAT_LONG_AFTER_SECONDS:
            return _STREAM_RUNNING_HEARTBEAT_LONG_SECONDS  # 15s after 120s
    return _STREAM_RUNNING_HEARTBEAT_SECONDS  # 5s initially

_STREAM_COMPACT_RESULT = _stream_env_flag("PRISMBI_ASK_STREAM_COMPACT_RESULT", default=True)
_CHUNK_BOUNDARY_CHARS = frozenset(
    {
        " ",
        "\n",
        "\t",
        ",",
        ".",
        ";",
        ":",
        "!",
        "?",
        ")",
        "]",
        "}",
        "\uFF0C",
        "\u3002",
        "\uFF1B",
        "\uFF1A",
        "\uFF01",
        "\uFF1F",
        "\u3001",
    }
)


def _chunk_text(text: str, min_chars: int = _STREAM_MIN_CHARS, max_chars: int = _STREAM_MAX_CHARS) -> list[str]:
    if not text:
        return []
    normalized_min = max(1, int(min_chars))
    normalized_max = max(normalized_min, int(max_chars))
    chunks: list[str] = []
    buffer: list[str] = []
    for ch in text:
        buffer.append(ch)
        length = len(buffer)
        should_flush = length >= normalized_max
        if not should_flush and length >= normalized_min and ch in _CHUNK_BOUNDARY_CHARS:
            should_flush = True
        if should_flush:
            chunks.append("".join(buffer))
            buffer = []
    if buffer:
        chunks.append("".join(buffer))
    return chunks


def _stream_result_payload(result: dict, *, temporary: bool) -> dict:
    payload = result if isinstance(result, dict) else {}
    if not _STREAM_COMPACT_RESULT or temporary:
        return payload

    response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
    compact_payload = {
        "thread_id": payload.get("thread_id"),
        "summary": payload.get("summary", "") or "",
        "sql": payload.get("sql", "") or "",
        "response": None,
        "compact_result": True,
    }
    if response.get("id") is not None:
        compact_payload["response_id"] = response["id"]
    if response.get("created_at") is not None:
        compact_payload["response_created_at"] = response["created_at"]
    return compact_payload

router = APIRouter()
_bearer = HTTPBearer(auto_error=False)


async def _get_sse_user(body: AskRequest, credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer), token: Optional[str] = Query(None)) -> dict:
    if credentials:
        try:
            return get_payload_from_token(credentials.credentials)
        except HTTPException:
            pass
    if token:
        return get_payload_from_token(token)
    raise HTTPException(status_code=401, detail="Authentication required")


def _ask_project_id(body: AskRequest, user_id: int) -> int:
    if body.temporary:
        return 0
    project_id = get_thread_project_id(body.thread_id, user_id) if body.thread_id else get_user_default_project_id(user_id)
    if body.thread_id and project_id is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    if not project_id:
        raise HTTPException(status_code=400, detail="No active project. Use temporary ask for empty-project chat.")
    return project_id


def _require_ask_permission(body: AskRequest, payload: dict) -> None:
    project_id = _ask_project_id(body, int(payload["sub"]))
    if project_id is not None and project_id != 0 and not payload_has_permission(payload, "models", "read", project_id):
        raise HTTPException(status_code=403, detail="Permission denied")


def _run_ask_owner(body: AskRequest, payload: dict, progress_cb: StepProgress | None = None, *, cancel_event=None):
    return run_ask_question(
        body.question,
        int(payload["sub"]),
        body.thread_id,
        body.previous_questions,
        body.previous_answers,
        body.language,
        body.preview_row_limit,
        bool(body.temporary),
        progress_cb=progress_cb,
        cancel_event=cancel_event,
    )


@router.post("", response_model=dict)
def ask_question(body: AskRequest, payload: dict = Depends(get_current_user)):
    _require_ask_permission(body, payload)
    request_id = body.client_request_id or f"http-{uuid.uuid4().hex[:12]}"
    client_request_id = body.client_request_id or request_id
    LOGGER.info(
        "Ask transport start transport=http request_id=%s thread_id=%s client_request_id=%s",
        request_id,
        body.thread_id,
        client_request_id,
    )
    handle = acquire_ask_idempotency(body.thread_id, client_request_id)
    try:
        LOGGER.info(
            "Ask HTTP idempotency request_id=%s thread_id=%s client_request_id=%s role=%s enabled=%s",
            request_id,
            body.thread_id,
            client_request_id,
            "owner" if handle.is_owner else "follower",
            handle.enabled,
        )
        if handle.is_owner:
            try:
                data = _run_ask_owner(body, payload, cancel_event=handle.cancel_event)
            except Exception as exc:
                handle.complete_error(exc)
                raise
            handle.complete_success(data)
            LOGGER.info(
                "Ask transport completed transport=http request_id=%s thread_id=%s client_request_id=%s",
                request_id,
                body.thread_id,
                client_request_id,
            )
        else:
            data = handle.wait_result()
            LOGGER.info(
                "Ask HTTP follower replay request_id=%s thread_id=%s client_request_id=%s",
                request_id,
                body.thread_id,
                client_request_id,
            )
    except AskCancelledError as exc:
        LOGGER.warning(
            "Ask HTTP cancelled request_id=%s thread_id=%s client_request_id=%s detail=%s",
            request_id,
            body.thread_id,
            client_request_id,
            str(exc),
        )
        raise HTTPException(status_code=409, detail=str(exc) or "Ask request cancelled") from exc
    except ValueError as exc:
        LOGGER.warning(
            "Ask HTTP validation error request_id=%s thread_id=%s client_request_id=%s detail=%s",
            request_id,
            body.thread_id,
            client_request_id,
            str(exc),
        )
        detail = str(exc)
        if "Ask request cancelled" in detail:
            raise HTTPException(status_code=409, detail=detail) from exc
        status_code = 404 if "Thread not found" in detail else 400
        if status_code == 404:
            raise HTTPException(status_code=404, detail="Thread not found") from exc
        raise HTTPException(status_code=400, detail="Invalid request") from exc
    except Exception:
        LOGGER.exception(
            "Ask HTTP failed request_id=%s thread_id=%s client_request_id=%s",
            request_id,
            body.thread_id,
            client_request_id,
        )
        raise
    finally:
        handle.release(disconnected=False)
        LOGGER.info(
            "Ask HTTP released request_id=%s thread_id=%s client_request_id=%s",
            request_id,
            body.thread_id,
            client_request_id,
        )
    return {"data": data}


@router.post("/stream")
async def ask_stream_sse(body: AskRequest, payload: dict = Depends(_get_sse_user)):
    _require_ask_permission(body, payload)
    request_id = body.client_request_id or f"sse-{uuid.uuid4().hex[:12]}"
    client_request_id = body.client_request_id or request_id

    async def event_generator():
        handle = acquire_ask_idempotency(body.thread_id, client_request_id)
        disconnected = False
        future: asyncio.Future | None = None
        ask_started_at = time.monotonic()
        seq = 0

        def _sse_frame(payload: dict) -> str:
            nonlocal seq
            seq += 1
            enriched = dict(payload)
            enriched["request_id"] = request_id
            enriched["seq"] = seq
            enriched["ts"] = int(time.time() * 1000)
            enriched["elapsed_ms"] = int(max(0.0, (time.monotonic() - ask_started_at) * 1000.0))
            return f"data: {json.dumps(enriched)}\n\n"

        try:
            LOGGER.info(
                "Ask transport start transport=sse request_id=%s thread_id=%s client_request_id=%s role=%s enabled=%s",
                request_id,
                body.thread_id,
                client_request_id,
                "owner" if handle.is_owner else "follower",
                handle.enabled,
            )
            step_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
            yield _sse_frame({"type": "delta", "content_type": "state", "content": "running"})
            last_state_emit = time.monotonic()

            loop = asyncio.get_running_loop()
            progress_cb = StepProgress(loop, step_queue)

            def _run_owner_ask():
                try:
                    result = _run_ask_owner(body, payload, progress_cb, cancel_event=handle.cancel_event)
                except Exception as exc:
                    handle.complete_error(exc)
                    raise
                handle.complete_success(result)
                return result

            if handle.is_owner:
                future = loop.run_in_executor(None, _run_owner_ask)
            else:
                future = loop.run_in_executor(None, handle.wait_result)

            try:
                while not future.done():
                    try:
                        step = await asyncio.wait_for(step_queue.get(), timeout=0.2)
                    except asyncio.TimeoutError:
                        now = time.monotonic()
                        heartbeat_interval = _stream_heartbeat_interval(now - ask_started_at)
                        if heartbeat_interval > 0 and now - last_state_emit >= heartbeat_interval:
                            yield _sse_frame({"type": "delta", "content_type": "state", "content": "running"})
                            last_state_emit = now
                        continue
                    if step is None:
                        break
                    yield _sse_frame(
                        {
                            "type": "delta",
                            "content_type": "step",
                            "content": json.dumps(step),
                        }
                    )

                data = await future
                await progress_cb.flush_async(timeout=0.25)
            except asyncio.CancelledError:
                disconnected = True
                if future is not None and not future.done():
                    future.cancel()
                LOGGER.warning(
                    "Ask transport cancelled transport=sse request_id=%s thread_id=%s client_request_id=%s",
                    request_id,
                    body.thread_id,
                    client_request_id,
                )
                raise
            except AskCancelledError as exc:
                LOGGER.warning(
                    "Ask transport cancelled transport=sse request_id=%s thread_id=%s client_request_id=%s detail=%s",
                    request_id,
                    body.thread_id,
                    client_request_id,
                    str(exc),
                )
                yield _sse_frame({"type": "error", "message": str(exc) or "Ask request cancelled"})
                return
            except ValueError as exc:
                detail = str(exc)
                if "Ask request cancelled" in detail:
                    LOGGER.warning(
                        "Ask transport cancelled transport=sse request_id=%s thread_id=%s client_request_id=%s detail=%s",
                        request_id,
                        body.thread_id,
                        client_request_id,
                        detail,
                    )
                    yield _sse_frame({"type": "error", "message": detail})
                    return
                LOGGER.warning(
                    "Ask transport validation_error transport=sse request_id=%s thread_id=%s client_request_id=%s",
                    request_id,
                    body.thread_id,
                    client_request_id,
                )
                yield _sse_frame({"type": "error", "message": "Invalid request parameters"})
                return
            except Exception:
                LOGGER.exception(
                    "Ask transport failed transport=sse request_id=%s thread_id=%s client_request_id=%s",
                    request_id,
                    body.thread_id,
                    client_request_id,
                )
                yield _sse_frame({"type": "error", "message": "An internal error occurred during processing"})
                return

            while not step_queue.empty():
                try:
                    step = step_queue.get_nowait()
                    if step is None:
                        break
                    yield _sse_frame(
                        {
                            "type": "delta",
                            "content_type": "step",
                            "content": json.dumps(step),
                        }
                    )
                except asyncio.QueueEmpty:
                    break

            answer = data.get("summary", "") or ""
            result_payload = _stream_result_payload(data, temporary=bool(body.temporary))

            if answer:
                chunks = _chunk_text(answer)
                for i, chunk in enumerate(chunks):
                    yield _sse_frame({"type": "delta", "content_type": "text", "content": chunk})
                    if i < len(chunks) - 1 and _STREAM_FLUSH_SECONDS > 0:
                        await asyncio.sleep(_STREAM_FLUSH_SECONDS)

            yield _sse_frame(
                {
                    "type": "result",
                    "data": result_payload,
                }
            )
            LOGGER.info(
                "Ask transport completed transport=sse request_id=%s thread_id=%s client_request_id=%s compact=%s",
                request_id,
                body.thread_id,
                client_request_id,
                bool(result_payload.get("compact_result")),
            )
        finally:
            handle.release(disconnected=disconnected)
            LOGGER.info(
                "Ask SSE released request_id=%s thread_id=%s client_request_id=%s disconnected=%s",
                request_id,
                body.thread_id,
                client_request_id,
                disconnected,
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
