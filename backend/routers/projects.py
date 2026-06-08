from __future__ import annotations

import json
import logging
import os
import shutil
import threading

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response

from db import connection_lock, get_connection
from models.schemas import (
    AddProjectMemberRequest,
    ProjectCreate,
    ProjectDatasourceUpdate,
    ProjectUpdate,
    UpdateProjectMemberRequest,
)
from routers.auth import get_current_user, payload_has_permission, require_permission
from services.ask_service import clear_analysis_cache
from services.crypto_service import decrypt_json, encrypt_json, is_encrypted_value
from services.prompt_templates import DEFAULT_PROJECT_PROMPT

LOGGER = logging.getLogger(__name__)

router = APIRouter()
_tables_ensured = False
_tables_lock = threading.Lock()
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.getenv("PRISMBI_DATA_DIR") or os.path.join(BACKEND_DIR, "data")
PROJECT_DATA_DIR = os.path.join(DATA_DIR, "projects")
SENSITIVE_CONFIG_KEYS = {
    "password",
    "passwd",
    "pwd",
    "api_key",
    "apikey",
    "access_key",
    "secret",
    "token",
    "client_secret",
    "private_key",
}
PROJECT_DELETE_TABLES = [
    "metadata.modeling_diagrams",
    "metadata.views",
    "metadata.relations",
    "metadata.calculated_fields",
    "metadata.models",
    "metadata.model_datasource_mappings",
    "metadata.instructions",
    "metadata.sql_pairs",
    "metadata.recommended_questions_cache",
    "metadata.question_sql_catalog",
    "metadata.interest_clusters",
    "metadata.recommendation_feedback",
    "metadata.recommendation_scores",
    "metadata.recommendations",
    "metadata.recommendation_bootstrap_status",
    "metadata.row_level_security_policies",
    "metadata.column_level_security_policies",
    "metadata.dashboards",
    "metadata.threads",
    "metadata.api_history",
    "metadata.memories",
]
_BOOTSTRAP_SYNC_TRUTHY = {"1", "true", "yes", "y", "on"}


def _coerce_positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(str(value).strip()) if value is not None else default
    except Exception:
        return default
    return parsed if parsed > 0 else default


PROJECT_RECOMMENDATION_BOOTSTRAP_MAX_RESULTS = _coerce_positive_int(
    os.getenv("PRISMBI_PROJECT_RECOMMENDATION_BOOTSTRAP_MAX_RESULTS"),
    6,
)


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

def _safe_json_loads(value, fallback=None):
    decoded = decrypt_json(value, fallback)
    return decoded if decoded is not None else fallback


def _normalize_language(value: str | None) -> str:
    language = str(value or "en").strip().lower()
    return language or "en"


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


def _count_active_recommendations(con, project_id: int) -> int:
    row = con.execute(
        "SELECT COUNT(*) FROM metadata.recommendations WHERE project_id = ? AND status = 'active'",
        [project_id],
    ).fetchone()
    return int((row[0] if row else 0) or 0)


def _upsert_recommendation_bootstrap_status(
    con,
    project_id: int,
    status: str,
    recommendation_count: int,
    error: str | None = None,
    mark_started: bool = False,
    mark_finished: bool = False,
    reset_timing: bool = False,
) -> None:
    _ensure_recommendation_bootstrap_status_table(con)
    con.execute(
        """
        INSERT INTO metadata.recommendation_bootstrap_status (
            project_id,
            status,
            recommendation_count,
            error,
            started_at,
            finished_at,
            updated_at
        )
        VALUES (
            ?,
            ?,
            ?,
            ?,
            CASE WHEN ? THEN now() ELSE NULL END,
            CASE WHEN ? THEN now() ELSE NULL END,
            now()
        )
        ON CONFLICT(project_id) DO UPDATE SET
            status = EXCLUDED.status,
            recommendation_count = EXCLUDED.recommendation_count,
            error = EXCLUDED.error,
            started_at = CASE
                WHEN ? THEN now()
                WHEN ? THEN NULL
                ELSE metadata.recommendation_bootstrap_status.started_at
            END,
            finished_at = CASE
                WHEN ? THEN now()
                WHEN ? THEN NULL
                ELSE metadata.recommendation_bootstrap_status.finished_at
            END,
            updated_at = now()
        """,
        [
            project_id,
            status,
            int(recommendation_count),
            (error or None),
            bool(mark_started),
            bool(mark_finished),
            bool(mark_started),
            bool(reset_timing),
            bool(mark_finished),
            bool(reset_timing),
        ],
    )


def _bootstrap_project_recommendations(project_id: int, user_id: int, language: str | None = None) -> None:
    from routers.recommendations import _persist_generated_recommendations
    from services.recommendation_service import RecommendationService

    try:
        con = get_connection()
        with connection_lock():
            _ensure_recommendation_bootstrap_status_table(con)
            exists = con.execute("SELECT id FROM metadata.projects WHERE id = ?", [project_id]).fetchone()
            if not exists:
                return
            existing_active = _count_active_recommendations(con, project_id)
            _upsert_recommendation_bootstrap_status(
                con,
                project_id,
                status="running",
                recommendation_count=existing_active,
                error=None,
                mark_started=True,
            )
        if existing_active > 0:
            with connection_lock():
                _upsert_recommendation_bootstrap_status(
                    con,
                    project_id,
                    status="completed",
                    recommendation_count=existing_active,
                    error=None,
                    mark_finished=True,
                )
            return

        svc = RecommendationService(con)
        generated = svc.get_recommendations(
            project_id,
            context=None,
            max_results=PROJECT_RECOMMENDATION_BOOTSTRAP_MAX_RESULTS,
            user_id=max(int(user_id), 0),
            language=_normalize_language(language),
        )
        with connection_lock():
            if generated:
                _persist_generated_recommendations(con, project_id, generated)
            active_count = _count_active_recommendations(con, project_id)
            _upsert_recommendation_bootstrap_status(
                con,
                project_id,
                status="completed",
                recommendation_count=active_count,
                error=None,
                mark_finished=True,
            )
    except Exception as exc:
        err_msg = str(exc).strip() or "bootstrap_failed"
        with connection_lock():
            try:
                con = get_connection()
                _ensure_recommendation_bootstrap_status_table(con)
                active_count = _count_active_recommendations(con, project_id)
                _upsert_recommendation_bootstrap_status(
                    con,
                    project_id,
                    status="failed",
                    recommendation_count=active_count,
                    error=err_msg[:500],
                    mark_finished=True,
                )
            except Exception:
                pass
        LOGGER.exception("Failed to bootstrap recommendations for project %s", project_id)


def _schedule_project_recommendation_bootstrap(project_id: int, user_id: int, language: str | None = None) -> None:
    normalized_language = _normalize_language(language)
    pytest_mode = bool(os.getenv("PYTEST_CURRENT_TEST"))
    sync_mode = pytest_mode or str(os.getenv("PRISMBI_PROJECT_RECOMMENDATION_BOOTSTRAP_SYNC", "")).strip().lower() in _BOOTSTRAP_SYNC_TRUTHY
    if sync_mode:
        _bootstrap_project_recommendations(project_id, user_id, normalized_language)
        return

    worker = threading.Thread(
        target=_bootstrap_project_recommendations,
        args=(project_id, user_id, normalized_language),
        name=f"project-recommendation-bootstrap-{project_id}",
        daemon=True,
    )
    worker.start()


def _encrypted_json_value(value) -> str:
    return value if is_encrypted_value(value) else encrypt_json(value)


def _redact_config(value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if any(part in str(key).lower() for part in SENSITIVE_CONFIG_KEYS) else _redact_config(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_config(item) for item in value]
    return value


def _ensure_tables():
    global _tables_ensured
    with _tables_lock:
        if _tables_ensured:
            return
        con = get_connection()
        con.execute("""
            CREATE TABLE IF NOT EXISTS metadata.connections (
                id INTEGER PRIMARY KEY,
                name VARCHAR NOT NULL,
                type VARCHAR NOT NULL,
                config VARCHAR,
                is_available BOOLEAN DEFAULT true,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        _tables_ensured = True


def _project_row(row):
    connection_info = _safe_json_loads(row[5], row[5])
    return {
        "id": row[0],
        "name": row[1],
        "description": row[2],
        "display_name": row[3],
        "type": row[4],
        "connection_info": _redact_config(connection_info),
        "catalog": row[6],
        "schema_name": row[7],
        "sample_dataset": row[8],
        "language": row[9],
        "version": row[10],
        "is_current": row[11],
        "created_at": str(row[12]) if row[12] else None,
        "updated_at": str(row[13]) if row[13] else None,
        "prompt": row[14] if len(row) > 14 else None,
    }


def _enrich_project(data, con):
    member_row = con.execute(
        "SELECT COUNT(*) FROM metadata.user_roles WHERE project_id = ?", [data["id"]]
    ).fetchone()
    datasource_row = con.execute(
        "SELECT COUNT(*) FROM metadata.project_datasources WHERE project_id = ?", [data["id"]]
    ).fetchone()
    data["member_count"] = member_row[0] if member_row else 0
    data["datasource_count"] = datasource_row[0] if datasource_row else 0
    return data


def _assign_project_creator(con, project_id: int, user_id: int) -> None:
    role = con.execute(
        "SELECT id FROM metadata.roles WHERE name = 'project_admin' LIMIT 1"
    ).fetchone()
    if not role:
        return
    existing = con.execute(
        """SELECT id FROM metadata.user_roles
           WHERE user_id = ? AND role_id = ? AND project_id = ?""",
        [user_id, role[0], project_id],
    ).fetchone()
    if existing:
        return
    max_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.user_roles").fetchone()[0]
    con.execute(
        "INSERT INTO metadata.user_roles (id, user_id, role_id, project_id, granted_by) VALUES (?, ?, ?, ?, ?)",
        [max_id, user_id, role[0], project_id, user_id],
    )


def _require_project_role(con, role_id: int) -> None:
    role = con.execute("SELECT scope FROM metadata.roles WHERE id = ?", [role_id]).fetchone()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if str(role[0]).upper() != "PROJECT":
        raise HTTPException(status_code=400, detail="Only project-scoped roles can be assigned to project members")


def _table_exists(con, qualified_table: str) -> bool:
    schema, table = qualified_table.split(".", 1)
    row = con.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = ? AND table_name = ?
        LIMIT 1
        """,
        [schema, table],
    ).fetchone()
    return bool(row)


def _delete_project_files(project_id: int) -> None:
    """Delete project data files from disk.
    
    SAFETY: This function has multiple safeguards to prevent accidental deletion:
    1. Validates project_id is a positive integer
    2. Validates the resolved path is under PROJECT_DATA_DIR (prevents path traversal)
    3. Logs all deletion attempts for audit trail
    4. Never silently swallows exceptions
    5. Refuses to delete project_id <= 0 (system projects)
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # Guard 1: Validate project_id
    if not isinstance(project_id, int) or project_id <= 0:
        logger.error("SAFETY: Refusing to delete project files: invalid project_id=%r", project_id)
        return
    
    project_dir = os.path.join(PROJECT_DATA_DIR, str(project_id))
    
    # Guard 2: Validate resolved path is under PROJECT_DATA_DIR
    resolved = os.path.realpath(project_dir)
    expected_prefix = os.path.realpath(PROJECT_DATA_DIR)
    if not resolved.startswith(expected_prefix + os.sep):
        logger.error(
            "SAFETY: Refusing to delete project files: path traversal detected. "
            "project_dir=%s, resolved=%s, expected_prefix=%s",
            project_dir, resolved, expected_prefix
        )
        return
    
    # Guard 3: Log the deletion attempt
    logger.info(
        "AUDIT: Deleting project files: project_id=%d, path=%s, resolved=%s",
        project_id, project_dir, resolved,
    )
    
    if not os.path.exists(project_dir):
        logger.info("Project directory does not exist, skipping: %s", project_dir)
        return
    
    # Guard 4: List files before deletion for audit
    try:
        files_before = []
        for root, dirs, files in os.walk(project_dir):
            for f in files:
                files_before.append(os.path.join(root, f))
        logger.info("Files to be deleted: %s", files_before)
    except Exception as exc:
        logger.warning("Failed to list files before deletion: %s", exc)
    
    try:
        shutil.rmtree(project_dir)
        logger.info("Successfully deleted project files: %s", project_dir)
    except Exception as exc:
        logger.error("Failed to delete project files: path=%s, error=%s", project_dir, exc, exc_info=True)


def _delete_project_data(con, project_id: int) -> None:
    """Delete all project data from database and files.
    
    This function is called by the DELETE /api/projects/{id} endpoint.
    It performs cascading deletes across all related tables and then
    deletes the project's data files from disk.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # Verify project exists before deletion
    project = con.execute(
        "SELECT id, name FROM metadata.projects WHERE id = ?", [project_id]
    ).fetchone()
    if not project:
        logger.warning("Attempted to delete non-existent project: project_id=%d", project_id)
        return
    
    logger.info(
        "Starting project deletion: project_id=%d, project_name=%s",
        project_id, project[1]
    )
    
    bindings = con.execute(
        "SELECT id, datasource_id FROM metadata.project_datasources WHERE project_id = ?",
        [project_id],
    ).fetchall()
    binding_ids = [int(row[0]) for row in bindings]
    datasource_ids = sorted({int(row[1]) for row in bindings})

    for sql in [
        "DELETE FROM metadata.dashboard_items WHERE dashboard_id IN (SELECT id FROM metadata.dashboards WHERE project_id = ?)",
        "DELETE FROM metadata.thread_responses WHERE thread_id IN (SELECT id FROM metadata.threads WHERE project_id = ?)",
        "DELETE FROM metadata.api_history WHERE thread_id IN (SELECT id FROM metadata.threads WHERE project_id = ?)",
        "DELETE FROM metadata.recommendation_ratings WHERE recommendation_id IN (SELECT id FROM metadata.recommendations WHERE project_id = ?)",
        "DELETE FROM metadata.layer_weight_history WHERE triggered_by_score_id IN (SELECT id FROM metadata.recommendation_scores WHERE project_id = ?)",
    ]:
        if _table_exists(con, sql.split(" ")[2]):
            con.execute(sql, [project_id])

    if binding_ids and _table_exists(con, "metadata.datasource_runtime_state"):
        placeholders = ",".join(["?"] * len(binding_ids))
        con.execute(
            f"DELETE FROM metadata.datasource_runtime_state WHERE binding_id IN ({placeholders})",
            binding_ids,
        )

    for table in PROJECT_DELETE_TABLES:
        if _table_exists(con, table):
            con.execute(f"DELETE FROM {table} WHERE project_id = ?", [project_id])

    con.execute("DELETE FROM metadata.project_datasources WHERE project_id = ?", [project_id])
    con.execute("DELETE FROM metadata.user_roles WHERE project_id = ?", [project_id])
    con.execute("DELETE FROM metadata.user_permission_overrides WHERE project_id = ?", [project_id])
    con.execute("UPDATE metadata.users SET default_project_id = NULL WHERE default_project_id = ?", [project_id])

    for datasource_id in datasource_ids:
        still_bound = con.execute(
            "SELECT COUNT(*) FROM metadata.project_datasources WHERE datasource_id = ?",
            [datasource_id],
        ).fetchone()[0]
        if still_bound:
            continue
        con.execute("DELETE FROM metadata.datasources WHERE id = ?", [datasource_id])

    _delete_project_files(project_id)


def _require_project_permission(payload: dict, resource: str, action: str, project_id: int | None = None) -> None:
    if project_id is not None and project_id <= 0:
        raise HTTPException(status_code=400, detail="A real project is required")
    if not payload_has_permission(payload, resource, action, project_id):
        raise HTTPException(status_code=403, detail="Permission denied")


@router.get("")
def list_projects(
    payload: dict = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = Query(""),
):
    with connection_lock():
        con = get_connection()
        if search:
            like = f"%{_escape_like(search)}%"
            rows = con.execute(
                "SELECT * FROM metadata.projects WHERE name ILIKE ? OR display_name ILIKE ? OR description ILIKE ? ESCAPE '\\' ORDER BY created_at DESC",
                [like, like, like],
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM metadata.projects ORDER BY created_at DESC"
            ).fetchall()
        offset = (page - 1) * page_size
        user_id = int(payload["sub"])
        visible = [r for r in rows if r[0] != 0 and payload_has_permission(payload, "projects", "read", r[0])]
        default_project = con.execute(
            "SELECT default_project_id FROM metadata.users WHERE id = ?",
            [user_id],
        ).fetchone()
        default_project_id = default_project[0] if default_project else None
        total = len(visible)
        items = []
        for row in visible[offset : offset + page_size]:
            item = _enrich_project(_project_row(row), con)
            item["is_current"] = item["id"] == default_project_id
            items.append(item)
        return {"data": {"items": items, "total": total, "page": page, "page_size": page_size}}


@router.post("")
def create_project(
    body: ProjectCreate,
    request: Request,
    payload: dict = Depends(require_permission("admin", "manage")),
):
    idempotency_key = request.headers.get("Idempotency-Key")
    created_project_id: int | None = None
    created_project_language = _normalize_language(body.language)
    creator_user_id = int(payload["sub"])
    with connection_lock():
        con = get_connection()

        if idempotency_key:
            row = con.execute(
                "SELECT response FROM metadata.idempotency_keys WHERE key = ? AND created_at > CURRENT_TIMESTAMP - INTERVAL 1 DAY",
                [idempotency_key],
            ).fetchone()
            if row is not None:
                cached = row[0]
                if isinstance(cached, str):
                    try:
                        cached = json.loads(cached)
                    except Exception:
                        LOGGER.warning("Invalid cached idempotency response for key %s, ignoring", idempotency_key)
                        cached = None
                if isinstance(cached, dict):
                    project_id = ((cached.get("data") or {}).get("id"))
                    if isinstance(project_id, int):
                        still_exists = con.execute(
                            "SELECT 1 FROM metadata.projects WHERE id = ?",
                            [project_id],
                        ).fetchone()
                        if still_exists:
                            return cached

        max_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.projects").fetchone()[0]
        con.execute(
            "INSERT INTO metadata.projects (id, name, display_name, description, prompt, type, connection_info, language, sample_dataset) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                max_id,
                body.name,
                body.display_name,
                body.description,
                body.prompt or DEFAULT_PROJECT_PROMPT,
                body.type,
                encrypt_json(_safe_json_loads(body.connection_info, {})) if body.connection_info else None,
                body.language or "EN",
                body.sample_dataset,
            ],
        )
        _assign_project_creator(con, max_id, creator_user_id)
        row = con.execute("SELECT * FROM metadata.projects WHERE id = ?", [max_id]).fetchone()
        data = _enrich_project(_project_row(row), con)
        data["is_current"] = False
        result = {"data": data}
        created_project_id = int(max_id)
        _upsert_recommendation_bootstrap_status(
            con,
            created_project_id,
            status="pending",
            recommendation_count=0,
            error=None,
            reset_timing=True,
        )

        if idempotency_key:
            con.execute(
                "INSERT OR REPLACE INTO metadata.idempotency_keys (key, response, created_at) VALUES (?, ?::JSON, CURRENT_TIMESTAMP)",
                [idempotency_key, json.dumps(result)],
            )

    if created_project_id is not None:
        _schedule_project_recommendation_bootstrap(created_project_id, creator_user_id, created_project_language)

    return result


@router.get("/{project_id:int}")
def get_project(
    project_id: int,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        _require_project_permission(payload, "projects", "read", project_id)
        con = get_connection()
        row = con.execute("SELECT * FROM metadata.projects WHERE id = ?", [project_id]).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Project not found")
        data = _enrich_project(_project_row(row), con)
        current = con.execute(
            "SELECT default_project_id FROM metadata.users WHERE id = ?",
            [int(payload["sub"])],
        ).fetchone()
        data["is_current"] = bool(current and current[0] == project_id)
        return {"data": data}


@router.put("/{project_id:int}")
def update_project(
    project_id: int,
    body: ProjectUpdate,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        _require_project_permission(payload, "projects", "update", project_id)
        con = get_connection()
        existing = con.execute("SELECT id FROM metadata.projects WHERE id = ?", [project_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Project not found")
        sets = []
        params = []
        for col in ("name", "display_name", "description", "prompt", "type", "language"):
            val = getattr(body, col, None)
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)
        if body.connection_info is not None:
            raw = _safe_json_loads(body.connection_info, None)
            if raw is not None:
                has_redacted = isinstance(raw, dict) and any(
                    v == "[REDACTED]" for v in raw.values() if isinstance(v, str)
                )
                if has_redacted:
                    current = con.execute(
                        "SELECT connection_info FROM metadata.projects WHERE id = ?",
                        [project_id],
                    ).fetchone()
                    if current and current[0]:
                        existing = _safe_json_loads(current[0], {})
                        if isinstance(existing, dict) and isinstance(raw, dict):
                            for k, v in raw.items():
                                if v != "[REDACTED]":
                                    existing[k] = v
                            raw = existing
                sets.append("connection_info = ?")
                params.append(encrypt_json(raw))
        if sets:
            sets.append("updated_at = CURRENT_TIMESTAMP")
            params.append(project_id)
            con.execute(f"UPDATE metadata.projects SET {', '.join(sets)} WHERE id = ?", params)
        row = con.execute("SELECT * FROM metadata.projects WHERE id = ?", [project_id]).fetchone()
        data = _enrich_project(_project_row(row), con)
        current = con.execute(
            "SELECT default_project_id FROM metadata.users WHERE id = ?",
            [int(payload["sub"])],
        ).fetchone()
        data["is_current"] = bool(current and current[0] == project_id)
        return {"data": data}


@router.delete("/{project_id:int}")
def delete_project(
    project_id: int,
    payload: dict = Depends(require_permission("admin", "manage")),
):
    if project_id <= 0:
        raise HTTPException(status_code=400, detail="Cannot delete system project")
    with connection_lock():
        con = get_connection()
        existing = con.execute("SELECT id, name FROM metadata.projects WHERE id = ?", [project_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Project not found")
        user_id = int(payload["sub"])
        default_check = con.execute(
            "SELECT default_project_id FROM metadata.users WHERE id = ?", [user_id]
        ).fetchone()
        if default_check and default_check[0] == project_id:
            con.execute("UPDATE metadata.users SET default_project_id = NULL WHERE id = ?", [user_id])
        LOGGER.info(
            "Deleting project: id=%d, name=%s, user_id=%d, username=%s",
            project_id, existing[1], user_id, payload.get("username", "unknown"),
        )
        _delete_project_data(con, project_id)
        con.execute("DELETE FROM metadata.projects WHERE id = ?", [project_id])
        return {"data": {"success": True}}


@router.post("/{project_id:int}/switch")
def switch_project(
    project_id: int,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        _require_project_permission(payload, "projects", "read", project_id)
        con = get_connection()
        existing = con.execute("SELECT id FROM metadata.projects WHERE id = ?", [project_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Project not found")
        con.execute(
            "UPDATE metadata.users SET default_project_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [project_id, int(payload["sub"])],
        )
        return {"data": {"success": True}}


# ── Members ──────────────────────────────────────────────────────────

@router.get("/{project_id:int}/members")
def list_members(
    project_id: int,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        _require_project_permission(payload, "projects", "manage", project_id)
        con = get_connection()
        rows = con.execute(
            """SELECT ur.id, ur.user_id, u.username, u.display_name, ur.role_id, r.name, ur.expires_at, ur.created_at
               FROM metadata.user_roles ur
               JOIN metadata.users u ON u.id = ur.user_id
               JOIN metadata.roles r ON r.id = ur.role_id
               WHERE ur.project_id = ?
               ORDER BY ur.created_at DESC""",
            [project_id],
        ).fetchall()
        data = [
            {
                "id": r[0],
                "user_id": r[1],
                "username": r[2],
                "display_name": r[3],
                "role_id": r[4],
                "role_name": r[5],
                "expires_at": str(r[6]) if r[6] else None,
                "created_at": str(r[7]) if r[7] else None,
            }
            for r in rows
        ]
    return {"data": data}


@router.post("/{project_id:int}/members")
def add_member(
    project_id: int,
    body: AddProjectMemberRequest,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        _require_project_permission(payload, "projects", "manage", project_id)
        con = get_connection()
        user = con.execute("SELECT id FROM metadata.users WHERE id = ?", [body.user_id]).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        _require_project_role(con, body.role_id)
        existing = con.execute(
            "SELECT id FROM metadata.user_roles WHERE user_id = ? AND role_id = ? AND project_id = ?",
            [body.user_id, body.role_id, project_id],
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Member already exists with this role")
        max_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.user_roles").fetchone()[0]
        con.execute(
            "INSERT INTO metadata.user_roles (id, user_id, role_id, project_id, granted_by) VALUES (?, ?, ?, ?, ?)",
            [max_id, body.user_id, body.role_id, project_id, int(payload["sub"])],
        )
        row = con.execute(
            """SELECT ur.id, ur.user_id, u.username, u.display_name, ur.role_id, r.name, ur.expires_at, ur.created_at
               FROM metadata.user_roles ur
               JOIN metadata.users u ON u.id = ur.user_id
               JOIN metadata.roles r ON r.id = ur.role_id
               WHERE ur.id = ?""",
            [max_id],
        ).fetchone()
        data = {
            "id": row[0],
            "user_id": row[1],
            "username": row[2],
            "display_name": row[3],
            "role_id": row[4],
            "role_name": row[5],
            "expires_at": str(row[6]) if row[6] else None,
            "created_at": str(row[7]) if row[7] else None,
        }
    return {"data": data}


@router.put("/{project_id:int}/members/{member_id:int}")
def update_member(
    project_id: int,
    member_id: int,
    body: UpdateProjectMemberRequest,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        _require_project_permission(payload, "projects", "manage", project_id)
        con = get_connection()
        _require_project_role(con, body.role_id)
        existing = con.execute(
            "SELECT id FROM metadata.user_roles WHERE id = ? AND project_id = ?",
            [member_id, project_id],
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Member not found")
        con.execute("UPDATE metadata.user_roles SET role_id = ? WHERE id = ?", [body.role_id, member_id])
        row = con.execute(
            """SELECT ur.id, ur.user_id, u.username, u.display_name, ur.role_id, r.name, ur.expires_at, ur.created_at
               FROM metadata.user_roles ur
               JOIN metadata.users u ON u.id = ur.user_id
               JOIN metadata.roles r ON r.id = ur.role_id
               WHERE ur.id = ?""",
            [member_id],
        ).fetchone()
        data = {
            "id": row[0],
            "user_id": row[1],
            "username": row[2],
            "display_name": row[3],
            "role_id": row[4],
            "role_name": row[5],
            "expires_at": str(row[6]) if row[6] else None,
            "created_at": str(row[7]) if row[7] else None,
        }
    return {"data": data}


@router.delete("/{project_id:int}/members/{member_id:int}")
def remove_member(
    project_id: int,
    member_id: int,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        _require_project_permission(payload, "projects", "manage", project_id)
        con = get_connection()
        existing = con.execute(
            "SELECT id FROM metadata.user_roles WHERE id = ? AND project_id = ?",
            [member_id, project_id],
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Member not found")
        con.execute("DELETE FROM metadata.user_roles WHERE id = ?", [member_id])
        return {"data": {"success": True}}


# ── Datasource (per-project) ─────────────────────────────────────────

@router.get("/{project_id:int}/datasource")
def get_project_datasource(
    project_id: int,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        _require_project_permission(payload, "datasources", "read", project_id)
        con = get_connection()
        row = con.execute(
            """SELECT pd.id, pd.project_id, pd.datasource_id, pd.alias, pd.config_overrides,
                      d.name, d.type, d.properties_encrypted, d.description
               FROM metadata.project_datasources pd
               JOIN metadata.datasources d ON d.id = pd.datasource_id
               WHERE pd.project_id = ?
               LIMIT 1""",
            [project_id],
        ).fetchone()
        if not row:
            return {"data": None}
        data = {
            "id": row[0],
            "project_id": row[1],
            "datasource_id": row[2],
            "alias": row[3],
            "config_overrides": _redact_config(_safe_json_loads(row[4], {})),
            "name": row[5],
            "type": row[6],
            "properties": _redact_config(_safe_json_loads(row[7], {})),
            "description": row[8],
        }
    return {"data": data}


@router.put("/{project_id:int}/datasource")
def update_project_datasource(
    project_id: int,
    body: ProjectDatasourceUpdate,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        _require_project_permission(payload, "datasources", "update", project_id)
        con = get_connection()
        existing = con.execute(
            "SELECT id, datasource_id FROM metadata.project_datasources WHERE project_id = ? LIMIT 1",
            [project_id],
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="No datasource bound to this project")
        binding_id, datasource_id = existing
        if body.properties is not None:
            con.execute(
                "UPDATE metadata.datasources SET properties_encrypted = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [_encrypted_json_value(body.properties), datasource_id],
            )
        if body.alias is not None or body.config_overrides is not None:
            sets = []
            params = []
            if body.alias is not None:
                sets.append("alias = ?")
                params.append(body.alias)
            if body.config_overrides is not None:
                sets.append("config_overrides = ?")
                params.append(encrypt_json(body.config_overrides))
            params.append(binding_id)
            con.execute(f"UPDATE metadata.project_datasources SET {', '.join(sets)} WHERE id = ?", params)
    clear_analysis_cache(project_id)
    return get_project_datasource(project_id, payload)


@router.post("/{project_id:int}/datasource/test")
def test_project_datasource(
    project_id: int,
    body: dict = None,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        _require_project_permission(payload, "datasources", "read", project_id)
        con = get_connection()
        row = con.execute(
            """SELECT d.type, d.properties_encrypted
               FROM metadata.project_datasources pd
               JOIN metadata.datasources d ON d.id = pd.datasource_id
               WHERE pd.project_id = ? LIMIT 1""",
            [project_id],
        ).fetchone()
    if not row:
        return {"data": {"success": False, "latency_ms": 0, "error": "No datasource bound to this project"}}
    ds_type, props_encrypted = row
    props = _safe_json_loads(props_encrypted, {})
    try:
        from routers.datasources import _list_tables_for_binding
        result = _list_tables_for_binding(ds_type, props, project_id=project_id, binding_id=0)
    except Exception:
        LOGGER.exception("Datasource test failed for project %d", project_id)
        return {"data": {"success": False, "latency_ms": 0, "error": "Connection failed"}}
    return {
        "data": {
            "success": True,
            "latency_ms": result.get("latency_ms", 0),
            "error": None,
            "tables_discovered": len(result.get("tables", [])),
            "warning": result.get("warning"),
        }
    }


@router.get("/{project_id:int}/datasource/schema")
def get_datasource_schema(
    project_id: int,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        _require_project_permission(payload, "datasources", "read", project_id)
        con = get_connection()
        row = con.execute(
            """SELECT d.type, d.properties_encrypted
               FROM metadata.project_datasources pd
               JOIN metadata.datasources d ON d.id = pd.datasource_id
               WHERE pd.project_id = ? LIMIT 1""",
            [project_id],
        ).fetchone()
    if not row:
        return {"data": {"tables": []}}
    ds_type, props_encrypted = row
    props = _safe_json_loads(props_encrypted, {})
    try:
        from routers.datasources import _list_tables_for_binding
        result = _list_tables_for_binding(ds_type, props, project_id=project_id, binding_id=0)
    except Exception:
        LOGGER.exception("Schema discovery failed for project %d", project_id)
        return {"data": {"tables": []}}
    return {"data": {"tables": result.get("tables", [])}}


@router.post("/{project_id:int}/datasource/rescan")
def rescan_datasource(
    project_id: int,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        _require_project_permission(payload, "datasources", "update", project_id)
        con = get_connection()
        row = con.execute(
            """SELECT d.type, d.properties_encrypted
               FROM metadata.project_datasources pd
               JOIN metadata.datasources d ON d.id = pd.datasource_id
               WHERE pd.project_id = ? LIMIT 1""",
            [project_id],
        ).fetchone()
    if not row:
        return {"data": {"tables_discovered": 0, "tables_removed": 0}}
    ds_type, props_encrypted = row
    props = _safe_json_loads(props_encrypted, {})
    try:
        from routers.datasources import _list_tables_for_binding
        result = _list_tables_for_binding(ds_type, props, project_id=project_id, binding_id=0)
    except Exception:
        LOGGER.exception("Datasource rescan failed for project %d", project_id)
        return {"data": {"tables_discovered": 0, "tables_removed": 0}}
    return {"data": {"tables_discovered": len(result.get("tables", [])), "tables_removed": 0}}


# ── Available Tables ──────────────────────────────────────────

@router.get("/datasource/available-tables")
def get_available_tables(payload: dict = Depends(get_current_user)):
    return {"data": {"tables": []}}


# ── Global Connections Management ────────────────────────────────────

@router.get("/datasource/connections")
def list_connections(payload: dict = Depends(get_current_user)):
    with connection_lock():
        _require_project_permission(payload, "datasources", "read")
        _ensure_tables()
        con = get_connection()
        rows = con.execute("SELECT * FROM metadata.connections ORDER BY created_at DESC").fetchall()
    return {
        "data": [
            {
                "id": r[0],
                "name": r[1],
                "type": r[2],
                "config": _redact_config(_safe_json_loads(r[3], {})),
                "is_available": r[4],
                "created_at": str(r[5]) if r[5] else None,
                "updated_at": str(r[6]) if r[6] else None,
            }
            for r in rows
        ]
    }


@router.post("/datasource/connections")
def create_connection(
    body: dict,
    payload: dict = Depends(get_current_user),
):
    name = body.get("name")
    type_ = body.get("type")
    if not name or not type_:
        raise HTTPException(status_code=422, detail="name and type are required")
    if not isinstance(name, str) or len(name) > 200:
        raise HTTPException(status_code=422, detail="name must be a string of at most 200 characters")
    if not isinstance(type_, str) or len(type_) > 50:
        raise HTTPException(status_code=422, detail="type must be a string of at most 50 characters")
    config = body.get("config")
    if config is not None and not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="config must be an object")
    with connection_lock():
        _require_project_permission(payload, "datasources", "create")
        _ensure_tables()
        con = get_connection()
        max_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.connections").fetchone()[0]
        con.execute(
            "INSERT INTO metadata.connections (id, name, type, config, is_available) VALUES (?, ?, ?, ?, ?)",
            [max_id, name, type_, encrypt_json(body.get("config", {})), body.get("is_available", True)],
        )
        row = con.execute("SELECT * FROM metadata.connections WHERE id = ?", [max_id]).fetchone()
    return {
        "data": {
            "id": row[0],
            "name": row[1],
            "type": row[2],
            "config": _redact_config(_safe_json_loads(row[3], {})),
            "is_available": row[4],
            "created_at": str(row[5]) if row[5] else None,
            "updated_at": str(row[6]) if row[6] else None,
        }
    }


@router.post("/datasource/connections/test")
def test_connection(
    body: dict,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        _require_project_permission(payload, "datasources", "read")
    return {"data": {"success": True, "latency_ms": 0}}


@router.delete("/datasource/connections/{connection_id:int}")
def delete_connection(
    connection_id: int,
    payload: dict = Depends(get_current_user),
):
    with connection_lock():
        _require_project_permission(payload, "datasources", "delete")
        _ensure_tables()
        con = get_connection()
        existing = con.execute("SELECT id FROM metadata.connections WHERE id = ?", [connection_id]).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Connection not found")
        con.execute("DELETE FROM metadata.connections WHERE id = ?", [connection_id])
        return {"data": {"success": True}}


# ── Export / Import ──────────────────────────────────────────────────────

from services.export_service import ExportService


@router.get("/{project_id:int}/export")
def export_project(project_id: int, format: str = Query("yaml", pattern="^(yaml|json)$"), payload: dict = Depends(get_current_user)):
    if not payload_has_permission(payload, "models", "read", project_id):
        raise HTTPException(status_code=403, detail="Permission denied")
    svc = ExportService()
    try:
        data = svc.export_project(project_id, format=format)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    content_type = "application/x-yaml" if format == "yaml" else "application/json"
    filename = f"project-{project_id}.{format}"
    return Response(content=data, media_type=content_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/import/file")
async def import_project_file(format: str = Query("yaml"), file: UploadFile = File(None), payload: dict = Depends(require_permission("projects", "manage"))):
    if not file:
        raise HTTPException(status_code=400, detail="No file provided")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 10MB)")
    try:
        svc = ExportService()
        project_id = svc.import_project(content, format=format)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with connection_lock():
        con = get_connection()
        project_row = con.execute(
            "SELECT id, name, display_name, description, type, created_at, updated_at FROM metadata.projects WHERE id = ?",
            [project_id],
        ).fetchone()
    if not project_row:
        raise HTTPException(status_code=404, detail="Imported project not found")
    project = {
        "id": project_row[0],
        "name": project_row[1],
        "display_name": project_row[2],
        "description": project_row[3],
        "type": project_row[4],
        "created_at": str(project_row[5]) if project_row[5] else None,
        "updated_at": str(project_row[6]) if project_row[6] else None,
    }
    return {"data": {"project": project, "project_id": project_id, "stats": {"imported": True}}}


# ── Legacy Migration ────────────────────────────────────────────────────

from services.migration_service import migrate_sqlite_to_prismbi


@router.post("/migrate/sqlite")
async def migrate_sqlite(file: UploadFile = File(None), default_user_id: int = Query(1), payload: dict = Depends(require_permission("projects", "manage"))):
    if not file:
        raise HTTPException(status_code=400, detail="No SQLite file provided")
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 50MB)")
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        result = migrate_sqlite_to_prismbi(tmp_path, default_user_id=default_user_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Migration failed: {exc}") from exc
    finally:
        os.unlink(tmp_path)
    return {"data": result}
