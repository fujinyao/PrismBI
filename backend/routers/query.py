from __future__ import annotations

import logging

import duckdb

from fastapi import APIRouter, Depends, Query

from models.schemas import DryPlanRequest, QueryRequest
from fastapi import HTTPException

from routers.auth import get_current_user, payload_has_permission
from services.ask_service import (
    execute_project_sql,
    get_execution_metrics_snapshot,
    get_route_dimension_metrics_snapshot,
)
from services.llm_service import get_llm_http_circuit_snapshot
from services.security_policy_service import plan_secured_sql

LOGGER = logging.getLogger(__name__)

router = APIRouter()


@router.get("/metrics", response_model=dict)
def execution_metrics(
    project_id: int = Query(..., description="Project ID for metrics scope"),
    include_route_dimensions: bool = Query(False, description="Include route-level dimensions and fallback metrics"),
    payload: dict = Depends(get_current_user),
):
    if project_id <= 0:
        raise HTTPException(status_code=400, detail="A real project is required")
    if not payload_has_permission(payload, "models", "read", project_id):
        raise HTTPException(status_code=403, detail="Permission denied")
    data = get_execution_metrics_snapshot(project_id=project_id)
    if include_route_dimensions:
        data = {
            "by_datasource": data,
            "route_dimensions": get_route_dimension_metrics_snapshot(project_id=project_id),
            "llm_http_circuit": get_llm_http_circuit_snapshot(),
        }
    return {"data": data}


@router.post("", response_model=dict)
def execute_query(body: QueryRequest, payload: dict = Depends(get_current_user)):
    if body.project_id <= 0:
        raise HTTPException(status_code=400, detail="A real project is required")
    if not payload_has_permission(payload, "models", "read", body.project_id):
        raise HTTPException(status_code=403, detail="Permission denied")
    try:
        if body.dry_run:
            data = plan_secured_sql(body.sql, body.project_id, int(payload["sub"]))
            return {"data": data}
        data = execute_project_sql(body.sql, body.project_id, int(payload["sub"]), body.limit)
    except ValueError as exc:
        LOGGER.warning("Query execution failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc) or "Query execution failed")
    except (duckdb.Error, Exception) as exc:
        LOGGER.warning("Query execution error: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc) or "Query execution failed")
    return {"data": data}


@router.post("/dry-plan", response_model=dict)
def dry_plan(body: DryPlanRequest, payload: dict = Depends(get_current_user)):
    if body.project_id <= 0:
        raise HTTPException(status_code=400, detail="A real project is required")
    if not payload_has_permission(payload, "models", "read", body.project_id):
        raise HTTPException(status_code=403, detail="Permission denied")
    try:
        data = plan_secured_sql(body.sql, body.project_id, int(payload["sub"]))
    except ValueError as exc:
        LOGGER.warning("Query planning failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc) or "Query planning failed")
    except (duckdb.Error, Exception) as exc:
        LOGGER.warning("Query planning error: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc) or "Query planning failed")
    return {"data": data}
