from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from db import connection_lock, get_connection
from models.schemas import (
    AssignRoleRequest,
    AuditLogExportRequest,
    BatchUpdatePermissionsRequest,
    CreateRoleRequest,
    CreateUserRequest,
    ResetPasswordRequest,
    RowSecurityPolicyCreate,
    RowSecurityPolicyUpdate,
    ColumnSecurityPolicyCreate,
    ColumnSecurityPolicyUpdate,
    SSOConfigUpdate,
    UpdateRoleRequest,
    UpdateUserRequest,
)
from routers.auth import log_audit, payload_has_permission, require_permission
from services.auth_service import auth_service as auth
from services.crypto_service import decrypt_json, encrypt_json
from services.security_policy_service import normalize_access_type, normalize_operator, _safe_identifier, _sql_literal

router = APIRouter()


class UpdatePermissionRequest(BaseModel):
    resource: Optional[str] = None
    action: Optional[str] = None
    description: Optional[str] = None


USER_SELECT = (
    "id, username, display_name, email, status, "
    "default_project_id, last_login_at, created_at, updated_at"
)

ROLE_SELECT = "id, name, scope, description, is_system, created_at"

PERMISSION_SELECT = "id, resource, action, description"

RLS_SELECT = "id, project_id, role_id, model_name, column_name, operator, value, value_source, user_attribute, filter_expression, description, is_enabled, created_at"
CLS_SELECT = "id, project_id, role_id, model_name, column_name, access_type, mask_with, is_enabled, created_at"


def _user_to_dict(row):
    return {
        "id": row[0],
        "username": row[1],
        "display_name": row[2],
        "email": row[3],
        "status": row[4],
        "default_project_id": row[5],
        "last_login_at": str(row[6]) if row[6] else None,
        "created_at": str(row[7]) if row[7] else None,
        "updated_at": str(row[8]) if row[8] else None,
    }


def _role_to_dict(row):
    return {
        "id": row[0],
        "name": row[1],
        "scope": row[2],
        "description": row[3],
        "is_system": row[4],
        "created_at": str(row[5]) if row[5] else None,
    }


def _perm_to_dict(row):
    return {
        "id": row[0],
        "resource": row[1],
        "action": row[2],
        "description": row[3],
    }


def _rls_to_dict(row):
    return {
        "id": row[0],
        "project_id": row[1],
        "role_id": row[2],
        "model_name": row[3],
        "column_name": row[4],
        "operator": row[5],
        "value": row[6],
        "value_source": row[7],
        "user_attribute": row[8],
        "filter_expression": row[9],
        "description": row[10],
        "is_enabled": bool(row[11]),
        "created_at": str(row[12]) if row[12] else None,
    }


def _cls_to_dict(row):
    return {
        "id": row[0],
        "project_id": row[1],
        "role_id": row[2],
        "model_name": row[3],
        "column_name": row[4],
        "access_type": row[5],
        "mask_with": row[6],
        "is_enabled": bool(row[7]),
        "created_at": str(row[8]) if row[8] else None,
    }


_ALLOWED_ADMIN_TABLES = frozenset({
    "metadata.users",
    "metadata.user_roles",
    "metadata.roles",
    "metadata.role_permissions",
    "metadata.row_level_security_policies",
    "metadata.column_level_security_policies",
})


def _next_id(con, table: str) -> int:
    if table not in _ALLOWED_ADMIN_TABLES:
        raise ValueError(f"Unknown table for ID generation: {table}")
    return con.execute(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table}").fetchone()[0]


def _current_user_id(payload: dict) -> int:
    return int(payload["sub"])


def _json_setting_value(value: Any, fallback: Any = None) -> Any:
    decoded = decrypt_json(value, fallback)
    return decoded if decoded is not None else fallback


def _role_summary_for_user(con, user_id: int) -> tuple[list[dict], Optional[int], str]:
    rows = con.execute(
        "SELECT r.id, r.name, r.scope, r.description, r.is_system, r.created_at, ur.project_id, ur.expires_at "
        "FROM metadata.roles r "
        "JOIN metadata.user_roles ur ON ur.role_id = r.id "
        "WHERE ur.user_id = ? "
        "AND (ur.expires_at IS NULL OR ur.expires_at > CURRENT_TIMESTAMP) "
        "ORDER BY CASE WHEN ur.project_id IS NULL THEN 0 ELSE 1 END, r.name",
        [user_id],
    ).fetchall()
    roles = []
    primary_role_id = None
    primary_role_name = ""
    for row in rows:
        role = _role_to_dict(row[:6])
        role["project_id"] = row[6]
        role["expires_at"] = str(row[7]) if row[7] else None
        roles.append(role)
        if primary_role_id is None and row[6] is None:
            primary_role_id = row[0]
            primary_role_name = row[1]
    if primary_role_id is None and rows:
        primary_role_id = rows[0][0]
        primary_role_name = rows[0][1]
    return roles, primary_role_id, primary_role_name


def _attach_user_roles(con, user: dict) -> dict:
    roles, role_id, role_name = _role_summary_for_user(con, user["id"])
    user["roles"] = roles
    user["role_id"] = role_id
    user["role"] = role_name
    return user


def _audit(payload: dict, event_type: str, resource_type: str, resource_id: Any, action: str, detail: Optional[dict] = None):
    log_audit(
        _current_user_id(payload),
        event_type,
        resource_type,
        str(resource_id),
        action,
        detail or {},
    )


# ── Users ────────────────────────────────────────────────────────────


@router.get("/users")
def list_users(
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    payload: dict = Depends(require_permission("users", "read")),
):
    with connection_lock():
        con = get_connection()
        conditions = []
        params = []
        if search:
            conditions.append("(username ILIKE ? OR display_name ILIKE ? OR email ILIKE ?) ESCAPE '\\'")
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            p = f"%{escaped}%"
            params.extend([p, p, p])
        if status:
            conditions.append("UPPER(status) = UPPER(?)")
            params.append(status)
        if role:
            conditions.append(
                "id IN (SELECT user_id FROM metadata.user_roles ur JOIN metadata.roles r ON ur.role_id = r.id WHERE r.name = ?)"
            )
            params.append(role)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        offset = (page - 1) * page_size
        count = con.execute(f"SELECT COUNT(*) FROM metadata.users{where}", params).fetchone()[0]
        rows = con.execute(
            f"SELECT {USER_SELECT} FROM metadata.users{where} ORDER BY id LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        items = [_attach_user_roles(con, _user_to_dict(r)) for r in rows]
        return {"data": {"items": items, "total": count, "page": page, "page_size": page_size}}


@router.post("/users")
def create_user(body: CreateUserRequest, payload: dict = Depends(require_permission("users", "create"))):
    with connection_lock():
        con = get_connection()
        existing = con.execute("SELECT id FROM metadata.users WHERE username = ?", [body.username]).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Username already exists")
        max_id = _next_id(con, "metadata.users")
        password_hash = auth.hash_password(body.password)
        con.execute(
            "INSERT INTO metadata.users (id, username, password_hash, display_name, email, status) VALUES (?, ?, ?, ?, ?, ?)",
            [max_id, body.username, password_hash, body.display_name, body.email, body.status],
        )
        role = con.execute("SELECT id FROM metadata.roles WHERE name = 'viewer'").fetchone()
        if role:
            existing_role = con.execute(
                "SELECT id FROM metadata.user_roles WHERE user_id = ? AND role_id = ? AND project_id IS NULL",
                [max_id, role[0]],
            ).fetchone()
            if not existing_role:
                con.execute(
                    "INSERT INTO metadata.user_roles (id, user_id, role_id, granted_by) VALUES (?, ?, ?, ?)",
                    [_next_id(con, "metadata.user_roles"), max_id, role[0], _current_user_id(payload)],
                )
        row = con.execute(f"SELECT {USER_SELECT} FROM metadata.users WHERE id = ?", [max_id]).fetchone()
        user = _attach_user_roles(con, _user_to_dict(row))
    _audit(payload, "USER_CREATE", "user", max_id, "create", {"username": body.username})
    return {"data": user}


@router.get("/users/{user_id}")
def get_user(user_id: int, payload: dict = Depends(require_permission("users", "read"))):
    with connection_lock():
        con = get_connection()
        row = con.execute(f"SELECT {USER_SELECT} FROM metadata.users WHERE id = ?", [user_id]).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        return {"data": _attach_user_roles(con, _user_to_dict(row))}


@router.put("/users/{user_id}")
def update_user(user_id: int, body: UpdateUserRequest, payload: dict = Depends(require_permission("users", "update"))):
    if body.status and body.status.upper() in ("INACTIVE", "SUSPENDED") and user_id == _current_user_id(payload):
        raise HTTPException(status_code=403, detail="Cannot deactivate your own account via this endpoint")
    with connection_lock():
        con = get_connection()
        existing = con.execute("SELECT id FROM metadata.users WHERE id = ?", [user_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="User not found")
        fields = []
        params = []
        for col, val in [("display_name", body.display_name), ("email", body.email), ("status", body.status)]:
            if val is not None:
                fields.append(f"{col} = ?")
                params.append(val)
        if fields:
            fields.append("updated_at = CURRENT_TIMESTAMP")
            params.append(user_id)
            con.execute(f"UPDATE metadata.users SET {', '.join(fields)} WHERE id = ?", params)
        row = con.execute(f"SELECT {USER_SELECT} FROM metadata.users WHERE id = ?", [user_id]).fetchone()
        user = _attach_user_roles(con, _user_to_dict(row))
    _audit(payload, "USER_UPDATE", "user", user_id, "update", body.model_dump(exclude_none=True))
    return {"data": user}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, payload: dict = Depends(require_permission("users", "delete"))):
    if user_id == _current_user_id(payload):
        raise HTTPException(status_code=400, detail="Cannot delete current user")
    with connection_lock():
        con = get_connection()
        existing = con.execute("SELECT id FROM metadata.users WHERE id = ?", [user_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="User not found")
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("DELETE FROM metadata.user_roles WHERE user_id = ?", [user_id])
            con.execute("DELETE FROM metadata.user_permission_overrides WHERE user_id = ?", [user_id])
            con.execute("DELETE FROM metadata.recommendation_scores WHERE user_id = ?", [user_id])
            con.execute("DELETE FROM metadata.recommendation_feedback WHERE user_id = ?", [user_id])
            con.execute("DELETE FROM metadata.api_tokens WHERE user_id = ?", [user_id])
            con.execute("UPDATE metadata.sessions SET is_revoked = true WHERE user_id = ?", [user_id])
            con.execute("DELETE FROM metadata.users WHERE id = ?", [user_id])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    _audit(payload, "USER_DELETE", "user", user_id, "delete")
    return {"data": {"success": True}}


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, body: ResetPasswordRequest, payload: dict = Depends(require_permission("users", "update"))):
    with connection_lock():
        con = get_connection()
        existing = con.execute("SELECT id FROM metadata.users WHERE id = ?", [user_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="User not found")
        password_hash = auth.hash_password(body.new_password)
        con.execute("UPDATE metadata.users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", [password_hash, user_id])
        con.execute("UPDATE metadata.sessions SET is_revoked = true WHERE user_id = ?", [user_id])
    _audit(payload, "USER_PASSWORD_RESET", "user", user_id, "reset_password")
    return {"data": {"success": True}}


@router.post("/users/{user_id}/deactivate")
def deactivate_user(user_id: int, payload: dict = Depends(require_permission("users", "update"))):
    if user_id == _current_user_id(payload):
        raise HTTPException(status_code=400, detail="Cannot deactivate current user")
    with connection_lock():
        con = get_connection()
        existing = con.execute("SELECT id FROM metadata.users WHERE id = ?", [user_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="User not found")
        con.execute("UPDATE metadata.users SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", ["INACTIVE", user_id])
        con.execute("UPDATE metadata.sessions SET is_revoked = true WHERE user_id = ?", [user_id])
        con.execute("UPDATE metadata.api_tokens SET is_revoked = true WHERE user_id = ?", [user_id])
    _audit(payload, "USER_DEACTIVATE", "user", user_id, "deactivate")
    return {"data": {"success": True}}


@router.post("/users/{user_id}/roles")
def assign_role(user_id: int, body: AssignRoleRequest, payload: dict = Depends(require_permission("users", "manage"))):
    with connection_lock():
        con = get_connection()
        user = con.execute("SELECT id FROM metadata.users WHERE id = ?", [user_id]).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        role = con.execute("SELECT id, scope FROM metadata.roles WHERE id = ?", [body.role_id]).fetchone()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        if body.project_id is None and str(role[1]).upper() != "SYSTEM":
            raise HTTPException(status_code=400, detail="Project role assignments require project_id")
        if body.project_id is not None and str(role[1]).upper() != "PROJECT":
            raise HTTPException(status_code=400, detail="Only project-scoped roles can be assigned to a project")
        existing = con.execute(
            "SELECT id FROM metadata.user_roles WHERE user_id = ? AND role_id = ? AND ((project_id IS NULL AND ? IS NULL) OR project_id = ?)",
            [user_id, body.role_id, body.project_id, body.project_id],
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="User already has this role")
        con.execute(
            "INSERT INTO metadata.user_roles (id, user_id, role_id, project_id, granted_by, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            [_next_id(con, "metadata.user_roles"), user_id, body.role_id, body.project_id, _current_user_id(payload), body.expires_at],
        )
    _audit(payload, "USER_ROLE_ASSIGN", "user", user_id, "assign_role", {"role_id": body.role_id, "project_id": body.project_id})
    return {"data": {"success": True}}


@router.delete("/users/{user_id}/roles/{role_id}")
def remove_role(
    user_id: int,
    role_id: int,
    project_id: Optional[int] = Query(None),
    payload: dict = Depends(require_permission("users", "manage")),
):
    with connection_lock():
        con = get_connection()
        if project_id is None:
            con.execute(
                "DELETE FROM metadata.user_roles WHERE user_id = ? AND role_id = ? AND project_id IS NULL",
                [user_id, role_id],
            )
        else:
            con.execute(
                "DELETE FROM metadata.user_roles WHERE user_id = ? AND role_id = ? AND project_id = ?",
                [user_id, role_id, project_id],
            )
    _audit(payload, "USER_ROLE_REMOVE", "user", user_id, "remove_role", {"role_id": role_id, "project_id": project_id})
    return {"data": {"success": True}}


# ── Roles ────────────────────────────────────────────────────────────


@router.get("/roles")
def list_roles(scope: Optional[str] = Query(None), payload: dict = Depends(require_permission("roles", "read"))):
    with connection_lock():
        con = get_connection()
        if scope:
            rows = con.execute(f"SELECT {ROLE_SELECT} FROM metadata.roles WHERE UPPER(scope) = UPPER(?) ORDER BY id", [scope]).fetchall()
        else:
            rows = con.execute(f"SELECT {ROLE_SELECT} FROM metadata.roles ORDER BY id").fetchall()
        items = []
        for r in rows:
            d = _role_to_dict(r)
            perms = con.execute(
                "SELECT p.id, p.resource, p.action, p.description FROM metadata.permissions p "
                "JOIN metadata.role_permissions rp ON rp.permission_id = p.id WHERE rp.role_id = ? ORDER BY p.resource, p.action",
                [r[0]],
            ).fetchall()
            d["permissions"] = [_perm_to_dict(p) for p in perms]
            cnt = con.execute("SELECT COUNT(*) FROM metadata.user_roles WHERE role_id = ?", [r[0]]).fetchone()[0]
            d["member_count"] = cnt
            d["permissionsCount"] = len(perms)
            d["userCount"] = cnt
            items.append(d)
        return {"data": {"roles": items, "total": len(items)}}


@router.post("/roles")
def create_role(body: CreateRoleRequest, payload: dict = Depends(require_permission("roles", "create"))):
    with connection_lock():
        con = get_connection()
        existing = con.execute("SELECT id FROM metadata.roles WHERE name = ?", [body.name]).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Role already exists")
        max_id = _next_id(con, "metadata.roles")
        con.execute(
            "INSERT INTO metadata.roles (id, name, scope, description, is_system) VALUES (?, ?, ?, ?, ?)",
            [max_id, body.name, body.scope, body.description, False],
        )
        for perm_id in body.permissions:
            con.execute(
                "INSERT INTO metadata.role_permissions (id, role_id, permission_id) VALUES (?, ?, ?)",
                [_next_id(con, "metadata.role_permissions"), max_id, perm_id],
            )
        row = con.execute(f"SELECT {ROLE_SELECT} FROM metadata.roles WHERE id = ?", [max_id]).fetchone()
        d = _role_to_dict(row)
        d["permissions"] = []
        d["member_count"] = 0
        d["permissionsCount"] = 0
        d["userCount"] = 0
    _audit(payload, "ROLE_CREATE", "role", max_id, "create", {"name": body.name})
    return {"data": d}


@router.get("/roles/{role_id}")
def get_role(role_id: int, payload: dict = Depends(require_permission("roles", "read"))):
    with connection_lock():
        con = get_connection()
        row = con.execute(f"SELECT {ROLE_SELECT} FROM metadata.roles WHERE id = ?", [role_id]).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Role not found")
        d = _role_to_dict(row)
        perms = con.execute(
            "SELECT p.id, p.resource, p.action, p.description FROM metadata.permissions p "
            "JOIN metadata.role_permissions rp ON rp.permission_id = p.id WHERE rp.role_id = ? ORDER BY p.resource, p.action",
            [role_id],
        ).fetchall()
        d["permissions"] = [_perm_to_dict(p) for p in perms]
        cnt = con.execute("SELECT COUNT(*) FROM metadata.user_roles WHERE role_id = ?", [role_id]).fetchone()[0]
        d["member_count"] = cnt
        d["permissionsCount"] = len(perms)
        d["userCount"] = cnt
        return {"data": d}


@router.put("/roles/{role_id}")
def update_role(role_id: int, body: UpdateRoleRequest, payload: dict = Depends(require_permission("roles", "update"))):
    with connection_lock():
        con = get_connection()
        existing = con.execute("SELECT id, is_system FROM metadata.roles WHERE id = ?", [role_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Role not found")
        if existing[1] and (body.name is not None or body.permissions is not None):
            raise HTTPException(status_code=400, detail="System role name and permissions cannot be modified")
        fields = []
        params = []
        if body.name is not None:
            fields.append("name = ?")
            params.append(body.name)
        if body.description is not None:
            fields.append("description = ?")
            params.append(body.description)
        if fields:
            params.append(role_id)
            con.execute(f"UPDATE metadata.roles SET {', '.join(fields)} WHERE id = ?", params)
        if body.permissions is not None:
            con.execute("BEGIN TRANSACTION")
            try:
                con.execute("DELETE FROM metadata.role_permissions WHERE role_id = ?", [role_id])
                for perm_id in body.permissions:
                    con.execute(
                        "INSERT INTO metadata.role_permissions (id, role_id, permission_id) VALUES (?, ?, ?)",
                        [_next_id(con, "metadata.role_permissions"), role_id, perm_id],
                    )
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        row = con.execute(f"SELECT {ROLE_SELECT} FROM metadata.roles WHERE id = ?", [role_id]).fetchone()
        d = _role_to_dict(row)
        perms = con.execute(
            "SELECT p.id, p.resource, p.action, p.description FROM metadata.permissions p "
            "JOIN metadata.role_permissions rp ON rp.permission_id = p.id WHERE rp.role_id = ? ORDER BY p.resource, p.action",
            [role_id],
        ).fetchall()
        d["permissions"] = [_perm_to_dict(p) for p in perms]
        cnt = con.execute("SELECT COUNT(*) FROM metadata.user_roles WHERE role_id = ?", [role_id]).fetchone()[0]
        d["member_count"] = cnt
        d["permissionsCount"] = len(perms)
        d["userCount"] = cnt
    _audit(payload, "ROLE_UPDATE", "role", role_id, "update", body.model_dump(exclude_none=True))
    return {"data": d}


@router.delete("/roles/{role_id}")
def delete_role(role_id: int, payload: dict = Depends(require_permission("roles", "delete"))):
    with connection_lock():
        con = get_connection()
        row = con.execute("SELECT is_system FROM metadata.roles WHERE id = ?", [role_id]).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Role not found")
        if row[0]:
            raise HTTPException(status_code=400, detail="Cannot delete system role")
        member_count = con.execute("SELECT COUNT(*) FROM metadata.user_roles WHERE role_id = ?", [role_id]).fetchone()[0]
        if member_count:
            raise HTTPException(status_code=409, detail="Role is assigned to users")
        con.execute("DELETE FROM metadata.role_permissions WHERE role_id = ?", [role_id])
        con.execute("DELETE FROM metadata.roles WHERE id = ?", [role_id])
    _audit(payload, "ROLE_DELETE", "role", role_id, "delete")
    return {"data": {"success": True}}


# ── Permissions ──────────────────────────────────────────────────────


@router.get("/permissions")
def list_permissions(payload: dict = Depends(require_permission("permissions", "read"))):
    with connection_lock():
        con = get_connection()
        rows = con.execute(f"SELECT {PERMISSION_SELECT} FROM metadata.permissions ORDER BY resource, action").fetchall()
        return {"data": [_perm_to_dict(r) for r in rows]}


@router.put("/permissions/{permission_id}")
def update_permission(permission_id: int, body: UpdatePermissionRequest, payload: dict = Depends(require_permission("permissions", "update"))):
    with connection_lock():
        con = get_connection()
        existing = con.execute("SELECT id FROM metadata.permissions WHERE id = ?", [permission_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Permission not found")
        fields = []
        params = []
        for col, val in [("resource", body.resource), ("action", body.action), ("description", body.description)]:
            if val is not None:
                fields.append(f"{col} = ?")
                params.append(val)
        if fields:
            params.append(permission_id)
            con.execute(f"UPDATE metadata.permissions SET {', '.join(fields)} WHERE id = ?", params)
        row = con.execute(f"SELECT {PERMISSION_SELECT} FROM metadata.permissions WHERE id = ?", [permission_id]).fetchone()
        data = _perm_to_dict(row)
    _audit(payload, "PERMISSION_UPDATE", "permission", permission_id, "update", body.model_dump(exclude_none=True))
    return {"data": data}


@router.put("/roles/{role_id}/permissions")
def update_role_permissions(role_id: int, body: BatchUpdatePermissionsRequest, payload: dict = Depends(require_permission("roles", "manage"))):
    with connection_lock():
        con = get_connection()
        role = con.execute("SELECT id, is_system FROM metadata.roles WHERE id = ?", [role_id]).fetchone()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        if role[1]:
            raise HTTPException(status_code=400, detail="System role permissions cannot be modified")
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("DELETE FROM metadata.role_permissions WHERE role_id = ?", [role_id])
            for perm_id in body.permission_ids:
                con.execute(
                    "INSERT INTO metadata.role_permissions (id, role_id, permission_id) VALUES (?, ?, ?)",
                    [_next_id(con, "metadata.role_permissions"), role_id, perm_id],
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    _audit(payload, "ROLE_PERMISSIONS_UPDATE", "role", role_id, "update_permissions", {"permission_ids": body.permission_ids})
    return {"data": {"success": True}}


# ── Security Policies ────────────────────────────────────────────────


def _require_security_policy_project_access(payload: dict, action: str, project_id: int | None) -> None:
    if project_id is not None and project_id > 0:
        if not payload_has_permission(payload, "security_policies", action, project_id):
            raise HTTPException(status_code=403, detail="Permission denied for this project")


@router.get("/security-policies/rls")
def list_rls_policies(
    project_id: Optional[int] = Query(None),
    role_id: Optional[int] = Query(None),
    payload: dict = Depends(require_permission("security_policies", "read")),
):
    _require_security_policy_project_access(payload, "read", project_id)
    with connection_lock():
        con = get_connection()
        conditions = []
        params = []
        if project_id is not None:
            conditions.append("project_id = ?")
            params.append(project_id)
        if role_id is not None:
            conditions.append("role_id = ?")
            params.append(role_id)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = con.execute(f"SELECT {RLS_SELECT} FROM metadata.row_level_security_policies{where} ORDER BY id", params).fetchall()
        return {"data": [_rls_to_dict(row) for row in rows]}


@router.post("/security-policies/rls")
def create_rls_policy(body: RowSecurityPolicyCreate, payload: dict = Depends(require_permission("security_policies", "create"))):
    _require_security_policy_project_access(payload, "create", body.project_id)
    operator = normalize_operator(body.operator)
    value_source = body.value_source.lower()
    if value_source not in {"literal", "user_attribute"}:
        raise HTTPException(status_code=400, detail="value_source must be literal or user_attribute")
    if value_source == "literal" and body.value is None:
        raise HTTPException(status_code=400, detail="value is required for literal RLS policies")
    if value_source == "user_attribute" and not body.user_attribute:
        raise HTTPException(status_code=400, detail="user_attribute is required for user_attribute RLS policies")
    _safe_identifier(body.model_name)
    _safe_identifier(body.column_name)
    if body.user_attribute:
        _safe_identifier(body.user_attribute)
    filter_expression = f"{_safe_identifier(body.column_name)} {operator} {_safe_identifier(body.user_attribute) if value_source == 'user_attribute' else _sql_literal(body.value)}"
    with connection_lock():
        con = get_connection()
        policy_id = _next_id(con, "metadata.row_level_security_policies")
        con.execute(
            "INSERT INTO metadata.row_level_security_policies "
            "(id, project_id, role_id, model_name, column_name, operator, value, value_source, user_attribute, filter_expression, description, is_enabled) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                policy_id,
                body.project_id,
                body.role_id,
                body.model_name,
                body.column_name,
                operator,
                body.value,
                value_source,
                body.user_attribute,
                filter_expression,
                body.description,
                body.is_enabled,
            ],
        )
        row = con.execute(f"SELECT {RLS_SELECT} FROM metadata.row_level_security_policies WHERE id = ?", [policy_id]).fetchone()
        data = _rls_to_dict(row)
    _audit(payload, "RLS_POLICY_CREATE", "row_level_security_policy", policy_id, "create", data)
    return {"data": data}


_RLS_UPDATE_COLUMNS = frozenset({
    "role_id", "model_name", "column_name", "operator", "value",
    "value_source", "user_attribute", "filter_expression", "description", "is_enabled",
})


@router.put("/security-policies/rls/{policy_id}")
def update_rls_policy(policy_id: int, body: RowSecurityPolicyUpdate, payload: dict = Depends(require_permission("security_policies", "update"))):
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT project_id, column_name, operator, value, value_source, user_attribute FROM metadata.row_level_security_policies WHERE id = ?",
            [policy_id],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="RLS policy not found")
        _require_security_policy_project_access(payload, "update", row[0])
        existing_col, existing_op, existing_val, existing_vs, existing_ua = row[1], row[2], row[3], row[4], row[5]
        updates = body.model_dump(exclude_none=True)
        unexpected = set(updates) - _RLS_UPDATE_COLUMNS
        if unexpected:
            raise HTTPException(status_code=400, detail=f"Unexpected fields: {', '.join(sorted(unexpected))}")
        if "operator" in updates:
            updates["operator"] = normalize_operator(updates["operator"])
        if "value_source" in updates:
            updates["value_source"] = updates["value_source"].lower()
            if updates["value_source"] not in {"literal", "user_attribute"}:
                raise HTTPException(status_code=400, detail="value_source must be literal or user_attribute")
        effective_column = updates.get("column_name", existing_col)
        effective_op = updates.get("operator", existing_op)
        effective_val = updates.get("value", existing_val)
        effective_vs = updates.get("value_source", existing_vs)
        effective_ua = updates.get("user_attribute", existing_ua)
        if "model_name" in updates:
            _safe_identifier(updates["model_name"])
        _safe_identifier(effective_column)
        if effective_ua:
            _safe_identifier(effective_ua)
        updates["filter_expression"] = f"{_safe_identifier(effective_column)} {effective_op} {_safe_identifier(effective_ua) if effective_vs == 'user_attribute' else _sql_literal(effective_val)}"
        if effective_vs == "user_attribute" and not effective_ua:
            raise HTTPException(status_code=400, detail="user_attribute is required for user_attribute RLS policies")
        if effective_vs == "literal" and effective_val is None:
            raise HTTPException(status_code=400, detail="value is required for literal RLS policies")
        fields = []
        params = []
        for col, val in updates.items():
            fields.append(f"{col} = ?")
            params.append(val)
        if fields:
            params.append(policy_id)
            con.execute(f"UPDATE metadata.row_level_security_policies SET {', '.join(fields)} WHERE id = ?", params)
        row = con.execute(f"SELECT {RLS_SELECT} FROM metadata.row_level_security_policies WHERE id = ?", [policy_id]).fetchone()
        data = _rls_to_dict(row)
    _audit(payload, "RLS_POLICY_UPDATE", "row_level_security_policy", policy_id, "update", updates)
    return {"data": data}


@router.delete("/security-policies/rls/{policy_id}")
def delete_rls_policy(policy_id: int, payload: dict = Depends(require_permission("security_policies", "delete"))):
    with connection_lock():
        con = get_connection()
        existing = con.execute("SELECT project_id FROM metadata.row_level_security_policies WHERE id = ?", [policy_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="RLS policy not found")
        _require_security_policy_project_access(payload, "delete", existing[0])
        con.execute("DELETE FROM metadata.row_level_security_policies WHERE id = ?", [policy_id])
    _audit(payload, "RLS_POLICY_DELETE", "row_level_security_policy", policy_id, "delete")
    return {"data": {"success": True}}


@router.get("/security-policies/cls")
def list_cls_policies(
    project_id: Optional[int] = Query(None),
    role_id: Optional[int] = Query(None),
    payload: dict = Depends(require_permission("security_policies", "read")),
):
    _require_security_policy_project_access(payload, "read", project_id)
    with connection_lock():
        con = get_connection()
        conditions = []
        params = []
        if project_id is not None:
            conditions.append("project_id = ?")
            params.append(project_id)
        if role_id is not None:
            conditions.append("role_id = ?")
            params.append(role_id)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = con.execute(f"SELECT {CLS_SELECT} FROM metadata.column_level_security_policies{where} ORDER BY id", params).fetchall()
        return {"data": [_cls_to_dict(row) for row in rows]}


@router.post("/security-policies/cls")
def create_cls_policy(body: ColumnSecurityPolicyCreate, payload: dict = Depends(require_permission("security_policies", "create"))):
    _require_security_policy_project_access(payload, "create", body.project_id)
    _safe_identifier(body.model_name)
    _safe_identifier(body.column_name)
    access_type = normalize_access_type(body.access_type)
    with connection_lock():
        con = get_connection()
        policy_id = _next_id(con, "metadata.column_level_security_policies")
        con.execute(
            "INSERT INTO metadata.column_level_security_policies "
            "(id, project_id, role_id, model_name, column_name, access_type, mask_with, is_enabled) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [policy_id, body.project_id, body.role_id, body.model_name, body.column_name, access_type, body.mask_with, body.is_enabled],
        )
        row = con.execute(f"SELECT {CLS_SELECT} FROM metadata.column_level_security_policies WHERE id = ?", [policy_id]).fetchone()
        data = _cls_to_dict(row)
    _audit(payload, "CLS_POLICY_CREATE", "column_level_security_policy", policy_id, "create", data)
    return {"data": data}


_CLS_UPDATE_COLUMNS = frozenset({
    "role_id", "model_name", "column_name", "access_type", "mask_with", "is_enabled",
})


@router.put("/security-policies/cls/{policy_id}")
def update_cls_policy(policy_id: int, body: ColumnSecurityPolicyUpdate, payload: dict = Depends(require_permission("security_policies", "update"))):
    with connection_lock():
        con = get_connection()
        existing = con.execute("SELECT id, project_id FROM metadata.column_level_security_policies WHERE id = ?", [policy_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="CLS policy not found")
        _require_security_policy_project_access(payload, "update", existing[1])
        updates = body.model_dump(exclude_none=True)
        unexpected = set(updates) - _CLS_UPDATE_COLUMNS
        if unexpected:
            raise HTTPException(status_code=400, detail=f"Unexpected fields: {', '.join(sorted(unexpected))}")
        if "access_type" in updates:
            updates["access_type"] = normalize_access_type(updates["access_type"])
        if "model_name" in updates:
            _safe_identifier(updates["model_name"])
        if "column_name" in updates:
            _safe_identifier(updates["column_name"])
        fields = []
        params = []
        for col, val in updates.items():
            fields.append(f"{col} = ?")
            params.append(val)
        if fields:
            params.append(policy_id)
            con.execute(f"UPDATE metadata.column_level_security_policies SET {', '.join(fields)} WHERE id = ?", params)
        row = con.execute(f"SELECT {CLS_SELECT} FROM metadata.column_level_security_policies WHERE id = ?", [policy_id]).fetchone()
        data = _cls_to_dict(row)
    _audit(payload, "CLS_POLICY_UPDATE", "column_level_security_policy", policy_id, "update", updates)
    return {"data": data}


@router.delete("/security-policies/cls/{policy_id}")
def delete_cls_policy(policy_id: int, payload: dict = Depends(require_permission("security_policies", "delete"))):
    with connection_lock():
        con = get_connection()
        existing = con.execute("SELECT project_id FROM metadata.column_level_security_policies WHERE id = ?", [policy_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="CLS policy not found")
        _require_security_policy_project_access(payload, "delete", existing[0])
        con.execute("DELETE FROM metadata.column_level_security_policies WHERE id = ?", [policy_id])
    _audit(payload, "CLS_POLICY_DELETE", "column_level_security_policy", policy_id, "delete")
    return {"data": {"success": True}}


# ── Audit Logs ───────────────────────────────────────────────────────


AUDIT_SELECT = "id, user_id, event_type, resource_type, resource_id, action, detail, ip_address, user_agent, status, created_at"


def _audit_to_dict(row):
    detail = row[6]
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except Exception:
            pass
    return {
        "id": row[0],
        "user_id": row[1],
        "event_type": row[2],
        "resource_type": row[3],
        "resource_id": row[4],
        "action": row[5],
        "detail": detail,
        "ip_address": row[7],
        "user_agent": row[8],
        "status": row[9],
        "created_at": str(row[10]) if row[10] else None,
    }


@router.get("/audit-logs")
def list_audit_logs(
    event_type: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    payload: dict = Depends(require_permission("audit_logs", "read")),
):
    with connection_lock():
        con = get_connection()
        conditions = []
        params = []
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if from_:
            conditions.append("created_at >= ?::TIMESTAMP")
            params.append(from_)
        if to:
            conditions.append("created_at <= ?::TIMESTAMP")
            params.append(to)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        offset = (page - 1) * page_size
        count = con.execute(f"SELECT COUNT(*) FROM metadata.audit_logs{where}", params).fetchone()[0]
        rows = con.execute(
            f"SELECT {AUDIT_SELECT} FROM metadata.audit_logs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        return {"data": {"items": [_audit_to_dict(r) for r in rows], "total": count, "page": page, "page_size": page_size}}


@router.post("/audit-logs/export")
def export_audit_logs(body: AuditLogExportRequest, payload: dict = Depends(require_permission("audit_logs", "export"))):
    with connection_lock():
        con = get_connection()
        conditions = []
        params = []
        if body.event_type:
            conditions.append("event_type = ?")
            params.append(body.event_type)
        if body.user_id:
            conditions.append("user_id = ?")
            params.append(body.user_id)
        if body.from_date:
            conditions.append("created_at >= ?::TIMESTAMP")
            params.append(body.from_date)
        if body.to_date:
            conditions.append("created_at <= ?::TIMESTAMP")
            params.append(body.to_date)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = con.execute(
            f"SELECT {AUDIT_SELECT} FROM metadata.audit_logs{where} ORDER BY created_at DESC LIMIT 10000",
            params,
        ).fetchall()
        items = [_audit_to_dict(r) for r in rows]
    _audit(payload, "AUDIT_LOG_EXPORT", "audit_logs", body.format, "export", {"format": body.format, "count": len(items)})
    return {"data": items}


# ── SSO ──────────────────────────────────────────────────────────────


@router.get("/sso")
def get_sso_config(payload: dict = Depends(require_permission("sso", "read"))):
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT value FROM metadata.settings WHERE key = ?", ["sso_config"]
        ).fetchone()
        if row:
            config = _json_setting_value(row[0], {})
            if isinstance(config, dict):
                config.setdefault("enabled", False)
                if config.get("client_secret"):
                    config["client_secret"] = "********"
            return {"data": config}
        return {"data": {"provider": None, "client_id": None, "issuer_url": None, "mapping_rules": None, "enabled": False}}


@router.put("/sso")
def update_sso_config(body: SSOConfigUpdate, payload: dict = Depends(require_permission("sso", "update"))):
    with connection_lock():
        con = get_connection()
        previous = {}
        row = con.execute(
            "SELECT value FROM metadata.settings WHERE key = ?",
            ["sso_config"],
        ).fetchone()
        if row:
            previous = _json_setting_value(row[0], {})
            if not isinstance(previous, dict):
                previous = {}
        config = {
            "provider": body.provider if body.provider is not None else previous.get("provider", ""),
            "client_id": body.client_id if body.client_id is not None else previous.get("client_id", ""),
            "client_secret": previous.get("client_secret") if body.client_secret in (None, "********") else body.client_secret,
            "issuer_url": body.issuer_url if body.issuer_url is not None else previous.get("issuer_url", ""),
            "mapping_rules": body.mapping_rules if body.mapping_rules is not None else previous.get("mapping_rules"),
            "enabled": bool(body.enabled) if body.enabled is not None else previous.get("enabled", False),
        }
        if config["enabled"]:
            if not config.get("provider"):
                raise HTTPException(status_code=400, detail="provider is required when SSO is enabled")
            if not config.get("client_id"):
                raise HTTPException(status_code=400, detail="client_id is required when SSO is enabled")
            if not config.get("issuer_url"):
                raise HTTPException(status_code=400, detail="issuer_url is required when SSO is enabled")
        con.execute(
            "INSERT OR REPLACE INTO metadata.settings (key, value) VALUES (?, ?::JSON)",
            ["sso_config", json.dumps(encrypt_json(config))],
        )
    _audit(payload, "SSO_UPDATE", "sso", "config", "update", {"provider": config.get("provider"), "enabled": config.get("enabled")})
    safe_config = {**config}
    if safe_config.get("client_secret"):
        safe_config["client_secret"] = "********"
    return {"data": safe_config        }


# ── Backup / Restore ──────────────────────────────────────────────────


@router.get("/backups")
def list_backups(payload: dict = Depends(require_permission("backup", "read"))):
    from services.backup_service import list_backups as _list
    return {"data": _list()}


@router.post("/backups")
def create_backup(payload: dict = Depends(require_permission("backup", "create"))):
    from services.backup_service import create_backup as _create
    result = _create()
    _audit(payload, "BACKUP_CREATE", "backup", result["name"], "create", {"size": result.get("size")})
    return {"data": result}


@router.get("/backups/{name}")
def get_backup(name: str, payload: dict = Depends(require_permission("backup", "read"))):
    from services.backup_service import get_backup as _get
    try:
        result = _get(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="Backup not found")
    return {"data": result}


@router.get("/backups/{name}/download")
def download_backup(name: str, payload: dict = Depends(require_permission("backup", "download"))):
    from services.backup_service import download_backup as _download
    try:
        data = _download(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if data is None:
        raise HTTPException(status_code=404, detail="Backup not found")
    from fastapi.responses import Response
    return Response(content=data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{name}.zip"',
    })


class RestoreRequest(BaseModel):
    name: str


@router.post("/backups/restore")
def restore_backup(body: RestoreRequest, payload: dict = Depends(require_permission("backup", "restore"))):
    from services.backup_service import get_backup, download_backup, restore_backup as _restore
    try:
        info = get_backup(body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if info is None:
        raise HTTPException(status_code=404, detail="Backup not found")
    try:
        data = download_backup(body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if data is None:
        raise HTTPException(status_code=404, detail="Backup data not found")
    result = _restore(data)
    _audit(payload, "BACKUP_RESTORE", "backup", body.name, "restore", result)
    return {"data": result}


@router.delete("/backups/{name}")
def delete_backup(name: str, payload: dict = Depends(require_permission("backup", "delete"))):
    from services.backup_service import delete_backup as _delete
    try:
        deleted = _delete(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail="Backup not found")
    _audit(payload, "BACKUP_DELETE", "backup", name, "delete", {})
    return {"data": {"success": True}}


# ── System ───────────────────────────────────────────────────────────


@router.get("/system")
def system_info(payload: dict = Depends(require_permission("settings", "read"))):
    with connection_lock():
        con = get_connection()
        user_count = con.execute("SELECT COUNT(*) FROM metadata.users").fetchone()[0]
        project_count = con.execute("SELECT COUNT(*) FROM metadata.projects").fetchone()[0]
        role_count = con.execute("SELECT COUNT(*) FROM metadata.roles").fetchone()[0]
        dashboard_count = con.execute("SELECT COUNT(*) FROM metadata.dashboards").fetchone()[0]
        return {
            "data": {
                "version": "1.0.0",
                "user_count": user_count,
                "project_count": project_count,
                "role_count": role_count,
                "dashboard_count": dashboard_count,
            }
        }
