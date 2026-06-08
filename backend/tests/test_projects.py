from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


class TestCreateProject:
    def test_create_project(self, test_app: TestClient, sample_project: dict, auth_headers: dict):
        response = test_app.post("/api/projects", json=sample_project, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["name"] == sample_project["name"]
        assert data["data"]["display_name"] == sample_project["display_name"]

    def test_create_project_missing_name(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/projects", json={"description": "missing name"}, headers=auth_headers
        )
        assert response.status_code == 422

    def test_create_project_idempotency_key_skips_deleted_cache(self, test_app: TestClient, auth_headers: dict):
        import db

        db.get_connection().execute(
            """
            CREATE TABLE IF NOT EXISTS metadata.idempotency_keys (
                key VARCHAR PRIMARY KEY,
                response JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        headers = {**auth_headers, "Idempotency-Key": "idem-create-project-1"}

        first = test_app.post(
            "/api/projects",
            json={"name": "idem-project-1", "display_name": "Idem Project 1"},
            headers=headers,
        )
        assert first.status_code == 200
        first_id = first.json()["data"]["id"]

        deleted = test_app.delete(f"/api/projects/{first_id}", headers=auth_headers)
        assert deleted.status_code == 200

        second = test_app.post(
            "/api/projects",
            json={"name": "idem-project-2", "display_name": "Idem Project 2"},
            headers=headers,
        )
        assert second.status_code == 200

        second_data = second.json()["data"]
        assert second_data["name"] == "idem-project-2"

        import db

        con = db.get_connection()
        rows = con.execute("SELECT name FROM metadata.projects").fetchall()
        assert [row[0] for row in rows] == ["idem-project-2"]

    def test_create_project_schedules_recommendation_bootstrap(self, test_app: TestClient, auth_headers: dict, monkeypatch):
        import db
        from routers import projects as projects_router

        calls: list[tuple[int, int, str | None]] = []
        monkeypatch.setattr(
            projects_router,
            "_schedule_project_recommendation_bootstrap",
            lambda project_id, user_id, language: calls.append((project_id, user_id, language)),
        )

        response = test_app.post(
            "/api/projects",
            json={"name": "bootstrap-project", "display_name": "Bootstrap Project"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        created_id = int(response.json()["data"]["id"])
        assert calls == [(created_id, 1, "en")]
        status_row = db.get_connection().execute(
            "SELECT status FROM metadata.recommendation_bootstrap_status WHERE project_id = ?",
            [created_id],
        ).fetchone()
        assert status_row is not None
        assert status_row[0] == "pending"

    def test_bootstrap_project_recommendations_persists_generated_rows(self, test_db, seed_project: dict, monkeypatch):
        from routers import projects as projects_router
        from services import recommendation_service as rec_service

        monkeypatch.setattr(
            rec_service.RecommendationService,
            "get_recommendations",
            lambda self, *_args, **_kwargs: [
                {
                    "title": "Bootstrapped recommendation",
                    "category": "trend",
                    "scope": "project",
                    "source_type": "llm",
                    "confidence": 0.83,
                    "metadata": {"question_type": "trend"},
                }
            ],
        )

        projects_router._bootstrap_project_recommendations(project_id=1, user_id=1, language="en")

        row = test_db.execute(
            "SELECT title, metadata FROM metadata.recommendations WHERE project_id = 1 AND title = 'Bootstrapped recommendation'"
        ).fetchone()
        assert row is not None
        assert row[0] == "Bootstrapped recommendation"
        meta_raw = row[1]
        if isinstance(meta_raw, str):
            metadata = json.loads(meta_raw)
        elif isinstance(meta_raw, dict):
            metadata = meta_raw
        else:
            metadata = {}
        assert metadata.get("auto_generated") is True


class TestListProjects:
    def test_list_projects(self, test_app: TestClient, auth_headers: dict, seed_project: dict):
        response = test_app.get("/api/projects", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["total"] == 1
        assert data["data"]["items"][0]["name"] == seed_project["name"]
        assert data["data"]["items"][0]["is_current"] is True

    def test_general_chat_project_is_not_persisted(self, test_app: TestClient, auth_headers: dict):
        import db

        response = test_app.get("/api/projects", headers=auth_headers)
        assert response.status_code == 200
        ids = [item["id"] for item in response.json()["data"]["items"]]
        assert 0 not in ids
        assert db.get_connection().execute("SELECT COUNT(*) FROM metadata.projects WHERE id = 0").fetchone()[0] == 0

    def test_create_thread_rejects_project_zero(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/threads",
            json={"project_id": 0, "summary": "Should not persist"},
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "A real project is required to create a persistent thread"


class TestGetProject:
    def test_get_project(self, test_app: TestClient, auth_headers: dict, seed_project: dict):
        response = test_app.get("/api/projects/1", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["id"] == seed_project["id"]
        assert data["data"]["name"] == seed_project["name"]

    def test_get_project_not_found(self, test_app: TestClient, auth_headers: dict):
        response = test_app.get("/api/projects/99999", headers=auth_headers)
        assert response.status_code == 404
        assert response.json()["detail"] == "Project not found"


class TestUpdateProject:
    def test_update_project(self, test_app: TestClient, auth_headers: dict, seed_project: dict):
        response = test_app.put(
            "/api/projects/1",
            json={"display_name": "Updated Project"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["display_name"] == "Updated Project"


class TestDeleteProject:
    def test_delete_project(self, test_app: TestClient, auth_headers: dict, seed_project: dict):
        response = test_app.delete("/api/projects/1", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["success"] is True

    def test_delete_project_cleans_related_records(self, test_app: TestClient, auth_headers: dict, seed_project: dict):
        import db

        con = db.get_connection()

        con.execute(
            "INSERT INTO metadata.datasources (id, name, type, properties_encrypted) VALUES (10, 'ds1', 'duckdb', '{}')"
        )
        con.execute(
            "INSERT INTO metadata.project_datasources (id, project_id, datasource_id, alias) VALUES (11, 1, 10, 'ds1')"
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata.datasource_runtime_state (
                binding_id INTEGER PRIMARY KEY,
                init_sql_hash VARCHAR,
                initialized_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            "INSERT INTO metadata.datasource_runtime_state (binding_id, init_sql_hash) VALUES (11, 'h1')"
        )

        con.execute(
            "INSERT INTO metadata.recommendations (id, project_id, title, status) VALUES (21, 1, 'r1', 'active')"
        )
        con.execute(
            "INSERT INTO metadata.recommendation_scores (id, user_id, recommendation_id, project_id, score) VALUES (22, 1, 21, 1, 5)"
        )
        con.execute(
            "INSERT INTO metadata.recommendation_bootstrap_status (project_id, status, recommendation_count) VALUES (1, 'completed', 1)"
        )
        con.execute(
            "INSERT INTO metadata.layer_weight_history (id, source_layer, triggered_by_score_id) VALUES (23, 'schema', 22)"
        )

        con.execute(
            "INSERT INTO metadata.row_level_security_policies (id, project_id, role_id, model_name, filter_expression) VALUES (31, 1, 1, 'orders', '1=1')"
        )
        con.execute(
            "INSERT INTO metadata.column_level_security_policies (id, project_id, role_id, model_name, column_name, access_type) VALUES (32, 1, 1, 'orders', 'secret', 'MASK')"
        )

        con.execute(
            "INSERT INTO metadata.threads (id, project_id, user_id, summary) VALUES (40, 1, 1, 'thread-1')"
        )
        con.execute(
            "INSERT INTO metadata.thread_responses (id, thread_id, user_id, question, sql) VALUES (41, 40, 1, 'Q1', 'SELECT 1')"
        )
        con.execute(
            "INSERT INTO metadata.dashboards (id, project_id, name) VALUES (42, 1, 'db-1')"
        )
        con.execute(
            "INSERT INTO metadata.dashboard_items (id, dashboard_id, type, display_name, response_id) VALUES (43, 42, 'chart', 'item-1', 41)"
        )
        con.execute(
            "INSERT INTO metadata.api_history (id, project_id, api_type, thread_id, status_code, duration_ms) VALUES ('h-1', 1, 'POST', 40, 200, 12)"
        )
        con.execute(
            "INSERT INTO metadata.api_history (id, project_id, api_type, thread_id, status_code, duration_ms) VALUES ('h-2', NULL, 'POST', 40, 200, 8)"
        )

        response = test_app.delete("/api/projects/1", headers=auth_headers)
        assert response.status_code == 200

        assert con.execute("SELECT COUNT(*) FROM metadata.projects WHERE id = 1").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM metadata.project_datasources WHERE project_id = 1").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM metadata.datasource_runtime_state WHERE binding_id = 11").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM metadata.datasources WHERE id = 10").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM metadata.recommendations WHERE project_id = 1").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM metadata.recommendation_scores WHERE project_id = 1").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM metadata.recommendation_bootstrap_status WHERE project_id = 1").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM metadata.layer_weight_history WHERE triggered_by_score_id = 22").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM metadata.row_level_security_policies WHERE project_id = 1").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM metadata.column_level_security_policies WHERE project_id = 1").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM metadata.threads WHERE project_id = 1").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM metadata.thread_responses WHERE thread_id = 40").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM metadata.dashboards WHERE project_id = 1").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM metadata.dashboard_items WHERE dashboard_id = 42").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM metadata.api_history WHERE id IN ('h-1', 'h-2')").fetchone()[0] == 0
