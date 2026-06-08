from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from models.schemas import MemoryForgetRequest, MemoryStoreRequest
from routers.auth import get_current_user, payload_has_permission
from services.memory_service import MemoryService

router = APIRouter()

_svc = MemoryService()


def _user_id(payload: dict) -> int:
    return int(payload.get("sub") or 0)


@router.get("/chart", response_model=dict)
def get_chart(payload: dict = Depends(get_current_user), question: str = None, sql: str = None, sample_size: int = None):
    raise HTTPException(status_code=501, detail="Chart export is not implemented")


@router.get("/memories/search", response_model=dict)
def search_memories(payload: dict = Depends(get_current_user), query: str = None, type: str = None, project_id: Optional[int] = None):
    if not query:
        return {"data": []}
    if project_id and not payload_has_permission(payload, "models", "read", project_id):
        return {"data": []}
    results = _svc.search(query, type=type, limit=10, user_id=_user_id(payload), project_id=project_id)
    return {"data": results}


@router.post("/memories/store", response_model=dict)
def store_memory(body: MemoryStoreRequest, payload: dict = Depends(get_current_user)):
    if body.project_id and not payload_has_permission(payload, "models", "create", body.project_id):
        raise HTTPException(status_code=403, detail="No access to project")
    memory_id = _svc.store(body.type, body.content, user_id=_user_id(payload), project_id=body.project_id)
    return {"data": {"id": memory_id}}


@router.get("/memories/list", response_model=dict)
def list_memories(payload: dict = Depends(get_current_user), type: str = None, project_id: Optional[int] = None):
    if project_id and not payload_has_permission(payload, "models", "read", project_id):
        return {"data": []}
    results = _svc.list(type=type, user_id=_user_id(payload), project_id=project_id)
    return {"data": results}


@router.post("/memories/forget", response_model=dict)
def forget_memory(body: MemoryForgetRequest, payload: dict = Depends(get_current_user)):
    success = _svc.forget(body.id, user_id=_user_id(payload))
    return {"data": {"success": success}}