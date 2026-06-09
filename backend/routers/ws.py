from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from urllib.parse import unquote

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError
from starlette.websockets import WebSocketState

from models.schemas import AskRequest
from routers.ask import _ask_project_id
from routers.auth import get_payload_from_token, payload_has_permission, consume_ws_ticket
from services.ask_idempotency import acquire_ask_idempotency
from services.ask_service import AskCancelledError, ask_question as run_ask_question
from services.step_progress import StepProgress, _STEP_KEYS

LOGGER = logging.getLogger(__name__)

_MAX_MSG_SIZE = 256 * 1024


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


_STREAM_MIN_CHARS = _coerce_stream_int_env("PRISMBI_ASK_STREAM_MIN_CHARS", 48, 20, 400)
_STREAM_MAX_CHARS = _coerce_stream_int_env("PRISMBI_ASK_STREAM_MAX_CHARS", 80, 20, 800)
if _STREAM_MAX_CHARS < _STREAM_MIN_CHARS:
    _STREAM_MAX_CHARS = _STREAM_MIN_CHARS
_STREAM_FLUSH_MS = _coerce_stream_int_env("PRISMBI_ASK_STREAM_FLUSH_MS", 40, 0, 2000)
_STREAM_FLUSH_SECONDS = float(_STREAM_FLUSH_MS) / 1000.0
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

router = APIRouter()

_WS_TICKET_PROTOCOL_PREFIXES = (
    "prismbi-ticket.",
    "prismbi-ticket:",
    "ticket.",
    "ticket:",
)
_WS_TOKEN_PROTOCOL_PREFIXES = (
    "prismbi-token.",
    "prismbi-token:",
    "token.",
    "token:",
)


def _clean_credential(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _extract_prefixed_protocol_value(candidate: str, prefixes: tuple[str, ...]) -> str | None:
    lowered = candidate.lower()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            raw = _clean_credential(candidate[len(prefix):])
            if not raw:
                return None
            try:
                return _clean_credential(unquote(raw))
            except Exception:
                return raw
    return None


def _extract_subprotocol_auth(websocket: WebSocket) -> tuple[str | None, str | None]:
    raw_header = _clean_credential(websocket.headers.get("sec-websocket-protocol"))
    if not raw_header:
        return None, None
    ticket_value: str | None = None
    token_value: str | None = None
    for item in raw_header.split(","):
        candidate = _clean_credential(item)
        if not candidate:
            continue
        if ticket_value is None:
            ticket_value = _extract_prefixed_protocol_value(candidate, _WS_TICKET_PROTOCOL_PREFIXES)
        if token_value is None:
            token_value = _extract_prefixed_protocol_value(candidate, _WS_TOKEN_PROTOCOL_PREFIXES)
        if ticket_value and token_value:
            break
    return ticket_value, token_value


def _extract_bearer_token(value: str | None) -> str | None:
    raw = _clean_credential(value)
    if not raw:
        return None
    if raw.lower().startswith("bearer "):
        token = raw[7:].strip()
        return token or None
    return raw


def _cookie_value(websocket: WebSocket, *names: str) -> str | None:
    cookies = getattr(websocket, "cookies", {}) or {}
    for name in names:
        value = _clean_credential(cookies.get(name))
        if value:
            return value
    return None


def _resolve_ws_auth_inputs(websocket: WebSocket) -> tuple[str | None, str | None]:
    protocol_ticket, protocol_token = _extract_subprotocol_auth(websocket)
    ticket = (
        _clean_credential(websocket.headers.get("x-ws-ticket"))
        or _clean_credential(websocket.headers.get("x-prismbi-ws-ticket"))
        or protocol_ticket
        or _cookie_value(websocket, "ws_ticket", "ticket")
        or _clean_credential(websocket.query_params.get("ticket"))
    )
    token = (
        _extract_bearer_token(websocket.headers.get("authorization"))
        or _clean_credential(websocket.headers.get("x-access-token"))
        or _clean_credential(websocket.headers.get("x-auth-token"))
        or protocol_token
        or _cookie_value(websocket, "access_token", "token")
        or _clean_credential(websocket.query_params.get("token"))
    )
    return token, ticket


def _ws_payload(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        return get_payload_from_token(token)
    except Exception:
        LOGGER.warning("WebSocket auth failed: invalid token")
        return None


async def _send_json_safe(websocket: WebSocket, data: dict, write_lock: asyncio.Lock) -> None:
    async with write_lock:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json(data)


async def _send_error(websocket: WebSocket, message: str, request_id: str | None = None, write_lock: asyncio.Lock | None = None) -> None:
    payload = {"type": "error", "message": message}
    if request_id:
        payload["request_id"] = request_id
    if write_lock:
        await _send_json_safe(websocket, payload, write_lock)
    else:
        await websocket.send_json(payload)


def _require_ws_ask_permission(body: AskRequest, payload: dict) -> None:
    project_id = _ask_project_id(body, int(payload["sub"]))
    if project_id is not None and project_id != 0 and not payload_has_permission(payload, "models", "read", project_id):
        raise PermissionError("Permission denied")


def _parse_ws_data(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Invalid message: expected a JSON object")
    return raw


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


@router.websocket("/ask")
async def websocket_ask(websocket: WebSocket):
    raw_token, raw_ticket = _resolve_ws_auth_inputs(websocket)
    payload = None
    if raw_ticket:
        payload = consume_ws_ticket(raw_ticket)
    if payload is None and raw_token:
        payload = _ws_payload(raw_token)
    if payload is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")
        return
    await websocket.accept()
    LOGGER.info("WebSocket ask connected user_id=%s", payload.get("sub"))

    write_lock = asyncio.Lock()
    active_ask: dict[str, asyncio.Task] = {}

    async def _send_step_deltas(step_queue: asyncio.Queue, request_id: str) -> None:
        while True:
            try:
                step = await asyncio.wait_for(step_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            if step is None:
                break
            await _send_json_safe(websocket, {
                "type": "delta",
                "content_type": "step",
                "content": json.dumps(step),
                "request_id": request_id or None,
            }, write_lock)

    async def _process_ask(body: AskRequest, request_id: str) -> None:
        client_request_id = body.client_request_id or request_id
        LOGGER.info(
            "Ask WS start request_id=%s thread_id=%s client_request_id=%s",
            request_id,
            body.thread_id,
            client_request_id,
        )
        handle = acquire_ask_idempotency(body.thread_id, client_request_id)
        LOGGER.info(
            "Ask WS idempotency request_id=%s thread_id=%s client_request_id=%s role=%s enabled=%s",
            request_id,
            body.thread_id,
            client_request_id,
            "owner" if handle.is_owner else "follower",
            handle.enabled,
        )
        disconnected = False
        step_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        step_sender = asyncio.create_task(_send_step_deltas(step_queue, request_id))
        progress_cb = StepProgress(asyncio.get_running_loop(), step_queue)

        def _run_owner_ask() -> dict:
            try:
                result = _do_ask(body, payload, progress_cb, handle.cancel_event)
            except Exception as exc:
                handle.complete_error(exc)
                raise
            handle.complete_success(result)
            return result

        try:
            _require_ws_ask_permission(body, payload)

            await _send_json_safe(websocket, {"type": "delta", "content_type": "state", "content": "running", "request_id": request_id or None}, write_lock)

            loop = asyncio.get_running_loop()
            if handle.is_owner:
                result = await loop.run_in_executor(None, _run_owner_ask)
            else:
                result = await loop.run_in_executor(None, handle.wait_result)

            answer = result.get("summary", "") or ""
            sql = result.get("sql", "") or ""

            if answer:
                chunks = _chunk_text(answer)
                for i, chunk in enumerate(chunks):
                    if websocket.client_state != WebSocketState.CONNECTED:
                        break
                    await _send_json_safe(websocket, {"type": "delta", "content_type": "text", "content": chunk, "request_id": request_id or None}, write_lock)
                    if i < len(chunks) - 1 and _STREAM_FLUSH_SECONDS > 0:
                        await asyncio.sleep(_STREAM_FLUSH_SECONDS)

            if sql:
                await _send_json_safe(websocket, {"type": "delta", "content_type": "sql", "content": sql, "request_id": request_id or None}, write_lock)

            await _send_json_safe(websocket, {"type": "result", "data": result, "request_id": request_id or None}, write_lock)
            LOGGER.info(
                "Ask WS completed request_id=%s thread_id=%s client_request_id=%s",
                request_id,
                body.thread_id,
                client_request_id,
            )
        except asyncio.CancelledError:
            disconnected = True
            LOGGER.warning(
                "Ask WS cancelled request_id=%s thread_id=%s client_request_id=%s",
                request_id,
                body.thread_id,
                client_request_id,
            )
            raise
        except Exception as exc:
            if isinstance(exc, AskCancelledError):
                LOGGER.warning(
                    "Ask WS cancelled by backend request_id=%s thread_id=%s client_request_id=%s",
                    request_id,
                    body.thread_id,
                    client_request_id,
                )
                if websocket.client_state == WebSocketState.CONNECTED:
                    await _send_json_safe(websocket, {"type": "error", "message": "Ask request cancelled", "request_id": request_id or None}, write_lock)
            else:
                LOGGER.exception(
                    "Ask WS failed request_id=%s thread_id=%s client_request_id=%s",
                    request_id,
                    body.thread_id,
                    client_request_id,
                )
                if websocket.client_state == WebSocketState.CONNECTED:
                    await _send_json_safe(websocket, {"type": "error", "message": "Ask execution failed", "request_id": request_id or None}, write_lock)
        finally:
            await step_queue.put(None)
            step_sender.cancel()
            try:
                await step_sender
            except asyncio.CancelledError:
                pass
            handle.release(disconnected=disconnected or websocket.client_state != WebSocketState.CONNECTED)
            LOGGER.info(
                "Ask WS released request_id=%s thread_id=%s client_request_id=%s disconnected=%s",
                request_id,
                body.thread_id,
                client_request_id,
                disconnected or websocket.client_state != WebSocketState.CONNECTED,
            )
            active_ask.pop(request_id, None)

    try:
        while True:
            raw = await websocket.receive_json()
            if len(str(raw).encode("utf-8")) > _MAX_MSG_SIZE:
                await _send_error(websocket, "Message too large", write_lock=write_lock)
                continue
            data = raw
            try:
                data = _parse_ws_data(raw)
            except ValueError:
                await _send_error(websocket, "Invalid message: expected a JSON object", write_lock=write_lock)
                continue
            message_type = str(data.get("type") or "ask").lower()
            request_id = str(data.get("request_id") or data.get("id") or str(uuid.uuid4())) if isinstance(data, dict) else str(uuid.uuid4())

            if message_type == "ping":
                await _send_json_safe(websocket, {"type": "pong"}, write_lock)
                continue

            if message_type != "ask":
                await _send_error(websocket, f"Unsupported message type: {message_type or 'unknown'}", request_id or None, write_lock=write_lock)
                continue

            try:
                body = AskRequest(**data)
                if not body.client_request_id:
                    body.client_request_id = request_id
                _require_ws_ask_permission(body, payload)
            except ValidationError:
                await _send_error(websocket, "Invalid request format", request_id or None, write_lock=write_lock)
                continue
            except PermissionError:
                await _send_error(websocket, "Permission denied", request_id or None, write_lock=write_lock)
                continue
            except Exception:
                LOGGER.exception("Unexpected error parsing WebSocket ask request")
                await _send_error(websocket, "Invalid request", request_id or None, write_lock=write_lock)
                continue

            if request_id and request_id in active_ask:
                LOGGER.warning(
                    "Ask WS duplicate request_id while active request_id=%s thread_id=%s client_request_id=%s",
                    request_id,
                    body.thread_id,
                    body.client_request_id,
                )
                await _send_error(websocket, "Previous request still processing", request_id or None, write_lock=write_lock)
                continue

            LOGGER.info(
                "Ask WS accepted request_id=%s thread_id=%s client_request_id=%s",
                request_id,
                body.thread_id,
                body.client_request_id,
            )
            active_ask[request_id] = asyncio.create_task(_process_ask(body, request_id))

    except WebSocketDisconnect:
        LOGGER.info("WebSocket ask disconnected by client")
    finally:
        for task in active_ask.values():
            if not task.done():
                task.cancel()
        LOGGER.info("WebSocket ask cleanup complete")


def _do_ask(body: AskRequest, payload: dict, progress_cb: StepProgress | None = None, cancel_event=None) -> dict:
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
