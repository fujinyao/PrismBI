from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from db import connection_lock, get_connection
from models.schemas import LoginRequest, RegisterRequest, SSOLoginRequest
from services.auth_service import auth_service as auth, is_default_secret
from services.sso_service import _get_sso_config, _validate_redirect_uri, generate_state, store_state, consume_state, get_authorization_url, exchange_code, verify_id_token, map_claims_to_roles, sso_login_or_create

router = APIRouter()
LOGGER = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)

_RATE_LIMIT_WINDOW = 300
_RATE_LIMIT_MAX = 10
_RATE_LIMIT_MAX_ENTRIES = 10000
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_rate_limit_lock = threading.Lock()

_WS_TICKET_TTL = 30
_ws_tickets: dict[str, tuple[dict, float]] = {}
_ws_ticket_lock = threading.Lock()
_ws_ticket_stats = {
    "issued": 0,
    "consumed": 0,
    "expired": 0,
    "invalid": 0,
}
_WS_TICKET_STATS_LOG_EVERY = 25


def _record_ws_ticket_stat(event: str) -> None:
    key = str(event or "").strip().lower()
    if key not in _ws_ticket_stats:
        return
    _ws_ticket_stats[key] = int(_ws_ticket_stats.get(key) or 0) + 1
    issued = int(_ws_ticket_stats.get("issued") or 0)
    if issued <= 0 or issued % _WS_TICKET_STATS_LOG_EVERY != 0:
        return
    consumed = int(_ws_ticket_stats.get("consumed") or 0)
    expired = int(_ws_ticket_stats.get("expired") or 0)
    invalid = int(_ws_ticket_stats.get("invalid") or 0)
    consumed_ratio = round(consumed / issued, 4)
    LOGGER.info(
        "WS ticket stats issued=%d consumed=%d expired=%d invalid=%d consumed_ratio=%.4f",
        issued,
        consumed,
        expired,
        invalid,
        consumed_ratio,
    )


def _check_rate_limit(ip: str) -> None:
    now = time.time()
    with _rate_limit_lock:
        attempts = _rate_limit_store[ip]
        _rate_limit_store[ip] = [t for t in attempts if now - t < _RATE_LIMIT_WINDOW]
        if len(_rate_limit_store[ip]) >= _RATE_LIMIT_MAX:
            raise HTTPException(status_code=429, detail="Too many login attempts. Please try again later.")
        _rate_limit_store[ip].append(now)
        _prune_rate_limit()


def _prune_rate_limit() -> None:
    if len(_rate_limit_store) > _RATE_LIMIT_MAX_ENTRIES:
        now = time.time()
        expired_ips = [ip for ip, times in _rate_limit_store.items() if not times or now - times[-1] > _RATE_LIMIT_WINDOW]
        for ip in expired_ips:
            del _rate_limit_store[ip]


def create_ws_ticket(payload: dict) -> str:
    ticket = secrets.token_urlsafe(32)
    now = time.time()
    with _ws_ticket_lock:
        expired = [k for k, (_, ts) in _ws_tickets.items() if now - ts > _WS_TICKET_TTL]
        for k in expired:
            del _ws_tickets[k]
        _ws_tickets[ticket] = (payload, now)
        _record_ws_ticket_stat("issued")
    return ticket


def consume_ws_ticket(ticket: str) -> dict | None:
    with _ws_ticket_lock:
        entry = _ws_tickets.pop(ticket, None)
        if entry is None:
            _record_ws_ticket_stat("invalid")
            return None
        payload, created_at = entry
        if time.time() - created_at > _WS_TICKET_TTL:
            _record_ws_ticket_stat("expired")
            return None
        _record_ws_ticket_stat("consumed")
    return payload


def _registration_enabled() -> bool:
    return os.getenv("PRISMBI_ENABLE_REGISTRATION", "false").strip().lower() in {"1", "true", "yes", "on"}


def _is_production() -> bool:
    return os.getenv("PRISMBI_ENV", os.getenv("ENV", "")).strip().lower() in {"prod", "production"}


def _validate_frontend_url(url: str) -> None:
    url = url.strip()
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid frontend URL scheme: {parsed.scheme}")
    allowed = os.getenv("PRISMBI_SSO_ALLOWED_ORIGINS", "").strip()
    if allowed:
        origins = [o.strip().rstrip("/") for o in allowed.split(",") if o.strip()]
        if origins:
            origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else url
            if origin not in origins and origin.rstrip("/") not in origins:
                raise ValueError(f"Frontend URL origin '{origin}' not in allowed list")
    elif parsed.scheme and parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid frontend URL scheme: {parsed.scheme}")


def _config_string(config: dict, key: str) -> str:
    return str(config.get(key) or "").strip()


def get_payload_from_token(token: str) -> dict:
    if _is_production() and is_default_secret() and os.getenv("PRISMBI_ALLOW_DEFAULT_SECRET", "").strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=500, detail="JWT_SECRET_KEY must be configured")
    if token.startswith("prismbi_"):
        return _api_token_payload(token)
    payload = auth.decode_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user_id = int(payload.get("sub") or 0)
    session_id = payload.get("sid")
    if not session_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    with connection_lock():
        con = get_connection()
        user = con.execute(
            "SELECT status FROM metadata.users WHERE id = ?",
            [user_id],
        ).fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        if str(user[0]).upper() != "ACTIVE":
            raise HTTPException(status_code=403, detail="User is inactive")
        session = con.execute(
            "SELECT id FROM metadata.sessions "
            "WHERE id = ? AND user_id = ? AND is_revoked = false "
            "AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)",
            [session_id, user_id],
        ).fetchone()
        if not session:
            raise HTTPException(status_code=401, detail="Session revoked or expired")
        con.execute(
            "UPDATE metadata.sessions SET last_active_at = CURRENT_TIMESTAMP WHERE id = ?",
            [session_id],
        )
    return payload


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    return get_payload_from_token(credentials.credentials)


def _parse_scope(value) -> list[str]:
    if value is None:
        return []
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return []


def _api_token_payload(token: str) -> dict:
    token_prefix = token[:12]
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            """
            SELECT t.id, t.user_id, t.token_hash, t.scope, u.username, u.status
            FROM metadata.api_tokens t
            JOIN metadata.users u ON u.id = t.user_id
            WHERE t.token_prefix = ?
              AND t.is_revoked = false
              AND (t.expires_at IS NULL OR t.expires_at > CURRENT_TIMESTAMP)
            ORDER BY t.id
            """,
            [token_prefix],
        ).fetchall()
        for row in rows:
            try:
                verified = auth.verify_password(token, row[2])
            except Exception:
                verified = False
            if not verified:
                continue
            if str(row[5]).upper() != "ACTIVE":
                raise HTTPException(status_code=403, detail="User is inactive")
            con.execute(
                "UPDATE metadata.api_tokens SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
                [row[0]],
            )
            return {
                "sub": str(row[1]),
                "username": row[4],
                "auth_type": "api_token",
                "api_token_id": row[0],
                "api_token_scopes": _parse_scope(row[3]),
            }
    raise HTTPException(status_code=401, detail="Invalid or expired token")


def get_effective_permissions(user_id: int, project_id: Optional[int] = None) -> list[dict]:
    with connection_lock():
        con = get_connection()
        params: list = [user_id]
        if project_id is not None:
            project_filter = " AND (ur.project_id IS NULL OR ur.project_id = ?)"
            params.append(project_id)
        else:
            project_filter = " AND ur.project_id IS NULL"

        rows = con.execute(
            "SELECT DISTINCT p.id, p.resource, p.action, p.description "
            "FROM metadata.permissions p "
            "JOIN metadata.role_permissions rp ON rp.permission_id = p.id "
            "JOIN metadata.user_roles ur ON ur.role_id = rp.role_id "
            "WHERE ur.user_id = ? "
            "AND (ur.expires_at IS NULL OR ur.expires_at > CURRENT_TIMESTAMP)"
            f"{project_filter} "
            "ORDER BY p.resource, p.action",
            params,
        ).fetchall()

        permissions = {
            (row[1], row[2]): {
                "id": row[0],
                "resource": row[1],
                "action": row[2],
                "description": row[3],
            }
            for row in rows
            if row[1] and row[2]
        }

        override_params: list = [user_id]
        if project_id is not None:
            override_filter = " AND (upo.project_id IS NULL OR upo.project_id = ?)"
            override_params.append(project_id)
        else:
            override_filter = " AND upo.project_id IS NULL"

        overrides = con.execute(
            "SELECT p.id, p.resource, p.action, p.description, UPPER(upo.grant_type) "
            "FROM metadata.user_permission_overrides upo "
            "JOIN metadata.permissions p ON p.id = upo.permission_id "
            "WHERE upo.user_id = ? "
            "AND (upo.expires_at IS NULL OR upo.expires_at > CURRENT_TIMESTAMP)"
            f"{override_filter}",
            override_params,
        ).fetchall()

        for row in overrides:
            key = (row[1], row[2])
            if row[4] in {"DENY", "REVOKE"}:
                permissions.pop(key, None)
            elif row[4] in {"ALLOW", "GRANT"}:
                permissions[key] = {
                    "id": row[0],
                    "resource": row[1],
                    "action": row[2],
                    "description": row[3],
                }

        return sorted(permissions.values(), key=lambda p: (p["resource"], p["action"]))


def has_permission(user_id: int, resource: str, action: str, project_id: Optional[int] = None, api_scopes: Optional[list[str]] = None) -> bool:
    permissions = get_effective_permissions(user_id, project_id)
    user_allowed = any(
        (p["resource"] == resource and p["action"] in {action, "manage"})
        or (p["resource"] == "admin" and p["action"] in {action, "manage"})
        for p in permissions
    )
    return user_allowed and _api_scope_allows(api_scopes, resource, action)


def payload_has_permission(payload: dict, resource: str, action: str, project_id: Optional[int] = None) -> bool:
    return has_permission(
        int(payload["sub"]),
        resource,
        action,
        project_id,
        payload.get("api_token_scopes") if payload.get("auth_type") == "api_token" else None,
    )


def _api_scope_allows(scopes: Optional[list[str]], resource: str, action: str) -> bool:
    if scopes is None:
        return True
    if not scopes:
        return False
    normalized = {str(scope).strip().lower().replace(".", ":") for scope in scopes if str(scope).strip()}
    resource = resource.lower()
    action = action.lower()
    return bool(
        "*" in normalized
        or f"{resource}:*" in normalized
        or f"{resource}:manage" in normalized
        or f"{resource}:{action}" in normalized
        or "admin:*" in normalized
        or "admin:manage" in normalized
    )


def require_permission(resource: str, action: str):
    def checker(payload: dict = Depends(get_current_user)):
        user_id = int(payload["sub"])
        with connection_lock():
            if not has_permission(user_id, resource, action, api_scopes=payload.get("api_token_scopes") if payload.get("auth_type") == "api_token" else None):
                raise HTTPException(status_code=403, detail="Permission denied")
        return payload

    return checker


def get_user_roles(user_id: int) -> list[dict]:
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            "SELECT r.id, r.name, r.scope, r.description, r.is_system, ur.project_id, ur.expires_at "
            "FROM metadata.roles r "
            "JOIN metadata.user_roles ur ON ur.role_id = r.id "
            "WHERE ur.user_id = ? "
            "AND (ur.expires_at IS NULL OR ur.expires_at > CURRENT_TIMESTAMP) "
            "ORDER BY r.name",
            [user_id],
        ).fetchall()
        return [
            {
                "id": row[0],
                "name": row[1],
                "scope": row[2],
                "description": row[3],
                "is_system": row[4],
                "project_id": row[5],
                "expires_at": str(row[6]) if row[6] else None,
            }
            for row in rows
        ]


def log_audit(
    user_id: Optional[int],
    event_type: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    action: Optional[str] = None,
    detail: Optional[dict] = None,
    status: str = "SUCCESS",
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    try:
        with connection_lock():
            con = get_connection()
            audit_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.audit_logs").fetchone()[0]
            con.execute(
                "INSERT INTO metadata.audit_logs "
                "(id, user_id, event_type, resource_type, resource_id, action, detail, ip_address, user_agent, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?::JSON, ?, ?, ?)",
                [
                    audit_id,
                    user_id,
                    event_type,
                    resource_type,
                    resource_id,
                    action,
                    json.dumps(detail or {}),
                    ip_address,
                    user_agent,
                    status,
                ],
            )
    except Exception:
        LOGGER.warning("Failed to write audit log entry", exc_info=True)


def _user_payload(row) -> dict:
    user_id = row[0]
    return {
        "id": user_id,
        "username": row[1],
        "display_name": row[2],
        "email": row[3],
        "status": row[4],
        "default_project_id": row[5] if len(row) > 5 else None,
        "last_login_at": str(row[6]) if len(row) > 6 and row[6] else None,
        "created_at": str(row[7]) if len(row) > 7 and row[7] else None,
        "updated_at": str(row[8]) if len(row) > 8 and row[8] else None,
        "roles": get_user_roles(user_id),
        "permissions": get_effective_permissions(user_id),
    }


@router.post("/login")
def login(body: LoginRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT id, username, password_hash, display_name, email, status, default_project_id, last_login_at, created_at "
            "FROM metadata.users WHERE username = ?",
            [body.username],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        user_id, username, password_hash, _display_name, _email, status = row[:6]
        if not auth.verify_password(body.password, password_hash):
            raise HTTPException(status_code=401, detail="Invalid username or password")
        if str(status).upper() != "ACTIVE":
            raise HTTPException(status_code=403, detail="User is inactive")
        is_first_login = row[7] is None
        session_id = str(uuid.uuid4())
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=24)
        token = auth.create_token(user_id, username, session_id=session_id)
        client_host = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        con.execute(
            "INSERT INTO metadata.sessions (id, user_id, token_type, issued_at, expires_at, last_active_at, ip_address, user_agent, is_revoked) "
            "VALUES (?, ?, 'bearer', CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP, ?, ?, false)",
            [session_id, user_id, expires_at, client_host, user_agent],
        )
        con.execute(
            "UPDATE metadata.users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
            [user_id],
        )
        updated = con.execute(
            "SELECT id, username, display_name, email, status, default_project_id, last_login_at, created_at, updated_at "
            "FROM metadata.users WHERE id = ?",
            [user_id],
        ).fetchone()
        user = _user_payload(updated)
    log_audit(user_id, "LOGIN", "user", str(user_id), "login", ip_address=client_host, user_agent=request.headers.get("user-agent"))
    return {
        "data": {
            "token": token,
            "user": user,
            "is_first_login": is_first_login,
        }
    }


@router.post("/register")
def register(body: RegisterRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)
    if not _registration_enabled():
        raise HTTPException(status_code=403, detail="Self registration is disabled")
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT id FROM metadata.users WHERE username = ?", [body.username]
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Username already exists")
        max_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.users").fetchone()[0]
        password_hash = auth.hash_password(body.password)
        con.execute(
            "INSERT INTO metadata.users (id, username, password_hash, display_name, status) VALUES (?, ?, ?, ?, 'ACTIVE')",
            [max_id, body.username, password_hash, body.display_name],
        )
        viewer_role = con.execute("SELECT id FROM metadata.roles WHERE name = 'viewer'").fetchone()
        if viewer_role:
            user_role_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.user_roles").fetchone()[0]
            con.execute(
                "INSERT INTO metadata.user_roles (id, user_id, role_id) VALUES (?, ?, ?)",
                [user_role_id, max_id, viewer_role[0]],
            )
        user = _user_payload(
            con.execute(
                "SELECT id, username, display_name, email, status, default_project_id, last_login_at, created_at, updated_at FROM metadata.users WHERE id = ?",
                [max_id],
            ).fetchone()
        )
    log_audit(max_id, "USER_REGISTER", "user", str(max_id), "register", ip_address=request.client.host if request.client else None, user_agent=request.headers.get("user-agent"))
    return {
        "data": {
            "user": user,
        }
    }


@router.get("/me")
def get_me(payload: dict = Depends(get_current_user)):
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT id, username, display_name, email, status, default_project_id, last_login_at, created_at, updated_at FROM metadata.users WHERE id = ?",
            [int(payload["sub"])],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        return {"data": _user_payload(row)}


@router.post("/refresh")
def refresh_token(payload: dict = Depends(get_current_user)):
    user_id = int(payload["sub"])
    username = str(payload.get("username") or "")
    session_id = str(payload["sid"])
    try:
        max_session_days = int(os.getenv("PRISMBI_MAX_SESSION_DAYS", "30"))
    except (ValueError, TypeError):
        max_session_days = 30
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT issued_at, expires_at FROM metadata.sessions WHERE id = ? AND user_id = ? AND is_revoked = false",
            [session_id, user_id],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Session not found or revoked")
        issued_at, current_expires_at = row
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=timezone.utc)
        absolute_max = issued_at + timedelta(days=max_session_days)
        now = datetime.now(timezone.utc)
        if now >= absolute_max:
            con.execute(
                "UPDATE metadata.sessions SET is_revoked = true WHERE id = ?",
                [session_id],
            )
            raise HTTPException(status_code=401, detail="Session expired, please log in again")
        new_expires_at = now + timedelta(hours=24)
        if new_expires_at > absolute_max:
            new_expires_at = absolute_max
        new_expires_at_naive = new_expires_at.replace(tzinfo=None)
        con.execute(
            "UPDATE metadata.sessions SET expires_at = ?, last_active_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            [new_expires_at_naive, session_id, user_id],
        )
    token = auth.create_token(user_id, username, session_id=session_id)
    return {"data": {"token": token}}


# ── SSO / OIDC ────────────────────────────────────────────────────────────


@router.get("/sso/login")
def sso_login(request: Request, provider: Optional[str] = None):
    config = _get_sso_config()
    if not config or not config.get("enabled"):
        raise HTTPException(status_code=400, detail="SSO is not enabled")
    issuer_url = _config_string(config, "issuer_url")
    client_id = _config_string(config, "client_id")
    client_secret = _config_string(config, "client_secret")
    if not issuer_url or not client_id:
        raise HTTPException(status_code=400, detail="SSO configuration incomplete: missing issuer_url or client_id")
    state = generate_state()
    nonce = store_state(state)
    redirect_uri = str(request.base_url).rstrip("/") + "/api/auth/sso/callback"
    _validate_redirect_uri(redirect_uri, config)
    auth_url = get_authorization_url(issuer_url, client_id, redirect_uri, state, nonce=nonce)
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/sso/callback")
def sso_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    if error:
        raise HTTPException(status_code=400, detail="SSO authentication failed. Please try again.")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing authorization code or state parameter")
    config = _get_sso_config()
    if not config or not config.get("enabled"):
        raise HTTPException(status_code=400, detail="SSO is not enabled")
    issuer_url = _config_string(config, "issuer_url")
    client_id = _config_string(config, "client_id")
    client_secret = _config_string(config, "client_secret")
    if not issuer_url or not client_id:
        raise HTTPException(status_code=500, detail="SSO configuration incomplete")

    nonce = consume_state(state)
    if nonce is None:
        raise HTTPException(status_code=400, detail="Invalid or expired SSO state parameter")

    try:
        redirect_uri = str(request.base_url).rstrip("/") + "/api/auth/sso/callback"
        _validate_redirect_uri(redirect_uri, config)
        token_response = exchange_code(issuer_url, client_id, client_secret, code, redirect_uri)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="SSO authentication failed. Please try again.") from exc
    except Exception:
        raise HTTPException(status_code=502, detail="SSO authentication failed. Please try again.")

    id_token_str = token_response.get("id_token")
    if not id_token_str:
        raise HTTPException(status_code=502, detail="SSO provider did not return an ID token")

    try:
        claims = verify_id_token(issuer_url, client_id, id_token_str, nonce=nonce)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="ID token verification failed") from exc
    except Exception as exc:
        raise HTTPException(status_code=401, detail="ID token verification failed") from exc

    mapping_rules = config.get("mapping_rules") or {}
    mapped_roles = map_claims_to_roles(claims, mapping_rules)

    try:
        user_data = sso_login_or_create(claims, mapped_roles)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    user_id = user_data["user_id"]
    username = user_data["username"]
    is_new = user_data.get("is_new", False)
    session_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=24)
    extra_claims = {"is_new": True} if is_new else None
    token = auth.create_token(user_id, username, session_id=session_id, extra_claims=extra_claims)

    with connection_lock():
        con = get_connection()
        client_host = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        con.execute(
            "INSERT INTO metadata.sessions (id, user_id, token_type, issued_at, expires_at, last_active_at, ip_address, user_agent, is_revoked) "
            "VALUES (?, ?, 'bearer', CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP, ?, ?, false)",
            [session_id, user_id, expires_at, client_host, user_agent],
        )

    log_audit(user_id, "SSO_LOGIN", "user", str(user_id), "sso_callback", {"provider": config.get("provider")}, ip_address=client_host, user_agent=request.headers.get("user-agent"))

    frontend_base = os.getenv("PRISMBI_FRONTEND_URL", "/")
    _validate_frontend_url(frontend_base)
    response = RedirectResponse(url=f"{frontend_base.rstrip('/')}/login?sso=1", status_code=302)
    response.set_cookie(
        key="sso_token",
        value=token,
        httponly=True,
        secure=frontend_base.startswith("https") or os.getenv("PRISMBI_SECURE_COOKIES", "").strip().lower() in {"1", "true", "yes", "on"},
        samesite="lax",
        max_age=120,
        path="/",
    )
    return response


@router.post("/sso/token")
def sso_token_exchange(body: SSOLoginRequest, request: Request):
    config = _get_sso_config()
    if not config or not config.get("enabled"):
        raise HTTPException(status_code=400, detail="SSO is not enabled")
    issuer_url = _config_string(config, "issuer_url")
    client_id = _config_string(config, "client_id")
    client_secret = _config_string(config, "client_secret")
    if not issuer_url or not client_id:
        raise HTTPException(status_code=500, detail="SSO configuration incomplete")

    if body.code:
        try:
            redirect_uri = body.redirect_uri or str(request.base_url).rstrip("/") + "/api/auth/sso/callback"
            _validate_redirect_uri(redirect_uri, config)
            token_response = exchange_code(issuer_url, client_id, client_secret, body.code, redirect_uri)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Failed to exchange authorization code with SSO provider") from exc
        id_token_str = token_response.get("id_token")
    elif body.id_token:
        id_token_str = body.id_token
    else:
        raise HTTPException(status_code=400, detail="Must provide either 'code' or 'id_token'")

    try:
        claims = verify_id_token(issuer_url, client_id, id_token_str, nonce=body.nonce)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="ID token verification failed") from exc
    except Exception:
        raise HTTPException(status_code=401, detail="ID token verification failed")

    mapping_rules = config.get("mapping_rules") or {}
    mapped_roles = map_claims_to_roles(claims, mapping_rules)

    try:
        user_data = sso_login_or_create(claims, mapped_roles)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    user_id = user_data["user_id"]
    username = user_data["username"]
    session_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=24)
    token = auth.create_token(user_id, username, session_id=session_id)

    with connection_lock():
        con = get_connection()
        client_host = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        con.execute(
            "INSERT INTO metadata.sessions (id, user_id, token_type, issued_at, expires_at, last_active_at, ip_address, user_agent, is_revoked) "
            "VALUES (?, ?, 'bearer', CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP, ?, ?, false)",
            [session_id, user_id, expires_at, client_host, user_agent],
        )

    with connection_lock():
        con = get_connection()
        user_row = con.execute(
            "SELECT id, username, display_name, email, status, default_project_id, last_login_at, created_at, updated_at "
            "FROM metadata.users WHERE id = ?",
            [user_id],
        ).fetchone()
    user = _user_payload(user_row)

    log_audit(user_id, "SSO_LOGIN", "user", str(user_id), "sso_token", {"provider": config.get("provider")}, ip_address=request.client.host if request.client else None, user_agent=request.headers.get("user-agent"))

    return {
        "data": {
            "token": token,
            "user": user,
            "is_first_login": user_data["is_new"],
        }
    }


@router.get("/sso/cookie-token")
def sso_cookie_token(request: Request):
    sso_token = request.cookies.get("sso_token")
    if not sso_token:
        raise HTTPException(status_code=400, detail="No SSO token cookie found")
    payload = get_payload_from_token(sso_token)
    user_id = int(payload["sub"])
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT id, username, display_name, email, status, default_project_id, last_login_at, created_at, updated_at FROM metadata.users WHERE id = ?",
            [user_id],
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    user = _user_payload(row)
    is_new = payload.get("is_new", False) if isinstance(payload, dict) else False
    response = {"data": {"token": sso_token, "user": user, "is_first_login": bool(is_new)}}
    from fastapi.responses import JSONResponse
    json_response = JSONResponse(content=response)
    json_response.delete_cookie("sso_token", path="/")
    return json_response


@router.post("/ws-ticket")
def create_ws_ticket_endpoint(payload: dict = Depends(get_current_user)):
    ticket = create_ws_ticket(payload)
    return {"data": {"ticket": ticket}}
