from __future__ import annotations

import hashlib
import logging
import secrets
import time
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from jose import JWTError, jwt

from db import connection_lock, get_connection
from services.crypto_service import decrypt_json

logger = logging.getLogger(__name__)

_OIDC_CACHE: dict = {}
_OIDC_CACHE_TTL = 3600
_SSO_STATE_TTL = 600


def _get_sso_config() -> dict | None:
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT value FROM metadata.settings WHERE key = ?", ["sso_config"]
        ).fetchone()
        if not row:
            return None
        config = decrypt_json(row[0], {})
        if not isinstance(config, dict):
            return None
        return config


def _validate_redirect_uri(redirect_uri: str, config: dict | None = None) -> str:
    parsed = urlparse(redirect_uri)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid redirect URI: must be an absolute URL")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Invalid redirect URI: only http/https schemes allowed")
    if config:
        raw_allowed_origins = config.get("allowed_redirect_origins", [])
        allowed_origins = [
            str(origin).strip().rstrip("/")
            for origin in (raw_allowed_origins if isinstance(raw_allowed_origins, list) else [raw_allowed_origins])
            if str(origin).strip()
        ]
        if allowed_origins:
            origin = f"{parsed.scheme}://{parsed.netloc}"
            allowed = False
            for allowed_origin in allowed_origins:
                if origin == allowed_origin or origin.rstrip("/") == allowed_origin:
                    allowed = True
                    break
            if not allowed:
                raise ValueError(f"Redirect URI origin '{origin}' is not in the allowed list")
    return redirect_uri


def _fetch_oidc_discovery(issuer_url: str) -> dict:
    now = time.time()
    cached = _OIDC_CACHE.get(issuer_url)
    if cached and now - cached.get("_ts", 0) < _OIDC_CACHE_TTL:
        return cached
    well_known = urljoin(issuer_url.rstrip("/") + "/", ".well-known/openid-configuration")
    resp = httpx.get(well_known, timeout=10, follow_redirects=True)
    resp.raise_for_status()
    doc = resp.json()
    doc["_ts"] = now
    _OIDC_CACHE[issuer_url] = doc
    return doc


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def store_state(state: str) -> str:
    nonce = secrets.token_urlsafe(16)
    with connection_lock():
        con = get_connection()
        con.execute(
            "INSERT INTO metadata.settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
            [f"sso_state:{state}", '{"ts":"' + str(int(time.time())) + '","nonce":"' + nonce + '"}',
             '{"ts":"' + str(int(time.time())) + '","nonce":"' + nonce + '"}'],
        )
    return nonce


def consume_state(state: str) -> str | None:
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT value FROM metadata.settings WHERE key = ?", [f"sso_state:{state}"]
        ).fetchone()
        if not row:
            return None
        raw = row[0]
        try:
            from json import loads as json_loads
            if isinstance(raw, dict):
                data = raw
            elif isinstance(raw, str):
                if raw.startswith("gAAAA"):
                    data = decrypt_json(raw, {})
                else:
                    data = json_loads(raw)
            else:
                data = {}
        except Exception:
            data = {"ts": str(int(time.time()))}
        created_ts = int(data.get("ts", "0") or "0") if isinstance(data, dict) else 0
        current_ts = int(time.time())
        con.execute("DELETE FROM metadata.settings WHERE key = ?", [f"sso_state:{state}"])
        if created_ts and (current_ts - created_ts) > _SSO_STATE_TTL:
            logger.warning("SSO state expired for %s (age=%ds)", state[:8], current_ts - created_ts)
            return None
        return data.get("nonce") if isinstance(data, dict) else None


def get_authorization_url(issuer_url: str, client_id: str, redirect_uri: str, state: str, nonce: str | None = None) -> str:
    discovery = _fetch_oidc_discovery(issuer_url)
    auth_endpoint = discovery.get("authorization_endpoint")
    if not auth_endpoint:
        raise ValueError("OIDC discovery document missing authorization_endpoint")
    params = [
        ("response_type", "code"),
        ("client_id", client_id),
        ("redirect_uri", redirect_uri),
        ("scope", "openid profile email"),
        ("state", state),
    ]
    if nonce:
        params.append(("nonce", nonce))
    query = "&".join(f"{k}={httpx.QueryParams(params=[(k, v)])[k]}" for k, v in params)
    sep = "&" if "?" in auth_endpoint else "?"
    return f"{auth_endpoint}{sep}{query}"


def exchange_code(issuer_url: str, client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    discovery = _fetch_oidc_discovery(issuer_url)
    token_endpoint = discovery.get("token_endpoint")
    if not token_endpoint:
        raise ValueError("OIDC discovery document missing token_endpoint")
    resp = httpx.post(
        token_endpoint,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=15,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return resp.json()


def verify_id_token(issuer_url: str, client_id: str, id_token: str, nonce: str | None = None) -> dict:
    discovery = _fetch_oidc_discovery(issuer_url)
    jwks_uri = discovery.get("jwks_uri")
    if not jwks_uri:
        raise ValueError("OIDC discovery document missing jwks_uri")
    jwks_resp = httpx.get(jwks_uri, timeout=10, follow_redirects=True)
    jwks_resp.raise_for_status()
    jwks = jwks_resp.json()
    try:
        unverified_header = jwt.get_unverified_header(id_token)
        kid = unverified_header.get("kid")
        rsa_key = {}
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                rsa_key = key
                break
        if not rsa_key:
            raise ValueError(f"Unable to find signing key with kid={kid}")
        payload = jwt.decode(
            id_token,
            rsa_key,
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
            audience=client_id,
            issuer=issuer_url,
        )
        if nonce:
            token_nonce = payload.get("nonce")
            if token_nonce != nonce:
                raise ValueError("ID token nonce mismatch — possible replay attack")
        return payload
    except JWTError as e:
        raise ValueError(f"ID token verification failed: {e}") from e


def map_claims_to_roles(claims: dict, mapping_rules: dict | None, default_role: str = "viewer") -> list[str]:
    if not mapping_rules:
        return [default_role]
    roles = set()
    for claim_key, role_value in mapping_rules.items():
        claim_val = claims.get(claim_key)
        if claim_val is None:
            continue
        if isinstance(claim_val, list):
            for v in claim_val:
                if isinstance(v, str) and v == role_value:
                    roles.add(role_value)
                elif isinstance(v, str):
                    roles.add(v)
        elif isinstance(claim_val, str):
            if claim_val == role_value:
                roles.add(role_value)
            else:
                parts = claim_val.split(",")
                for part in parts:
                    part = part.strip()
                    if part == role_value:
                        roles.add(role_value)
        elif isinstance(claim_val, bool) and claim_val:
            roles.add(role_value)
    if not roles:
        roles.add(default_role)
    return list(roles)


def sso_login_or_create(claims: dict, mapped_roles: list[str]) -> dict:
    sub = claims.get("sub")
    if not sub:
        raise ValueError("OIDC claims missing 'sub' field")
    email = claims.get("email") or ""
    display_name = claims.get("name") or claims.get("preferred_username") or email.split("@")[0]
    sso_username = f"sso_{sub}"

    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT id, username, display_name, email, status, default_project_id, last_login_at, created_at, updated_at "
            "FROM metadata.users WHERE username = ?",
            [sso_username],
        ).fetchone()

        if row:
            user_id = row[0]
            if str(row[4]).upper() != "ACTIVE":
                raise ValueError("User is inactive")
            con.execute(
                "UPDATE metadata.users SET last_login_at = CURRENT_TIMESTAMP, display_name = ?, email = ? WHERE id = ?",
                [display_name, email, user_id],
            )
        else:
            if email:
                existing_email = con.execute(
                    "SELECT id, username FROM metadata.users WHERE email = ? AND username != ?",
                    [email, sso_username],
                ).fetchone()
                if existing_email:
                    raise ValueError(
                        f"A user with email '{email}' already exists (username: {existing_email[1]}). "
                        "Please contact your administrator to link your account."
                    )
            user_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.users").fetchone()[0]
            con.execute(
                "INSERT INTO metadata.users (id, username, display_name, email, password_hash, status) VALUES (?, ?, ?, ?, '', 'ACTIVE')",
                [user_id, sso_username, display_name, email],
            )
            for role_name in mapped_roles:
                role_row = con.execute("SELECT id FROM metadata.roles WHERE name = ?", [role_name]).fetchone()
                if role_row:
                    ur_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.user_roles").fetchone()[0]
                    con.execute(
                        "INSERT INTO metadata.user_roles (id, user_id, role_id) VALUES (?, ?, ?)",
                        [ur_id, user_id, role_row[0]],
                    )

    return {"user_id": user_id, "username": sso_username, "is_new": row is None}
