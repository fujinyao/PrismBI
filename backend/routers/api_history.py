from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query

from db import connection_lock, get_connection
from routers.auth import require_permission

router = APIRouter()


def _json_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


@router.get("", response_model=dict)
def list_api_history(
    search: Optional[str] = Query(None),
    method: Optional[str] = Query(None),
    status_code: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    payload: dict = Depends(require_permission("audit_logs", "read")),
):
    conditions = []
    params = []
    if search:
        conditions.append("(CAST(headers AS VARCHAR) ILIKE ? OR api_type ILIKE ?) ESCAPE '\\'")
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.extend([f"%{escaped}%", f"%{escaped}%"])
    if method:
        conditions.append("api_type = ?")
        params.append(method.upper())
    if status_code is not None:
        conditions.append("status_code = ?")
        params.append(status_code)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * page_size
    with connection_lock():
        con = get_connection()
        total = con.execute(f"SELECT COUNT(*) FROM metadata.api_history{where}", params).fetchone()[0]
        rows = con.execute(
            "SELECT id, project_id, api_type, thread_id, headers, request_payload, response_payload, status_code, duration_ms, created_at "
            f"FROM metadata.api_history{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
    items = []
    for row in rows:
        headers = _json_value(row[4]) or {}
        items.append({
            "id": row[0],
            "project_id": row[1],
            "method": row[2],
            "api_type": row[2],
            "thread_id": row[3],
            "path": headers.get("path") if isinstance(headers, dict) else None,
            "query": headers.get("query") if isinstance(headers, dict) else None,
            "headers": headers,
            "request_payload": _json_value(row[5]),
            "response_payload": _json_value(row[6]),
            "status_code": row[7],
            "duration_ms": row[8],
            "created_at": str(row[9]) if row[9] else None,
        })
    return {"data": {"items": items, "total": total, "page": page, "page_size": page_size}}
