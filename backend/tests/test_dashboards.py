from __future__ import annotations

import json

from fastapi.testclient import TestClient


def test_create_dashboard_rejects_project_zero(test_app: TestClient, auth_headers: dict):
    response = test_app.post("/api/dashboards", json={"name": "Bad", "project_id": 0}, headers=auth_headers)
    assert response.status_code == 400


def test_list_dashboards_rejects_project_zero(test_app: TestClient, auth_headers: dict):
    response = test_app.get("/api/dashboards", params={"project_id": 0}, headers=auth_headers)
    assert response.status_code == 400


def test_create_dashboard_item_allows_empty_widget(test_app: TestClient, auth_headers: dict, seed_project: dict):
    dashboard = test_app.post("/api/dashboards", json={"name": "Main", "project_id": 1}, headers=auth_headers)
    assert dashboard.status_code == 200
    dashboard_id = dashboard.json()["data"]["id"]

    item = test_app.post(f"/api/dashboards/{dashboard_id}/items", json={"display_name": "Empty", "type": "CHART"}, headers=auth_headers)
    assert item.status_code == 200
    data = item.json()["data"]
    assert data["response_id"] is None
    assert data["chart_config"] is None

    preview = test_app.post(f"/api/dashboards/items/{data['id']}/preview", headers=auth_headers)
    assert preview.status_code == 200
    assert preview.json()["data"] == {"columns": [], "rows": []}


def test_create_dashboard_item_rejects_response_from_other_project(test_app: TestClient, auth_headers: dict, test_db, seed_project: dict):
    test_db.execute("INSERT INTO metadata.projects (id, name) VALUES (2, 'other')")
    test_db.execute("INSERT INTO metadata.threads (id, project_id, user_id, summary) VALUES (10, 2, 1, 'Other')")
    test_db.execute("INSERT INTO metadata.thread_responses (id, thread_id, user_id, question, sql, answer_detail) VALUES (20, 10, 1, 'Q', 'SELECT 1', ?::JSON)", [json.dumps({"columns": ["x"], "rows": [{"x": 1}]})])
    dashboard = test_app.post("/api/dashboards", json={"name": "Main", "project_id": 1}, headers=auth_headers)
    dashboard_id = dashboard.json()["data"]["id"]

    item = test_app.post(f"/api/dashboards/{dashboard_id}/items", json={"display_name": "Leak", "response_id": 20}, headers=auth_headers)
    assert item.status_code == 400
