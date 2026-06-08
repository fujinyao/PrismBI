from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from db import connection_lock, get_connection
from models.schemas import (
    KnowledgeInstructionCreate,
    KnowledgeInstructionUpdate,
    KnowledgeSqlPairCreate,
    KnowledgeSqlPairUpdate,
)
from routers.auth import get_current_user, payload_has_permission
from services.sql_guard import validate_read_only_sql

router = APIRouter()

INSTRUCTION_COLS = "id, project_id, instruction, category, scope, priority, questions, is_default, created_at, updated_at"
SQLPAIR_COLS = "id, project_id, question, sql, description, category, scope, created_at, updated_at"


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _inst_row_to_dict(r):
    return {
        "id": r[0],
        "project_id": r[1],
        "text": r[2],
        "category": r[3],
        "scope": r[4],
        "priority": r[5] if isinstance(r[5], int) else 0,
        "questions": r[6] if isinstance(r[6], list) else [],
        "is_default": bool(r[7]),
        "created_at": str(r[8]) if r[8] else None,
        "updated_at": str(r[9]) if r[9] else None,
    }


def _sqlpair_row_to_dict(r):
    return {
        "id": r[0],
        "project_id": r[1],
        "question": r[2],
        "sql": r[3],
        "description": r[4],
        "category": r[5],
        "scope": r[6],
        "created_at": str(r[7]) if r[7] else None,
        "updated_at": str(r[8]) if r[8] else None,
    }


def _require_knowledge_permission(payload: dict, action: str, project_id: int) -> None:
    if project_id <= 0:
        raise HTTPException(status_code=400, detail="A real project is required")
    if not payload_has_permission(payload, "knowledge", action, project_id):
        raise HTTPException(status_code=403, detail="Permission denied")


@router.get("/instructions", response_model=dict)
def list_instructions(
    payload: dict = Depends(get_current_user),
    project_id: int = Query(None),
    search: str = Query(None),
    sort: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    with connection_lock():
        if project_id is not None:
            _require_knowledge_permission(payload, "read", project_id)
        elif not payload_has_permission(payload, "knowledge", "read"):
            raise HTTPException(status_code=403, detail="Permission denied")
        con = get_connection()
        conditions = []
        params = []
        if project_id is not None:
            conditions.append("project_id = ?")
            params.append(project_id)
        if search:
            conditions.append("(instruction ILIKE ? ESCAPE '\\' OR category ILIKE ? ESCAPE '\\' OR scope ILIKE ? ESCAPE '\\')")
            escaped = f"%{_escape_like(search)}%"
            params.extend([escaped, escaped, escaped])
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        count = con.execute(
            f"SELECT COUNT(*) FROM metadata.instructions{where}", params
        ).fetchone()
        count = count[0] if count else 0

        offset = (page - 1) * page_size
        order = "ORDER BY created_at DESC"
        if sort == "priority":
            order = "ORDER BY priority DESC, id ASC"
        elif sort == "created_at":
            order = "ORDER BY created_at DESC"
        elif sort == "updated_at":
            order = "ORDER BY updated_at DESC"
        elif search:
            order = "ORDER BY search_score DESC, updated_at DESC"

        if project_id is None:
            # Fetch all items without pagination, filter by permission, then paginate in Python
            if search:
                score_sql = "((CASE WHEN instruction ILIKE ? ESCAPE '\\' THEN 100 ELSE 0 END) + (CASE WHEN category ILIKE ? ESCAPE '\\' THEN 20 ELSE 0 END) + (CASE WHEN scope ILIKE ? ESCAPE '\\' THEN 10 ELSE 0 END)) AS search_score"
                escaped = f"%{_escape_like(search)}%"
                inner_params = [escaped, escaped, escaped] + params
                all_rows = con.execute(
                    f"SELECT {INSTRUCTION_COLS} FROM ("
                    f"SELECT {INSTRUCTION_COLS}, {score_sql} "
                    f"FROM metadata.instructions{where}"
                    f") {order} LIMIT 50000",
                    inner_params,
                ).fetchall()
            else:
                all_rows = con.execute(
                    f"SELECT {INSTRUCTION_COLS} "
                    f"FROM metadata.instructions{where} {order} LIMIT 50000",
                    params,
                ).fetchall()
            all_items = [_inst_row_to_dict(r) for r in all_rows]
            permitted = [item for item in all_items if payload_has_permission(payload, "knowledge", "read", item["project_id"])]
            count = len(permitted)
            items = permitted[offset:offset + page_size]
        else:
            if search:
                score_sql = "((CASE WHEN instruction ILIKE ? ESCAPE '\\' THEN 100 ELSE 0 END) + (CASE WHEN category ILIKE ? ESCAPE '\\' THEN 20 ELSE 0 END) + (CASE WHEN scope ILIKE ? ESCAPE '\\' THEN 10 ELSE 0 END)) AS search_score"
                escaped_like = f"%{_escape_like(search)}%"
                inner_params = [escaped_like, escaped_like, escaped_like] + params
                rows = con.execute(
                    f"SELECT {INSTRUCTION_COLS} FROM ("
                    f"SELECT {INSTRUCTION_COLS}, {score_sql} "
                    f"FROM metadata.instructions{where}"
                    f") {order} LIMIT ? OFFSET ?",
                    inner_params + [page_size, offset],
                ).fetchall()
            else:
                rows = con.execute(
                    f"SELECT {INSTRUCTION_COLS} "
                    f"FROM metadata.instructions{where} {order} LIMIT ? OFFSET ?",
                    params + [page_size, offset],
                ).fetchall()
            items = [_inst_row_to_dict(r) for r in rows]
    return {
        "data": {
            "items": items,
            "total": count,
            "page": page,
            "page_size": page_size,
        }
    }


@router.post("/instructions", response_model=dict)
def create_instruction(
    body: KnowledgeInstructionCreate,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        _require_knowledge_permission(payload, "create", body.project_id)
        con = get_connection()
        max_id = con.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.instructions"
        ).fetchone()
        max_id = max_id[0] if max_id else 1
        now = datetime.now(timezone.utc)
        is_default = body.priority is not None and body.priority > 0
        con.execute(
            "INSERT INTO metadata.instructions (id, project_id, instruction, category, scope, priority, questions, is_default, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?, ?)",
            [max_id, body.project_id, body.text, body.category, body.scope, body.priority, is_default, now, now],
        )
    return {
        "data": {
            "id": max_id,
            "project_id": body.project_id,
            "text": body.text,
            "category": body.category,
            "scope": body.scope,
            "priority": body.priority,
            "questions": [],
            "is_default": is_default,
            "created_at": str(now),
            "updated_at": str(now),
        }
    }


@router.get("/instructions/{instruction_id}", response_model=dict)
def get_instruction(
    instruction_id: int,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        con = get_connection()
        row = con.execute(
            f"SELECT {INSTRUCTION_COLS} FROM metadata.instructions WHERE id = ?",
            [instruction_id],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Instruction not found")
        _require_knowledge_permission(payload, "read", row[1])
    return {"data": _inst_row_to_dict(row)}


@router.put("/instructions/{instruction_id}", response_model=dict)
def update_instruction(
    instruction_id: int,
    body: KnowledgeInstructionUpdate,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT id, project_id FROM metadata.instructions WHERE id = ?", [instruction_id]
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Instruction not found")
        _require_knowledge_permission(payload, "update", existing[1])

        updates = []
        params = []
        for col, val in [("instruction", body.text), ("category", body.category), ("scope", body.scope), ("priority", body.priority)]:
            if val is not None:
                updates.append(f"{col} = ?")
                params.append(val)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        now = datetime.now(timezone.utc)
        updates.append("updated_at = ?")
        params.append(now)
        params.append(instruction_id)
        con.execute(
            f"UPDATE metadata.instructions SET {', '.join(updates)} WHERE id = ?",
            params,
        )

        row = con.execute(
            f"SELECT {INSTRUCTION_COLS} FROM metadata.instructions WHERE id = ?",
            [instruction_id],
        ).fetchone()
    return {"data": _inst_row_to_dict(row)}


@router.delete("/instructions/{instruction_id}", response_model=dict)
def delete_instruction(
    instruction_id: int,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT id, project_id FROM metadata.instructions WHERE id = ?", [instruction_id]
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Instruction not found")
        _require_knowledge_permission(payload, "delete", existing[1])
        con.execute("DELETE FROM metadata.instructions WHERE id = ?", [instruction_id])
    return {"data": {"success": True}}


@router.get("/sql-pairs", response_model=dict)
def list_sql_pairs(
    payload: dict = Depends(get_current_user),
    project_id: int = Query(None),
    search: str = Query(None),
    sort: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    with connection_lock():
        if project_id is not None:
            _require_knowledge_permission(payload, "read", project_id)
        elif not payload_has_permission(payload, "knowledge", "read"):
            raise HTTPException(status_code=403, detail="Permission denied")
        con = get_connection()
        conditions = []
        params = []
        if project_id is not None:
            conditions.append("project_id = ?")
            params.append(project_id)
        if search:
            conditions.append("(question ILIKE ? ESCAPE '\\' OR sql ILIKE ? ESCAPE '\\' OR description ILIKE ? ESCAPE '\\' OR category ILIKE ? ESCAPE '\\' OR scope ILIKE ? ESCAPE '\\')")
            escaped_like = f"%{_escape_like(search)}%"
            params.extend([escaped_like, escaped_like, escaped_like, escaped_like, escaped_like])
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        count = con.execute(
            f"SELECT COUNT(*) FROM metadata.sql_pairs{where}", params
        ).fetchone()
        count = count[0] if count else 0

        order = "ORDER BY created_at DESC"
        if sort == "question":
            order = "ORDER BY question ASC"
        elif sort == "created_at":
            order = "ORDER BY created_at DESC"
        elif sort == "updated_at":
            order = "ORDER BY updated_at DESC"
        elif search:
            order = "ORDER BY search_score DESC, updated_at DESC"

        all_rows = con.execute(
            f"SELECT {SQLPAIR_COLS} "
            f"FROM metadata.sql_pairs{where} {order} LIMIT 50000",
            params,
        ).fetchall()

        all_items = [_sqlpair_row_to_dict(r) for r in all_rows]
        if project_id is None:
            all_items = [item for item in all_items if payload_has_permission(payload, "knowledge", "read", item["project_id"])]
        count = len(all_items)
        offset = (page - 1) * page_size
        items = all_items[offset:offset + page_size]
    return {
        "data": {
            "items": items,
            "total": count,
            "page": page,
            "page_size": page_size,
        }
    }


@router.post("/sql-pairs", response_model=dict)
def create_sql_pair(
    body: KnowledgeSqlPairCreate,
    payload: dict = Depends(get_current_user),
):
    normalized_sql = body.sql
    try:
        normalized_sql = validate_read_only_sql(body.sql)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with connection_lock():
        _require_knowledge_permission(payload, "create", body.project_id)
        con = get_connection()
        max_id = con.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.sql_pairs"
        ).fetchone()
        max_id = max_id[0] if max_id else 1
        now = datetime.now(timezone.utc)
        con.execute(
            "INSERT INTO metadata.sql_pairs (id, project_id, question, sql, description, category, scope, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [max_id, body.project_id, body.question, normalized_sql, body.description, body.category, body.scope, now, now],
        )
    return {
        "data": {
            "id": max_id,
            "project_id": body.project_id,
            "question": body.question,
            "sql": normalized_sql,
            "description": body.description,
            "category": body.category,
            "scope": body.scope,
            "created_at": str(now),
            "updated_at": str(now),
        }
    }


@router.get("/sql-pairs/{pair_id}", response_model=dict)
def get_sql_pair(
    pair_id: int,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        con = get_connection()
        row = con.execute(
            f"SELECT {SQLPAIR_COLS} FROM metadata.sql_pairs WHERE id = ?",
            [pair_id],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="SQL pair not found")
        _require_knowledge_permission(payload, "read", row[1])
    return {"data": _sqlpair_row_to_dict(row)}


@router.put("/sql-pairs/{pair_id}", response_model=dict)
def update_sql_pair(
    pair_id: int,
    body: KnowledgeSqlPairUpdate,
    payload: dict = Depends(get_current_user),
):
    normalized_sql = body.sql
    if body.sql is not None:
        try:
            normalized_sql = validate_read_only_sql(body.sql)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT id, project_id FROM metadata.sql_pairs WHERE id = ?", [pair_id]
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="SQL pair not found")
        _require_knowledge_permission(payload, "update", existing[1])

        updates = []
        params = []
        for col, val in [("question", body.question), ("sql", normalized_sql), ("description", body.description), ("category", body.category), ("scope", body.scope)]:
            if val is not None:
                updates.append(f"{col} = ?")
                params.append(val)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        now = datetime.now(timezone.utc)
        updates.append("updated_at = ?")
        params.append(now)
        params.append(pair_id)
        con.execute(
            f"UPDATE metadata.sql_pairs SET {', '.join(updates)} WHERE id = ?",
            params,
        )

        row = con.execute(
            f"SELECT {SQLPAIR_COLS} FROM metadata.sql_pairs WHERE id = ?",
            [pair_id],
        ).fetchone()
    return {"data": _sqlpair_row_to_dict(row)}


@router.delete("/sql-pairs/{pair_id}", response_model=dict)
def delete_sql_pair(
    pair_id: int,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT id, project_id FROM metadata.sql_pairs WHERE id = ?", [pair_id]
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="SQL pair not found")
        _require_knowledge_permission(payload, "delete", existing[1])
        con.execute("DELETE FROM metadata.sql_pairs WHERE id = ?", [pair_id])
    return {"data": {"success": True}}
