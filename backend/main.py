from __future__ import annotations

import os
import json
import time
import uuid
import logging
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from db import close_connection, connection_lock, get_connection, init_db


LOGGER = logging.getLogger(__name__)


SENSITIVE_LOG_KEYS = {
    "password",
    "passwd",
    "pwd",
    "new_password",
    "old_password",
    "api_key",
    "apikey",
    "access_key",
    "token",
    "ticket",
    "ws_ticket",
    "refresh_token",
    "secret",
    "client_secret",
    "private_key",
    "connection_info",
    "properties",
    "properties_encrypted",
    "authorization",
}


def _redact_for_audit(value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if any(part in str(key).lower() for part in SENSITIVE_LOG_KEYS) else _redact_for_audit(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_for_audit(item) for item in value]
    return value


def _redact_query_string(query: str) -> str:
    raw = str(query or "").strip()
    if not raw:
        return ""
    try:
        pairs = parse_qsl(raw, keep_blank_values=True)
    except Exception:
        return raw
    if not pairs:
        return ""
    redacted_pairs: list[tuple[str, str]] = []
    for key, value in pairs:
        lowered = str(key or "").lower()
        if any(part in lowered for part in SENSITIVE_LOG_KEYS):
            redacted_pairs.append((key, "[REDACTED]"))
        else:
            redacted_pairs.append((key, value))
    return urlencode(redacted_pairs, doseq=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        from services.ask_service import refresh_runtime_router_settings

        refresh_runtime_router_settings(force=True)
    except Exception:
        LOGGER.warning("Failed to refresh ask/router runtime settings during startup", exc_info=True)
    try:
        yield
    finally:
        close_connection()


app = FastAPI(
    title="PrismBI API",
    version="1.0.0",
    lifespan=lifespan,
)

cors_origins = [
    origin.strip()
    for origin in os.getenv("PRISMBI_CORS_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def api_history_middleware(request: Request, call_next):
    from services.backup_service import is_restore_in_progress
    if is_restore_in_progress() and request.url.path.startswith("/api") and not request.url.path.startswith("/api/auth"):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={"detail": "Server is restoring a backup. Please try again in a moment."})
    start = time.perf_counter()
    request_body = None
    if request.url.path.startswith("/api") and request.method not in {"GET", "HEAD", "OPTIONS"}:
        try:
            raw = await request.body()
            request_body = json.loads(raw.decode("utf-8")) if raw else None
            request_body = _redact_for_audit(request_body)
        except Exception:
            request_body = None
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    duration_ms = round((time.perf_counter() - start) * 1000)
    if request.url.path.startswith("/api") and not request.url.path.startswith("/api/settings"):
        try:
            with connection_lock():
                con = get_connection()
                project_id = request.query_params.get("project_id") or request.query_params.get("projectId")
                if project_id is None and isinstance(request_body, dict):
                    project_id = request_body.get("project_id") or request_body.get("projectId")
                normalized_project_id = int(project_id) if project_id is not None else None
                if normalized_project_id is not None and normalized_project_id <= 0:
                    normalized_project_id = None
                con.execute(
                    "INSERT INTO metadata.api_history (id, project_id, api_type, thread_id, headers, request_payload, response_payload, status_code, duration_ms) "
                    "VALUES (?, ?, ?, ?, ?::JSON, ?::JSON, ?::JSON, ?, ?)",
                    [
                        str(uuid.uuid4()),
                        normalized_project_id,
                        request.method,
                        request_body.get("thread_id") if isinstance(request_body, dict) else None,
                        json.dumps({"path": request.url.path, "query": _redact_query_string(str(request.url.query)), "user_agent": request.headers.get("user-agent")}),
                        json.dumps(request_body),
                        json.dumps({"status": response.status_code}),
                        response.status_code,
                        duration_ms,
                    ],
                )
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).warning("API history write failed: %s", exc)
    return response

# ── Register routers ─────────────────────────────────────────────────
from routers import (
    admin,
    api_history,
    ask,
    auth,
    dashboards,
    datasources,
    exports,
    knowledge,
    modeling,
    profile,
    projects,
    query,
    recommendations,
    settings,
    threads,
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(api_history.router, prefix="/api/api-history", tags=["api-history"])
app.include_router(ask.router, prefix="/api/ask", tags=["ask"])
app.include_router(profile.router, prefix="/api/profile", tags=["profile"])
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(threads.router, prefix="/api", tags=["threads"])
app.include_router(query.router, prefix="/api/query", tags=["query"])
app.include_router(modeling.router, prefix="/api/modeling", tags=["modeling"])
app.include_router(
    dashboards.router, prefix="/api/dashboards", tags=["dashboards"]
)
app.include_router(knowledge.router, prefix="/api/knowledge", tags=["knowledge"])
app.include_router(
    recommendations.router, prefix="/api/recommendations", tags=["recommendations"]
)
app.include_router(datasources.router, prefix="/api", tags=["datasources"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(exports.router, prefix="/api/exports", tags=["exports"])

from routers import ws

app.include_router(ws.router, prefix="/ws", tags=["websocket"])


@app.get("/health")
def health():
    try:
        with connection_lock():
            get_connection().execute("SELECT 1").fetchone()
    except Exception:
        from fastapi.responses import JSONResponse
        return JSONResponse({"status": "error", "detail": "database unavailable"}, status_code=503)
    return {"status": "ok"}
