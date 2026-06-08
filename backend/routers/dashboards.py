from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from db import connection_lock, get_connection
from models.schemas import (
    DashboardCreate,
    DashboardItemCreate,
    DashboardItemLayoutsUpdate,
    DashboardItemUpdate,
    DashboardScheduleRequest,
    DashboardUpdate,
)
from routers.auth import get_current_user, payload_has_permission
from services.ask_service import execute_project_sql
from services.security_policy_service import apply_cls_to_rows, get_effective_security_policies

LOGGER = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300

router = APIRouter()


def _require_dashboard_permission(con, payload: dict, dashboard_id: int, action: str) -> None:
    row = con.execute("SELECT project_id FROM metadata.dashboards WHERE id = ?", [dashboard_id]).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    if not payload_has_permission(payload, "dashboards", action, row[0]):
        raise HTTPException(status_code=403, detail="Permission denied")


def _dashboard_project_id(con, dashboard_id: int) -> int:
    row = con.execute("SELECT project_id FROM metadata.dashboards WHERE id = ?", [dashboard_id]).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return row[0]


def _dashboard_to_dict(row):
    return {
        "id": row[0],
        "project_id": row[1],
        "name": row[2],
        "cache_enabled": row[3],
        "schedule_frequency": row[4],
        "schedule_timezone": row[5],
        "schedule_cron": row[6],
        "created_at": str(row[7]) if row[7] else None,
        "items": [],
    }


def _safe_json_loads(value: Any, fallback: Any = None):
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return fallback
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return fallback
    return value


def _safe_int(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except (ValueError, TypeError):
            return None
    return None


def _response_chart_data(con, response_id: Optional[int], project_id: int) -> dict[str, Any]:
    if response_id is None:
        return {}
    row = con.execute(
        """SELECT tr.sql, tr.answer_detail
           FROM metadata.thread_responses tr
           JOIN metadata.threads t ON t.id = tr.thread_id
           WHERE tr.id = ? AND t.project_id = ?""",
        [response_id, project_id],
    ).fetchone()
    if not row:
        return {}
    answer_detail = _safe_json_loads(row[1], {})
    if not isinstance(answer_detail, dict):
        answer_detail = {}
    return {
        "sql": row[0],
        "columns": answer_detail.get("columns"),
        "rows": answer_detail.get("rows"),
        "preview_row_limit": answer_detail.get("previewRowLimit"),
        "total_rows": answer_detail.get("totalRows"),
        "execution_time_ms": answer_detail.get("executionTimeMs"),
    }


def _enrich_chart_config(con, project_id: int, response_id: Optional[int], chart_config: Any):
    if not isinstance(chart_config, dict):
        return chart_config
    source_response_id = response_id or _safe_int(chart_config.get("source_response_id"))
    if source_response_id is None:
        return chart_config

    response_data = _response_chart_data(con, source_response_id, project_id)
    if not response_data:
        return chart_config

    enriched = dict(chart_config)
    enriched.setdefault("source_response_id", source_response_id)
    if not enriched.get("sql") and response_data.get("sql"):
        enriched["sql"] = response_data["sql"]
    if not isinstance(enriched.get("columns"), list) and isinstance(response_data.get("columns"), list):
        enriched["columns"] = response_data["columns"]
    if not isinstance(enriched.get("rows"), list) and isinstance(response_data.get("rows"), list):
        enriched["rows"] = response_data["rows"]
    if enriched.get("preview_row_limit") is None and response_data.get("preview_row_limit") is not None:
        enriched["preview_row_limit"] = response_data["preview_row_limit"]
    if enriched.get("total_rows") is None and response_data.get("total_rows") is not None:
        enriched["total_rows"] = response_data["total_rows"]
    if enriched.get("execution_time_ms") is None and response_data.get("execution_time_ms") is not None:
        enriched["execution_time_ms"] = response_data["execution_time_ms"]
    return enriched


def _item_to_dict(row, con=None, project_id: Optional[int] = None):
    chart_config = _safe_json_loads(row[5])
    response_id = row[4]
    if response_id is None and isinstance(chart_config, dict):
        response_id = _safe_int(chart_config.get("source_response_id"))
    if con is not None and project_id is not None:
        chart_config = _enrich_chart_config(con, project_id, response_id, chart_config)
    return {
        "id": row[0],
        "dashboard_id": row[1],
        "type": row[2],
        "display_name": row[3],
        "response_id": response_id,
        "chart_config": chart_config,
        "data_source": row[6],
        "layout_x": row[7],
        "layout_y": row[8],
        "layout_w": row[9],
        "layout_h": row[10],
        "cache_data": _safe_json_loads(row[11]),
        "cache_created_at": str(row[12]) if row[12] else None,
    }


ITEM_SELECT = (
    "id, dashboard_id, type, display_name, response_id, "
    "chart_config, data_source, layout_x, layout_y, layout_w, layout_h, "
    "cache_data, cache_created_at"
)

DASHBOARD_SELECT = (
    "id, project_id, name, cache_enabled, "
    "schedule_frequency, schedule_timezone, schedule_cron, created_at"
)


@router.get("")
def list_dashboards(
    project_id: Optional[int] = Query(None),
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        con = get_connection()
        if project_id is not None and project_id <= 0:
            raise HTTPException(status_code=400, detail="A real project is required")
        if project_id is not None and not payload_has_permission(payload, "dashboards", "read", project_id):
            raise HTTPException(status_code=403, detail="Permission denied")
        if project_id is not None:
            rows = con.execute(
                f"SELECT {DASHBOARD_SELECT}, (SELECT COUNT(*) FROM metadata.dashboard_items di WHERE di.dashboard_id = d.id) AS item_count FROM metadata.dashboards d WHERE d.project_id = ? ORDER BY d.id",
                [project_id],
            ).fetchall()
        else:
            rows = con.execute(
                f"SELECT {DASHBOARD_SELECT}, (SELECT COUNT(*) FROM metadata.dashboard_items di WHERE di.dashboard_id = d.id) AS item_count FROM metadata.dashboards d ORDER BY d.id",
            ).fetchall()
        items = []
        for r in rows:
            if not payload_has_permission(payload, "dashboards", "read", r[1]):
                continue
            d = _dashboard_to_dict(r[:8])
            d["item_count"] = r[8]
            items.append(d)
    return {"data": items}


@router.post("")
def create_dashboard(body: DashboardCreate, payload: dict = Depends(get_current_user)):
    with connection_lock():
        con = get_connection()
        if body.project_id <= 0:
            raise HTTPException(status_code=400, detail="A real project is required")
        if not payload_has_permission(payload, "dashboards", "create", body.project_id):
            raise HTTPException(status_code=403, detail="Permission denied")
        project = con.execute("SELECT id FROM metadata.projects WHERE id = ?", [body.project_id]).fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        max_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.dashboards").fetchone()[0]
        con.execute(
            "INSERT INTO metadata.dashboards (id, project_id, name, cache_enabled, schedule_frequency, schedule_timezone, schedule_cron) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [max_id, body.project_id, body.name, body.cache_enabled, body.schedule_frequency, body.schedule_timezone, body.schedule_cron],
        )
        row = con.execute(f"SELECT {DASHBOARD_SELECT} FROM metadata.dashboards WHERE id = ?", [max_id]).fetchone()
        d = _dashboard_to_dict(row)
    return {"data": d}


@router.get("/{dashboard_id:int}")
def get_dashboard(dashboard_id: int, payload: dict = Depends(get_current_user)):
    with connection_lock():
        con = get_connection()
        _require_dashboard_permission(con, payload, dashboard_id, "read")
        row = con.execute(
            f"SELECT {DASHBOARD_SELECT} FROM metadata.dashboards WHERE id = ?",
            [dashboard_id],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Dashboard not found")
        d = _dashboard_to_dict(row)
        items = con.execute(
            f"SELECT {ITEM_SELECT} FROM metadata.dashboard_items WHERE dashboard_id = ? ORDER BY id",
            [dashboard_id],
        ).fetchall()
        d["items"] = [_item_to_dict(r, con, d["project_id"]) for r in items]
    return {"data": d}


@router.put("/{dashboard_id:int}")
def update_dashboard(dashboard_id: int, body: DashboardUpdate, payload: dict = Depends(get_current_user)):
    with connection_lock():
        con = get_connection()
        _require_dashboard_permission(con, payload, dashboard_id, "update")
        existing = con.execute("SELECT id FROM metadata.dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Dashboard not found")
        fields = []
        params = []
        for col, val in [
            ("name", body.name),
            ("cache_enabled", body.cache_enabled),
            ("schedule_frequency", body.schedule_frequency),
            ("schedule_timezone", body.schedule_timezone),
            ("schedule_cron", body.schedule_cron),
        ]:
            if val is not None:
                fields.append(f"{col} = ?")
                params.append(val)
        if fields:
            params.append(dashboard_id)
            con.execute(f"UPDATE metadata.dashboards SET {', '.join(fields)} WHERE id = ?", params)
        row = con.execute(f"SELECT {DASHBOARD_SELECT} FROM metadata.dashboards WHERE id = ?", [dashboard_id]).fetchone()
        d = _dashboard_to_dict(row)
        return {"data": d}


@router.delete("/{dashboard_id:int}")
def delete_dashboard(dashboard_id: int, payload: dict = Depends(get_current_user)):
    with connection_lock():
        con = get_connection()
        _require_dashboard_permission(con, payload, dashboard_id, "delete")
        existing = con.execute("SELECT id FROM metadata.dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Dashboard not found")
        con.execute("DELETE FROM metadata.dashboard_items WHERE dashboard_id = ?", [dashboard_id])
        con.execute("DELETE FROM metadata.dashboards WHERE id = ?", [dashboard_id])
    return {"data": {"success": True}}


@router.get("/{dashboard_id:int}/items")
def list_dashboard_items(dashboard_id: int, payload: dict = Depends(get_current_user)):
    with connection_lock():
        con = get_connection()
        _require_dashboard_permission(con, payload, dashboard_id, "read")
        dashboard = con.execute("SELECT project_id FROM metadata.dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not dashboard:
            raise HTTPException(status_code=404, detail="Dashboard not found")
        rows = con.execute(
            f"SELECT {ITEM_SELECT} FROM metadata.dashboard_items WHERE dashboard_id = ? ORDER BY id",
            [dashboard_id],
        ).fetchall()
        return {"data": [_item_to_dict(r, con, dashboard[0]) for r in rows]}


@router.post("/{dashboard_id:int}/items")
def create_dashboard_item(dashboard_id: int, body: DashboardItemCreate, payload: dict = Depends(get_current_user)):
    with connection_lock():
        con = get_connection()
        _require_dashboard_permission(con, payload, dashboard_id, "update")
        dashboard = con.execute("SELECT project_id FROM metadata.dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not dashboard:
            raise HTTPException(status_code=404, detail="Dashboard not found")
        project_id = dashboard[0]
        max_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.dashboard_items").fetchone()[0]
        chart_config_data = body.chart_config
        response_id = body.response_id
        if response_id is None and isinstance(chart_config_data, dict):
            response_id = _safe_int(chart_config_data.get("source_response_id"))
        if response_id is not None and not _response_chart_data(con, response_id, project_id):
            raise HTTPException(status_code=400, detail="Response does not belong to dashboard project")
        if isinstance(chart_config_data, dict) and response_id is not None:
            chart_config_data = _enrich_chart_config(con, project_id, response_id, chart_config_data)
        chart_config = json.dumps(chart_config_data) if chart_config_data else None
        display_name = body.display_name if body.display_name is not None else (body.title or None)
        data_source = body.data_source
        con.execute(
            "INSERT INTO metadata.dashboard_items (id, dashboard_id, type, display_name, chart_config, data_source, response_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [max_id, dashboard_id, body.type, display_name, chart_config, data_source, response_id],
        )
        row = con.execute(f"SELECT {ITEM_SELECT} FROM metadata.dashboard_items WHERE id = ?", [max_id]).fetchone()
        return {"data": _item_to_dict(row, con, project_id)}


@router.put("/{dashboard_id:int}/items/{item_id:int}")
def update_dashboard_item(dashboard_id: int, item_id: int, body: DashboardItemUpdate, payload: dict = Depends(get_current_user)):
    with connection_lock():
        con = get_connection()
        _require_dashboard_permission(con, payload, dashboard_id, "update")
        existing = con.execute(
            "SELECT d.project_id FROM metadata.dashboard_items di JOIN metadata.dashboards d ON d.id = di.dashboard_id WHERE di.id = ? AND di.dashboard_id = ?",
            [item_id, dashboard_id],
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Dashboard item not found")
        project_id = existing[0]
        fields = []
        params = []
        if body.type is not None:
            fields.append("type = ?")
            params.append(body.type)
        display_name = body.display_name if body.display_name is not None else (body.title or None)
        if display_name is not None:
            fields.append("display_name = ?")
            params.append(display_name)
        if body.chart_config is not None:
            response_id = _safe_int(body.chart_config.get("source_response_id")) if isinstance(body.chart_config, dict) else None
            if response_id is not None and not _response_chart_data(con, response_id, project_id):
                raise HTTPException(status_code=400, detail="Response does not belong to dashboard project")
            fields.append("chart_config = ?")
            params.append(json.dumps(_enrich_chart_config(con, project_id, response_id, body.chart_config)))
        if body.data_source is not None:
            fields.append("data_source = ?")
            params.append(body.data_source)
        if fields:
            params.append(item_id)
            con.execute(f"UPDATE metadata.dashboard_items SET {', '.join(fields)} WHERE id = ?", params)
        row = con.execute(f"SELECT {ITEM_SELECT} FROM metadata.dashboard_items WHERE id = ?", [item_id]).fetchone()
        return {"data": _item_to_dict(row, con, project_id)}


@router.delete("/{dashboard_id:int}/items/{item_id:int}")
def delete_dashboard_item(dashboard_id: int, item_id: int, payload: dict = Depends(get_current_user)):
    with connection_lock():
        con = get_connection()
        _require_dashboard_permission(con, payload, dashboard_id, "delete")
        existing = con.execute("SELECT id FROM metadata.dashboard_items WHERE id = ? AND dashboard_id = ?", [item_id, dashboard_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Dashboard item not found")
        con.execute("DELETE FROM metadata.dashboard_items WHERE id = ?", [item_id])
    return {"data": {"success": True}}


@router.get("/{dashboard_id:int}/export")
def export_dashboard(dashboard_id: int, format: str = Query("json", pattern="^(json|yaml)$"), payload: dict = Depends(get_current_user)):
    with connection_lock():
        con = get_connection()
        _require_dashboard_permission(con, payload, dashboard_id, "read")
        row = con.execute(
            f"SELECT {DASHBOARD_SELECT} FROM metadata.dashboards WHERE id = ?",
            [dashboard_id],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Dashboard not found")
        d = _dashboard_to_dict(row)
        items = con.execute(
            f"SELECT {ITEM_SELECT} FROM metadata.dashboard_items WHERE dashboard_id = ? ORDER BY id",
            [dashboard_id],
        ).fetchall()
        d["items"] = [_item_to_dict(r, con, d["project_id"]) for r in items]
    if format.lower() == "yaml":
        import yaml
        from fastapi.responses import Response
        content = yaml.dump(d, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return Response(content=content.encode("utf-8"), media_type="application/x-yaml", headers={"Content-Disposition": f'attachment; filename="dashboard-{dashboard_id}.yml"'})
    return {"data": d}


@router.put("/items/layouts")
def update_item_layouts(body: DashboardItemLayoutsUpdate, payload: dict = Depends(get_current_user)):
    with connection_lock():
        con = get_connection()
        for layout in body.layouts:
            row = con.execute(
                "SELECT d.project_id FROM metadata.dashboard_items di JOIN metadata.dashboards d ON d.id = di.dashboard_id WHERE di.id = ?",
                [layout.item_id],
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Dashboard item {layout.item_id} not found")
            if not payload_has_permission(payload, "dashboards", "update", row[0]):
                raise HTTPException(status_code=403, detail="Permission denied")
        for layout in body.layouts:
            con.execute(
                "UPDATE metadata.dashboard_items SET layout_x = ?, layout_y = ?, layout_w = ?, layout_h = ? WHERE id = ?",
                [layout.x, layout.y, layout.w, layout.h, layout.item_id],
            )
    return {"data": {"success": True}}


@router.post("/items/{item_id:int}/preview")
def preview_item(item_id: int, force_refresh: bool = False, payload: dict = Depends(get_current_user)):
    user_id = int(payload["sub"])
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT d.project_id, d.cache_enabled, di.response_id, di.chart_config, di.cache_data, di.cache_created_at "
            "FROM metadata.dashboard_items di JOIN metadata.dashboards d ON d.id = di.dashboard_id WHERE di.id = ?",
            [item_id],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Dashboard item not found")
        project_id, cache_enabled, response_id, chart_config_raw, cache_data_raw, cache_created_at = row
        if not payload_has_permission(payload, "dashboards", "read", project_id):
            raise HTTPException(status_code=403, detail="Permission denied")
        if response_id:
            response_data = _response_chart_data(con, response_id, project_id)
        else:
            response_data = {}

    chart_config = _safe_json_loads(chart_config_raw, {})

    columns = None
    rows = None
    sql = None
    should_update_cache = False

    cache_data = _safe_json_loads(cache_data_raw)
    if isinstance(cache_data, dict) and isinstance(cache_data.get("columns"), list) and isinstance(cache_data.get("rows"), list):
        if not force_refresh and cache_created_at:
            try:
                cache_age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(cache_created_at))).total_seconds()
                if cache_age < CACHE_TTL_SECONDS:
                    columns = cache_data["columns"]
                    rows = cache_data["rows"]
                    sql = cache_data.get("sql")
            except (ValueError, TypeError):
                pass
        if columns is None:
            columns = cache_data.get("columns")
            rows = cache_data.get("rows")
            sql = cache_data.get("sql")
            should_update_cache = True

    if columns is None:
        if isinstance(chart_config, dict) and isinstance(chart_config.get("columns"), list) and isinstance(chart_config.get("rows"), list):
            columns = chart_config["columns"]
            rows = chart_config["rows"]
            sql = chart_config.get("sql")
        elif response_data:
            columns = response_data.get("columns") or []
            rows = response_data.get("rows") or []
            sql = response_data.get("sql")
        else:
            columns = []
            rows = []

    if sql and project_id > 0 and not columns and not rows:
        try:
            result = execute_project_sql(sql, project_id, user_id, limit=500)
            columns = result.get("columns") or []
            rows = result.get("rows") or []
            should_update_cache = True
        except Exception as exc:
            LOGGER.warning("Dashboard preview SQL execution failed for item %s: %s", item_id, exc)

    if should_update_cache and columns is not None and rows is not None:
        new_cache = {"columns": columns, "rows": rows}
        if sql:
            new_cache["sql"] = sql
        try:
            with connection_lock():
                get_connection().execute(
                    "UPDATE metadata.dashboard_items SET cache_data = ?::JSON, cache_created_at = CURRENT_TIMESTAMP WHERE id = ?",
                    [json.dumps(new_cache), item_id],
                )
        except Exception as exc:
            LOGGER.warning("Failed to update dashboard item cache for item %s: %s", item_id, exc)

    if columns is None:
        columns = []
    if rows is None:
        rows = []

    if project_id > 0 and user_id:
        try:
            policies = get_effective_security_policies(user_id, project_id)
            rls_policies = policies.get("row_policies", [])
            cls_policies = policies.get("column_policies", [])
            has_rls = any(
                str(p.get("model_name", "")).lower() in {c.lower() for c in columns}
                or str(p.get("model_name", "")).lower() in {r.get("__model__", "").lower() for r in (rows if isinstance(rows, list) else []) if isinstance(r, dict)}
                for p in rls_policies
            )
            if has_rls and sql:
                try:
                    viewer_result = execute_project_sql(sql, project_id, user_id, limit=500)
                    viewer_columns = viewer_result.get("columns") or []
                    viewer_rows = viewer_result.get("rows") or []
                    if viewer_columns and viewer_rows:
                        columns = viewer_columns
                        rows = viewer_rows
                except Exception as exc:
                    LOGGER.warning("Dashboard viewer-aware RLS re-execution failed for item %s: %s", item_id, exc)
            if cls_policies:
                rows = apply_cls_to_rows(rows, cls_policies)
                hidden_columns = {p["column_name"] for p in cls_policies if str(p.get("access_type", "")).upper() == "HIDE"}
                if hidden_columns:
                    columns = [c for c in columns if c not in hidden_columns]
        except Exception as exc:
            LOGGER.warning("CLS policy application failed for dashboard item %s: %s", item_id, exc)

    return {"data": {"columns": columns, "rows": rows}}


@router.post("/{dashboard_id:int}/schedule")
def set_dashboard_schedule(dashboard_id: int, body: DashboardScheduleRequest, payload: dict = Depends(get_current_user)):
    with connection_lock():
        con = get_connection()
        _require_dashboard_permission(con, payload, dashboard_id, "update")
        existing = con.execute("SELECT id FROM metadata.dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Dashboard not found")
        con.execute(
            "UPDATE metadata.dashboards SET schedule_frequency = ?, schedule_timezone = ?, schedule_cron = ? WHERE id = ?",
            [body.frequency, body.timezone, body.cron, dashboard_id],
        )
    return {"data": {"success": True}}
