from __future__ import annotations

import json

from fastapi.testclient import TestClient


def test_create_view_from_thread_response(test_app: TestClient, auth_headers: dict, test_db, seed_project: dict):
    test_db.execute("INSERT INTO metadata.threads (id, project_id, user_id, summary) VALUES (10, 1, 1, 'Thread')")
    test_db.execute(
        "INSERT INTO metadata.thread_responses (id, thread_id, user_id, question, sql, answer_detail) VALUES (20, 10, 1, 'Q', 'SELECT 1 AS x', ?::JSON)",
        [json.dumps({"columns": ["x"], "rows": [{"x": 1}]})],
    )

    response = test_app.post(
        "/api/modeling/1/views",
        json={"name": "saved_view", "source_response_id": 20, "columns": [{"name": "x", "type": "INTEGER"}]},
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["source_response_id"] == 20
    assert data["sql"] == "SELECT 1 AS x"
    assert data["model_id"] is None


def test_create_view_normalizes_split_read_only_sql(
    test_app: TestClient,
    auth_headers: dict,
    test_db,
    seed_project: dict,
    seed_model: dict,
):
    response = test_app.post(
        "/api/modeling/1/views",
        json={
            "name": "normalized_view",
            "model_id": seed_model["id"],
            "sql": "WITH cte AS (SELECT 1 AS x); SELECT x FROM cte",
            "columns": [{"name": "x", "type": "INTEGER"}],
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["sql"] == "WITH cte AS (SELECT 1 AS x) SELECT x FROM cte"
