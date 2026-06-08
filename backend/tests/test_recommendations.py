from __future__ import annotations

import json

from fastapi.testclient import TestClient


def test_recommendations_reject_project_zero(test_app: TestClient, auth_headers: dict):
    response = test_app.get("/api/recommendations/0", headers=auth_headers)
    assert response.status_code == 400


def test_catalog_create_list_update_delete(test_app: TestClient, auth_headers: dict, seed_project: dict):
    created = test_app.post(
        "/api/recommendations/1/catalog",
        json={"question": "Total sales?", "sql": "SELECT SUM(amount) FROM orders", "verified": True},
        headers=auth_headers,
    )
    assert created.status_code == 200
    entry_id = created.json()["data"]["id"]

    listed = test_app.get("/api/recommendations/1/catalog", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()["data"][0]["question"] == "Total sales?"
    assert listed.json()["data"][0]["verified"] is True

    updated = test_app.put(
        f"/api/recommendations/1/catalog/{entry_id}",
        json={"question": "Total revenue?", "verified": False},
        headers=auth_headers,
    )
    assert updated.status_code == 200

    listed_after_update = test_app.get("/api/recommendations/1/catalog", headers=auth_headers)
    assert listed_after_update.status_code == 200
    assert listed_after_update.json()["data"][0]["question"] == "Total revenue?"
    assert listed_after_update.json()["data"][0]["verified"] is False

    deleted = test_app.delete(f"/api/recommendations/1/catalog/{entry_id}", headers=auth_headers)
    assert deleted.status_code == 200


def test_hints_create_update_delete(test_app: TestClient, auth_headers: dict, seed_project: dict):
    created = test_app.post(
        "/api/recommendations/1/hints",
        json={"hint_text": "Prefer revenue questions", "confidence": 0.8},
        headers=auth_headers,
    )
    assert created.status_code == 200
    hint_id = created.json()["data"]["id"]

    listed = test_app.get("/api/recommendations/1/hints", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()["data"][0]["hint_text"] == "Prefer revenue questions"

    updated = test_app.put(
        f"/api/recommendations/1/hints/{hint_id}",
        json={"hint_text": "Prefer margin questions"},
        headers=auth_headers,
    )
    assert updated.status_code == 200

    deleted = test_app.delete(f"/api/recommendations/1/hints/{hint_id}", headers=auth_headers)
    assert deleted.status_code == 200


def test_rate_validates_range(test_app: TestClient, auth_headers: dict, test_db, seed_project: dict):
    test_db.execute(
        "INSERT INTO metadata.recommendations (id, project_id, title, category, status) VALUES (1, 1, 'Try revenue', 'catalog', 'active')"
    )
    response = test_app.post(
        "/api/recommendations/1/rate/1",
        json={"rating": 6},
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_rate_and_scores(test_app: TestClient, auth_headers: dict, test_db, seed_project: dict):
    test_db.execute(
        "INSERT INTO metadata.recommendations (id, project_id, title, category, status) VALUES (1, 1, 'Try revenue', 'catalog', 'active')"
    )
    response = test_app.post(
        "/api/recommendations/1/rate/1",
        json={"rating": 4, "comment": "useful", "source_layer": "catalog"},
        headers=auth_headers,
    )
    assert response.status_code == 200

    scores = test_app.get("/api/recommendations/1/scores", headers=auth_headers)
    assert scores.status_code == 200
    assert scores.json()["data"][0]["score"] == 4


def test_dismiss_marks_recommendation_dismissed(test_app: TestClient, auth_headers: dict, test_db, seed_project: dict):
    test_db.execute(
        "INSERT INTO metadata.recommendations (id, project_id, title, category, status) VALUES (1, 1, 'Try revenue', 'catalog', 'active')"
    )
    response = test_app.post("/api/recommendations/1/dismiss/1", headers=auth_headers)
    assert response.status_code == 200
    status = test_db.execute("SELECT status FROM metadata.recommendations WHERE id = 1").fetchone()[0]
    assert status == "dismissed"


def test_recommendation_bootstrap_status_defaults_to_idle_when_no_rows(
    test_app: TestClient,
    auth_headers: dict,
    seed_project: dict,
):
    response = test_app.get(
        "/api/recommendations/1/bootstrap-status",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["project_id"] == 1
    assert data["status"] == "idle"
    assert data["is_bootstrapping"] is False
    assert data["ready"] is False
    assert data["active_recommendations"] == 0
    assert data["recommendation_count"] == 0


def test_recommendation_bootstrap_status_falls_back_to_completed_with_active_rows(
    test_app: TestClient,
    auth_headers: dict,
    test_db,
    seed_project: dict,
):
    test_db.execute(
        "INSERT INTO metadata.recommendations (id, project_id, title, category, source_type, confidence, status, metadata) VALUES (41, 1, 'Cached rec', 'trend', 'schema', 0.72, 'active', ?::JSON)",
        [json.dumps({})],
    )

    response = test_app.get(
        "/api/recommendations/1/bootstrap-status",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "completed"
    assert data["ready"] is True
    assert data["active_recommendations"] == 1
    assert data["recommendation_count"] == 1


def test_recommendation_bootstrap_status_reads_failed_state_from_table(
    test_app: TestClient,
    auth_headers: dict,
    test_db,
    seed_project: dict,
):
    test_db.execute(
        "INSERT INTO metadata.recommendation_bootstrap_status (project_id, status, recommendation_count, error) VALUES (1, 'failed', 0, 'llm timeout')"
    )

    response = test_app.get(
        "/api/recommendations/1/bootstrap-status",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "failed"
    assert data["is_bootstrapping"] is False
    assert data["ready"] is False
    assert data["error"] == "llm timeout"


def test_recommendations_types_filter_blocks_llm_when_not_requested(
    test_app: TestClient,
    auth_headers: dict,
    seed_project: dict,
    monkeypatch,
):
    from services import recommendation_service as rec_service

    def _fake_llm_recommendations(project_id: int, language: str = "en", max_questions: int = 8):
        return [{
            "title": "LLM-only question",
            "category": "trend",
            "scope": "project",
            "source_type": "llm",
            "confidence": 0.99,
            "metadata": {"question_type": "trend", "generated_by": "llm"},
        }]

    monkeypatch.setattr(rec_service, "generate_llm_recommendations", _fake_llm_recommendations)

    schema_only = test_app.get(
        "/api/recommendations/1",
        params={"types": "schema", "include_generated": True},
        headers=auth_headers,
    )
    assert schema_only.status_code == 200
    assert all((item.get("source_type") or "") != "llm" for item in schema_only.json()["data"])

    llm_only = test_app.get(
        "/api/recommendations/1",
        params={"types": "llm", "include_generated": True},
        headers=auth_headers,
    )
    assert llm_only.status_code == 200
    assert any((item.get("source_type") or "") == "llm" for item in llm_only.json()["data"])


def test_generate_llm_recommendations_builds_model_summary_without_name_error(
    seed_project: dict,
    monkeypatch,
):
    from services import ask_service
    from services import llm_service
    from services import recommendation_service as rec_service

    class _FakeLLM:
        response_formats: list[object] = []

        def is_configured(self):
            return True

        def chat(self, _messages, response_format=None):
            type(self).response_formats.append(response_format)
            return {
                "content": json.dumps(
                    {
                        "recommendations": [
                            {"question": "Revenue trend by month", "category": "trend", "confidence": 0.8}
                        ]
                    }
                )
            }

    monkeypatch.setattr(
        ask_service,
        "_models_for_project",
        lambda _project_id: [
            {
                "name": "orders",
                "display_name": "Orders",
                "columns": [
                    {"name": "order_date", "type": "DATE"},
                    {"name": "amount", "type": "DOUBLE"},
                ],
            }
        ],
    )
    monkeypatch.setattr(llm_service, "LLMService", _FakeLLM)

    candidates = rec_service.generate_llm_recommendations(1, language="en", max_questions=3)
    assert len(candidates) == 1
    assert candidates[0]["source_type"] == "llm"
    assert _FakeLLM.response_formats and _FakeLLM.response_formats[0] == "json"


def test_generate_llm_recommendations_repairs_invalid_json_payload_once(
    seed_project: dict,
    monkeypatch,
):
    from services import ask_service
    from services import llm_service
    from services import recommendation_service as rec_service

    class _RepairLLM:
        call_count = 0
        response_formats: list[object] = []

        def is_configured(self):
            return True

        def chat(self, _messages, response_format=None):
            type(self).call_count += 1
            type(self).response_formats.append(response_format)
            if type(self).call_count == 1:
                return {
                    "content": '{"recommendations":[{"question":"Revenue trend","category":"trend","confidence":0.8}'
                }
            return {
                "content": json.dumps(
                    {
                        "recommendations": [
                            {"question": "Revenue trend by month", "category": "trend", "confidence": 0.82}
                        ]
                    }
                )
            }

    monkeypatch.setattr(
        ask_service,
        "_models_for_project",
        lambda _project_id: [
            {
                "name": "orders",
                "display_name": "Orders",
                "columns": [
                    {"name": "order_date", "type": "DATE"},
                    {"name": "amount", "type": "DOUBLE"},
                ],
            }
        ],
    )
    monkeypatch.setattr(llm_service, "LLMService", _RepairLLM)

    candidates = rec_service.generate_llm_recommendations(1, language="en", max_questions=3)

    assert len(candidates) == 1
    assert candidates[0]["title"] == "Revenue trend by month"
    assert _RepairLLM.call_count == 2
    assert _RepairLLM.response_formats == ["json", "json"]


def test_list_recommendations_persists_generated_candidates_without_context(
    test_app: TestClient,
    auth_headers: dict,
    test_db,
    seed_project: dict,
    monkeypatch,
):
    from services import recommendation_service as rec_service

    generated = [
        {
            "title": "Generated trend question",
            "category": "trend",
            "scope": "project",
            "source_type": "llm",
            "confidence": 0.88,
            "metadata": {"generated_by": "llm", "question_type": "trend"},
        },
        {
            "title": "Generated contribution question",
            "category": "contribution",
            "scope": "project",
            "source_type": "schema",
            "confidence": 0.81,
            "metadata": {"question_type": "contribution"},
        },
    ]

    monkeypatch.setattr(
        rec_service.RecommendationService,
        "get_recommendations",
        lambda self, *_args, **_kwargs: generated,
    )

    response = test_app.get(
        "/api/recommendations/1",
        params={"include_generated": True, "max_results": 2},
        headers=auth_headers,
    )

    assert response.status_code == 200
    rows = response.json()["data"]
    titles = {str(row.get("title") or "") for row in rows}
    assert "Generated trend question" in titles
    assert "Generated contribution question" in titles

    persisted_rows = test_db.execute(
        "SELECT id, title, source_type FROM metadata.recommendations WHERE project_id = 1 ORDER BY id"
    ).fetchall()
    persisted_titles = {str(row[1] or "") for row in persisted_rows}
    assert "Generated trend question" in persisted_titles
    assert "Generated contribution question" in persisted_titles
    assert all(int(row[0]) > 0 for row in persisted_rows)


def test_list_recommendations_skips_generation_when_cached_rows_are_enough(
    test_app: TestClient,
    auth_headers: dict,
    test_db,
    seed_project: dict,
    monkeypatch,
):
    from services import recommendation_service as rec_service

    test_db.execute(
        "INSERT INTO metadata.recommendations (id, project_id, title, category, source_type, confidence, status, metadata) VALUES (101, 1, 'Cached 1', 'aggregation', 'schema', 0.9, 'active', ?::JSON)",
        [json.dumps({})],
    )
    test_db.execute(
        "INSERT INTO metadata.recommendations (id, project_id, title, category, source_type, confidence, status, metadata) VALUES (102, 1, 'Cached 2', 'trend', 'schema', 0.8, 'active', ?::JSON)",
        [json.dumps({})],
    )

    def _should_not_be_called(self, *_args, **_kwargs):
        raise AssertionError("get_recommendations should not run when cache is sufficient")

    monkeypatch.setattr(rec_service.RecommendationService, "get_recommendations", _should_not_be_called)

    response = test_app.get(
        "/api/recommendations/1",
        params={"include_generated": True, "max_results": 2},
        headers=auth_headers,
    )

    assert response.status_code == 200
    titles = {str(item.get("title") or "") for item in response.json()["data"]}
    assert "Cached 1" in titles
    assert "Cached 2" in titles


def test_list_recommendations_include_generated_false_never_calls_generation(
    test_app: TestClient,
    auth_headers: dict,
    test_db,
    seed_project: dict,
    monkeypatch,
):
    from services import recommendation_service as rec_service

    test_db.execute(
        "INSERT INTO metadata.recommendations (id, project_id, title, category, source_type, confidence, status, metadata) VALUES (151, 1, 'Persisted only', 'aggregation', 'schema', 0.77, 'active', ?::JSON)",
        [json.dumps({})],
    )

    def _should_not_be_called(self, *_args, **_kwargs):
        raise AssertionError("get_recommendations should not run when include_generated is false")

    monkeypatch.setattr(rec_service.RecommendationService, "get_recommendations", _should_not_be_called)

    response = test_app.get(
        "/api/recommendations/1",
        params={"include_generated": False, "max_results": 6},
        headers=auth_headers,
    )

    assert response.status_code == 200
    titles = {str(item.get("title") or "") for item in response.json()["data"]}
    assert "Persisted only" in titles


def test_list_recommendations_refresh_generated_forces_generation_when_cache_is_enough(
    test_app: TestClient,
    auth_headers: dict,
    test_db,
    seed_project: dict,
    monkeypatch,
):
    from services import recommendation_service as rec_service

    test_db.execute(
        "INSERT INTO metadata.recommendations (id, project_id, title, category, source_type, confidence, status, metadata) VALUES (201, 1, 'Cached A', 'aggregation', 'schema', 0.9, 'active', ?::JSON)",
        [json.dumps({})],
    )
    test_db.execute(
        "INSERT INTO metadata.recommendations (id, project_id, title, category, source_type, confidence, status, metadata) VALUES (202, 1, 'Cached B', 'trend', 'schema', 0.8, 'active', ?::JSON)",
        [json.dumps({})],
    )

    call_counter = {"count": 0}

    def _generated_once(self, *_args, **_kwargs):
        call_counter["count"] += 1
        return [
            {
                "title": "Fresh generated",
                "category": "trend",
                "scope": "project",
                "source_type": "llm",
                "confidence": 0.91,
                "metadata": {"question_type": "trend"},
            }
        ]

    monkeypatch.setattr(rec_service.RecommendationService, "get_recommendations", _generated_once)

    response = test_app.get(
        "/api/recommendations/1",
        params={"include_generated": True, "max_results": 2, "refresh_generated": True},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert call_counter["count"] == 1

    persisted_rows = test_db.execute(
        "SELECT title, metadata FROM metadata.recommendations WHERE project_id = 1 ORDER BY id"
    ).fetchall()
    persisted_titles = {str(row[0] or "") for row in persisted_rows}
    assert "Fresh generated" in persisted_titles
    fresh_meta_raw = next((row[1] for row in persisted_rows if str(row[0] or "") == "Fresh generated"), None)
    if isinstance(fresh_meta_raw, str):
        fresh_meta = json.loads(fresh_meta_raw)
    elif isinstance(fresh_meta_raw, dict):
        fresh_meta = fresh_meta_raw
    else:
        fresh_meta = {}
    assert fresh_meta.get("auto_generated") is True


def test_list_recommendations_refresh_generated_replaces_auto_generated_rows(
    test_app: TestClient,
    auth_headers: dict,
    test_db,
    seed_project: dict,
    monkeypatch,
):
    from services import recommendation_service as rec_service

    test_db.execute(
        "INSERT INTO metadata.recommendations (id, project_id, title, category, source_type, confidence, status, metadata) VALUES (301, 1, 'Manual keeper', 'catalog', 'project', 0.7, 'active', ?::JSON)",
        [json.dumps({"manual": True})],
    )
    test_db.execute(
        "INSERT INTO metadata.recommendations (id, project_id, title, category, source_type, confidence, status, metadata) VALUES (302, 1, 'Old generated', 'trend', 'llm', 0.95, 'active', ?::JSON)",
        [json.dumps({"auto_generated": True, "question_type": "trend"})],
    )

    monkeypatch.setattr(
        rec_service.RecommendationService,
        "get_recommendations",
        lambda self, *_args, **_kwargs: [
            {
                "title": "New generated",
                "category": "trend",
                "scope": "project",
                "source_type": "llm",
                "confidence": 0.84,
                "metadata": {"question_type": "trend"},
            }
        ],
    )

    response = test_app.get(
        "/api/recommendations/1",
        params={"include_generated": True, "max_results": 2, "refresh_generated": True},
        headers=auth_headers,
    )

    assert response.status_code == 200

    active_rows = test_db.execute(
        "SELECT title, metadata FROM metadata.recommendations WHERE project_id = 1 AND status = 'active' ORDER BY id"
    ).fetchall()
    active_titles = {str(row[0] or "") for row in active_rows}
    assert "Manual keeper" in active_titles
    assert "Old generated" not in active_titles
    assert "New generated" in active_titles


def test_recommendations_route_aware_rerank_prefers_sql_candidate_under_mixed_clause_signals(
    test_app: TestClient,
    auth_headers: dict,
    test_db,
    seed_project: dict,
    monkeypatch,
):
    from services import recommendation_service as rec_service

    test_db.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata.sql_route_events (
            id INTEGER PRIMARY KEY,
            event_type VARCHAR,
            project_id INTEGER,
            payload JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    test_db.execute(
        "INSERT INTO metadata.sql_route_events (id, event_type, project_id, payload) VALUES (1, 'question_route_decision', 1, ?::JSON)",
        [json.dumps({"clause_mixed": True, "metadata_clause_count": 1, "non_metadata_clause_count": 1})],
    )
    test_db.execute(
        "INSERT INTO metadata.sql_route_events (id, event_type, project_id, payload) VALUES (2, 'execution_route_decision', 1, ?::JSON)",
        [json.dumps({"route_kind": "single_duckdb"})],
    )
    test_db.execute(
        "INSERT INTO metadata.sql_route_events (id, event_type, project_id, payload) VALUES (3, 'ask_route_success', 1, ?::JSON)",
        [json.dumps({"has_sql": True})],
    )

    monkeypatch.setattr(
        rec_service,
        "_generate_mdl_candidates",
        lambda *_args, **_kwargs: [
            {
                "title": "按城市统计销售额",
                "category": "aggregation",
                "scope": "project",
                "source_type": "schema",
                "confidence": 0.52,
                "metadata": {"question_type": "aggregation"},
            }
        ],
    )
    monkeypatch.setattr(
        rec_service,
        "_generate_session_followups",
        lambda *_args, **_kwargs: [
            {
                "title": "解释为什么销量会波动",
                "category": "follow_up",
                "scope": "project",
                "source_type": "session",
                "confidence": 0.58,
                "metadata": {"question_type": "follow_up"},
            }
        ],
    )
    monkeypatch.setattr(rec_service, "_get_hot_catalog_questions", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(rec_service, "_collaborative_filtering", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(rec_service, "_preference_learning", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(rec_service, "_intent_trends", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(rec_service, "generate_llm_recommendations", lambda *_args, **_kwargs: [])

    response = test_app.get(
        "/api/recommendations/1",
        params={"include_generated": True},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) >= 2
    assert data[0]["title"] == "按城市统计销售额"
    assert (data[0].get("confidence") or 0) >= (data[1].get("confidence") or 0)


def test_statistics_include_project_route_signals(
    test_app: TestClient,
    auth_headers: dict,
    test_db,
    seed_project: dict,
):
    test_db.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata.sql_route_events (
            id INTEGER PRIMARY KEY,
            event_type VARCHAR,
            project_id INTEGER,
            payload JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    test_db.execute(
        "INSERT INTO metadata.question_sql_catalog (id, project_id, question, sql_text, frequency, metadata, verified) VALUES (1, 1, 'Q1', 'SELECT 1', 2, ?::JSON, true)",
        [json.dumps({})],
    )
    test_db.execute(
        "INSERT INTO metadata.projects (id, name, display_name, description, language, prompt) VALUES (2, 'test-project-2', 'Test Project 2', 'Another test project', 'EN', 'Test prompt 2')",
    )
    test_db.execute(
        "INSERT INTO metadata.question_sql_catalog (id, project_id, question, sql_text, frequency, metadata, verified) VALUES (2, 2, 'Q2', 'SELECT 2', 3, ?::JSON, true)",
        [json.dumps({})],
    )
    test_db.execute(
        "INSERT INTO metadata.sql_route_events (id, event_type, project_id, payload) VALUES (10, 'question_route_decision', 1, ?::JSON)",
        [json.dumps({"clause_mixed": True, "metadata_clause_count": 1, "non_metadata_clause_count": 1})],
    )
    test_db.execute(
        "INSERT INTO metadata.sql_route_events (id, event_type, project_id, payload) VALUES (11, 'execution_route_decision', 1, ?::JSON)",
        [json.dumps({"route_kind": "single_duckdb"})],
    )
    test_db.execute(
        "INSERT INTO metadata.sql_route_events (id, event_type, project_id, payload) VALUES (12, 'ask_route_success', 1, ?::JSON)",
        [json.dumps({"has_sql": True})],
    )

    response = test_app.get(
        "/api/recommendations/statistics",
        params={"project_id": 1},
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total_catalogs"] == 1
    assert data["top_queries"][0]["question"] == "Q1"
    assert data["route_signals"]["available"] is True
    assert data["route_signals"]["project_id"] == 1
    assert data["route_signals"]["dominant_route_kind"] == "single_duckdb"
    assert data["route_signals"]["mixed_ratio"] > 0


def test_statistics_project_scope_limits_hints_and_weight_history(
    test_app: TestClient,
    auth_headers: dict,
    test_db,
    seed_project: dict,
):
    test_db.execute(
        "INSERT INTO metadata.projects (id, name, display_name, description, language, prompt) VALUES (2, 'scope-project-2', 'Scope Project 2', 'Another project', 'EN', 'prompt')",
    )
    test_db.execute(
        "INSERT INTO metadata.users (id, username, password_hash, display_name, email, status) VALUES (2, 'scope-user', 'x', 'Scope User', 'scope@example.com', 'ACTIVE')",
    )

    test_db.execute(
        "INSERT INTO metadata.user_preference_hints (id, user_id, hint_text, source_query, confidence) VALUES (1, 1, 'u1 hint', NULL, 1.0)",
    )
    test_db.execute(
        "INSERT INTO metadata.user_preference_hints (id, user_id, hint_text, source_query, confidence) VALUES (2, 2, 'u2 hint', NULL, 1.0)",
    )

    test_db.execute(
        "INSERT INTO metadata.recommendation_scores (id, user_id, recommendation_id, project_id, source_layer, recommend_type, score) VALUES (1, 1, NULL, 1, 'schema', 'catalog', 4)",
    )
    test_db.execute(
        "INSERT INTO metadata.recommendation_scores (id, user_id, recommendation_id, project_id, source_layer, recommend_type, score) VALUES (2, 1, NULL, 2, 'session', 'catalog', 2)",
    )
    test_db.execute(
        "INSERT INTO metadata.layer_weight_history (id, source_layer, previous_weight, new_weight, reason, triggered_by_score_id) VALUES (1, 'schema', 0.2, 0.3, 'p1', 1)",
    )
    test_db.execute(
        "INSERT INTO metadata.layer_weight_history (id, source_layer, previous_weight, new_weight, reason, triggered_by_score_id) VALUES (2, 'session', 0.1, 0.2, 'p2', 2)",
    )

    response = test_app.get(
        "/api/recommendations/statistics",
        params={"project_id": 1},
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert int(data["total_hints"]) == 1
    history_layers = [str(item.get("layer")) for item in data.get("weight_history") or []]
    assert history_layers == ["schema"]
