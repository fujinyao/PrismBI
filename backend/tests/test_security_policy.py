from __future__ import annotations

from fastapi.testclient import TestClient


def _super_admin_role_id(con) -> int:
    return int(con.execute("SELECT id FROM metadata.roles WHERE name = 'super_admin'").fetchone()[0])


def _seed_rls(con, role_id: int, model: str = "orders", column: str = "tenant_id", value: str = "7") -> None:
    con.execute(
        """
        INSERT INTO metadata.row_level_security_policies
        (id, project_id, role_id, model_name, column_name, operator, value, value_source, filter_expression, is_enabled)
        VALUES (?, 1, ?, ?, ?, '=', ?, 'literal', 'tenant_id = 7', true)
        """,
        [100 + len(con.execute("SELECT id FROM metadata.row_level_security_policies").fetchall()), role_id, model, column, value],
    )


def test_rls_pushes_into_aggregate_query(test_app: TestClient, auth_headers: dict, test_db, seed_model: dict):
    _seed_rls(test_db, _super_admin_role_id(test_db))

    response = test_app.post(
        "/api/query/dry-plan",
        json={"sql": "SELECT COUNT(*) AS total FROM orders", "project_id": 1},
        headers=auth_headers,
    )

    assert response.status_code == 200
    planned = response.json()["data"]["planned_sql"]
    assert "FROM (SELECT * FROM orders WHERE tenant_id = 7) AS orders" in planned
    assert "COUNT(*)" in planned


def test_rls_handles_join_aliases(test_app: TestClient, auth_headers: dict, test_db, seed_model: dict):
    role_id = _super_admin_role_id(test_db)
    _seed_rls(test_db, role_id, "orders", "tenant_id", "7")
    _seed_rls(test_db, role_id, "customers", "tenant_id", "7")

    response = test_app.post(
        "/api/query/dry-plan",
        json={"sql": "SELECT o.id, c.name FROM orders o JOIN customers c ON c.id = o.customer_id", "project_id": 1},
        headers=auth_headers,
    )

    assert response.status_code == 200
    planned = response.json()["data"]["planned_sql"]
    assert "FROM (SELECT * FROM orders WHERE tenant_id = 7) AS o" in planned
    assert "JOIN (SELECT * FROM customers WHERE tenant_id = 7) AS c" in planned


def test_rls_handles_cte_and_subquery(test_app: TestClient, auth_headers: dict, test_db, seed_model: dict):
    _seed_rls(test_db, _super_admin_role_id(test_db))

    response = test_app.post(
        "/api/query/dry-plan",
        json={
            "sql": "WITH base AS (SELECT * FROM orders o WHERE o.amount > 0) SELECT COUNT(*) FROM base",
            "project_id": 1,
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    planned = response.json()["data"]["planned_sql"]
    assert "WITH base AS (SELECT * FROM (SELECT * FROM orders WHERE tenant_id = 7) AS o WHERE o.amount > 0)" in planned


def test_hidden_cls_rejects_column_reference(test_app: TestClient, auth_headers: dict, test_db, seed_model: dict):
    role_id = _super_admin_role_id(test_db)
    test_db.execute(
        """
        INSERT INTO metadata.column_level_security_policies
        (id, project_id, role_id, model_name, column_name, access_type, is_enabled)
        VALUES (1, 1, ?, 'orders', 'secret', 'HIDE', true)
        """,
        [role_id],
    )

    response = test_app.post(
        "/api/query/dry-plan",
        json={"sql": "SELECT COUNT(DISTINCT secret) FROM orders", "project_id": 1},
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert response.json()["detail"] is not None
