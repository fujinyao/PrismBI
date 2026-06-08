from __future__ import annotations

from fastapi.testclient import TestClient


def test_datasource_properties_are_encrypted_at_rest(test_app: TestClient, auth_headers: dict):
    from services.crypto_service import decrypt_json, is_encrypted_value
    import db

    response = test_app.post(
        "/api/system/datasources",
        headers=auth_headers,
        json={
            "name": "warehouse",
            "type": "postgresql",
            "properties": {"host": "localhost", "password": "secret-password"},
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["properties"]["password"] == "[REDACTED]"

    stored = db.get_connection().execute(
        "SELECT properties_encrypted FROM metadata.datasources WHERE id = ?",
        [response.json()["data"]["id"]],
    ).fetchone()[0]
    assert is_encrypted_value(stored)
    assert decrypt_json(stored)["password"] == "secret-password"


def test_safe_warning_from_exception_redacts_sensitive_tokens():
    from routers import datasources as ds_router

    msg = ds_router._safe_warning_from_exception(
        RuntimeError("connection failed password=supersecret url=postgres://alice:secret123@db.internal:5432/sales")
    )

    assert "supersecret" not in msg
    assert "secret123" not in msg
    assert "[REDACTED]" in msg


def test_discover_external_tables_returns_reason_when_discovery_fails(monkeypatch):
    from routers import datasources as ds_router

    def _raise(_props):
        raise RuntimeError("dial tcp timeout password=hidden")

    monkeypatch.setattr(ds_router, "_discover_postgresql", _raise)
    result = ds_router._discover_external_tables("postgresql", {"host": "db.internal"})
    warning = result.get("warning", "")

    assert "Live metadata discovery failed for datasource type 'postgresql'" in warning
    assert "timeout" in warning
    assert "hidden" not in warning
    assert "[REDACTED]" in warning


def test_register_and_bind_updates_existing_properties_for_same_name(
    test_app: TestClient,
    auth_headers: dict,
    seed_project: dict,
):
    from services.crypto_service import decrypt_json
    import db

    first = test_app.post(
        "/api/projects/1/datasources/register",
        headers=auth_headers,
        json={
            "name": "warehouse",
            "type": "postgresql",
            "properties": {
                "host": "localhost",
                "port": 5432,
                "user": "postgres",
                "password": "old-secret",
                "database": "sales",
            },
        },
    )
    assert first.status_code == 200

    second = test_app.post(
        "/api/projects/1/datasources/register",
        headers=auth_headers,
        json={
            "name": "warehouse",
            "type": "postgresql",
            "properties": {
                "host": "172.17.0.1",
                "port": 5432,
                "user": "postgres",
                "password": "new-secret",
                "database": "sales",
            },
        },
    )
    assert second.status_code == 200

    first_data = first.json()["data"]
    second_data = second.json()["data"]
    assert second_data["id"] == first_data["id"]
    assert second_data["bindingId"] == first_data["bindingId"]

    stored = db.get_connection().execute(
        "SELECT properties_encrypted FROM metadata.datasources WHERE id = ?",
        [first_data["id"]],
    ).fetchone()[0]
    props = decrypt_json(stored)
    assert props["host"] == "172.17.0.1"
    assert props["password"] == "new-secret"


def test_rows_to_table_details_includes_table_types():
    from routers import datasources as ds_router

    rows = [
        ("public", "orders", "id", "integer", True, None, None, "BASE TABLE"),
        ("public", "orders", "amount", "numeric", False, None, None, "BASE TABLE"),
        ("public", "orders_view", "id", "integer", False, None, None, "VIEW"),
        ("public", "orders_mv", "id", "integer", False, None, None, "MATERIALIZED VIEW"),
    ]

    result = ds_router._rows_to_table_details(rows)
    detail_by_name = {item["name"]: item for item in result["table_details"]}

    assert detail_by_name["orders"]["table_type"] == "table"
    assert detail_by_name["orders_view"]["table_type"] == "view"
    assert detail_by_name["orders_mv"]["table_type"] == "materialized_view"


def test_metadata_from_config_normalizes_table_type():
    from routers import datasources as ds_router

    result = ds_router._metadata_from_config(
        {
            "table_details": [
                {
                    "name": "mv_sales",
                    "schema": "analytics",
                    "table_type": "materialized view",
                    "columns": [{"name": "day", "type": "date"}],
                }
            ]
        },
        "postgresql",
    )

    assert result is not None
    assert result["table_details"][0]["table_type"] == "materialized_view"


def test_list_tables_api_returns_table_type_in_details(
    test_app: TestClient,
    auth_headers: dict,
    seed_project: dict,
    monkeypatch,
):
    from routers import datasources as ds_router

    registered = test_app.post(
        "/api/projects/1/datasources/register",
        headers=auth_headers,
        json={
            "name": "warehouse-views",
            "type": "postgresql",
            "properties": {
                "host": "db.internal",
                "port": 5432,
                "user": "postgres",
                "password": "secret",
                "database": "sales",
            },
        },
    )
    assert registered.status_code == 200
    binding_id = registered.json()["data"]["bindingId"]

    def _fake_list_tables(_ds_type, _props, _project_id, _binding_id):
        return {
            "tables": ["public.orders_mv"],
            "table_details": [
                {
                    "name": "orders_mv",
                    "schema": "public",
                    "reference": "public.orders_mv",
                    "table_type": "materialized_view",
                    "columns": [],
                }
            ],
        }

    monkeypatch.setattr(ds_router, "_list_tables_for_binding", _fake_list_tables)

    response = test_app.get(
        f"/api/projects/1/datasources/{binding_id}/tables",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["table_details"][0]["table_type"] == "materialized_view"


def test_discover_external_tables_supports_all_configured_types(monkeypatch):
    from routers import datasources as ds_router

    monkeypatch.setattr(ds_router, "_import_optional", lambda _name: None)
    supported_external_types = [
        "postgresql",
        "redshift",
        "mysql",
        "mariadb",
        "mssql",
        "clickhouse",
        "trino",
        "athena",
        "oracle",
        "snowflake",
        "bigquery",
        "databricks",
    ]

    for ds_type in supported_external_types:
        result = ds_router._discover_external_tables(ds_type, {})
        warning = str(result.get("warning") or "").lower()
        assert "not implemented yet" not in warning


def test_normalize_ds_type_resolves_aliases_to_canonical_types():
    from routers import datasources as ds_router

    assert ds_router._normalize_ds_type("redshift") == "redshift"
    assert ds_router._normalize_ds_type("postgres") == "postgresql"
    assert ds_router._normalize_ds_type("mariadb") == "mysql"
    assert ds_router._normalize_ds_type("sqlserver") == "mssql"
    assert ds_router._normalize_ds_type("customdb") == "customdb"


def test_discover_external_tables_uses_canonical_handler_for_alias(monkeypatch):
    from routers import datasources as ds_router

    called = {"postgresql": 0}

    def _fake_discover(_props):
        called["postgresql"] += 1
        return {"tables": ["public.orders"], "table_details": []}

    monkeypatch.setattr(ds_router, "_discover_postgresql", _fake_discover)

    result = ds_router._discover_external_tables("redshift", {})

    assert called["postgresql"] == 1
    assert result["tables"] == ["public.orders"]
