from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from db import connection_lock, get_connection
from routers.auth import get_current_user, payload_has_permission

router = APIRouter()


class RecommendationCreate(BaseModel):
    title: str = Field(min_length=1)
    description: Optional[str] = None
    category: str = "catalog"
    scope: str = "project"
    source_type: Optional[str] = None
    source_id: Optional[int] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    status: str = "active"
    metadata: Optional[dict[str, Any]] = None


class RecommendationUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    scope: Optional[str] = None
    source_type: Optional[str] = None
    source_id: Optional[int] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    status: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class CatalogEntryCreate(BaseModel):
    question: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    metadata: Optional[dict[str, Any]] = None
    verified: bool = False


class CatalogEntryUpdate(BaseModel):
    question: Optional[str] = None
    sql: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    verified: Optional[bool] = None


class HintCreate(BaseModel):
    hint_text: str = Field(min_length=1)
    source_query: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class HintUpdate(BaseModel):
    hint_text: Optional[str] = None
    source_query: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)


class RateRequest(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str | None = None
    source_layer: str | None = None
    recommend_type: str | None = None
    context: str | None = None


def _require_recommendation_permission(payload: dict, action: str, project_id: int) -> None:
    if project_id <= 0:
        raise HTTPException(status_code=400, detail="A real project is required")
    if not payload_has_permission(payload, "recommendations", action, project_id):
        raise HTTPException(status_code=403, detail="Permission denied")


_ALLOWED_ID_TABLES = frozenset({
    "metadata.recommendations",
    "metadata.question_sql_catalog",
    "metadata.user_preference_hints",
    "metadata.recommendation_ratings",
    "metadata.recommendation_scores",
    "metadata.recommendation_feedback",
})


def _max_id(con, table: str) -> int:
    if table not in _ALLOWED_ID_TABLES:
        raise ValueError(f"Unknown table for ID generation: {table}")
    con.execute("INSERT INTO metadata.id_sequences VALUES (?, COALESCE((SELECT MAX(id) FROM %s), 0)) ON CONFLICT DO NOTHING" % table, [table])
    existing = con.execute("SELECT next_id FROM metadata.id_sequences WHERE table_name = ?", [table]).fetchone()
    if existing and existing[0] <= 1:
        max_existing = con.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0]
        if max_existing > 0:
            con.execute("UPDATE metadata.id_sequences SET next_id = ? WHERE table_name = ?", [max_existing, table])
    return con.execute("UPDATE metadata.id_sequences SET next_id = next_id + 1 WHERE table_name = ? RETURNING next_id", [table]).fetchone()[0]


def _json_load(value: Any, fallback: Any = None) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return fallback
    return value


def _recommendation_to_dict(row) -> dict[str, Any]:
    metadata = _json_load(row[10], {})
    return {
        "id": row[0],
        "project_id": row[1],
        "title": row[2],
        "description": row[3],
        "category": row[4],
        "scope": row[5],
        "source_type": row[6],
        "source_id": row[7],
        "confidence": row[8],
        "status": row[9],
        "metadata": metadata if isinstance(metadata, dict) else {},
        "created_at": str(row[11]) if row[11] else None,
        "updated_at": str(row[12]) if row[12] else None,
    }


def _catalog_to_dict(row) -> dict[str, Any]:
    return {
        "id": row[0],
        "project_id": row[1],
        "question": row[2],
        "sql": row[3],
        "sql_text": row[3],
        "frequency": row[4],
        "last_used": str(row[5]) if row[5] else None,
        "metadata": _json_load(row[6], {}),
        "verified": bool(row[7]),
    }


def _hint_to_dict(row) -> dict[str, Any]:
    return {
        "id": row[0],
        "user_id": row[1],
        "hint_text": row[2],
        "text": row[2],
        "source_query": row[3],
        "confidence": row[4],
        "weight": row[4],
        "created_at": str(row[5]) if row[5] else None,
        "expires_at": str(row[6]) if row[6] else None,
    }


def _score_to_dict(row) -> dict[str, Any]:
    return {
        "id": row[0],
        "user_id": row[1],
        "recommendation_id": row[2],
        "project_id": row[3],
        "source_layer": row[4],
        "recommend_type": row[5],
        "score": row[6],
        "session_context": row[7],
        "source_question": row[8],
        "weight_adjustment": row[9],
        "created_at": str(row[10]) if row[10] else None,
        "date": str(row[10]) if row[10] else None,
        "source": row[4] or row[5] or "recommendation",
        "reason": row[8] or row[7] or "",
    }


def _list_recommendation_rows(con, project_id: int, category: str | None = None, status_value: str = "active") -> list[dict[str, Any]]:
    where = ["project_id = ?"]
    params: list[Any] = [project_id]
    if category:
        where.append("category = ?")
        params.append(category)
    if status_value:
        where.append("status = ?")
        params.append(status_value)
    rows = con.execute(
        "SELECT id, project_id, title, description, category, scope, source_type, source_id, confidence, status, metadata, created_at, updated_at "
        f"FROM metadata.recommendations WHERE {' AND '.join(where)} ORDER BY created_at DESC",
        params,
    ).fetchall()
    return [_recommendation_to_dict(row) for row in rows]


def _normalize_generated_metadata(value: Any) -> dict[str, Any]:
    parsed = _json_load(value, {})
    return parsed if isinstance(parsed, dict) else {}


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _to_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _persist_generated_recommendations(con, project_id: int, generated: list[dict[str, Any]]) -> int:
    if not generated:
        return 0
    existing_rows = con.execute(
        "SELECT id, LOWER(TRIM(title)), status, metadata FROM metadata.recommendations WHERE project_id = ? AND title IS NOT NULL",
        [project_id],
    ).fetchall()
    existing_map: dict[str, dict[str, Any]] = {}
    for row in existing_rows:
        title_key = str(row[1] or "").strip().lower()
        if not title_key:
            continue
        existing_metadata = _normalize_generated_metadata(row[3])
        existing_map[title_key] = {
            "id": int(row[0]),
            "status": str(row[2] or "active").strip().lower(),
            "metadata": existing_metadata,
            "auto_generated": _is_truthy(existing_metadata.get("auto_generated")),
        }

    inserted = 0
    for item in generated:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        title_key = title.lower()
        existing = existing_map.get(title_key)
        metadata = _normalize_generated_metadata(item.get("metadata"))
        category = str(item.get("category") or "aggregation")
        scope = str(item.get("scope") or "project")
        source_type = str(item.get("source_type") or "schema")
        source_id = _to_optional_int(item.get("source_id"))
        confidence = _to_optional_float(item.get("confidence"))
        description = item.get("description")

        if existing:
            status = str(existing.get("status") or "active")
            if status == "dismissed":
                continue
            if status == "active":
                existing_metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
                merged_metadata = dict(existing_metadata)
                merged_metadata.update(metadata)
                if _is_truthy(existing.get("auto_generated")):
                    merged_metadata["auto_generated"] = True
                else:
                    merged_metadata.pop("auto_generated", None)
                con.execute(
                    "UPDATE metadata.recommendations SET description = ?, category = ?, scope = ?, source_type = ?, source_id = ?, confidence = ?, metadata = ?::JSON, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    [description, category, scope, source_type, source_id, confidence, json.dumps(merged_metadata), int(existing["id"])],
                )
                existing_map[title_key] = {
                    "id": int(existing["id"]),
                    "status": "active",
                    "metadata": merged_metadata,
                    "auto_generated": _is_truthy(existing.get("auto_generated")),
                }
            continue

        metadata = dict(metadata)
        metadata["auto_generated"] = True
        rec_id = _max_id(con, "metadata.recommendations")
        con.execute(
            "INSERT INTO metadata.recommendations (id, project_id, title, description, category, scope, source_type, source_id, confidence, status, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?::JSON)",
            [rec_id, project_id, title, description, category, scope, source_type, source_id, confidence, json.dumps(metadata)],
        )
        existing_map[title_key] = {
            "id": rec_id,
            "status": "active",
            "metadata": metadata,
            "auto_generated": True,
        }
        inserted += 1
    return inserted


def _clear_auto_generated_active_recommendations(con, project_id: int) -> int:
    rows = con.execute(
        "SELECT id, metadata FROM metadata.recommendations WHERE project_id = ? AND status = 'active'",
        [project_id],
    ).fetchall()
    removable_ids: list[int] = []
    for row in rows:
        rec_id = int(row[0])
        metadata = _normalize_generated_metadata(row[1])
        if _is_truthy(metadata.get("auto_generated")):
            removable_ids.append(rec_id)
    if not removable_ids:
        return 0
    placeholders = ", ".join("?" for _ in removable_ids)
    con.execute(
        f"DELETE FROM metadata.recommendations WHERE project_id = ? AND id IN ({placeholders})",
        [project_id, *removable_ids],
    )
    return len(removable_ids)


def _ensure_recommendation_bootstrap_status_table(con) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata.recommendation_bootstrap_status (
            project_id INTEGER PRIMARY KEY REFERENCES metadata.projects(id),
            status VARCHAR NOT NULL DEFAULT 'idle',
            recommendation_count INTEGER DEFAULT 0,
            error TEXT,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


@router.get("/statistics")
def statistics(
    project_id: int | None = Query(None, ge=1),
    payload: dict = Depends(get_current_user),
):
    if project_id is not None:
        _require_recommendation_permission(payload, "read", int(project_id))
    elif not payload_has_permission(payload, "recommendations", "read"):
        raise HTTPException(status_code=403, detail="Permission denied")

    from services.recommendation_service import _route_signal_snapshot

    request_user_id = int(payload["sub"])
    with connection_lock():
        con = get_connection()
        if project_id is None:
            total_catalogs = con.execute("SELECT COUNT(*) FROM metadata.question_sql_catalog").fetchone()[0]
            top_queries = con.execute(
                "SELECT question, sql_text, frequency, last_used FROM metadata.question_sql_catalog ORDER BY frequency DESC, last_used DESC LIMIT 10"
            ).fetchall()
            scores = con.execute(
                "SELECT source_layer, score, COUNT(*) FROM metadata.recommendation_scores GROUP BY source_layer, score ORDER BY source_layer, score"
            ).fetchall()
            total_hints = con.execute("SELECT COUNT(*) FROM metadata.user_preference_hints").fetchone()[0]
            history = con.execute(
                "SELECT source_layer, previous_weight, new_weight, reason, created_at FROM metadata.layer_weight_history ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        else:
            total_catalogs = con.execute(
                "SELECT COUNT(*) FROM metadata.question_sql_catalog WHERE project_id = ?",
                [int(project_id)],
            ).fetchone()[0]
            top_queries = con.execute(
                "SELECT question, sql_text, frequency, last_used FROM metadata.question_sql_catalog WHERE project_id = ? ORDER BY frequency DESC, last_used DESC LIMIT 10",
                [int(project_id)],
            ).fetchall()
            scores = con.execute(
                "SELECT source_layer, score, COUNT(*) FROM metadata.recommendation_scores WHERE project_id = ? GROUP BY source_layer, score ORDER BY source_layer, score",
                [int(project_id)],
            ).fetchall()
            total_hints = con.execute(
                "SELECT COUNT(*) FROM metadata.user_preference_hints WHERE user_id = ?",
                [request_user_id],
            ).fetchone()[0]
            history = con.execute(
                "SELECT h.source_layer, h.previous_weight, h.new_weight, h.reason, h.created_at "
                "FROM metadata.layer_weight_history h "
                "JOIN metadata.recommendation_scores s ON s.id = h.triggered_by_score_id "
                "WHERE s.project_id = ? "
                "ORDER BY h.created_at DESC LIMIT 100",
                [int(project_id)],
            ).fetchall()

    route_signals = _route_signal_snapshot(int(project_id) if project_id is not None else None)

    layer_performance: dict[str, dict[str, float]] = {}
    score_distribution: dict[str, int] = {}
    for layer, score, count in scores:
        key = str(layer or "unknown")
        stats = layer_performance.setdefault(key, {"count": 0, "avg_score": 0})
        stats["avg_score"] = ((stats["avg_score"] * stats["count"]) + (float(score) * int(count))) / (stats["count"] + int(count))
        stats["count"] += int(count)
        score_distribution[str(score)] = score_distribution.get(str(score), 0) + int(count)
    return {
        "data": {
            "total_catalogs": total_catalogs,
            "total_hints": total_hints,
            "top_queries": [
                {"question": row[0], "sql": row[1], "frequency": row[2], "last_used": str(row[3]) if row[3] else None}
                for row in top_queries
            ],
            "layer_performance": layer_performance,
            "score_distribution": score_distribution,
            "route_signals": route_signals,
            "weight_history": [
                {"layer": row[0], "previous_weight": row[1], "weight": row[2], "reason": row[3], "adjusted_at": str(row[4]) if row[4] else None}
                for row in history
            ],
        }
    }


@router.get("/statistics/weight-history")
def weight_history(payload: dict = Depends(get_current_user)):
    if not payload_has_permission(payload, "recommendations", "read"):
        raise HTTPException(status_code=403, detail="Permission denied")
    with connection_lock():
        rows = get_connection().execute(
            "SELECT source_layer, previous_weight, new_weight, reason, created_at FROM metadata.layer_weight_history ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    return {"data": {"history": [{"layer": r[0], "previous_weight": r[1], "weight": r[2], "reason": r[3], "adjusted_at": str(r[4]) if r[4] else None} for r in rows]}}


@router.get("/statistics/low-score-alerts")
def low_score_alerts(payload: dict = Depends(get_current_user)):
    if not payload_has_permission(payload, "recommendations", "read"):
        raise HTTPException(status_code=403, detail="Permission denied")
    with connection_lock():
        rows = get_connection().execute(
            "SELECT source_layer, score, created_at FROM metadata.recommendation_scores WHERE score <= 2 ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    return {"data": {"alerts": [{"source_layer": r[0], "consecutive_low": 1, "last_score": r[1], "timestamp": str(r[2]) if r[2] else None} for r in rows]}}


@router.get("/{recommendation_id:int}/rating")
def rating_detail(recommendation_id: int, payload: dict = Depends(get_current_user)):
    if not payload_has_permission(payload, "recommendations", "read"):
        raise HTTPException(status_code=403, detail="Permission denied")
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            "SELECT rating, COUNT(*) FROM metadata.recommendation_ratings WHERE recommendation_id = ? GROUP BY rating ORDER BY rating",
            [recommendation_id],
        ).fetchall()
    total = sum(int(row[1]) for row in rows)
    weighted = sum(int(row[0]) * int(row[1]) for row in rows)
    return {"data": {"avg_score": round(weighted / total, 2) if total else 0, "total_ratings": total, "distribution": {str(row[0]): row[1] for row in rows}}}


@router.get("/{project_id:int}/bootstrap-status")
def recommendation_bootstrap_status(project_id: int, payload: dict = Depends(get_current_user)):
    _require_recommendation_permission(payload, "read", project_id)
    with connection_lock():
        con = get_connection()
        _ensure_recommendation_bootstrap_status_table(con)
        row = con.execute(
            "SELECT status, recommendation_count, error, started_at, finished_at, updated_at FROM metadata.recommendation_bootstrap_status WHERE project_id = ?",
            [project_id],
        ).fetchone()
        active_count = int(
            con.execute(
                "SELECT COUNT(*) FROM metadata.recommendations WHERE project_id = ? AND status = 'active'",
                [project_id],
            ).fetchone()[0]
        )

    if row:
        status = str(row[0] or "idle").strip().lower() or "idle"
        recommendation_count = int((row[1] or 0) or 0)
        error = str(row[2]) if row[2] else None
        started_at = str(row[3]) if row[3] else None
        finished_at = str(row[4]) if row[4] else None
        updated_at = str(row[5]) if row[5] else None
    else:
        status = "completed" if active_count > 0 else "idle"
        recommendation_count = active_count
        error = None
        started_at = None
        finished_at = None
        updated_at = None

    recommendation_count = max(recommendation_count, active_count)
    is_bootstrapping = status in {"pending", "running"}
    ready = bool(recommendation_count > 0 or status == "completed")
    return {
        "data": {
            "project_id": project_id,
            "status": status,
            "is_bootstrapping": is_bootstrapping,
            "ready": ready,
            "recommendation_count": recommendation_count,
            "active_recommendations": active_count,
            "error": error,
            "started_at": started_at,
            "finished_at": finished_at,
            "updated_at": updated_at,
        }
    }


@router.get("/{project_id:int}")
def list_recommendations(
    project_id: int,
    context: Optional[str] = Query(None),
    max_results: int = Query(5, ge=1, le=50),
    types: Optional[str] = Query(None),
    include_generated: bool = Query(True),
    refresh_generated: bool = Query(False),
    language: Optional[str] = Query(None),
    payload: dict = Depends(get_current_user),
):
    _require_recommendation_permission(payload, "read", project_id)
    from services.recommendation_service import RecommendationService

    con = get_connection()
    svc = RecommendationService(con)
    generated: list[dict[str, Any]] = []

    with connection_lock():
        result_rows = _list_recommendation_rows(con, project_id)
        refresh_cache = bool(refresh_generated) and not bool(context)
        if bool(include_generated) and refresh_cache:
            try:
                _clear_auto_generated_active_recommendations(con, project_id)
                result_rows = _list_recommendation_rows(con, project_id)
            except Exception:
                pass

    should_generate = bool(include_generated) and (bool(context) or refresh_cache or len(result_rows) < max_results)
    if should_generate:
        try:
            generated = svc.get_recommendations(
                project_id,
                context=context,
                max_results=max_results,
                types=types,
                user_id=int(payload.get("sub", 0)),
                language=language or "en",
            )
        except Exception:
            generated = []
        if not context and generated:
            try:
                with connection_lock():
                    _persist_generated_recommendations(con, project_id, generated)
                    result_rows = _list_recommendation_rows(con, project_id)
                    generated = []
            except Exception:
                pass
    existing_titles = {(r.get("title") or "").strip().lower() for r in result_rows if r.get("title")}
    gen_idx = 0
    for g in generated:
        title_lower = (g.get("title") or "").strip().lower()
        if title_lower and title_lower not in existing_titles:
            gen_idx += 1
            g_meta = g.get("metadata", {})
            if isinstance(g_meta, str):
                try:
                    g_meta = json.loads(g_meta)
                except Exception:
                    g_meta = {}
            existing_id = g.get("id")
            result_rows.append({
                "id": existing_id if isinstance(existing_id, int) and existing_id > 0 else -gen_idx,
                "project_id": project_id,
                "title": g["title"],
                "description": g.get("description"),
                "category": g.get("category", "aggregation"),
                "scope": g.get("scope", "project"),
                "source_type": g.get("source_type", "schema"),
                "source_id": g.get("source_id"),
                "confidence": g.get("confidence", 0.5),
                "status": g.get("status", "active"),
                "metadata": g_meta if isinstance(g_meta, dict) else {},
                "created_at": None,
                "updated_at": None,
            })
    result_rows.sort(key=lambda r: -r.get("confidence", 0))
    return {"data": result_rows[:max_results * 2]}


@router.get("/{project_id:int}/onboarding")
def get_onboarding(
    project_id: int,
    max_results: int = Query(5, ge=1, le=20),
    language: Optional[str] = Query(None),
    payload: dict = Depends(get_current_user),
):
    _require_recommendation_permission(payload, "read", project_id)
    from services.recommendation_service import RecommendationService
    with connection_lock():
        con = get_connection()
        svc = RecommendationService(con)
        try:
            results = svc.get_onboarding(project_id, max_results=max_results, language=language or "en")
        except Exception:
            results = []
    return {"data": results}


@router.get("/{project_id:int}/sample-questions")
def get_sample_questions(
    project_id: int,
    language: Optional[str] = Query(None),
    payload: dict = Depends(get_current_user),
):
    _require_recommendation_permission(payload, "read", project_id)
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT sample_dataset FROM metadata.projects WHERE id = ?",
            [project_id],
        ).fetchone()
    sample_dataset = ""
    if row and row[0]:
        sample_dataset = str(row[0]).strip().lower()
    if not sample_dataset:
        with connection_lock():
            con = get_connection()
            rows = con.execute(
                """SELECT d.properties_encrypted FROM metadata.project_datasources pd
                JOIN metadata.datasources d ON d.id = pd.datasource_id
                WHERE pd.project_id = ? AND LOWER(d.type) IN ('duckdb', 'sample')""",
                [project_id],
            ).fetchall()
            for r in rows:
                props = _json_load(r[0], {})
                sd = str(props.get("sampleDataset") or props.get("sample_dataset") or "").strip().lower()
                if sd:
                    sample_dataset = sd
                    break
    from services.recommendation_service import get_sample_dataset_questions
    questions = get_sample_dataset_questions(sample_dataset, language=language or "en")
    return {"data": questions}


@router.post("/{project_id:int}")
def create_recommendation(project_id: int, body: RecommendationCreate, payload: dict = Depends(get_current_user)):
    _require_recommendation_permission(payload, "update", project_id)
    with connection_lock():
        con = get_connection()
        rec_id = _max_id(con, "metadata.recommendations")
        con.execute(
            "INSERT INTO metadata.recommendations (id, project_id, title, description, category, scope, source_type, source_id, confidence, status, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::JSON)",
            [rec_id, project_id, body.title, body.description, body.category, body.scope, body.source_type, body.source_id, body.confidence, body.status, json.dumps(body.metadata or {})],
        )
        row = con.execute(
            "SELECT id, project_id, title, description, category, scope, source_type, source_id, confidence, status, metadata, created_at, updated_at FROM metadata.recommendations WHERE id = ?",
            [rec_id],
        ).fetchone()
    return {"data": _recommendation_to_dict(row)}


@router.put("/{project_id:int}/{recommendation_id:int}")
def update_recommendation(project_id: int, recommendation_id: int, body: RecommendationUpdate, payload: dict = Depends(get_current_user)):
    _require_recommendation_permission(payload, "update", project_id)
    with connection_lock():
        con = get_connection()
        if not con.execute("SELECT id FROM metadata.recommendations WHERE id = ? AND project_id = ?", [recommendation_id, project_id]).fetchone():
            raise HTTPException(status_code=404, detail="Recommendation not found")
        fields = []
        params: list[Any] = []
        for field, value in body.model_dump(exclude_unset=True).items():
            column = "metadata" if field == "metadata" else field
            fields.append(f"{column} = ?::JSON" if field == "metadata" else f"{column} = ?")
            params.append(json.dumps(value or {}) if field == "metadata" else value)
        if fields:
            fields.append("updated_at = CURRENT_TIMESTAMP")
            params.append(recommendation_id)
            con.execute(f"UPDATE metadata.recommendations SET {', '.join(fields)} WHERE id = ?", params)
        row = con.execute(
            "SELECT id, project_id, title, description, category, scope, source_type, source_id, confidence, status, metadata, created_at, updated_at FROM metadata.recommendations WHERE id = ?",
            [recommendation_id],
        ).fetchone()
    return {"data": _recommendation_to_dict(row)}


@router.delete("/{project_id:int}/{recommendation_id:int}")
def delete_recommendation(project_id: int, recommendation_id: int, payload: dict = Depends(get_current_user)):
    _require_recommendation_permission(payload, "update", project_id)
    with connection_lock():
        con = get_connection()
        con.execute("DELETE FROM metadata.recommendations WHERE id = ? AND project_id = ?", [recommendation_id, project_id])
    return {"data": {"success": True}}


CATALOG_SORT_ALLOWED = frozenset({"frequency", "last_used"})


@router.get("/{project_id:int}/catalog")
def list_catalog(project_id: int, search: Optional[str] = None, sort: str = "frequency", payload: dict = Depends(get_current_user)):
    _require_recommendation_permission(payload, "read", project_id)
    sort_key = sort if sort in CATALOG_SORT_ALLOWED else "frequency"
    order = "frequency DESC, last_used DESC" if sort_key == "frequency" else "last_used DESC"
    conditions = ["project_id = ?"]
    params: list[Any] = [project_id]
    if search:
        conditions.append("(question LIKE ? OR sql_text LIKE ?) ESCAPE '\\'")
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.extend([f"%{escaped}%", f"%{escaped}%"])
    with connection_lock():
        rows = get_connection().execute(
            f"SELECT id, project_id, question, sql_text, frequency, last_used, metadata, verified FROM metadata.question_sql_catalog WHERE {' AND '.join(conditions)} ORDER BY {order}",
            params,
        ).fetchall()
    return {"data": [_catalog_to_dict(row) for row in rows]}


@router.post("/{project_id:int}/catalog")
def create_catalog(project_id: int, body: CatalogEntryCreate, payload: dict = Depends(get_current_user)):
    _require_recommendation_permission(payload, "update", project_id)
    with connection_lock():
        con = get_connection()
        entry_id = _max_id(con, "metadata.question_sql_catalog")
        con.execute(
            "INSERT INTO metadata.question_sql_catalog (id, project_id, question, sql_text, metadata, verified) VALUES (?, ?, ?, ?, ?::JSON, ?)",
            [entry_id, project_id, body.question, body.sql, json.dumps(body.metadata or {}), bool(body.verified)],
        )
    return {"data": {"id": entry_id}}


@router.put("/{project_id:int}/catalog/{entry_id:int}")
def update_catalog(project_id: int, entry_id: int, body: CatalogEntryUpdate, payload: dict = Depends(get_current_user)):
    _require_recommendation_permission(payload, "update", project_id)
    with connection_lock():
        con = get_connection()
        if not con.execute("SELECT id FROM metadata.question_sql_catalog WHERE id = ? AND project_id = ?", [entry_id, project_id]).fetchone():
            raise HTTPException(status_code=404, detail="Catalog entry not found")
        fields = []
        params: list[Any] = []
        if body.question is not None:
            fields.append("question = ?")
            params.append(body.question)
        if body.sql is not None:
            fields.append("sql_text = ?")
            params.append(body.sql)
        if body.metadata is not None:
            fields.append("metadata = ?::JSON")
            params.append(json.dumps(body.metadata))
        if body.verified is not None:
            fields.append("verified = ?")
            params.append(bool(body.verified))
        if fields:
            params.append(entry_id)
            con.execute(f"UPDATE metadata.question_sql_catalog SET {', '.join(fields)} WHERE id = ?", params)
    return {"data": {"success": True}}


@router.delete("/{project_id:int}/catalog/{entry_id:int}")
def delete_catalog(project_id: int, entry_id: int, payload: dict = Depends(get_current_user)):
    _require_recommendation_permission(payload, "update", project_id)
    with connection_lock():
        get_connection().execute("DELETE FROM metadata.question_sql_catalog WHERE id = ? AND project_id = ?", [entry_id, project_id])
    return {"data": {"success": True}}


@router.get("/{project_id:int}/hints")
def list_hints(project_id: int, payload: dict = Depends(get_current_user)):
    _require_recommendation_permission(payload, "read", project_id)
    with connection_lock():
        rows = get_connection().execute(
            "SELECT id, user_id, hint_text, source_query, confidence, created_at, expires_at FROM metadata.user_preference_hints WHERE user_id = ? ORDER BY created_at DESC",
            [int(payload["sub"])],
        ).fetchall()
    return {"data": [_hint_to_dict(row) for row in rows]}


@router.post("/{project_id:int}/hints")
def create_hint(project_id: int, body: HintCreate, payload: dict = Depends(get_current_user)):
    _require_recommendation_permission(payload, "update", project_id)
    with connection_lock():
        con = get_connection()
        hint_id = _max_id(con, "metadata.user_preference_hints")
        con.execute(
            "INSERT INTO metadata.user_preference_hints (id, user_id, hint_text, source_query, confidence) VALUES (?, ?, ?, ?, ?)",
            [hint_id, int(payload["sub"]), body.hint_text, body.source_query, body.confidence],
        )
    return {"data": {"id": hint_id}}


@router.put("/{project_id:int}/hints/{hint_id:int}")
def update_hint(project_id: int, hint_id: int, body: HintUpdate, payload: dict = Depends(get_current_user)):
    _require_recommendation_permission(payload, "update", project_id)
    with connection_lock():
        con = get_connection()
        if not con.execute("SELECT id FROM metadata.user_preference_hints WHERE id = ? AND user_id = ?", [hint_id, int(payload["sub"])]).fetchone():
            raise HTTPException(status_code=404, detail="Hint not found")
        fields = []
        params: list[Any] = []
        for col in ("hint_text", "source_query", "confidence"):
            value = getattr(body, col, None)
            if value is not None:
                fields.append(f"{col} = ?")
                params.append(value)
        if fields:
            params.append(hint_id)
            con.execute(f"UPDATE metadata.user_preference_hints SET {', '.join(fields)} WHERE id = ?", params)
    return {"data": {"success": True}}


@router.delete("/{project_id:int}/hints/{hint_id:int}")
def delete_hint(project_id: int, hint_id: int, payload: dict = Depends(get_current_user)):
    _require_recommendation_permission(payload, "update", project_id)
    with connection_lock():
        get_connection().execute("DELETE FROM metadata.user_preference_hints WHERE id = ? AND user_id = ?", [hint_id, int(payload["sub"])])
    return {"data": {"success": True}}


@router.get("/{project_id:int}/scores")
def list_scores(project_id: int, source_layer: Optional[str] = Query(None), payload: dict = Depends(get_current_user)):
    _require_recommendation_permission(payload, "read", project_id)
    conditions = ["project_id = ?"]
    params: list[Any] = [project_id]
    if source_layer:
        conditions.append("source_layer = ?")
        params.append(source_layer)
    with connection_lock():
        rows = get_connection().execute(
            f"SELECT id, user_id, recommendation_id, project_id, source_layer, recommend_type, score, session_context, source_question, weight_adjustment, created_at FROM metadata.recommendation_scores WHERE {' AND '.join(conditions)} ORDER BY created_at DESC",
            params,
        ).fetchall()
    return {"data": [_score_to_dict(row) for row in rows]}


@router.post("/{project_id:int}/rate/{recommendation_id:int}")
def rate_recommendation(project_id: int, recommendation_id: int, body: RateRequest, payload: dict = Depends(get_current_user)):
    _require_recommendation_permission(payload, "read", project_id)
    with connection_lock():
        con = get_connection()
        if not con.execute("SELECT id FROM metadata.recommendations WHERE id = ? AND project_id = ?", [recommendation_id, project_id]).fetchone():
            raise HTTPException(status_code=404, detail="Recommendation not found")
        con.execute("BEGIN TRANSACTION")
        try:
            rating_id = _max_id(con, "metadata.recommendation_ratings")
            con.execute(
                "INSERT INTO metadata.recommendation_ratings (id, recommendation_id, user_id, rating, comment) VALUES (?, ?, ?, ?, ?)",
                [rating_id, recommendation_id, int(payload["sub"]), body.rating, body.comment],
            )
            score_id = _max_id(con, "metadata.recommendation_scores")
            con.execute(
                "INSERT INTO metadata.recommendation_scores (id, user_id, recommendation_id, project_id, source_layer, recommend_type, score, session_context, source_question) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [score_id, int(payload["sub"]), recommendation_id, project_id, body.source_layer, body.recommend_type, body.rating, body.context, body.comment],
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    try:
        from services.recommendation_service import _adjust_weights_from_scores
        _adjust_weights_from_scores(project_id)
    except Exception:
        pass
    return {"data": {"id": rating_id, "score_id": score_id, "recommendation_id": recommendation_id, "rating": body.rating, "comment": body.comment}}


@router.post("/{project_id:int}/accept/{recommendation_id:int}")
def accept_recommendation(project_id: int, recommendation_id: int, payload: dict = Depends(get_current_user)):
    _require_recommendation_permission(payload, "update", project_id)
    with connection_lock():
        con = get_connection()
        if not con.execute("SELECT id FROM metadata.recommendations WHERE id = ? AND project_id = ?", [recommendation_id, project_id]).fetchone():
            raise HTTPException(status_code=404, detail="Recommendation not found")
        feedback_id = _max_id(con, "metadata.recommendation_feedback")
        con.execute(
            "INSERT INTO metadata.recommendation_feedback (id, user_id, project_id, recommendation_id, action) VALUES (?, ?, ?, ?, 'accept')",
            [feedback_id, int(payload["sub"]), project_id, recommendation_id],
        )
        con.execute("UPDATE metadata.recommendations SET status = 'accepted', updated_at = CURRENT_TIMESTAMP WHERE id = ?", [recommendation_id])
    return {"data": {"success": True}}


@router.post("/{project_id:int}/dismiss/{recommendation_id:int}")
def dismiss_recommendation(project_id: int, recommendation_id: int, payload: dict = Depends(get_current_user)):
    _require_recommendation_permission(payload, "update", project_id)
    with connection_lock():
        con = get_connection()
        if not con.execute("SELECT id FROM metadata.recommendations WHERE id = ? AND project_id = ?", [recommendation_id, project_id]).fetchone():
            raise HTTPException(status_code=404, detail="Recommendation not found")
        feedback_id = _max_id(con, "metadata.recommendation_feedback")
        con.execute(
            "INSERT INTO metadata.recommendation_feedback (id, user_id, project_id, recommendation_id, action) VALUES (?, ?, ?, ?, 'dismiss')",
            [feedback_id, int(payload["sub"]), project_id, recommendation_id],
        )
        con.execute("UPDATE metadata.recommendations SET status = 'dismissed', updated_at = CURRENT_TIMESTAMP WHERE id = ?", [recommendation_id])
    return {"data": {"success": True}}
