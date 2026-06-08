from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from db import connection_lock, get_connection
from routers.auth import get_current_user, payload_has_permission
from services.ask_service import (
    ask_question as run_ask_question,
    get_thread_project_id,
    list_thread_responses,
    normalize_preview_row_limit as normalize_preview_row_limit_runtime,
)

LOGGER = logging.getLogger(__name__)
router = APIRouter()


class CreateThreadRequest(BaseModel):
    project_id: Optional[int] = None
    summary: str = ""
    preview_row_limit: int = 20


class UpdateThreadRequest(BaseModel):
    summary: Optional[str] = None


class CreateResponseRequest(BaseModel):
    question: str
    sql: Optional[str] = None
    asking_task_id: Optional[str] = None


def _normalize_preview_row_limit(value: Optional[int]) -> int:
    return normalize_preview_row_limit_runtime(value)


def _thread_to_dict(row, responses=None, response_count: Optional[int] = None):
    data = {
        "id": row[0],
        "project_id": row[1],
        "summary": row[2],
        "user_id": row[3],
        "created_at": str(row[4]) if row[4] else None,
        "updated_at": str(row[5]) if row[5] else None,
        "preview_row_limit": row[6],
        "summary_manual": bool(row[7]),
    }
    if response_count is not None:
        data["response_count"] = response_count
    if responses is not None:
        data["responses"] = responses
    return data


@router.get("/threads")
def list_threads(
    search: Optional[str] = None,
    project_id: Optional[int] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        con = get_connection()
        conditions = []
        params = []
        conditions.append("user_id = ?")
        params.append(int(payload["sub"]))
        if search:
            conditions.append("summary LIKE ? ESCAPE '\\'")
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{escaped}%")
        if project_id is not None:
            if project_id <= 0:
                return {"data": {"items": [], "total": 0, "page": page, "page_size": page_size}}
            if not payload_has_permission(payload, "projects", "read", project_id):
                raise HTTPException(status_code=403, detail="Permission denied")
            conditions.append("project_id = ?")
            params.append(project_id)
        where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        offset = (page - 1) * page_size
        count_row = con.execute(
            f"SELECT COUNT(*) FROM metadata.threads{where_clause}", params
        ).fetchone()
        rows = con.execute(
            f"SELECT id, project_id, summary, user_id, created_at, updated_at, preview_row_limit, COALESCE(summary_manual, false), (SELECT COUNT(*) FROM metadata.thread_responses tr WHERE tr.thread_id = metadata.threads.id) AS response_count FROM metadata.threads{where_clause} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
    total = count_row[0] if count_row else 0
    items = [_thread_to_dict(r[:8], response_count=r[8]) for r in rows]
    return {"data": {"items": items, "total": total, "page": page, "page_size": page_size}}


@router.post("/threads")
def create_thread(body: CreateThreadRequest, payload: dict = Depends(get_current_user)):
    with connection_lock():
        con = get_connection()
        con.execute("INSERT INTO metadata.id_sequences VALUES ('metadata.threads', COALESCE((SELECT MAX(id) FROM metadata.threads), 0)) ON CONFLICT DO NOTHING")
        existing = con.execute("SELECT next_id FROM metadata.id_sequences WHERE table_name = 'metadata.threads'").fetchone()
        if existing and existing[0] <= 1:
            max_existing = con.execute("SELECT COALESCE(MAX(id), 0) FROM metadata.threads").fetchone()[0]
            if max_existing > 0:
                con.execute("UPDATE metadata.id_sequences SET next_id = ? WHERE table_name = 'metadata.threads'", [max_existing])
        max_id = con.execute("UPDATE metadata.id_sequences SET next_id = next_id + 1 WHERE table_name = 'metadata.threads' RETURNING next_id").fetchone()[0]
        project_id = body.project_id
        if project_id is None:
            row = con.execute("SELECT default_project_id FROM metadata.users WHERE id = ?", [int(payload["sub"])]).fetchone()
            project_id = row[0] if row and row[0] is not None else None
        if not project_id or project_id <= 0:
            raise HTTPException(status_code=400, detail="A real project is required to create a persistent thread")
        if not con.execute("SELECT id FROM metadata.projects WHERE id = ?", [project_id]).fetchone():
            raise HTTPException(status_code=404, detail="Project not found")
        if not payload_has_permission(payload, "projects", "read", project_id):
            raise HTTPException(status_code=403, detail="Permission denied")
        preview_row_limit = _normalize_preview_row_limit(body.preview_row_limit)
        summary = (body.summary or "").strip() or "New Conversation"
        summary_manual = bool((body.summary or "").strip())
        con.execute(
            "INSERT INTO metadata.threads (id, project_id, summary, summary_manual, user_id, preview_row_limit) VALUES (?, ?, ?, ?, ?, ?)",
            [max_id, project_id, summary[:128], summary_manual, int(payload["sub"]), preview_row_limit],
        )
    return {"data": {"id": max_id, "preview_row_limit": preview_row_limit}}


@router.get("/threads/{id}")
def get_thread(id: int, payload: dict = Depends(get_current_user)):
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT id, project_id, summary, user_id, created_at, updated_at, preview_row_limit, COALESCE(summary_manual, false) FROM metadata.threads WHERE id = ? AND user_id = ?",
            [id, int(payload["sub"])],
        ).fetchone()
        responses = list_thread_responses(id, int(payload["sub"])) if row else []
    if row is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"data": _thread_to_dict(row, responses=responses)}


@router.put("/threads/{id}")
def update_thread(id: int, body: UpdateThreadRequest, payload: dict = Depends(get_current_user)):
    summary = (body.summary or "").strip()
    if not summary:
        summary = "Untitled"
    with connection_lock():
        con = get_connection()
        row = con.execute("SELECT id FROM metadata.threads WHERE id = ? AND user_id = ?", [id, int(payload["sub"])]).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Thread not found")
        con.execute(
            "UPDATE metadata.threads SET summary = ?, summary_manual = true, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            [summary[:128], id, int(payload["sub"])],
        )
        updated = con.execute(
            "SELECT id, project_id, summary, user_id, created_at, updated_at, preview_row_limit, COALESCE(summary_manual, false) FROM metadata.threads WHERE id = ? AND user_id = ?",
            [id, int(payload["sub"])],
        ).fetchone()
    return {"data": _thread_to_dict(updated, responses=list_thread_responses(id, int(payload["sub"])))}


@router.delete("/threads/{id}")
def delete_thread(id: int, payload: dict = Depends(get_current_user)):
    with connection_lock():
        con = get_connection()
        row = con.execute("SELECT id FROM metadata.threads WHERE id = ? AND user_id = ?", [id, int(payload["sub"])]).fetchone()
        if row:
            con.execute("DELETE FROM metadata.thread_responses WHERE thread_id = ?", [id])
            con.execute("DELETE FROM metadata.api_history WHERE thread_id = ?", [id])
            con.execute("DELETE FROM metadata.threads WHERE id = ? AND user_id = ?", [id, int(payload["sub"])])
    return {"data": {"success": True}}


@router.post("/threads/{id}/responses")
def create_response(id: int, body: CreateResponseRequest, payload: dict = Depends(get_current_user)):
    project_id = get_thread_project_id(id, int(payload["sub"]))
    if project_id is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    if project_id and not payload_has_permission(payload, "models", "read", project_id):
        raise HTTPException(status_code=403, detail="Permission denied")
    try:
        result = run_ask_question(body.question, int(payload["sub"]), id, None)
    except Exception as exc:
        LOGGER.exception("Ask question failed for thread %s", id, extra={"thread_id": id, "user_id": int(payload["sub"])})
        raise HTTPException(status_code=500, detail="Internal error processing question") from exc
    return {"data": result["response"]}


from services.ask_service import _response_row

@router.get("/threads/{id}/responses")
def list_responses(id: int, payload: dict = Depends(get_current_user)):
    with connection_lock():
        con = get_connection()
        thread = con.execute("SELECT id FROM metadata.threads WHERE id = ? AND user_id = ?", [id, int(payload["sub"])]).fetchone()
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        rows = con.execute(
            "SELECT id, thread_id, user_id, question, sql, asking_task_id, breakdown_detail, answer_detail, chart_detail, adjustment, created_at FROM metadata.thread_responses WHERE thread_id = ? ORDER BY created_at ASC",
            [id],
        ).fetchall()
    items = [_response_row(r) for r in rows]
    return {"data": items}


@router.delete("/responses")
def cleanup_responses(
    before: Optional[str] = None,
    project_id: Optional[int] = None,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        if not payload_has_permission(payload, "admin", "manage"):
            raise HTTPException(status_code=403, detail="Permission denied")
        con = get_connection()
        conditions = []
        params = []
        if project_id is not None:
            conditions.append("thread_id IN (SELECT id FROM metadata.threads WHERE project_id = ?)")
            params.append(project_id)
        if before:
            try:
                con.execute("SELECT ?::TIMESTAMP", [before])
            except Exception:
                raise HTTPException(status_code=400, detail=f"Invalid timestamp format: {before}")
            conditions.append("created_at < ?::TIMESTAMP")
            params.append(before)
        if conditions:
            con.execute(
                f"DELETE FROM metadata.thread_responses WHERE {' AND '.join(conditions)}",
                params,
            )
    return {"data": {"success": True}}


@router.delete("/history")
def cleanup_history(
    before: Optional[str] = None,
    status_code: Optional[int] = None,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        if not payload_has_permission(payload, "admin", "manage"):
            raise HTTPException(status_code=403, detail="Permission denied")
        con = get_connection()
        conditions = []
        params = []
        if before:
            try:
                con.execute("SELECT ?::TIMESTAMP", [before])
            except Exception:
                raise HTTPException(status_code=400, detail=f"Invalid timestamp format: {before}")
            conditions.append("created_at < ?::TIMESTAMP")
            params.append(before)
        if status_code is not None:
            conditions.append("status_code = ?")
            params.append(status_code)
        if conditions:
            con.execute(
                f"DELETE FROM metadata.api_history WHERE {' AND '.join(conditions)}",
                params,
            )
    return {"data": {"success": True}}
