from __future__ import annotations

import asyncio
import json
import logging
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
        "Ask HTTP start request_id=%s thread_id=%s client_request_id=%s",
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
                "Ask HTTP owner completed request_id=%s thread_id=%s client_request_id=%s",
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
        try:
            LOGGER.info(
                "Ask SSE start request_id=%s thread_id=%s client_request_id=%s role=%s enabled=%s",
                request_id,
                body.thread_id,
                client_request_id,
                "owner" if handle.is_owner else "follower",
                handle.enabled,
            )
            step_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
            yield f"data: {json.dumps({'type': 'delta', 'content_type': 'state', 'content': 'running'})}\n\n"
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
                        if now - last_state_emit >= 5.0:
                            yield f"data: {json.dumps({'type': 'delta', 'content_type': 'state', 'content': 'running'})}\n\n"
                            last_state_emit = now
                        continue
                    if step is None:
                        break
                    yield f"data: {json.dumps({'type': 'delta', 'content_type': 'step', 'content': json.dumps(step)})}\n\n"

                data = await future
            except asyncio.CancelledError:
                disconnected = True
                if future is not None:
                    def _consume_background_exception(done_future: asyncio.Future) -> None:
                        try:
                            done_future.result()
                        except Exception:
                            LOGGER.debug(
                                "Ask SSE background future finished after disconnect request_id=%s thread_id=%s client_request_id=%s",
                                request_id,
                                body.thread_id,
                                client_request_id,
                                exc_info=True,
                            )

                    if future.done():
                        _consume_background_exception(future)
                    else:
                        future.add_done_callback(_consume_background_exception)
                LOGGER.warning(
                    "Ask SSE cancelled request_id=%s thread_id=%s client_request_id=%s",
                    request_id,
                    body.thread_id,
                    client_request_id,
                )
                raise
            except AskCancelledError as exc:
                LOGGER.warning(
                    "Ask SSE request cancelled request_id=%s thread_id=%s client_request_id=%s detail=%s",
                    request_id,
                    body.thread_id,
                    client_request_id,
                    str(exc),
                )
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc) or 'Ask request cancelled'})}\n\n"
                return
            except ValueError as exc:
                detail = str(exc)
                if "Ask request cancelled" in detail:
                    LOGGER.warning(
                        "Ask SSE request cancelled via value error request_id=%s thread_id=%s client_request_id=%s detail=%s",
                        request_id,
                        body.thread_id,
                        client_request_id,
                        detail,
                    )
                    yield f"data: {json.dumps({'type': 'error', 'message': detail})}\n\n"
                    return
                LOGGER.warning(
                    "Ask SSE validation error request_id=%s thread_id=%s client_request_id=%s",
                    request_id,
                    body.thread_id,
                    client_request_id,
                )
                yield f"data: {json.dumps({'type': 'error', 'message': 'Invalid request parameters'})}\n\n"
                return
            except Exception:
                LOGGER.exception(
                    "Ask SSE failed request_id=%s thread_id=%s client_request_id=%s",
                    request_id,
                    body.thread_id,
                    client_request_id,
                )
                yield f"data: {json.dumps({'type': 'error', 'message': 'An internal error occurred during processing'})}\n\n"
                return

            while not step_queue.empty():
                try:
                    step = step_queue.get_nowait()
                    if step is None:
                        break
                    yield f"data: {json.dumps({'type': 'delta', 'content_type': 'step', 'content': json.dumps(step)})}\n\n"
                except asyncio.QueueEmpty:
                    break

            answer = data.get("summary", "") or ""
            sql = data.get("sql", "") or ""
            summary = answer
            chunk_size = max(1, len(answer) // max(1, min(20, len(answer) // 8))) if answer else 0

            if answer:
                pos = 0
                while pos < len(answer):
                    end = min(pos + chunk_size, len(answer))
                    chunk = answer[pos:end]
                    yield f"data: {json.dumps({'type': 'delta', 'content_type': 'text', 'content': chunk})}\n\n"
                    pos = end
                    await asyncio.sleep(0.01)

            response_data = data.get("response") or {}
            yield f"data: {json.dumps({'type': 'result', 'data': {'sql': sql, 'summary': summary, 'answer': answer, 'thread_id': data.get('thread_id'), 'response': response_data}})}\n\n"
            LOGGER.info(
                "Ask SSE completed request_id=%s thread_id=%s client_request_id=%s",
                request_id,
                body.thread_id,
                client_request_id,
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
