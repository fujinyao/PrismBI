from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from db import connection_lock, get_connection
from models.schemas import (
    ChangePasswordRequest,
    CreateApiTokenRequest,
    UpdateProfileRequest,
)
from routers.auth import get_current_user
from services.auth_service import auth_service as auth

router = APIRouter()


def _get_current_user_id(payload: dict) -> int:
    return int(payload["sub"])


@router.get("", response_model=dict)
def get_profile(
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT id, username, display_name, email, status, default_project_id, last_login_at, created_at "
            "FROM metadata.users WHERE id = ?",
            [_get_current_user_id(payload)],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
    return {
        "data": {
            "id": row[0],
            "username": row[1],
            "display_name": row[2],
            "email": row[3],
            "status": row[4],
            "default_project_id": row[5],
            "last_login_at": str(row[6]) if row[6] else None,
            "created_at": str(row[7]) if row[7] else None,
        }
    }


@router.put("", response_model=dict)
def update_profile(
    body: UpdateProfileRequest,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        con = get_connection()
        user_id = _get_current_user_id(payload)
        updates = []
        params = []
        if body.display_name is not None:
            updates.append("display_name = ?")
            params.append(body.display_name)
        if body.email is not None:
            updates.append("email = ?")
            params.append(body.email)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        updates.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).replace(tzinfo=None))
        params.append(user_id)
        con.execute(
            f"UPDATE metadata.users SET {', '.join(updates)} WHERE id = ?",
            params,
        )
    return {"data": {"success": True}}


@router.post("/change-password", response_model=dict)
def change_password(
    body: ChangePasswordRequest,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        con = get_connection()
        user_id = _get_current_user_id(payload)
        row = con.execute(
            "SELECT password_hash FROM metadata.users WHERE id = ?", [user_id]
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        if not auth.verify_password(body.old_password, row[0]):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        new_hash = auth.hash_password(body.new_password)
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(
                "UPDATE metadata.users SET password_hash = ?, updated_at = ? WHERE id = ?",
                [new_hash, now_naive, user_id],
            )
            session_id = payload.get("sid")
            if session_id:
                con.execute(
                    "UPDATE metadata.sessions SET is_revoked = true WHERE user_id = ? AND id <> ?",
                    [user_id, session_id],
                )
            else:
                con.execute(
                    "UPDATE metadata.sessions SET is_revoked = true WHERE user_id = ?",
                    [user_id],
                )
            con.execute(
                "UPDATE metadata.api_tokens SET is_revoked = true WHERE user_id = ?",
                [user_id],
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return {"data": {"success": True}}


@router.get("/tokens", response_model=dict)
def list_tokens(
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            "SELECT id, name, token_prefix, scope, expires_at, last_used_at, is_revoked, created_at "
            "FROM metadata.api_tokens WHERE user_id = ? ORDER BY created_at DESC",
            [_get_current_user_id(payload)],
        ).fetchall()
    items = []
    for r in rows:
        items.append({
            "id": r[0],
            "name": r[1],
            "token_prefix": r[2],
            "scope": (json.loads(r[3]) if isinstance(r[3], str) else r[3]) if r[3] is not None else [],
            "expires_at": str(r[4]) if r[4] else None,
            "last_used_at": str(r[5]) if r[5] else None,
            "is_revoked": bool(r[6]),
            "created_at": str(r[7]) if r[7] else None,
        })
    return {"data": items}


@router.post("/tokens", response_model=dict)
def create_token(
    body: CreateApiTokenRequest,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        con = get_connection()
        user_id = _get_current_user_id(payload)
        max_id = con.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.api_tokens"
        ).fetchone()[0]
        raw_token = "prismbi_" + secrets.token_urlsafe(32)
        token_hash = auth.hash_password(raw_token)
        token_prefix = raw_token[:12]
        scope = json.dumps(body.scope or [])
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        con.execute(
            "INSERT INTO metadata.api_tokens (id, user_id, name, token_hash, token_prefix, scope, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?::JSON, ?, ?)",
            [max_id, user_id, body.name, token_hash, token_prefix, scope, body.expires_at, now_naive],
        )
    return {
        "data": {
            "id": max_id,
            "token": raw_token,
            "name": body.name,
        }
    }


@router.post("/tokens/{token_id}/revoke", response_model=dict)
def revoke_token(
    token_id: int,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        con = get_connection()
        user_id = _get_current_user_id(payload)
        existing = con.execute(
            "SELECT id FROM metadata.api_tokens WHERE id = ? AND user_id = ?",
            [token_id, user_id],
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Token not found")
        con.execute(
            "UPDATE metadata.api_tokens SET is_revoked = true WHERE id = ?",
            [token_id],
        )
    return {"data": {"success": True}}


@router.get("/sessions", response_model=dict)
def list_sessions(
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            "SELECT id, token_type, issued_at, expires_at, last_active_at, ip_address, user_agent, is_revoked "
            "FROM metadata.sessions WHERE user_id = ? AND is_revoked = false ORDER BY issued_at DESC",
            [_get_current_user_id(payload)],
        ).fetchall()
    items = []
    for r in rows:
        items.append({
            "id": r[0],
            "token_type": r[1],
            "issued_at": str(r[2]) if r[2] else None,
            "expires_at": str(r[3]) if r[3] else None,
            "last_active_at": str(r[4]) if r[4] else None,
            "ip_address": r[5],
            "user_agent": r[6],
            "is_revoked": bool(r[7]),
        })
    return {"data": items}


@router.post("/sessions/{session_id}/revoke", response_model=dict)
def revoke_session(
    session_id: str,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        con = get_connection()
        user_id = _get_current_user_id(payload)
        existing = con.execute(
            "SELECT id FROM metadata.sessions WHERE id = ? AND user_id = ?",
            [session_id, user_id],
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Session not found")
        con.execute(
            "UPDATE metadata.sessions SET is_revoked = true WHERE id = ?",
            [session_id],
        )
    return {"data": {"success": True}}
