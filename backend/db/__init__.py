from __future__ import annotations

import os
import json
import logging
import shutil
import secrets
import stat
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import duckdb

from services.auth_service import AuthService
from services.crypto_service import decrypt_json, encrypt_json, is_encrypted_value
from services.prompt_templates import DEFAULT_SYSTEM_PROMPT

LOGGER = logging.getLogger(__name__)

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.getenv("PRISMBI_DATA_DIR") or os.path.join(BACKEND_DIR, "data")
DEFAULT_SYSTEM_DB_PATH = os.path.join(DATA_DIR, "prismbi.duckdb")

_con: Optional[duckdb.DuckDBPyConnection] = None
_con_lock = threading.RLock()


def get_connection() -> duckdb.DuckDBPyConnection:
    global _con
    if _con is None:
        with _con_lock:
            if _con is None:
                _con = _init_db_unlocked()
    return _con


@contextmanager
def connection_lock():
    with _con_lock:
        yield


def row_to_dict(row, description):
    """Convert a DuckDB result tuple to a dict using cursor description for column names."""
    return {desc[0]: row[i] for i, desc in enumerate(description)}


def rows_to_dicts(rows, description):
    """Convert a list of DuckDB result tuples to a list of dicts."""
    if not rows:
        return []
    columns = [desc[0] for desc in description]
    return [dict(zip(columns, row)) for row in rows]


def init_db() -> duckdb.DuckDBPyConnection:
    with _con_lock:
        global _con
        if _con is not None:
            return _con
        return _init_db_unlocked()


def close_connection() -> None:
    global _con
    with _con_lock:
        if _con is None:
            return
        try:
            _con.execute("CHECKPOINT")
            _con.close()
        finally:
            _con = None


def _init_db_unlocked() -> duckdb.DuckDBPyConnection:
    db_path = os.getenv("PRISMBI_DB_PATH", DEFAULT_SYSTEM_DB_PATH)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    con = _connect_system_db(db_path)
    con.execute("CREATE SCHEMA IF NOT EXISTS metadata")
    con.execute("CREATE SCHEMA IF NOT EXISTS cache")
    con.execute("CREATE SCHEMA IF NOT EXISTS system")

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.users (
            id INTEGER PRIMARY KEY,
            username VARCHAR UNIQUE NOT NULL,
            password_hash VARCHAR NOT NULL,
            display_name VARCHAR,
            email VARCHAR,
            default_project_id INTEGER,
            last_login_at TIMESTAMP,
            status VARCHAR DEFAULT 'ACTIVE',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.projects (
            id INTEGER PRIMARY KEY,
            name VARCHAR NOT NULL,
            description VARCHAR,
            display_name VARCHAR,
            type VARCHAR,
            connection_info VARCHAR,
            catalog VARCHAR DEFAULT '',
            schema_name VARCHAR DEFAULT '',
            sample_dataset VARCHAR,
            language VARCHAR DEFAULT 'EN',
            version VARCHAR DEFAULT '1.0',
            is_current BOOLEAN DEFAULT false,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            prompt TEXT
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.threads (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            summary VARCHAR DEFAULT '',
            summary_manual BOOLEAN DEFAULT false,
            user_id INTEGER REFERENCES metadata.users(id),
            preview_row_limit INTEGER DEFAULT 20,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.idempotency_keys (
            key VARCHAR PRIMARY KEY,
            response JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.thread_responses (
            id INTEGER PRIMARY KEY,
            thread_id INTEGER NOT NULL REFERENCES metadata.threads(id),
            user_id INTEGER REFERENCES metadata.users(id),
            question VARCHAR NOT NULL,
            sql TEXT,
            asking_task_id VARCHAR,
            breakdown_detail JSON,
            answer_detail JSON,
            chart_detail JSON,
            adjustment JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.id_sequences (
            table_name VARCHAR PRIMARY KEY,
            next_id INTEGER NOT NULL
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.dashboards (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            name VARCHAR NOT NULL,
            cache_enabled BOOLEAN DEFAULT false,
            schedule_frequency VARCHAR,
            schedule_timezone VARCHAR DEFAULT 'UTC',
            schedule_cron VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.dashboard_items (
            id INTEGER PRIMARY KEY,
            dashboard_id INTEGER NOT NULL REFERENCES metadata.dashboards(id),
            type VARCHAR NOT NULL,
            display_name VARCHAR,
            response_id INTEGER REFERENCES metadata.thread_responses(id),
            chart_config JSON,
            data_source VARCHAR,
            layout_x INTEGER DEFAULT 0,
            layout_y INTEGER DEFAULT 0,
            layout_w INTEGER DEFAULT 3,
            layout_h INTEGER DEFAULT 2,
            cache_data JSON,
            cache_created_at TIMESTAMP,
            cache_overridden_at TIMESTAMP,
            override BOOLEAN DEFAULT false
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.models (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            name VARCHAR NOT NULL,
            display_name VARCHAR,
            description VARCHAR,
            table_reference VARCHAR,
            model_type VARCHAR DEFAULT 'table',
            source_binding_id INTEGER,
            column_defs JSON,
            relation_defs JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.views (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            name VARCHAR NOT NULL,
            display_name VARCHAR,
            description VARCHAR,
            model_id INTEGER REFERENCES metadata.models(id),
            column_defs JSON,
            sql TEXT,
            source_response_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.relations (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            name VARCHAR NOT NULL,
            description VARCHAR,
            source_model_id INTEGER NOT NULL REFERENCES metadata.models(id),
            source_column VARCHAR NOT NULL,
            target_model_id INTEGER NOT NULL REFERENCES metadata.models(id),
            target_column VARCHAR NOT NULL,
            relation_type VARCHAR DEFAULT 'MANY_TO_ONE',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.calculated_fields (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            name VARCHAR NOT NULL,
            display_name VARCHAR,
            description VARCHAR,
            model_id INTEGER NOT NULL REFERENCES metadata.models(id),
            expression TEXT NOT NULL,
            result_type VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.instructions (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            instruction TEXT NOT NULL,
            category VARCHAR,
            scope VARCHAR,
            priority INTEGER DEFAULT 0,
            questions JSON DEFAULT '[]',
            is_default BOOLEAN DEFAULT false,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.sql_pairs (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            sql TEXT NOT NULL,
            question VARCHAR NOT NULL,
            description VARCHAR,
            category VARCHAR,
            scope VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.datasources (
            id INTEGER PRIMARY KEY,
            name VARCHAR NOT NULL,
            type VARCHAR NOT NULL,
            properties_encrypted VARCHAR NOT NULL,
            description VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.project_datasources (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            datasource_id INTEGER NOT NULL REFERENCES metadata.datasources(id),
            alias VARCHAR,
            config_overrides VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, datasource_id)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.model_datasource_mappings (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            model_name VARCHAR NOT NULL,
            project_datasource_id INTEGER NOT NULL REFERENCES metadata.project_datasources(id),
            table_catalog VARCHAR,
            table_schema VARCHAR,
            UNIQUE(project_id, model_name)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.roles (
            id INTEGER PRIMARY KEY,
            name VARCHAR NOT NULL,
            scope VARCHAR NOT NULL,
            description VARCHAR,
            is_system BOOLEAN DEFAULT false,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.permissions (
            id INTEGER PRIMARY KEY,
            resource VARCHAR NOT NULL,
            action VARCHAR NOT NULL,
            description VARCHAR,
            UNIQUE(resource, action)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.role_permissions (
            id INTEGER PRIMARY KEY,
            role_id INTEGER NOT NULL REFERENCES metadata.roles(id),
            permission_id INTEGER NOT NULL REFERENCES metadata.permissions(id),
            UNIQUE(role_id, permission_id)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.user_roles (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES metadata.users(id),
            role_id INTEGER NOT NULL REFERENCES metadata.roles(id),
            project_id INTEGER REFERENCES metadata.projects(id),
            granted_by INTEGER REFERENCES metadata.users(id),
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, role_id, project_id)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.user_permission_overrides (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES metadata.users(id),
            permission_id INTEGER NOT NULL REFERENCES metadata.permissions(id),
            project_id INTEGER REFERENCES metadata.projects(id),
            grant_type VARCHAR NOT NULL,
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.row_level_security_policies (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            role_id INTEGER NOT NULL REFERENCES metadata.roles(id),
            model_name VARCHAR NOT NULL,
            column_name VARCHAR,
            operator VARCHAR,
            value VARCHAR,
            value_source VARCHAR DEFAULT 'literal',
            user_attribute VARCHAR,
            filter_expression VARCHAR NOT NULL,
            description VARCHAR,
            is_enabled BOOLEAN DEFAULT true,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.column_level_security_policies (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            role_id INTEGER NOT NULL REFERENCES metadata.roles(id),
            model_name VARCHAR NOT NULL,
            column_name VARCHAR NOT NULL,
            access_type VARCHAR NOT NULL,
            mask_with VARCHAR,
            is_enabled BOOLEAN DEFAULT true,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.audit_logs (
            id BIGINT PRIMARY KEY,
            user_id INTEGER REFERENCES metadata.users(id),
            event_type VARCHAR NOT NULL,
            resource_type VARCHAR,
            resource_id VARCHAR,
            action VARCHAR,
            detail JSON,
            ip_address VARCHAR,
            user_agent VARCHAR,
            status VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.sessions (
            id VARCHAR PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES metadata.users(id),
            token_type VARCHAR DEFAULT 'bearer',
            issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            last_active_at TIMESTAMP,
            ip_address VARCHAR,
            user_agent VARCHAR,
            is_revoked BOOLEAN DEFAULT false
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.api_tokens (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES metadata.users(id),
            name VARCHAR NOT NULL,
            token_hash VARCHAR NOT NULL,
            token_prefix VARCHAR NOT NULL,
            scope JSON DEFAULT '[]',
            expires_at TIMESTAMP,
            last_used_at TIMESTAMP,
            is_revoked BOOLEAN DEFAULT false,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.api_history (
            id VARCHAR PRIMARY KEY,
            project_id INTEGER,
            api_type VARCHAR NOT NULL,
            thread_id BIGINT,
            headers JSON,
            request_payload JSON,
            response_payload JSON,
            status_code INTEGER,
            duration_ms INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.settings (
            key VARCHAR PRIMARY KEY,
            value JSON NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.llm_capabilities (
            provider VARCHAR NOT NULL,
            endpoint VARCHAR NOT NULL,
            model VARCHAR NOT NULL,
            model_family VARCHAR,
            model_tier VARCHAR,
            structured_output JSON,
            sql_quality JSON,
            instruction JSON,
            repair JSON,
            performance JSON,
            probe_level VARCHAR DEFAULT 'keyword_only',
            probe_count INTEGER DEFAULT 0,
            probed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (provider, endpoint, model)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.recommended_questions_cache (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            user_id INTEGER REFERENCES metadata.users(id),
            session_id VARCHAR,
            question TEXT NOT NULL,
            model_names VARCHAR[],
            recommend_type VARCHAR,
            score FLOAT,
            source VARCHAR,
            llm_explanation TEXT,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expired_at TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.recommendation_bootstrap_status (
            project_id INTEGER PRIMARY KEY REFERENCES metadata.projects(id),
            status VARCHAR NOT NULL DEFAULT 'idle',
            recommendation_count INTEGER DEFAULT 0,
            error TEXT,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.question_sql_catalog (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            question TEXT NOT NULL,
            sql_text TEXT NOT NULL,
            frequency INTEGER DEFAULT 1,
            last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata JSON,
            verified BOOLEAN DEFAULT FALSE
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.user_preference_hints (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES metadata.users(id),
            hint_text TEXT NOT NULL,
            source_query TEXT,
            confidence FLOAT DEFAULT 1.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.interest_clusters (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            cluster_name VARCHAR,
            cluster_embedding FLOAT[],
            member_queries TEXT[],
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.recommendation_feedback (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            recommendation_id INTEGER,
            action VARCHAR,
            session_context TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.recommendation_scores (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            recommendation_id INTEGER,
            project_id INTEGER NOT NULL,
            source_layer VARCHAR,
            recommend_type VARCHAR,
            score INTEGER NOT NULL CHECK (score >= 1 AND score <= 5),
            session_context TEXT,
            source_question TEXT,
            weight_adjustment FLOAT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.layer_weight_history (
            id INTEGER PRIMARY KEY,
            source_layer VARCHAR NOT NULL,
            previous_weight FLOAT,
            new_weight FLOAT,
            reason VARCHAR,
            triggered_by_score_id INTEGER REFERENCES metadata.recommendation_scores(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.recommendations (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL,
            title VARCHAR,
            description VARCHAR,
            category VARCHAR,
            scope VARCHAR,
            source_type VARCHAR,
            source_id INTEGER,
            confidence FLOAT,
            status VARCHAR DEFAULT 'active',
            metadata JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.recommendation_ratings (
            id INTEGER PRIMARY KEY,
            recommendation_id INTEGER,
            user_id INTEGER,
            rating INTEGER,
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.memories (
            id VARCHAR PRIMARY KEY,
            type VARCHAR,
            content JSON,
            embedding JSON,
            user_id INTEGER,
            project_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

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

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.datasource_runtime_state (
            binding_id INTEGER PRIMARY KEY,
            init_sql_hash VARCHAR,
            initialized_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata.modeling_diagrams (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            name VARCHAR DEFAULT 'default',
            layout JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    _migrate_schema(con)
    _migrate_models_model_type(con)
    _seed_default_settings(con)
    _seed_default_rbac(con)
    _seed_default_admin(con)
    _seed_default_admin_role(con)
    _migrate_encrypted_secrets(con)
    _create_indexes(con)
    global _con
    _con = con
    return con


def _connect_system_db(db_path: str) -> duckdb.DuckDBPyConnection:
    try:
        return duckdb.connect(db_path)
    except duckdb.InternalException as exc:
        message = str(exc)
        wal_path = f"{db_path}.wal"
        if "replaying WAL" not in message or not os.path.exists(wal_path):
            raise

        recovery_dir = os.path.join(os.path.dirname(db_path), "recovery")
        os.makedirs(recovery_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        db_backup_path = os.path.join(recovery_dir, f"{os.path.basename(db_path)}.{timestamp}.bak")
        wal_backup_path = os.path.join(recovery_dir, f"{os.path.basename(wal_path)}.{timestamp}.bak")

        if os.path.exists(db_path):
            shutil.copy2(db_path, db_backup_path)
        shutil.copy2(wal_path, wal_backup_path)
        os.replace(wal_path, f"{wal_backup_path}.quarantined")

        return duckdb.connect(db_path)


def _create_indexes(con: duckdb.DuckDBPyConnection) -> None:
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_threads_project_id ON metadata.threads(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_threads_user_id ON metadata.threads(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_thread_responses_thread_id ON metadata.thread_responses(thread_id)",
        "CREATE INDEX IF NOT EXISTS idx_user_roles_user_id ON metadata.user_roles(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_user_roles_role_id ON metadata.user_roles(role_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON metadata.sessions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_token_type ON metadata.sessions(token_type)",
        "CREATE INDEX IF NOT EXISTS idx_api_history_thread_id ON metadata.api_history(thread_id)",
        "CREATE INDEX IF NOT EXISTS idx_api_history_project_id ON metadata.api_history(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON metadata.audit_logs(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON metadata.audit_logs(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_api_tokens_user_id ON metadata.api_tokens(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_models_project_id ON metadata.models(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_relations_project_id ON metadata.relations(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_dashboards_project_id ON metadata.dashboards(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_dashboard_items_dashboard_id ON metadata.dashboard_items(dashboard_id)",
        "CREATE INDEX IF NOT EXISTS idx_instructions_project_id ON metadata.instructions(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_sql_pairs_project_id ON metadata.sql_pairs(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_projects_is_current ON metadata.projects(is_current)",
        "CREATE INDEX IF NOT EXISTS idx_project_datasources_project_id ON metadata.project_datasources(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_settings_key ON metadata.settings(key)",
    ]
    for idx_sql in indexes:
        try:
            con.execute(idx_sql)
        except Exception:
            pass


def _migrate_schema(con: duckdb.DuckDBPyConnection) -> None:
    for table, col, dtype in [
        ("metadata.instructions", "category", "VARCHAR"),
        ("metadata.instructions", "scope", "VARCHAR"),
        ("metadata.instructions", "priority", "INTEGER DEFAULT 0"),
        ("metadata.sql_pairs", "description", "VARCHAR"),
        ("metadata.sql_pairs", "category", "VARCHAR"),
        ("metadata.sql_pairs", "scope", "VARCHAR"),
        ("metadata.thread_responses", "breakdown_detail", "JSON"),
        ("metadata.thread_responses", "answer_detail", "JSON"),
        ("metadata.thread_responses", "chart_detail", "JSON"),
        ("metadata.thread_responses", "adjustment", "JSON"),
        ("metadata.threads", "preview_row_limit", "INTEGER DEFAULT 20"),
        ("metadata.threads", "summary_manual", "BOOLEAN DEFAULT false"),
        ("metadata.projects", "prompt", "TEXT"),
        ("metadata.models", "description", "VARCHAR"),
        ("metadata.models", "model_type", "VARCHAR DEFAULT 'table'"),
        ("metadata.views", "description", "VARCHAR"),
        ("metadata.views", "sql", "TEXT"),
        ("metadata.views", "source_response_id", "INTEGER"),
        ("metadata.relations", "description", "VARCHAR"),
        ("metadata.calculated_fields", "description", "VARCHAR"),
        ("metadata.memories", "user_id", "INTEGER"),
        ("metadata.memories", "project_id", "INTEGER"),
        ("metadata.project_datasources", "config_overrides", "VARCHAR"),
        ("metadata.question_sql_catalog", "verified", "BOOLEAN DEFAULT FALSE"),
    ]:
        try:
            con.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {dtype}")
        except Exception as exc:
            LOGGER.warning("Migration skip: %s", exc)

    # Migrate api_history.thread_id from INTEGER to BIGINT
    try:
        import duckdb
        col_info = con.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = 'metadata' AND table_name = 'api_history' AND column_name = 'thread_id'"
        ).fetchone()
        if col_info and col_info[0] == "INTEGER":
            con.execute("""
                CREATE TABLE metadata.__migration_api_history_bigint (
                    id VARCHAR PRIMARY KEY,
                    project_id INTEGER,
                    api_type VARCHAR NOT NULL,
                    thread_id BIGINT,
                    headers JSON,
                    request_payload JSON,
                    response_payload JSON,
                    status_code INTEGER,
                    duration_ms INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            con.execute("""
                INSERT INTO metadata.__migration_api_history_bigint
                    (id, project_id, api_type, thread_id, headers, request_payload,
                     response_payload, status_code, duration_ms, created_at)
                SELECT id, project_id, api_type, CAST(thread_id AS BIGINT), headers,
                       request_payload, response_payload, status_code, duration_ms, created_at
                FROM metadata.api_history
            """)
            con.execute("DROP TABLE metadata.api_history")
            con.execute("ALTER TABLE metadata.__migration_api_history_bigint RENAME TO api_history")
            con.execute("CREATE INDEX IF NOT EXISTS idx_api_history_thread_id ON metadata.api_history(thread_id)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_api_history_project_id ON metadata.api_history(project_id)")
            LOGGER.info("Migrated metadata.api_history.thread_id from INTEGER to BIGINT")
    except (ValueError, duckdb.Error) as exc:
        LOGGER.warning("Migration skip (api_history.thread_id): %s", exc)


def _metadata_column_exists(
    con: duckdb.DuckDBPyConnection,
    table_name: str,
    column_name: str,
) -> bool:
    row = con.execute(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = 'metadata' AND table_name = ? AND column_name = ?",
        [table_name, column_name],
    ).fetchone()
    return bool(row and row[0])


def _normalize_model_type(value: object) -> str:
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


def _migrate_models_model_type(con: duckdb.DuckDBPyConnection) -> bool:
    if not _metadata_column_exists(con, "models", "model_type"):
        return False

    migration_marker_key = "migration_models_model_type_legacy_backfill_done"
    legacy_override_allowed = True
    try:
        marker_row = con.execute(
            "SELECT value FROM metadata.settings WHERE key = ?",
            [migration_marker_key],
        ).fetchone()
        if marker_row:
            marker_value = marker_row[0]
            if isinstance(marker_value, str):
                try:
                    marker_value = json.loads(marker_value)
                except Exception:
                    marker_value = marker_value.strip('"')
            marker_text = str(marker_value).strip().lower()
            legacy_override_allowed = marker_text not in {"true", "1", "yes", "on"}
    except Exception:
        legacy_override_allowed = True

    legacy_columns = [
        col
        for col in ("source_type", "table_type", "type")
        if _metadata_column_exists(con, "models", col)
    ]
    select_cols = ["id", "model_type", *legacy_columns]
    select_cols_sql = ", ".join(f'"{col}"' for col in select_cols)
    rows = con.execute(
        f"SELECT {select_cols_sql} FROM metadata.models ORDER BY id"
    ).fetchall()

    updates: list[tuple[str, int]] = []
    for row in rows:
        model_id = int(row[0])
        current_raw = row[1]
        current_text = str(current_raw).strip() if current_raw not in (None, "") else ""
        current_normalized = (
            _normalize_model_type(current_raw)
            if current_raw not in (None, "")
            else ""
        )

        legacy_normalized = ""
        for legacy_value in row[2:]:
            if legacy_value in (None, ""):
                continue
            legacy_normalized = _normalize_model_type(legacy_value)
            if legacy_normalized:
                break

        next_value = current_normalized
        if legacy_normalized and (
            not next_value
            or (
                legacy_override_allowed
                and current_text.lower() == "table"
                and legacy_normalized != "table"
            )
        ):
            next_value = legacy_normalized
        if not next_value:
            next_value = "table"

        if current_text != next_value:
            updates.append((next_value, model_id))

    for model_type, model_id in updates:
        con.execute(
            "UPDATE metadata.models SET model_type = ? WHERE id = ?",
            [model_type, model_id],
        )

    if legacy_override_allowed:
        try:
            con.execute(
                "INSERT OR REPLACE INTO metadata.settings (key, value, updated_at) VALUES (?, ?::JSON, CURRENT_TIMESTAMP)",
                [migration_marker_key, json.dumps(True)],
            )
        except Exception:
            LOGGER.warning("Failed to persist model_type migration marker", exc_info=True)

    return bool(updates)


def _seed_default_settings(con: duckdb.DuckDBPyConnection) -> None:
    defaults = {
        "app_name": '"PrismBI"',
        "app_description": '"数据中洞悉价值"',
        "app_logo": '"/prismbi-icon.svg"',
        "app_icon": '"/prismbi-icon.svg"',
        "theme_mode": '"light"',
        "theme_primary_color": '"#1677ff"',
        "theme_font": '"Inter"',
        "theme_border_radius": '"md"',
        "llm_provider": '"openai"',
        "llm_model": '"gpt-4o"',
        "llm_endpoint": '"https://api.openai.com/v1"',
        "llm_max_tokens": "4096",
        "llm_temperature": "0.7",
        "llm_extra_params": "null",
        "llm_system_prompt": json.dumps(DEFAULT_SYSTEM_PROMPT),
        "language": '"zh"',
        "timezone": '"UTC"',
        "date_format": '"YYYY-MM-DD"',
        "session_timeout": "60",
        "default_page": '"/home"',
        "auto_save": "true",
        "telemetry_enabled": "false",
        "recommender_max_results": "5",
        "recommender_schema_weight": "0.22",
        "recommender_session_weight": "0.18",
        "recommender_user_weight": "0.13",
        "recommender_project_weight": "0.13",
        "recommender_global_weight": "0.08",
        "recommender_llm_weight": "0.08",
        "recommender_novelty_weight": "0.05",
        "recommender_score_weight": "0.13",
        "recommender_score_learning_rate": "0.05",
        "recommender_score_half_life_days": "14",
        "recommender_low_score_threshold": "2",
        "recommender_consecutive_low_alert": "5",
        "recommender_weight_auto_recover": "true",
        "recommender_catalog_auto_learn": "true",
        "recommender_llm_quality_check": "true",
        "timeout_request_ms": "120000",
        "timeout_llm_connect_s": "10",
        "timeout_llm_read_s": "600",
        "timeout_llm_write_s": "10",
        "timeout_llm_pool_s": "10",
        "timeout_db_connect_s": "60",
        "timeout_model_list_s": "15",
        "llm_endpoint_whitelist_enabled": "true",
        "llm_endpoint_whitelist": json.dumps([
            "https://",
            "http://localhost",
            "http://127.0.0.1",
            "http://0.0.0.0",
            "http://10.",
            "http://172.",
            "http://192.168.",
            "http://host.docker.internal",
        ]),
        "llm_max_retries": "3",
        "llm_retry_base_delay_s": "1.0",
        "llm_retry_max_delay_s": "10.0",
        "llm_http_circuit_enabled": "true",
        "llm_http_circuit_failure_threshold": "3",
        "llm_http_circuit_open_seconds": "60",
        "llm_chat_history_limit": "5",
        "llm_general_chat_history_limit": "3",
        "ask_max_sql_rows": "200",
        "ask_default_preview_row_limit": "20",
        "ask_min_preview_row_limit": "5",
        "ask_max_preview_row_limit": "100",
        "ask_max_source_materialization_rows": "5000",
        "ask_analysis_cache_max": "128",
        "ask_analysis_cache_ttl_s": "300",
        "router_tier1_max_retries": "1",
        "router_tier2_max_retries": "2",
        "router_tier3_max_retries": "3",
        "router_tier1_max_columns_per_model": "12",
        "router_tier2_max_columns_per_model": "15",
        "router_tier3_max_columns_per_model": "20",
        "router_max_sub_questions": "5",
        "router_max_suggested_questions": "5",
        "router_metadata_summary_max_models": "10",
        "router_guidance_llm_available": "true",
        "router_schema_pruning_enabled": "true",
        "router_cross_source_max_workers": "4",
        "router_decompose_merge_enabled": "true",
        "router_decompose_merge_circuit_enabled": "true",
        "router_decompose_merge_failure_threshold": "1",
        "router_decompose_merge_disable_seconds": "3600",
        "router_external_connection_pool_enabled": "true",
        "router_external_connection_pool_max_per_key": "4",
        "router_external_connection_pool_idle_seconds": "300",
        "router_execution_metrics_log_every": "25",
        "router_execution_metrics_log_interval_seconds": "180",
        "router_execution_metrics_max_samples": "400",
        "router_sql_route_v2_enabled": "true",
        "router_sql_route_allowlist_projects": "[]",
        "router_sql_route_shadow_mode": "false",
        "router_sql_route_event_persist_enabled": "true",
        "router_sql_route_profile_id": '"prismbi.default"',
        "router_sql_route_profile_version": '"v2"',
        "router_sql_route_strict_json_probe_enabled": "true",
        "security_sql_forbidden_keywords": json.dumps(["attach", "detach", "pragma", "install", "vacuum", "checkpoint", "force", "grant", "revoke"]),
        "security_forbidden_duckdb_functions": json.dumps(["read_csv", "read_csv_auto", "read_json", "read_json_auto", "read_parquet", "read_ndjson", "read_text", "read_blob", "read_xlsx", "httpfs_scan", "postgres_scan", "mysql_scan", "sqlite_scan", "parquet_scan", "glob", "query"]),
        "security_allowed_operators": json.dumps(["=", "!=", ">", ">=", "<", "<=", "IN", "NOT IN", "LIKE", "ILIKE"]),
        "security_allowed_access_types": json.dumps(["HIDE", "MASK"]),
        "security_rate_limit_window_s": "300",
        "security_rate_limit_max": "10",
        "security_rate_limit_max_entries": "10000",
        "security_ws_ticket_ttl_s": "30",
        "security_jwt_expiry_hours": "24",
        "security_sso_state_ttl_s": "600",
        "security_oidc_cache_ttl_s": "3600",
        "security_max_session_days": "30",
        "embedding_dim": "384",
        "memory_similarity_threshold": "0.1",
        "dashboard_cache_ttl_s": "300",
        "dashboard_cleanup_threshold_hours": "24",
    }
    for key, value in defaults.items():
        con.execute(
            "INSERT OR IGNORE INTO metadata.settings (key, value) VALUES (?, ?::JSON)",
            [key, value],
        )
    for key, value in {
        "app_description": '"数据中洞悉价值"',
        "app_logo": '"/prismbi-icon.svg"',
        "app_icon": '"/prismbi-icon.svg"',
    }.items():
        current = con.execute("SELECT value FROM metadata.settings WHERE key = ?", [key]).fetchone()
        if not current or current[0] in (None, "null"):
            con.execute("INSERT OR REPLACE INTO metadata.settings (key, value) VALUES (?, ?::JSON)", [key, value])


DEFAULT_PERMISSIONS = [
    ("projects", "create", "Create projects"),
    ("projects", "read", "View projects"),
    ("projects", "update", "Update projects"),
    ("projects", "delete", "Delete projects"),
    ("projects", "manage", "Manage project settings and members"),
    ("datasources", "create", "Register data sources"),
    ("datasources", "read", "View data sources"),
    ("datasources", "update", "Update data sources"),
    ("datasources", "delete", "Delete data sources"),
    ("datasources", "manage", "Bind and unbind project data sources"),
    ("models", "create", "Create semantic models"),
    ("models", "read", "View semantic models"),
    ("models", "update", "Update semantic models"),
    ("models", "delete", "Delete semantic models"),
    ("dashboards", "create", "Create dashboards"),
    ("dashboards", "read", "View dashboards"),
    ("dashboards", "update", "Update dashboards"),
    ("dashboards", "delete", "Delete dashboards"),
    ("knowledge", "create", "Create knowledge base entries"),
    ("knowledge", "read", "View knowledge base entries"),
    ("knowledge", "update", "Update knowledge base entries"),
    ("knowledge", "delete", "Delete knowledge base entries"),
    ("recommendations", "read", "View recommendations"),
    ("recommendations", "update", "Update recommendations"),
    ("settings", "read", "View system settings"),
    ("settings", "update", "Update system settings"),
    ("backup", "create", "Create backups"),
    ("backup", "restore", "Restore from backup"),
    ("backup", "delete", "Delete backups"),
    ("backup", "read", "View backups"),
    ("backup", "download", "Download backup archives"),
    ("admin", "read", "View administration pages"),
    ("admin", "manage", "Full administration access"),
    ("users", "create", "Create users"),
    ("users", "read", "View users"),
    ("users", "update", "Update users"),
    ("users", "delete", "Delete users"),
    ("users", "manage", "Assign user roles"),
    ("roles", "create", "Create roles"),
    ("roles", "read", "View roles"),
    ("roles", "update", "Update roles"),
    ("roles", "delete", "Delete roles"),
    ("roles", "manage", "Manage role permissions"),
    ("permissions", "read", "View permissions"),
    ("permissions", "update", "Update permission definitions"),
    ("audit_logs", "read", "View audit logs"),
    ("audit_logs", "export", "Export audit logs"),
    ("sso", "read", "View SSO settings"),
    ("sso", "update", "Update SSO settings"),
    ("security_policies", "create", "Create RLS/CLS policies"),
    ("security_policies", "read", "View RLS/CLS policies"),
    ("security_policies", "update", "Update RLS/CLS policies"),
    ("security_policies", "delete", "Delete RLS/CLS policies"),
    ("security_policies", "manage", "Manage RLS/CLS policies"),
]


DEFAULT_ROLES = {
    "super_admin": {
        "scope": "SYSTEM",
        "description": "System super administrator",
        "permissions": "*",
    },
    "admin": {
        "scope": "SYSTEM",
        "description": "System administrator",
        "permissions": "*",
    },
    "viewer": {
        "scope": "SYSTEM",
        "description": "Authenticated user without project access by default",
        "permissions": [],
    },
    "project_admin": {
        "scope": "PROJECT",
        "description": "Project administrator",
        "permissions": [
            ("projects", "read"),
            ("projects", "update"),
            ("projects", "manage"),
            ("datasources", "create"),
            ("datasources", "read"),
            ("datasources", "update"),
            ("datasources", "delete"),
            ("datasources", "manage"),
            ("models", "create"),
            ("models", "read"),
            ("models", "update"),
            ("models", "delete"),
            ("dashboards", "create"),
            ("dashboards", "read"),
            ("dashboards", "update"),
            ("dashboards", "delete"),
            ("knowledge", "create"),
            ("knowledge", "read"),
            ("knowledge", "update"),
            ("knowledge", "delete"),
            ("recommendations", "read"),
            ("recommendations", "update"),
        ],
    },
    "analyst": {
        "scope": "PROJECT",
        "description": "Business analyst",
        "permissions": [
            ("projects", "read"),
            ("datasources", "read"),
            ("models", "read"),
            ("models", "update"),
            ("dashboards", "create"),
            ("dashboards", "read"),
            ("dashboards", "update"),
            ("knowledge", "create"),
            ("knowledge", "read"),
            ("knowledge", "update"),
            ("recommendations", "read"),
            ("recommendations", "update"),
        ],
    },
}


_NEXT_ID_TABLES = frozenset({
    "metadata.users",
    "metadata.permissions",
    "metadata.roles",
    "metadata.role_permissions",
    "metadata.user_roles",
})


def _next_id(con: duckdb.DuckDBPyConnection, table: str) -> int:
    if table not in _NEXT_ID_TABLES:
        raise ValueError(f"Unknown table for ID generation: {table}")
    con.execute(f"INSERT INTO metadata.id_sequences VALUES (?, COALESCE((SELECT MAX(id) FROM {table}), 0)) ON CONFLICT DO NOTHING", [table])
    existing = con.execute("SELECT next_id FROM metadata.id_sequences WHERE table_name = ?", [table]).fetchone()
    if existing and existing[0] <= 1:
        max_existing = con.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0]
        if max_existing > 0:
            con.execute("UPDATE metadata.id_sequences SET next_id = ? WHERE table_name = ?", [max_existing, table])
    return con.execute("UPDATE metadata.id_sequences SET next_id = next_id + 1 WHERE table_name = ? RETURNING next_id", [table]).fetchone()[0]


def _get_or_create_permission(
    con: duckdb.DuckDBPyConnection,
    resource: str,
    action: str,
    description: str,
) -> int:
    row = con.execute(
        "SELECT id FROM metadata.permissions WHERE resource = ? AND action = ?",
        [resource, action],
    ).fetchone()
    if row:
        con.execute(
            "UPDATE metadata.permissions SET description = ? WHERE id = ?",
            [description, row[0]],
        )
        return row[0]

    permission_id = _next_id(con, "metadata.permissions")
    con.execute(
        "INSERT INTO metadata.permissions (id, resource, action, description) VALUES (?, ?, ?, ?)",
        [permission_id, resource, action, description],
    )
    return permission_id


def _get_or_create_role(
    con: duckdb.DuckDBPyConnection,
    name: str,
    scope: str,
    description: str,
    is_system: bool,
) -> int:
    row = con.execute("SELECT id FROM metadata.roles WHERE name = ?", [name]).fetchone()
    if row:
        con.execute(
            "UPDATE metadata.roles SET scope = ?, description = ?, is_system = ? WHERE id = ?",
            [scope, description, is_system, row[0]],
        )
        return row[0]

    role_id = _next_id(con, "metadata.roles")
    con.execute(
        "INSERT INTO metadata.roles (id, name, scope, description, is_system) VALUES (?, ?, ?, ?, ?)",
        [role_id, name, scope, description, is_system],
    )
    return role_id


def _assign_permission_to_role(
    con: duckdb.DuckDBPyConnection,
    role_id: int,
    permission_id: int,
) -> None:
    existing = con.execute(
        "SELECT id FROM metadata.role_permissions WHERE role_id = ? AND permission_id = ?",
        [role_id, permission_id],
    ).fetchone()
    if existing:
        return
    con.execute(
        "INSERT INTO metadata.role_permissions (id, role_id, permission_id) VALUES (?, ?, ?)",
        [_next_id(con, "metadata.role_permissions"), role_id, permission_id],
    )


def _assign_role_to_user(
    con: duckdb.DuckDBPyConnection,
    user_id: int,
    role_id: int,
    project_id: Optional[int] = None,
    granted_by: Optional[int] = None,
) -> None:
    if project_id is None:
        existing = con.execute(
            "SELECT id FROM metadata.user_roles WHERE user_id = ? AND role_id = ? AND project_id IS NULL",
            [user_id, role_id],
        ).fetchone()
    else:
        existing = con.execute(
            "SELECT id FROM metadata.user_roles WHERE user_id = ? AND role_id = ? AND project_id = ?",
            [user_id, role_id, project_id],
        ).fetchone()
    if existing:
        return
    con.execute(
        "INSERT INTO metadata.user_roles (id, user_id, role_id, project_id, granted_by) VALUES (?, ?, ?, ?, ?)",
        [_next_id(con, "metadata.user_roles"), user_id, role_id, project_id, granted_by],
    )


def _cleanup_general_chat_project(con: duckdb.DuckDBPyConnection) -> None:
    """Clean up temporary project_id=0 data created by general chat (no-project) sessions.
    
    This function ONLY affects project_id=0. It should NEVER affect real projects (id > 0).
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # AUDIT: Log the state of all project directories before cleanup
    try:
        projects_dir = os.path.join(DATA_DIR, "projects")
        if os.path.exists(projects_dir):
            project_dirs = []
            for item in os.listdir(projects_dir):
                item_path = os.path.join(projects_dir, item)
                if os.path.isdir(item_path):
                    try:
                        file_count = sum(len(files) for _, _, files in os.walk(item_path))
                        project_dirs.append(f"{item}({file_count} files)")
                    except Exception:
                        project_dirs.append(f"{item}(error)")
            logger.info("AUDIT: Project directories before cleanup: %s", project_dirs)
    except Exception as exc:
        logger.warning("AUDIT: Failed to list project directories: %s", exc)
    
    for sql in [
        "DELETE FROM metadata.api_history WHERE project_id = 0",
        "DELETE FROM metadata.api_history WHERE thread_id IN (SELECT id FROM metadata.threads WHERE project_id = 0)",
        "DELETE FROM metadata.dashboard_items WHERE dashboard_id IN (SELECT id FROM metadata.dashboards WHERE project_id = 0)",
        "DELETE FROM metadata.thread_responses WHERE thread_id IN (SELECT id FROM metadata.threads WHERE project_id = 0)",
        "DELETE FROM metadata.recommendation_ratings WHERE recommendation_id IN (SELECT id FROM metadata.recommendations WHERE project_id = 0)",
        "DELETE FROM metadata.datasource_runtime_state WHERE binding_id IN (SELECT id FROM metadata.project_datasources WHERE project_id = 0)",
        "DELETE FROM metadata.modeling_diagrams WHERE project_id = 0",
        "DELETE FROM metadata.views WHERE project_id = 0",
        "DELETE FROM metadata.relations WHERE project_id = 0",
        "DELETE FROM metadata.calculated_fields WHERE project_id = 0",
        "DELETE FROM metadata.models WHERE project_id = 0",
        "DELETE FROM metadata.model_datasource_mappings WHERE project_id = 0",
        "DELETE FROM metadata.instructions WHERE project_id = 0",
        "DELETE FROM metadata.sql_pairs WHERE project_id = 0",
        "DELETE FROM metadata.recommended_questions_cache WHERE project_id = 0",
        "DELETE FROM metadata.question_sql_catalog WHERE project_id = 0",
        "DELETE FROM metadata.interest_clusters WHERE project_id = 0",
        "DELETE FROM metadata.recommendation_feedback WHERE project_id = 0",
        "DELETE FROM metadata.recommendation_scores WHERE project_id = 0",
        "DELETE FROM metadata.recommendations WHERE project_id = 0",
        "DELETE FROM metadata.row_level_security_policies WHERE project_id = 0",
        "DELETE FROM metadata.column_level_security_policies WHERE project_id = 0",
        "DELETE FROM metadata.dashboards WHERE project_id = 0",
        "DELETE FROM metadata.threads WHERE project_id = 0",
        "DELETE FROM metadata.project_datasources WHERE project_id = 0",
        "DELETE FROM metadata.user_roles WHERE project_id = 0",
        "DELETE FROM metadata.user_permission_overrides WHERE project_id = 0",
        "UPDATE metadata.users SET default_project_id = NULL WHERE default_project_id = 0",
        "DELETE FROM metadata.projects WHERE id = 0",
    ]:
        try:
            con.execute(sql)
        except Exception:
            pass
    
    # CRITICAL: Only delete data/projects/0, NEVER any other project directory
    try:
        project_zero_dir = os.path.join(DATA_DIR, "projects", "0")
        resolved = os.path.realpath(project_zero_dir)
        expected_prefix = os.path.realpath(os.path.join(DATA_DIR, "projects"))
        
        # Safety check: ensure we're only deleting the "0" directory
        if not resolved.startswith(expected_prefix + os.sep):
            logger.error("SAFETY: Refusing to delete directory outside expected path: %s", resolved)
            return
        
        if not resolved.endswith(os.sep + "0") and not resolved.endswith("/0"):
            logger.error("SAFETY: Refusing to delete non-zero project directory: %s", resolved)
            return
        
        if os.path.exists(project_zero_dir):
            logger.info("Cleaning up temporary project_id=0 directory: %s", project_zero_dir)
            shutil.rmtree(project_zero_dir)
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("Failed to cleanup project_id=0 directory: %s", exc)
    
    # AUDIT: Log the state of all project directories after cleanup
    try:
        projects_dir = os.path.join(DATA_DIR, "projects")
        if os.path.exists(projects_dir):
            project_dirs = []
            for item in os.listdir(projects_dir):
                item_path = os.path.join(projects_dir, item)
                if os.path.isdir(item_path):
                    try:
                        file_count = sum(len(files) for _, _, files in os.walk(item_path))
                        project_dirs.append(f"{item}({file_count} files)")
                    except Exception:
                        project_dirs.append(f"{item}(error)")
            logger.info("AUDIT: Project directories after cleanup: %s", project_dirs)
    except Exception as exc:
        logger.warning("AUDIT: Failed to list project directories: %s", exc)


def _seed_default_rbac(con: duckdb.DuckDBPyConnection) -> None:
    permission_ids = {}
    for resource, action, description in DEFAULT_PERMISSIONS:
        permission_ids[(resource, action)] = _get_or_create_permission(con, resource, action, description)

    for role_name, config in DEFAULT_ROLES.items():
        role_id = _get_or_create_role(
            con,
            role_name,
            config["scope"],
            config["description"],
            True,
        )
        permissions = config["permissions"]
        role_permission_ids = permission_ids.values() if permissions == "*" else [permission_ids[p] for p in permissions]
        if role_name == "viewer":
            con.execute("DELETE FROM metadata.role_permissions WHERE role_id = ?", [role_id])
        for permission_id in role_permission_ids:
            _assign_permission_to_role(con, role_id, permission_id)


def _seed_default_admin(con: duckdb.DuckDBPyConnection) -> None:
    auth = AuthService(secret_key="seed-only")
    configured_password = os.getenv("PRISMBI_ADMIN_PASSWORD")
    allow_default_password = os.getenv("PRISMBI_ALLOW_DEFAULT_ADMIN_PASSWORD", "").strip().lower() in {"1", "true", "yes", "on"}
    if configured_password == "admin123" and not allow_default_password:
        raise RuntimeError("Refusing to seed admin with default password admin123")

    existing = con.execute("SELECT id, password_hash FROM metadata.users WHERE username = 'admin'").fetchone()
    if existing:
        try:
            uses_default_password = auth.verify_password("admin123", existing[1])
        except Exception:
            uses_default_password = False
        if uses_default_password and not allow_default_password:
            password = configured_password or secrets.token_urlsafe(24)
            con.execute(
                "UPDATE metadata.users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [auth.hash_password(password), existing[0]],
            )
            if not configured_password:
                _write_bootstrap_admin_password(password, "rotated")
        return

    seed_admin = os.getenv("PRISMBI_SEED_ADMIN", "true").strip().lower() in {"1", "true", "yes", "on"}
    if not seed_admin:
        return

    password = configured_password or secrets.token_urlsafe(24)
    password_hash = auth.hash_password(password)
    admin_id = _next_id(con, "metadata.users")
    con.execute(
        "INSERT INTO metadata.users (id, username, password_hash, display_name, email, status) VALUES (?, 'admin', ?, 'System Admin', 'admin@prismbi.local', 'ACTIVE')",
        [admin_id, password_hash],
    )
    if not configured_password:
        _write_bootstrap_admin_password(password, "created")


def _write_bootstrap_admin_password(password: str, reason: str) -> None:
    secret_path = os.getenv("PRISMBI_BOOTSTRAP_ADMIN_PASSWORD_FILE") or os.path.join(DATA_DIR, "bootstrap-admin-password")
    os.makedirs(os.path.dirname(secret_path), exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(secret_path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(password)
            handle.write("\n")
        os.chmod(secret_path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        try:
            os.unlink(secret_path)
        except Exception:
            pass
        raise
    LOGGER.info("PrismBI bootstrap admin password %s; stored in %s", reason, secret_path)


def _seed_default_admin_role(con: duckdb.DuckDBPyConnection) -> None:
    user = con.execute("SELECT id FROM metadata.users WHERE username = 'admin'").fetchone()
    role = con.execute("SELECT id FROM metadata.roles WHERE name = 'super_admin'").fetchone()
    if not user or not role:
        return
    _assign_role_to_user(con, user[0], role[0], granted_by=user[0])


def _project_datasource_config_overrides_type(con: duckdb.DuckDBPyConnection) -> str | None:
    row = con.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = 'metadata' AND table_name = 'project_datasources' "
        "AND column_name = 'config_overrides'"
    ).fetchone()
    if not row or not row[0]:
        return None
    return str(row[0]).upper()


def _migrate_project_datasources_config_overrides_to_varchar(con: duckdb.DuckDBPyConnection) -> bool:
    col_type = _project_datasource_config_overrides_type(con)
    if col_type in (None, "VARCHAR"):
        return False

    has_model_mapping_table = bool(
        con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'metadata' AND table_name = 'model_datasource_mappings'"
        ).fetchone()[0]
    )

    in_transaction = False
    try:
        con.execute("BEGIN TRANSACTION")
        in_transaction = True

        con.execute("DROP TABLE IF EXISTS metadata.__migration_model_datasource_mappings_backup")
        con.execute("DROP TABLE IF EXISTS metadata.__migration_project_datasources_varchar")

        if has_model_mapping_table:
            con.execute(
                "CREATE TABLE metadata.__migration_model_datasource_mappings_backup AS "
                "SELECT id, project_id, model_name, project_datasource_id, table_catalog, table_schema "
                "FROM metadata.model_datasource_mappings"
            )
            con.execute("DROP TABLE metadata.model_datasource_mappings")

        con.execute(
            """
            CREATE TABLE metadata.__migration_project_datasources_varchar (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
                datasource_id INTEGER NOT NULL REFERENCES metadata.datasources(id),
                alias VARCHAR,
                config_overrides VARCHAR,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(project_id, datasource_id)
            )
            """
        )
        con.execute(
            """
            INSERT INTO metadata.__migration_project_datasources_varchar
            (id, project_id, datasource_id, alias, config_overrides, created_at)
            SELECT
                id,
                project_id,
                datasource_id,
                alias,
                CASE
                    WHEN config_overrides IS NULL THEN NULL
                    ELSE CAST(config_overrides AS VARCHAR)
                END,
                created_at
            FROM metadata.project_datasources
            """
        )

        con.execute("DROP TABLE metadata.project_datasources")
        con.execute("ALTER TABLE metadata.__migration_project_datasources_varchar RENAME TO project_datasources")

        if has_model_mapping_table:
            con.execute(
                """
                CREATE TABLE metadata.model_datasource_mappings (
                    id INTEGER PRIMARY KEY,
                    project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
                    model_name VARCHAR NOT NULL,
                    project_datasource_id INTEGER NOT NULL REFERENCES metadata.project_datasources(id),
                    table_catalog VARCHAR,
                    table_schema VARCHAR,
                    UNIQUE(project_id, model_name)
                )
                """
            )
            con.execute(
                """
                INSERT INTO metadata.model_datasource_mappings
                (id, project_id, model_name, project_datasource_id, table_catalog, table_schema)
                SELECT
                    id,
                    project_id,
                    model_name,
                    project_datasource_id,
                    table_catalog,
                    table_schema
                FROM metadata.__migration_model_datasource_mappings_backup
                """
            )
            con.execute("DROP TABLE metadata.__migration_model_datasource_mappings_backup")

        con.execute("CREATE INDEX IF NOT EXISTS idx_project_datasources_project_id ON metadata.project_datasources(project_id)")
        con.execute("COMMIT")
        in_transaction = False
        LOGGER.info("Migrated metadata.project_datasources.config_overrides from %s to VARCHAR", col_type)
        return True
    except Exception:
        if in_transaction:
            try:
                con.execute("ROLLBACK")
            except Exception:
                LOGGER.debug("Rollback failed while migrating project_datasources config_overrides", exc_info=True)
        raise
    finally:
        try:
            con.execute("DROP TABLE IF EXISTS metadata.__migration_model_datasource_mappings_backup")
        except Exception:
            pass
        try:
            con.execute("DROP TABLE IF EXISTS metadata.__migration_project_datasources_varchar")
        except Exception:
            pass


def _migrate_encrypted_secrets(con: duckdb.DuckDBPyConnection) -> None:
    try:
        rows = con.execute("SELECT id, properties_encrypted FROM metadata.datasources").fetchall()
        for datasource_id, properties in rows:
            if properties is None or is_encrypted_value(properties):
                continue
            decoded = decrypt_json(properties, None)
            if decoded is None:
                continue
            con.execute(
                "UPDATE metadata.datasources SET properties_encrypted = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [encrypt_json(decoded), datasource_id],
            )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Failed to migrate datasources encryption")

    try:
        rows = con.execute("SELECT id, connection_info FROM metadata.projects").fetchall()
        for project_id, connection_info in rows:
            if connection_info is None or is_encrypted_value(connection_info):
                continue
            decoded = decrypt_json(connection_info, None)
            if decoded is None:
                continue
            con.execute(
                "UPDATE metadata.projects SET connection_info = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [encrypt_json(decoded), project_id],
            )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Failed to migrate project connection_info encryption")

    try:
        rows = con.execute("SELECT id, config FROM metadata.connections").fetchall()
        for connection_id, config in rows:
            if config is None or is_encrypted_value(config):
                continue
            decoded = decrypt_json(config, None)
            if decoded is None:
                continue
            con.execute(
                "UPDATE metadata.connections SET config = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [encrypt_json(decoded), connection_id],
            )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Failed to migrate connections config encryption")

    try:
        con.execute("ALTER TABLE metadata.dashboard_items ADD COLUMN data_source VARCHAR")
    except Exception:
        pass

    try:
        rows = con.execute(
            "SELECT key, value FROM metadata.settings WHERE key IN ('llm_api_key', 'llm_key', 'sso_config')"
        ).fetchall()
        for key, value in rows:
            if value is None or is_encrypted_value(value):
                continue
            decoded = decrypt_json(value, None)
            if decoded in (None, ""):
                continue
            con.execute(
                "UPDATE metadata.settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ?",
                [encrypt_json(decoded), key],
            )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Failed to migrate settings encryption")

    try:
        _migrate_project_datasources_config_overrides_to_varchar(con)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Failed to migrate config_overrides to VARCHAR")
