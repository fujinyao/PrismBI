from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
import uuid

import duckdb
import pytest
from fastapi.testclient import TestClient


def _create_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("CREATE SCHEMA IF NOT EXISTS metadata")
    conn.execute("CREATE SCHEMA IF NOT EXISTS cache")
    conn.execute("CREATE SCHEMA IF NOT EXISTS system")

    conn.execute("""
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata.projects (
            id INTEGER PRIMARY KEY,
            name VARCHAR NOT NULL,
            description VARCHAR,
            display_name VARCHAR,
            type VARCHAR,
            connection_info JSON,
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

    conn.execute("""
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata.idempotency_keys (
            key VARCHAR PRIMARY KEY,
            response JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata.id_sequences (
            table_name VARCHAR PRIMARY KEY,
            next_id INTEGER NOT NULL
        )
    """)

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata.project_datasources (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            datasource_id INTEGER NOT NULL REFERENCES metadata.datasources(id),
            alias VARCHAR,
            config_overrides JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, datasource_id)
        )
    """)

    conn.execute("""
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata.roles (
            id INTEGER PRIMARY KEY,
            name VARCHAR NOT NULL,
            scope VARCHAR NOT NULL,
            description VARCHAR,
            is_system BOOLEAN DEFAULT false,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata.permissions (
            id INTEGER PRIMARY KEY,
            resource VARCHAR NOT NULL,
            action VARCHAR NOT NULL,
            description VARCHAR,
            UNIQUE(resource, action)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata.role_permissions (
            id INTEGER PRIMARY KEY,
            role_id INTEGER NOT NULL REFERENCES metadata.roles(id),
            permission_id INTEGER NOT NULL REFERENCES metadata.permissions(id),
            UNIQUE(role_id, permission_id)
        )
    """)

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata.api_history (
            id VARCHAR PRIMARY KEY,
            project_id INTEGER,
            api_type VARCHAR NOT NULL,
            thread_id INTEGER REFERENCES metadata.threads(id),
            headers JSON,
            request_payload JSON,
            response_payload JSON,
            status_code INTEGER,
            duration_ms INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata.settings (
            key VARCHAR PRIMARY KEY,
            value JSON NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata.interest_clusters (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            cluster_name VARCHAR,
            cluster_embedding FLOAT[],
            member_queries TEXT[],
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata.recommendation_ratings (
            id INTEGER PRIMARY KEY,
            recommendation_id INTEGER,
            user_id INTEGER,
            rating INTEGER,
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    _seed_test_settings(conn)


def _seed_test_settings(conn: duckdb.DuckDBPyConnection) -> None:
    defaults = {
        "app_name": '"PrismBI"',
        "app_logo": "null",
        "app_icon": "null",
        "theme_mode": '"light"',
        "theme_primary_color": '"#1677ff"',
        "theme_font": '"Inter"',
        "llm_provider": '"openai"',
        "llm_model": '"gpt-4o"',
        "llm_endpoint": '"https://api.openai.com/v1"',
        "llm_max_tokens": "4096",
        "llm_temperature": "0.7",
        "llm_extra_params": "null",
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
        "router_route_observability_window_seconds": "1800",
        "router_route_observability_max_events_per_project": "20000",
        "router_route_observability_persist_enabled": "true",
        "router_route_observability_persist_interval_seconds": "30",
        "router_route_observability_persist_event_delta": "20",
        "router_sql_route_v2_enabled": "true",
        "router_sql_route_allowlist_projects": "[]",
        "router_sql_route_shadow_mode": "false",
        "router_sql_route_event_persist_enabled": "true",
        "router_sql_route_profile_id": '"prismbi.default"',
        "router_sql_route_profile_version": '"v2"',
        "router_sql_route_strict_json_probe_enabled": "true",
        "security_sql_forbidden_keywords": json.dumps(["attach", "detach", "pragma", "install", "vacuum", "checkpoint", "force", "grant", "revoke"]),
        "security_forbidden_duckdb_functions": json.dumps(["read_csv", "read_csv_auto", "read_json", "read_json_auto", "read_parquet", "read_ndjson", "read_text", "read_blob", "read_xlsx", "httpfs_scan", "postgres_scan", "mysql_scan", "sqlite_scan", "parquet_scan", "glob", "query"]),
        "security_rate_limit_window_s": "300",
        "security_rate_limit_max": "10",
        "security_jwt_expiry_hours": "24",
        "embedding_dim": "384",
        "memory_similarity_threshold": "0.1",
        "dashboard_cache_ttl_s": "300",
        "default_page": '"/home"',
        "auto_save": "true",
        "telemetry_enabled": "false",
    }
    for key, value in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO metadata.settings (key, value) VALUES (?, ?::JSON)",
            [key, value],
        )


@pytest.fixture(autouse=True)
def test_db(monkeypatch):
    conn = duckdb.connect(":memory:")
    _create_schema(conn)

    import db
    from services import crypto_service

    monkeypatch.setenv("PRISMBI_ENCRYPTION_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")
    monkeypatch.setattr(crypto_service, "_fernet", None)
    db._cleanup_general_chat_project(conn)
    db._seed_default_rbac(conn)
    monkeypatch.setattr(db, "_con", conn)

    yield conn

    try:
        conn.close()
    except Exception:
        pass


@pytest.fixture
def seed_user(test_db) -> dict:
    from db import _assign_role_to_user
    from services.auth_service import AuthService

    row = test_db.execute("SELECT id, username FROM metadata.users WHERE username = 'testuser'").fetchone()
    if row:
        user_id = int(row[0])
    else:
        user_id = 1
        password_hash = AuthService(secret_key="test-only").hash_password("password123")
        test_db.execute(
            """
            INSERT INTO metadata.users (id, username, password_hash, display_name, email, status)
            VALUES (?, 'testuser', ?, 'Test User', 'testuser@example.com', 'ACTIVE')
            """,
            [user_id, password_hash],
        )

    role = test_db.execute("SELECT id FROM metadata.roles WHERE name = 'super_admin'").fetchone()
    if role:
        _assign_role_to_user(test_db, user_id, int(role[0]), granted_by=user_id)

    return {"id": user_id, "username": "testuser"}


@pytest.fixture
def seed_project(test_db, seed_user: dict) -> dict:
    row = test_db.execute("SELECT id, name FROM metadata.projects WHERE id = 1").fetchone()
    if not row:
        test_db.execute(
            """
            INSERT INTO metadata.projects (id, name, display_name, description, language, prompt)
            VALUES (1, 'test-project', 'Test Project', 'A test project', 'EN', 'Test prompt')
            """
        )
    test_db.execute(
        "UPDATE metadata.users SET default_project_id = 1 WHERE id = ?",
        [seed_user["id"]],
    )
    return {"id": 1, "name": "test-project", "display_name": "Test Project"}


@pytest.fixture
def seed_model(test_db, seed_project: dict) -> dict:
    test_db.execute(
        """
        INSERT OR REPLACE INTO metadata.models (id, project_id, name, display_name, table_reference, source_binding_id, column_defs)
        VALUES (1, 1, 'orders', 'Orders', 'orders', NULL, ?::JSON)
        """,
        ['[{"name":"id","type":"INTEGER"},{"name":"customer_id","type":"INTEGER"},{"name":"tenant_id","type":"INTEGER"},{"name":"amount","type":"DOUBLE"},{"name":"secret","type":"VARCHAR"}]'],
    )
    test_db.execute(
        """
        INSERT OR REPLACE INTO metadata.models (id, project_id, name, display_name, table_reference, source_binding_id, column_defs)
        VALUES (2, 1, 'customers', 'Customers', 'customers', NULL, ?::JSON)
        """,
        ['[{"name":"id","type":"INTEGER"},{"name":"tenant_id","type":"INTEGER"},{"name":"name","type":"VARCHAR"}]'],
    )
    return {"id": 1, "name": "orders"}


@pytest.fixture
def test_app(test_db) -> TestClient:
    from main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth_headers(test_db, seed_user: dict) -> dict[str, str]:
    from routers.auth import auth

    session_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    token = auth.create_token(seed_user["id"], seed_user["username"], session_id=session_id)
    test_db.execute(
        """
        INSERT INTO metadata.sessions (id, user_id, token_type, issued_at, expires_at, last_active_at, is_revoked)
        VALUES (?, ?, 'bearer', CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP, false)
        """,
        [session_id, seed_user["id"], expires_at],
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sample_user() -> dict:
    return {
        "username": "testuser",
        "password": "password123",
        "display_name": "Test User",
    }


@pytest.fixture
def sample_project() -> dict:
    return {
        "name": "test-project",
        "display_name": "Test Project",
        "description": "A test project",
    }
