from __future__ import annotations

from fastapi.testclient import TestClient


def test_create_model_persists_model_type(
    test_app: TestClient,
    auth_headers: dict,
    test_db,
    seed_project: dict,
):
    response = test_app.post(
        "/api/modeling/1/models",
        json={
            "name": "orders_mv",
            "display_name": "Orders MV",
            "table_reference": "public.orders_mv",
            "model_type": "materialized view",
            "columns": [{"name": "order_id", "type": "INTEGER"}],
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["model_type"] == "materialized_view"

    stored = test_db.execute(
        "SELECT model_type FROM metadata.models WHERE id = ?",
        [payload["id"]],
    ).fetchone()
    assert stored == ("materialized_view",)


def test_update_model_normalizes_model_type(
    test_app: TestClient,
    auth_headers: dict,
    test_db,
    seed_project: dict,
):
    test_db.execute(
        """
        INSERT INTO metadata.models (id, project_id, name, table_reference, model_type, column_defs)
        VALUES (101, 1, 'raw_events', 'public.raw_events', 'table', '[]'::JSON)
        """
    )

    response = test_app.put(
        "/api/modeling/1/models/101",
        json={"model_type": "external table"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["model_type"] == "other"

    stored = test_db.execute(
        "SELECT model_type FROM metadata.models WHERE id = 101"
    ).fetchone()
    assert stored == ("other",)
