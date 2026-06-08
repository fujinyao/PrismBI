from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

import threading

from db import connection_lock, get_connection
from models.schemas import (
    CalculatedFieldCreate,
    CalculatedFieldUpdate,
    ColumnDef,
    ModelCreate,
    ModelUpdate,
    RelationCreate,
    RelationUpdate,
    ViewCreate,
    ViewUpdate,
)
from routers.auth import get_current_user, payload_has_permission
from services.ask_service import clear_analysis_cache
from services.security_policy_service import _safe_identifier
from services.sql_guard import validate_read_only_sql

router = APIRouter()
_tables_ensured = False
_tables_lock = threading.Lock()


def _ensure_tables():
    global _tables_ensured
    with _tables_lock:
        if _tables_ensured:
            return
        con = get_connection()
        con.execute("""
            CREATE TABLE IF NOT EXISTS metadata.modeling_diagrams (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
                name VARCHAR NOT NULL DEFAULT 'default',
                layout JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(project_id, name)
            )
        """)
        _tables_ensured = True


def _require_model_permission(payload: dict, action: str, project_id: int) -> None:
    if project_id <= 0:
        raise HTTPException(status_code=400, detail="A real project is required")
    if not payload_has_permission(payload, "models", action, project_id):
        raise HTTPException(status_code=403, detail="Permission denied")


def _require_model_in_project(con, model_id: int, project_id: int) -> None:
    row = con.execute(
        "SELECT id FROM metadata.models WHERE id = ? AND project_id = ?",
        [model_id, project_id],
    ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Model does not belong to this project")


def _require_binding_in_project(con, binding_id: int | None, project_id: int) -> None:
    if binding_id is None:
        # Check if project has any datasource bindings
        row = con.execute(
            "SELECT id FROM metadata.project_datasources WHERE project_id = ? LIMIT 1",
            [project_id],
        ).fetchone()
        if row:
            raise HTTPException(
                status_code=400,
                detail="source_binding_id is required when creating models. "
                       "Please select a datasource binding from the project settings.",
            )
        return
    row = con.execute(
        "SELECT id FROM metadata.project_datasources WHERE id = ? AND project_id = ?",
        [binding_id, project_id],
    ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Datasource binding does not belong to this project")


MODEL_SELECT = """
    SELECT id, project_id, name, display_name, description, table_reference, model_type, source_binding_id,
           column_defs, relation_defs, created_at, updated_at
    FROM metadata.models
"""

VIEW_SELECT = """
    SELECT id, project_id, name, display_name, description, model_id, column_defs, sql, source_response_id, created_at, updated_at
    FROM metadata.views
"""

RELATION_SELECT = """
    SELECT id, project_id, name, description, source_model_id, source_column, target_model_id,
           target_column, relation_type, created_at, updated_at
    FROM metadata.relations
"""

CALCULATED_FIELD_SELECT = """
    SELECT id, project_id, name, display_name, description, model_id, expression, result_type,
           created_at, updated_at
    FROM metadata.calculated_fields
"""


def _normalize_column_defs(column_defs):
    if isinstance(column_defs, str):
        try:
            column_defs = json.loads(column_defs)
        except Exception:
            column_defs = []
    if not isinstance(column_defs, list):
        return []
    return column_defs


def _normalize_model_type(value: str | None) -> str:
    raw = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    compact = "".join(raw.split())
    if not raw:
        return "table"
    if "materialized" in raw and "view" in raw:
        return "materialized_view"
    if compact in {"materializedview", "matview", "mview"}:
        return "materialized_view"
    if "view" in raw:
        return "view"
    if compact in {"table", "basetable"}:
        return "table"
    if compact in {
        "foreigntable",
        "externaltable",
        "temporarytable",
        "localtemporary",
        "localtemporarytable",
        "temptable",
    }:
        return "other"
    if "table" in raw:
        return "other"
    return "other"


def _model_row(row):
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to create model")
    raw_defs = _normalize_column_defs(row[8])
    column_defs = [ColumnDef(**c) for c in raw_defs if isinstance(c, dict) and c.get("name")]

    fields = [
        {
            "name": c.name,
            "type": c.type if c.type != "UNKNOWN" else "UNKNOWN",
            "display_name": c.display_name,
            "description": c.description,
            "primaryKey": c.is_primary_key,
            "isPrimaryKey": c.is_primary_key,
        }
        for c in column_defs
    ]

    return {
        "id": row[0],
        "project_id": row[1],
        "name": row[2],
        "display_name": row[3],
        "description": row[4],
        "table_reference": row[5],
        "model_type": _normalize_model_type(row[6]),
        "source_binding_id": row[7],
        "column_defs": [c.model_dump() for c in column_defs],
        "fields": fields,
        "relation_defs": row[9],
        "created_at": str(row[10]) if row[10] else None,
        "updated_at": str(row[11]) if row[11] else None,
    }


def _view_row(row):
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to create view")
    raw_defs = _normalize_column_defs(row[6])
    column_defs = [ColumnDef(**c) for c in raw_defs if isinstance(c, dict) and c.get("name")]
    return {
        "id": row[0],
        "project_id": row[1],
        "name": row[2],
        "display_name": row[3],
        "description": row[4],
        "model_id": row[5],
        "column_defs": [c.model_dump() for c in column_defs],
        "sql": row[7],
        "source_response_id": row[8],
        "fields": [
            {
                "name": c.name,
                "type": c.type,
                "display_name": c.display_name,
                "description": c.description,
                "primaryKey": c.is_primary_key,
                "isPrimaryKey": c.is_primary_key,
            }
            for c in column_defs
        ],
        "created_at": str(row[9]) if row[9] else None,
        "updated_at": str(row[10]) if row[10] else None,
    }


def _response_in_project(con, response_id: int, project_id: int):
    return con.execute(
        """SELECT tr.id, tr.question, tr.sql, tr.answer_detail
           FROM metadata.thread_responses tr
           JOIN metadata.threads t ON t.id = tr.thread_id
           WHERE tr.id = ? AND t.project_id = ?""",
        [response_id, project_id],
    ).fetchone()


def _default_model_id(con, project_id: int) -> int | None:
    row = con.execute("SELECT id FROM metadata.models WHERE project_id = ? ORDER BY id LIMIT 1", [project_id]).fetchone()
    return int(row[0]) if row else None


def _relation_row(row):
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to create relation")
    return {
        "id": row[0],
        "project_id": row[1],
        "name": row[2],
        "description": row[3],
        "source_model_id": row[4],
        "source_column": row[5],
        "target_model_id": row[6],
        "target_column": row[7],
        "relation_type": row[8],
        "created_at": str(row[9]) if row[9] else None,
        "updated_at": str(row[10]) if row[10] else None,
    }


def _cf_row(row):
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to create calculated field")
    return {
        "id": row[0],
        "project_id": row[1],
        "name": row[2],
        "display_name": row[3],
        "description": row[4],
        "model_id": row[5],
        "expression": row[6],
        "result_type": row[7],
        "created_at": str(row[8]) if row[8] else None,
        "updated_at": str(row[9]) if row[9] else None,
    }


# ── Diagram ──────────────────────────────────────────────────────────

@router.get("/{project_id:int}/diagram")
def get_diagram(
    project_id: int,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "read", project_id)
    _ensure_tables()
    with connection_lock():
        con = get_connection()
        models = [_model_row(r) for r in con.execute(
            f"{MODEL_SELECT} WHERE project_id = ? ORDER BY id", [project_id]
        ).fetchall()]
        views = [_view_row(r) for r in con.execute(
            f"{VIEW_SELECT} WHERE project_id = ? ORDER BY id", [project_id]
        ).fetchall()]
        relations = [_relation_row(r) for r in con.execute(
            f"{RELATION_SELECT} WHERE project_id = ? ORDER BY id", [project_id]
        ).fetchall()]
        calculated_fields = [_cf_row(r) for r in con.execute(
            f"{CALCULATED_FIELD_SELECT} WHERE project_id = ? ORDER BY id", [project_id]
        ).fetchall()]
    return {
        "data": {
            "models": models,
            "views": views,
            "relations": relations,
            "calculated_fields": calculated_fields,
        }
    }


@router.put("/{project_id:int}/diagram")
def update_diagram(
    project_id: int,
    body: dict,
    payload: dict = Depends(get_current_user),
):
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object")
    _require_model_permission(payload, "update", project_id)
    _ensure_tables()
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT id FROM metadata.modeling_diagrams WHERE project_id = ? AND name = 'default'",
            [project_id],
        ).fetchone()
        if existing:
            con.execute(
                "UPDATE metadata.modeling_diagrams SET layout = ?::JSON, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [json.dumps(body), existing[0]],
            )
        else:
            max_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.modeling_diagrams").fetchone()[0]
            con.execute(
                "INSERT INTO metadata.modeling_diagrams (id, project_id, name, layout) VALUES (?, ?, 'default', ?::JSON)",
                [max_id, project_id, json.dumps(body)],
            )
    clear_analysis_cache(project_id)
    return {"data": body}


# ── Models ───────────────────────────────────────────────────────────

@router.get("/{project_id:int}/models")
def list_models(
    project_id: int,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "read", project_id)
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            f"{MODEL_SELECT} WHERE project_id = ? ORDER BY id", [project_id]
        ).fetchall()
    return {"data": [_model_row(r) for r in rows]}


@router.post("/{project_id:int}/models")
def create_model(
    project_id: int,
    body: ModelCreate,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "create", project_id)
    with connection_lock():
        con = get_connection()
        _require_binding_in_project(con, body.source_binding_id, project_id)
        max_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.models").fetchone()[0]
        con.execute(
            "INSERT INTO metadata.models (id, project_id, name, display_name, description, table_reference, model_type, source_binding_id, column_defs) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?::JSON)",
            [
                max_id,
                project_id,
                body.name,
                body.display_name,
                body.description,
                body.table_reference,
                _normalize_model_type(body.model_type),
                body.source_binding_id,
                json.dumps([c.model_dump() for c in body.columns]) if body.columns else "[]",
            ],
        )
        row = con.execute(f"{MODEL_SELECT} WHERE id = ?", [max_id]).fetchone()
    clear_analysis_cache(project_id)
    return {"data": _model_row(row)}


@router.get("/{project_id:int}/models/{model_id:int}")
def get_model(
    project_id: int,
    model_id: int,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "read", project_id)
    with connection_lock():
        con = get_connection()
        row = con.execute(
            f"{MODEL_SELECT} WHERE id = ? AND project_id = ?",
            [model_id, project_id],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Model not found")
        data = _model_row(row)
    return {"data": data}


@router.put("/{project_id:int}/models/{model_id:int}")
def update_model(
    project_id: int,
    model_id: int,
    body: ModelUpdate,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "update", project_id)
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT id FROM metadata.models WHERE id = ? AND project_id = ?",
            [model_id, project_id],
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Model not found")
        sets = []
        params = []
        for col in ("name", "display_name", "description", "table_reference"):
            val = getattr(body, col, None)
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)
        if body.model_type is not None:
            sets.append("model_type = ?")
            params.append(_normalize_model_type(body.model_type))
        if body.source_binding_id is not None:
            _require_binding_in_project(con, body.source_binding_id, project_id)
            sets.append("source_binding_id = ?")
            params.append(body.source_binding_id)
        if body.columns is not None:
            sets.append("column_defs = ?::JSON")
            params.append(json.dumps([c.model_dump() for c in body.columns]))
        if sets:
            sets.append("updated_at = CURRENT_TIMESTAMP")
            params.append(model_id)
            con.execute(f"UPDATE metadata.models SET {', '.join(sets)} WHERE id = ?", params)
        row = con.execute(f"{MODEL_SELECT} WHERE id = ?", [model_id]).fetchone()
        data = _model_row(row)
    clear_analysis_cache(project_id)
    return {"data": data}


@router.delete("/{project_id:int}/models/{model_id:int}")
def delete_model(
    project_id: int,
    model_id: int,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "delete", project_id)
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT id FROM metadata.models WHERE id = ? AND project_id = ?",
            [model_id, project_id],
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Model not found")
        con.execute("DELETE FROM metadata.views WHERE model_id = ?", [model_id])
        con.execute("DELETE FROM metadata.calculated_fields WHERE model_id = ?", [model_id])
        con.execute("DELETE FROM metadata.relations WHERE source_model_id = ? OR target_model_id = ?", [model_id, model_id])
        con.execute("DELETE FROM metadata.models WHERE id = ?", [model_id])
    clear_analysis_cache(project_id)
    return {"data": {"success": True}}


@router.get("/{project_id:int}/models/{model_id:int}/compiled-sql")
def get_model_compiled_sql(
    project_id: int,
    model_id: int,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "read", project_id)
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT name, table_reference, column_defs FROM metadata.models WHERE id = ? AND project_id = ?",
            [model_id, project_id],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Model not found")
        model_name, table_ref, col_defs = row
        try:
            table_ref = _safe_identifier(table_ref) if table_ref else None
            compiled_from = table_ref or _safe_identifier(model_name)
        except ValueError:
            raise HTTPException(status_code=400, detail="Model name or table reference contains unsafe characters")
    return {"data": {"sql": f"SELECT * FROM {compiled_from}", "dialect": "duckdb"}}


# ── Views ────────────────────────────────────────────────────────────

@router.get("/{project_id:int}/views")
def list_views(
    project_id: int,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "read", project_id)
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            f"{VIEW_SELECT} WHERE project_id = ? ORDER BY id", [project_id]
        ).fetchall()
    return {"data": [_view_row(r) for r in rows]}


@router.post("/{project_id:int}/views")
def create_view(
    project_id: int,
    body: ViewCreate,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "create", project_id)
    with connection_lock():
        con = get_connection()
        model_id = body.model_id
        if model_id is not None:
            _require_model_in_project(con, model_id, project_id)
        elif body.source_response_id is None:
            model_id = _default_model_id(con, project_id)
            if model_id is None:
                raise HTTPException(status_code=400, detail="model_id or source_response_id is required")
        source_sql = body.sql
        if source_sql is not None:
            try:
                source_sql = validate_read_only_sql(source_sql)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
        if body.source_response_id is not None:
            response = _response_in_project(con, body.source_response_id, project_id)
            if not response:
                raise HTTPException(status_code=404, detail="Source response not found")
            source_sql = source_sql or response[2]
        if body.source_response_id is not None and not source_sql:
            raise HTTPException(status_code=400, detail="Source response does not contain SQL")
        max_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.views").fetchone()[0]
        con.execute(
            "INSERT INTO metadata.views (id, project_id, name, display_name, description, model_id, column_defs, sql, source_response_id) VALUES (?, ?, ?, ?, ?, ?, ?::JSON, ?, ?)",
            [
                max_id,
                project_id,
                body.name,
                body.display_name,
                body.description,
                model_id,
                json.dumps([c.model_dump() for c in body.columns]) if body.columns else "[]",
                source_sql,
                body.source_response_id,
            ],
        )
        row = con.execute(f"{VIEW_SELECT} WHERE id = ?", [max_id]).fetchone()
        data = _view_row(row)
    clear_analysis_cache(project_id)
    return {"data": data}


@router.get("/{project_id:int}/views/{view_id:int}")
def get_view(
    project_id: int,
    view_id: int,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "read", project_id)
    with connection_lock():
        con = get_connection()
        row = con.execute(
            f"{VIEW_SELECT} WHERE id = ? AND project_id = ?",
            [view_id, project_id],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="View not found")
        data = _view_row(row)
    return {"data": data}


@router.put("/{project_id:int}/views/{view_id:int}")
def update_view(
    project_id: int,
    view_id: int,
    body: ViewUpdate,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "update", project_id)
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT id FROM metadata.views WHERE id = ? AND project_id = ?",
            [view_id, project_id],
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="View not found")
        sets = []
        params = []
        for col in ("name", "display_name", "description"):
            val = getattr(body, col, None)
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)
        if body.columns is not None:
            sets.append("column_defs = ?::JSON")
            params.append(json.dumps([c.model_dump() for c in body.columns]))
        if body.sql is not None:
            try:
                normalized_sql = validate_read_only_sql(body.sql)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            sets.append("sql = ?")
            params.append(normalized_sql)
        if sets:
            sets.append("updated_at = CURRENT_TIMESTAMP")
            params.append(view_id)
            con.execute(f"UPDATE metadata.views SET {', '.join(sets)} WHERE id = ?", params)
        row = con.execute(f"{VIEW_SELECT} WHERE id = ?", [view_id]).fetchone()
        data = _view_row(row)
    clear_analysis_cache(project_id)
    return {"data": data}


@router.delete("/{project_id:int}/views/{view_id:int}")
def delete_view(
    project_id: int,
    view_id: int,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "delete", project_id)
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT id FROM metadata.views WHERE id = ? AND project_id = ?",
            [view_id, project_id],
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="View not found")
        con.execute("DELETE FROM metadata.views WHERE id = ?", [view_id])
    clear_analysis_cache(project_id)
    return {"data": {"success": True}}


# ── Relations ────────────────────────────────────────────────────────

@router.get("/{project_id:int}/relations")
def list_relations(
    project_id: int,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "read", project_id)
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            f"{RELATION_SELECT} WHERE project_id = ? ORDER BY id", [project_id]
        ).fetchall()
    return {"data": [_relation_row(r) for r in rows]}


@router.post("/{project_id:int}/relations")
def create_relation(
    project_id: int,
    body: RelationCreate,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "create", project_id)
    with connection_lock():
        con = get_connection()
        _require_model_in_project(con, body.source_model_id, project_id)
        _require_model_in_project(con, body.target_model_id, project_id)
        relation_type = body.relation_type
        max_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.relations").fetchone()[0]
        con.execute(
            "INSERT INTO metadata.relations (id, project_id, name, description, source_model_id, source_column, target_model_id, target_column, relation_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [max_id, project_id, body.name, body.description, body.source_model_id, body.source_column, body.target_model_id, body.target_column, relation_type],
        )
        row = con.execute(f"{RELATION_SELECT} WHERE id = ?", [max_id]).fetchone()
        data = _relation_row(row)
    clear_analysis_cache(project_id)
    return {"data": data}


@router.put("/{project_id:int}/relations/{relation_id:int}")
def update_relation(
    project_id: int,
    relation_id: int,
    body: RelationUpdate,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "update", project_id)
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT id FROM metadata.relations WHERE id = ? AND project_id = ?",
            [relation_id, project_id],
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Relation not found")
        sets = []
        params = []
        for col in ("name", "description", "source_column", "target_column"):
            val = getattr(body, col, None)
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)
        relation_type = body.relation_type
        if relation_type is not None:
            sets.append("relation_type = ?")
            params.append(relation_type)
        if sets:
            sets.append("updated_at = CURRENT_TIMESTAMP")
            params.append(relation_id)
            con.execute(f"UPDATE metadata.relations SET {', '.join(sets)} WHERE id = ?", params)
        row = con.execute(f"{RELATION_SELECT} WHERE id = ?", [relation_id]).fetchone()
        data = _relation_row(row)
    clear_analysis_cache(project_id)
    return {"data": data}


@router.delete("/{project_id:int}/relations/{relation_id:int}")
def delete_relation(
    project_id: int,
    relation_id: int,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "delete", project_id)
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT id FROM metadata.relations WHERE id = ? AND project_id = ?",
            [relation_id, project_id],
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Relation not found")
        con.execute("DELETE FROM metadata.relations WHERE id = ?", [relation_id])
    clear_analysis_cache(project_id)
    return {"data": {"success": True}}


# ── Calculated Fields ────────────────────────────────────────────────

@router.get("/{project_id:int}/calculated-fields")
def list_calculated_fields(
    project_id: int,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "read", project_id)
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            f"{CALCULATED_FIELD_SELECT} WHERE project_id = ? ORDER BY id", [project_id]
        ).fetchall()
    return {"data": [_cf_row(r) for r in rows]}


@router.post("/{project_id:int}/calculated-fields")
def create_calculated_field(
    project_id: int,
    body: CalculatedFieldCreate,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "create", project_id)
    with connection_lock():
        con = get_connection()
        _require_model_in_project(con, body.model_id, project_id)
        max_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.calculated_fields").fetchone()[0]
        con.execute(
            "INSERT INTO metadata.calculated_fields (id, project_id, name, display_name, description, model_id, expression, result_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [max_id, project_id, body.name, body.display_name, body.description, body.model_id, body.expression, body.result_type],
        )
        row = con.execute(f"{CALCULATED_FIELD_SELECT} WHERE id = ?", [max_id]).fetchone()
        data = _cf_row(row)
    clear_analysis_cache(project_id)
    return {"data": data}


@router.put("/{project_id:int}/calculated-fields/{field_id:int}")
def update_calculated_field(
    project_id: int,
    field_id: int,
    body: CalculatedFieldUpdate,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "update", project_id)
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT id FROM metadata.calculated_fields WHERE id = ? AND project_id = ?",
            [field_id, project_id],
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Calculated field not found")
        sets = []
        params = []
        for col in ("name", "display_name", "description", "expression", "result_type"):
            val = getattr(body, col, None)
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)
        if sets:
            sets.append("updated_at = CURRENT_TIMESTAMP")
            params.append(field_id)
            con.execute(f"UPDATE metadata.calculated_fields SET {', '.join(sets)} WHERE id = ?", params)
        row = con.execute(f"{CALCULATED_FIELD_SELECT} WHERE id = ?", [field_id]).fetchone()
        data = _cf_row(row)
    clear_analysis_cache(project_id)
    return {"data": data}


@router.delete("/{project_id:int}/calculated-fields/{field_id:int}")
def delete_calculated_field(
    project_id: int,
    field_id: int,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "delete", project_id)
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT id FROM metadata.calculated_fields WHERE id = ? AND project_id = ?",
            [field_id, project_id],
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Calculated field not found")
        con.execute("DELETE FROM metadata.calculated_fields WHERE id = ?", [field_id])
    clear_analysis_cache(project_id)
    return {"data": {"success": True}}


# ── Relationships (bulk) ─────────────────────────────────────────────

@router.get("/{project_id:int}/relationships")
def get_relationships(
    project_id: int,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "read", project_id)
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            f"{RELATION_SELECT} WHERE project_id = ? ORDER BY id", [project_id]
        ).fetchall()
    return {"data": [_relation_row(r) for r in rows]}


@router.put("/{project_id:int}/relationships")
def update_relationships(
    project_id: int,
    body: list,
    payload: dict = Depends(get_current_user),
):
    _require_model_permission(payload, "update", project_id)
    if not isinstance(body, list):
        raise HTTPException(status_code=400, detail="Expected a list of relationship objects")
    for rel in body:
        if not isinstance(rel, dict):
            raise HTTPException(status_code=400, detail="Each relationship must be a JSON object")
    with connection_lock():
        con = get_connection()
        for rel in body:
            source_id = rel.get("source_model_id")
            target_id = rel.get("target_model_id")
            if source_id is None:
                raise HTTPException(status_code=400, detail="Missing source_model_id in relationship")
            if target_id is None:
                raise HTTPException(status_code=400, detail="Missing target_model_id in relationship")
            _require_model_in_project(con, source_id, project_id)
            _require_model_in_project(con, target_id, project_id)
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("DELETE FROM metadata.relations WHERE project_id = ?", [project_id])
            for rel in body:
                max_rel_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.relations").fetchone()[0]
                con.execute(
                    "INSERT INTO metadata.relations (id, project_id, name, description, source_model_id, source_column, target_model_id, target_column, relation_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        max_rel_id,
                        project_id,
                        rel.get("name", ""),
                        rel.get("description"),
                        rel.get("source_model_id"),
                        rel.get("source_column", ""),
                        rel.get("target_model_id"),
                        rel.get("target_column", ""),
                        rel.get("relation_type") or rel.get("type") or "MANY_TO_ONE",
                    ],
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    clear_analysis_cache(project_id)
    return get_relationships(project_id, payload)


# ── Model Binding Diagnostics ────────────────────────────────────────

@router.get("/{project_id:int}/models/binding-status")
def get_model_binding_status(
    project_id: int,
    payload: dict = Depends(get_current_user),
):
    """Check which models have valid source_binding_id and which are missing it."""
    _require_model_permission(payload, "read", project_id)
    with connection_lock():
        con = get_connection()
        
        # Get all models for the project
        models = con.execute(
            """SELECT id, name, display_name, source_binding_id, table_reference, model_type
               FROM metadata.models
               WHERE project_id = ?
               ORDER BY id""",
            [project_id],
        ).fetchall()
        
        # Get all valid bindings for the project
        bindings = con.execute(
            "SELECT id, datasource_id, alias FROM metadata.project_datasources WHERE project_id = ?",
            [project_id],
        ).fetchall()
        valid_binding_ids = {row[0] for row in bindings}
        
        bound_models = []
        unbound_models = []
        
        for model in models:
            model_id, name, display_name, binding_id, table_ref, model_type = model
            model_info = {
                "id": model_id,
                "name": name,
                "display_name": display_name,
                "table_reference": table_ref,
                "model_type": _normalize_model_type(model_type),
                "source_binding_id": binding_id,
            }
            
            if binding_id is None:
                model_info["status"] = "missing_binding"
                model_info["issue"] = "source_binding_id is NULL"
                unbound_models.append(model_info)
            elif binding_id not in valid_binding_ids:
                model_info["status"] = "invalid_binding"
                model_info["issue"] = f"source_binding_id {binding_id} does not exist in project_datasources"
                unbound_models.append(model_info)
            else:
                model_info["status"] = "valid"
                bound_models.append(model_info)
        
        return {
            "data": {
                "project_id": project_id,
                "total_models": len(models),
                "bound_models": len(bound_models),
                "unbound_models": len(unbound_models),
                "valid_bindings": len(valid_binding_ids),
                "models": {
                    "bound": bound_models,
                    "unbound": unbound_models,
                },
            }
        }
