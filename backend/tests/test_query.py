from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient


class TestExecuteQuery:
    def test_execute_query_without_bindings_returns_warning(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/query",
            json={"sql": "SELECT 1", "project_id": 1},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["warning"] == "Project has no datasource bindings."
        assert data["data"]["columns"] == []

    def test_execute_query_with_limit(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/query",
            json={"sql": "SELECT * FROM test", "project_id": 1, "limit": 100},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["data"]["warning"] == "Project has no datasource bindings."

    def test_execute_query_recombines_split_read_only_sql(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/query",
            json={"sql": "WITH cte AS (SELECT 1 AS id); SELECT id FROM cte", "project_id": 1},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["warning"] == "Project has no datasource bindings."
        assert data["columns"] == []

    def test_execute_query_routes_to_external_binding_without_sqlglot(
        self,
        test_app: TestClient,
        auth_headers: dict,
        seed_project: dict,
        seed_model: dict,
        test_db,
        monkeypatch,
    ):
        import services.ask_service as ask_service

        test_db.execute(
            "INSERT INTO metadata.datasources (id, name, type, properties_encrypted) VALUES (1, 'pg', 'postgresql', '{}')"
        )
        test_db.execute(
            "INSERT INTO metadata.project_datasources (id, project_id, datasource_id, alias) VALUES (1, 1, 1, 'pg')"
        )
        test_db.execute("UPDATE metadata.models SET source_binding_id = 1 WHERE id = 1")

        captured: dict[str, str] = {}

        def _fake_external_query(ds_type: str, props: dict, sql: str, row_limit: int):
            captured["ds_type"] = ds_type
            captured["sql"] = sql
            return {"columns": ["order_count"], "tuples": [(3,)]}

        monkeypatch.setattr(ask_service, "sqlglot", None)
        monkeypatch.setattr(ask_service, "_execute_external_raw_query", _fake_external_query)

        response = test_app.post(
            "/api/query",
            json={"sql": "SELECT COUNT(*) AS order_count FROM orders", "project_id": 1},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert captured["ds_type"] == "postgresql"
        assert data["rows"] == [{"order_count": 3}]
        assert "sqlglot is not installed" in (data.get("warning") or "")

    def test_execute_query_rejects_physical_table_without_semantic_model(self, test_app: TestClient, auth_headers: dict, seed_project: dict):
        import db

        con = db.get_connection()
        con.execute("INSERT INTO metadata.datasources (id, name, type, properties_encrypted) VALUES (1, 'sample', 'sample', '{}')")
        con.execute("INSERT INTO metadata.project_datasources (id, project_id, datasource_id, alias) VALUES (1, 1, 1, 'sample')")

        response = test_app.post(
            "/api/query",
            json={"sql": "SELECT * FROM physical_orders", "project_id": 1, "limit": 20},
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert "SQL must reference semantic model names" in response.json()["data"]["warning"]

    def test_dry_plan_canonicalizes_known_physical_table_reference_to_model_name(
        self,
        test_app: TestClient,
        auth_headers: dict,
        seed_project: dict,
        seed_model: dict,
        test_db,
    ):
        test_db.execute(
            "UPDATE metadata.models SET name = 'orders_model', table_reference = 'public.orders_mv' WHERE id = 1"
        )

        response = test_app.post(
            "/api/query/dry-plan",
            json={"sql": "SELECT COUNT(*) AS order_count FROM public.orders_mv", "project_id": 1},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["model_refs"] == ["orders_model"]
        assert data.get("model_ref_case_sensitive") is True
        assert "orders_model" in str(data.get("planned_sql") or "")
        assert "orders_mv" not in str(data.get("planned_sql") or "")

    def test_dry_plan_can_disable_case_sensitive_model_reference_matching(
        self,
        test_app: TestClient,
        auth_headers: dict,
        seed_project: dict,
        seed_model: dict,
        test_db,
    ):
        test_db.execute(
            "UPDATE metadata.models SET name = 'OrdersModel', table_reference = 'public.orders_mv' WHERE id = 1"
        )
        test_db.execute(
            "INSERT OR REPLACE INTO metadata.settings (key, value) VALUES ('router_model_ref_case_sensitive', ?::JSON)",
            ["false"],
        )

        response = test_app.post(
            "/api/query/dry-plan",
            json={"sql": "SELECT COUNT(*) AS order_count FROM ordersmodel", "project_id": 1},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data.get("model_ref_case_sensitive") is False

    def test_execute_project_sql_routed_accepts_known_physical_table_reference(
        self,
        monkeypatch,
        seed_project: dict,
        seed_model: dict,
        test_db,
    ):
        import services.ask_service as ask_service

        test_db.execute(
            "INSERT INTO metadata.datasources (id, name, type, properties_encrypted) VALUES (1, 'pg', 'postgresql', '{}')"
        )
        test_db.execute(
            "INSERT INTO metadata.project_datasources (id, project_id, datasource_id, alias) VALUES (1, 1, 1, 'pg')"
        )
        test_db.execute(
            "UPDATE metadata.models SET name = 'orders_model', table_reference = 'public.orders_mv', source_binding_id = 1 WHERE id = 1"
        )

        captured: dict[str, str] = {}

        monkeypatch.setattr(ask_service, "_transpile_sql_for_dialect", lambda sql, _ds_type: (sql, None))

        def _fake_external_query_scoped(ds_type: str, props: dict, sql: str, row_limit: int, project_id: int | None):
            captured["ds_type"] = ds_type
            captured["sql"] = sql
            return {"columns": ["order_count"], "tuples": [(7,)]}

        monkeypatch.setattr(ask_service, "_execute_external_raw_query_scoped", _fake_external_query_scoped)

        result = ask_service._execute_project_sql_routed(
            "SELECT COUNT(*) AS order_count FROM public.orders_mv",
            project_id=1,
            user_id=1,
            limit=20,
        )

        assert captured["ds_type"] == "postgresql"
        assert "orders_mv" in str(captured.get("sql") or "")
        assert result["rows"] == [{"order_count": 7}]
        assert result["columns"] == ["order_count"]
        assert "warning" not in result

    def test_execute_project_sql_routed_handles_mixed_case_model_name_reference(
        self,
        monkeypatch,
        seed_project: dict,
        seed_model: dict,
        test_db,
    ):
        import services.ask_service as ask_service

        test_db.execute(
            "INSERT INTO metadata.datasources (id, name, type, properties_encrypted) VALUES (1, 'pg', 'postgresql', '{}')"
        )
        test_db.execute(
            "INSERT INTO metadata.project_datasources (id, project_id, datasource_id, alias) VALUES (1, 1, 1, 'pg')"
        )
        test_db.execute(
            "UPDATE metadata.models SET name = 'OrdersModel', table_reference = 'public.orders_mv', source_binding_id = 1 WHERE id = 1"
        )

        captured: dict[str, str] = {}

        monkeypatch.setattr(ask_service, "_transpile_sql_for_dialect", lambda sql, _ds_type: (sql, None))

        def _fake_external_query_scoped(ds_type: str, props: dict, sql: str, row_limit: int, project_id: int | None):
            captured["ds_type"] = ds_type
            captured["sql"] = sql
            return {"columns": ["order_count"], "tuples": [(9,)]}

        monkeypatch.setattr(ask_service, "_execute_external_raw_query_scoped", _fake_external_query_scoped)

        result = ask_service._execute_project_sql_routed(
            "SELECT COUNT(*) AS order_count FROM public.orders_mv",
            project_id=1,
            user_id=1,
            limit=20,
        )

        assert captured["ds_type"] == "postgresql"
        assert "orders_mv" in str(captured.get("sql") or "")
        assert result["rows"] == [{"order_count": 9}]
        assert "warning" not in result

    def test_execute_project_sql_routed_does_not_loosen_qualified_table_mapping(
        self,
        seed_project: dict,
        seed_model: dict,
        test_db,
    ):
        import services.ask_service as ask_service

        test_db.execute(
            "INSERT INTO metadata.datasources (id, name, type, properties_encrypted) VALUES (1, 'pg', 'postgresql', '{}')"
        )
        test_db.execute(
            "INSERT INTO metadata.project_datasources (id, project_id, datasource_id, alias) VALUES (1, 1, 1, 'pg')"
        )
        test_db.execute(
            "UPDATE metadata.models SET name = 'orders_model', table_reference = 'public.orders_mv', source_binding_id = 1 WHERE id = 1"
        )
        test_db.execute(
            "UPDATE metadata.models SET source_binding_id = 1 WHERE id = 2"
        )

        result = ask_service._execute_project_sql_routed(
            "SELECT COUNT(*) AS order_count FROM audit.orders_mv",
            project_id=1,
            user_id=1,
            limit=20,
        )

        assert "warning" in result
        assert "SQL must reference semantic model names" in str(result.get("warning") or "")

    def test_execute_project_sql_routed_defaults_to_case_sensitive_model_matching(
        self,
        seed_project: dict,
        seed_model: dict,
        test_db,
    ):
        import services.ask_service as ask_service

        test_db.execute(
            "INSERT INTO metadata.datasources (id, name, type, properties_encrypted) VALUES (1, 'pg', 'postgresql', '{}')"
        )
        test_db.execute(
            "INSERT INTO metadata.project_datasources (id, project_id, datasource_id, alias) VALUES (1, 1, 1, 'pg')"
        )
        test_db.execute(
            "UPDATE metadata.models SET name = 'OrdersModel', table_reference = 'public.orders_mv', source_binding_id = 1 WHERE id = 1"
        )

        result = ask_service._execute_project_sql_routed(
            "SELECT COUNT(*) AS order_count FROM ordersmodel",
            project_id=1,
            user_id=1,
            limit=20,
        )

        assert "warning" in result
        warning_text = str(result.get("warning") or "")
        assert (
            "SQL references models that are not mapped to a datasource binding" in warning_text
            or "SQL must reference semantic model names" in warning_text
        )
        assert bool((result.get("security_plan") or {}).get("model_ref_case_sensitive")) is True

    def test_execute_project_sql_routed_can_disable_case_sensitive_model_matching_via_setting(
        self,
        monkeypatch,
        seed_project: dict,
        seed_model: dict,
        test_db,
    ):
        import services.ask_service as ask_service

        test_db.execute(
            "INSERT INTO metadata.datasources (id, name, type, properties_encrypted) VALUES (1, 'pg', 'postgresql', '{}')"
        )
        test_db.execute(
            "INSERT INTO metadata.project_datasources (id, project_id, datasource_id, alias) VALUES (1, 1, 1, 'pg')"
        )
        test_db.execute(
            "UPDATE metadata.models SET name = 'OrdersModel', table_reference = 'public.orders_mv', source_binding_id = 1 WHERE id = 1"
        )
        test_db.execute(
            "INSERT OR REPLACE INTO metadata.settings (key, value) VALUES ('router_model_ref_case_sensitive', ?::JSON)",
            ["false"],
        )

        captured: dict[str, str] = {}

        monkeypatch.setattr(ask_service, "_transpile_sql_for_dialect", lambda sql, _ds_type: (sql, None))

        def _fake_external_query_scoped(ds_type: str, props: dict, sql: str, row_limit: int, project_id: int | None):
            captured["ds_type"] = ds_type
            captured["sql"] = sql
            return {"columns": ["order_count"], "tuples": [(11,)]}

        monkeypatch.setattr(ask_service, "_execute_external_raw_query_scoped", _fake_external_query_scoped)

        result = ask_service._execute_project_sql_routed(
            "SELECT COUNT(*) AS order_count FROM ordersmodel",
            project_id=1,
            user_id=1,
            limit=20,
        )

        assert captured["ds_type"] == "postgresql"
        assert "orders_mv" in str(captured.get("sql") or "")
        assert result["rows"] == [{"order_count": 11}]
        assert "warning" not in result
        assert bool((result.get("security_plan") or {}).get("model_ref_case_sensitive")) is False

    def test_execute_invalid_sql(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/query",
            json={"sql": "INVALID SQL !!!", "project_id": 1},
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert response.json()["detail"] is not None

    def test_execute_forbidden_duckdb_function(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/query",
            json={"sql": "SELECT * FROM read_csv('/etc/passwd')", "project_id": 1},
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert response.json()["detail"] is not None

    def test_execute_query_missing_sql(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/query", json={"project_id": 1}, headers=auth_headers
        )
        assert response.status_code == 422

    def test_execute_query_missing_project_id(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/query", json={"sql": "SELECT 1"}, headers=auth_headers
        )
        assert response.status_code == 422

    def test_execute_query_rejects_project_zero(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/query", json={"sql": "SELECT 1", "project_id": 0}, headers=auth_headers
        )
        assert response.status_code == 400

    def test_execute_query_dry_run_returns_plan(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/query", json={"sql": "SELECT 1", "project_id": 1, "dry_run": True}, headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["data"]["planned_sql"] == "SELECT 1"

    def test_query_metrics_endpoint_returns_project_scoped_metrics(self, test_app: TestClient, auth_headers: dict):
        import services.ask_service as ask_service

        ask_service.clear_execution_metrics()
        ask_service._record_execution_metric("postgresql", "success", 12.5, 3, project_id=1)
        ask_service._record_execution_metric("mysql", "error", 20.0, 0, project_id=2)

        response = test_app.get("/api/query/metrics?project_id=1", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()["data"]
        assert "postgresql" in data
        assert data["postgresql"]["total"] == 1
        assert "mysql" not in data

    def test_query_metrics_endpoint_rejects_invalid_project_id(self, test_app: TestClient, auth_headers: dict):
        response = test_app.get("/api/query/metrics?project_id=0", headers=auth_headers)
        assert response.status_code == 400

    def test_query_metrics_endpoint_can_include_route_dimensions(self, test_app: TestClient, auth_headers: dict):
        import services.ask_service as ask_service

        ask_service.clear_execution_metrics()
        ask_service._record_execution_metric("postgresql", "success", 12.5, 3, project_id=1)
        ask_service._emit_route_event(
            "generation_route_decision",
            {
                "generation_engine": "direct_llm",
                "strategy_selected_engine": "fewshot_cot",
                "strategy_mode": "adaptive_risk",
                "strategy_policy": "risk_consensus_fewshot",
                "strategy_risk_score": 6,
                "strategy_risk_level": "medium",
                "strict_json_mode": "json_schema",
                "fallback_count": 1,
                "fallback_chain": ["repair"],
            },
            project_id=1,
        )
        ask_service._emit_route_event(
            "execution_route_decision",
            {
                "route_kind": "single_external",
            },
            project_id=1,
        )
        ask_service._emit_route_event(
            "sql_generation_retry",
            {
                "reason": "empty_llm_content",
                "attempt": 1,
                "max_retries": 3,
                "generation_engine": "direct_llm",
            },
            project_id=1,
        )
        ask_service._emit_route_event(
            "sql_validation_issue",
            {
                "stage": "validate_sql_columns",
                "issue_buckets": {"duplicate_alias": 2, "wrong_alias_owner": 1},
            },
            project_id=1,
        )
        ask_service._emit_route_event(
            "repair_guard_blocked",
            {
                "issue_buckets": {"duplicate_alias": 1},
                "errors": ["repair returned unresolved SQL references"],
            },
            project_id=1,
        )
        ask_service._emit_route_event(
            "sql_repair_short_circuit",
            {
                "reason": "column_validation",
                "attempt": 1,
                "max_retries": 2,
                "generation_engine": "fewshot_cot",
            },
            project_id=1,
        )
        ask_service._emit_route_event(
            "schema_link_fallback",
            {
                "reason": "empty_content",
                "fallback": "token_semantic_matching",
            },
            project_id=1,
        )
        ask_service._emit_route_event(
            "sql_generation_fallback",
            {
                "from_engine": "decompose_merge",
                "to_engine": "direct_llm",
                "reason": "group_by",
            },
            project_id=1,
        )
        ask_service._emit_route_event(
            "final_answer_fallback",
            {
                "reason": "ungrounded_summary",
                "mode": "deterministic_row_summary",
            },
            project_id=1,
        )

        response = test_app.get(
            "/api/query/metrics?project_id=1&include_route_dimensions=true",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["by_datasource"]["postgresql"]["total"] == 1
        assert data["route_dimensions"]["generation_engine"]["direct_llm"] >= 1
        assert data["route_dimensions"]["strategy_selected_engine"]["fewshot_cot"] >= 1
        assert data["route_dimensions"]["strategy_mode"]["adaptive_risk"] >= 1
        assert data["route_dimensions"]["strategy_policy"]["risk_consensus_fewshot"] >= 1
        assert data["route_dimensions"]["strategy_risk_level"]["medium"] >= 1
        assert data["route_dimensions"]["strategy_risk_score_total"] >= 6
        assert data["route_dimensions"]["strategy_risk_score_avg"] >= 0.0
        assert data["route_dimensions"]["route_kind"]["single_external"] >= 1
        assert data["route_dimensions"]["strict_json_mode"]["json_schema"] >= 1
        assert data["route_dimensions"]["generation_retry_reason"]["empty_llm_content"] >= 1
        assert data["route_dimensions"]["llm_empty_response_retry"] >= 1
        assert data["route_dimensions"]["validation_issue_bucket"]["duplicate_alias"] >= 3
        assert data["route_dimensions"]["repair_guard_blocked"] >= 1
        assert data["route_dimensions"]["repair_short_circuit"] >= 1
        assert data["route_dimensions"]["repair_short_circuit_reason"]["column_validation"] >= 1
        assert data["route_dimensions"]["schema_link_fallback_total"] >= 1
        assert data["route_dimensions"]["schema_link_fallback_reason"]["empty_content"] >= 1
        assert data["route_dimensions"]["sql_generation_fallback_total"] >= 1
        assert data["route_dimensions"]["sql_generation_fallback_reason"]["group_by"] >= 1
        assert data["route_dimensions"]["final_answer_fallback_total"] >= 1
        assert data["route_dimensions"]["final_answer_fallback_reason"]["ungrounded_summary"] >= 1
        assert data["route_dimensions"]["generation_decision_total"] >= 1
        assert data["route_dimensions"]["schema_link_fallback_rate"] >= 0.0
        assert data["route_dimensions"]["sql_generation_fallback_rate"] >= 0.0
        assert data["route_dimensions"]["final_answer_fallback_rate"] >= 0.0
        assert data["route_dimensions"]["window_seconds"] == ask_service.ROUTER_CONFIG["route_observability_window_seconds"]
        assert isinstance(data["strategy_trend_history"], list)
        assert len(data["strategy_trend_history"]) >= 1
        latest_trend = data["strategy_trend_history"][-1]
        assert latest_trend["decision_total"] >= 1
        assert latest_trend["dominant_mode"] == "adaptive_risk"
        assert latest_trend["dominant_policy"] == "risk_consensus_fewshot"

    def test_strategy_trend_history_recovers_from_persisted_snapshot(
        self,
        test_app: TestClient,
        auth_headers: dict,
        test_db,
        monkeypatch,
    ):
        import services.ask_service as ask_service

        ask_service.clear_execution_metrics()
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_window_seconds", 1800)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_persist_enabled", True)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_persist_interval_seconds", 1)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_persist_event_delta", 1)

        clock = {"mono": 1000.0, "wall": 100000.0}
        monkeypatch.setattr(ask_service.time, "monotonic", lambda: clock["mono"])
        monkeypatch.setattr(ask_service.time, "time", lambda: clock["wall"])

        ask_service._emit_route_event(
            "generation_route_decision",
            {
                "generation_engine": "direct_llm",
                "strategy_selected_engine": "fewshot_cot",
                "strategy_mode": "adaptive_risk",
                "strategy_policy": "risk_consensus_fewshot",
                "strategy_risk_score": 5,
                "strategy_risk_level": "medium",
                "strict_json_mode": "json_schema",
                "fallback_count": 0,
            },
            project_id=1,
        )

        trend_key = ask_service._route_observability_strategy_trend_setting_key(1)
        persisted = test_db.execute("SELECT value FROM metadata.settings WHERE key = ?", [trend_key]).fetchone()
        assert persisted is not None

        with ask_service._route_dimension_metrics_lock:
            ask_service._route_dimension_metrics_by_project.clear()
            ask_service._route_dimension_events_by_project.clear()
            ask_service._route_observability_snapshot_state_by_project.clear()
            ask_service._route_strategy_trend_points_by_project.clear()

        restored = test_app.get(
            "/api/query/metrics?project_id=1&include_route_dimensions=true",
            headers=auth_headers,
        )
        assert restored.status_code == 200
        restored_data = restored.json()["data"]
        history = restored_data["strategy_trend_history"]
        assert len(history) >= 1
        latest = history[-1]
        assert latest["decision_total"] >= 1
        assert latest["dominant_mode"] == "adaptive_risk"
        assert latest["dominant_policy"] == "risk_consensus_fewshot"

    def test_route_dimensions_respect_observability_window(self, test_app: TestClient, auth_headers: dict, monkeypatch):
        import services.ask_service as ask_service

        ask_service.clear_execution_metrics()
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_window_seconds", 1800)

        clock = {"now": 1000.0}
        monkeypatch.setattr(ask_service.time, "monotonic", lambda: clock["now"])

        ask_service._emit_route_event(
            "sql_generation_retry",
            {
                "reason": "empty_llm_content",
                "attempt": 1,
                "max_retries": 3,
                "generation_engine": "direct_llm",
            },
            project_id=1,
        )

        clock["now"] = 3200.0
        ask_service._emit_route_event(
            "repair_guard_blocked",
            {
                "issue_buckets": {"duplicate_alias": 1},
                "errors": ["repair returned unresolved SQL references"],
            },
            project_id=1,
        )

        response = test_app.get(
            "/api/query/metrics?project_id=1&include_route_dimensions=true",
            headers=auth_headers,
        )

        assert response.status_code == 200
        route_dimensions = response.json()["data"]["route_dimensions"]
        assert route_dimensions["window_seconds"] == 1800
        assert route_dimensions["repair_guard_blocked"] >= 1
        assert route_dimensions["generation_retry_reason"].get("empty_llm_content", 0) == 0

    def test_query_metrics_include_llm_http_circuit_snapshot(self, test_app: TestClient, auth_headers: dict, monkeypatch):
        import services.llm_service as llm_service

        llm_service.clear_llm_http_circuit_state()
        clock = {"mono": 1000.0}
        monkeypatch.setattr(llm_service.time, "monotonic", lambda: clock["mono"])
        llm_service._LLM_HTTP_CIRCUIT_STATE_BY_KEY["openai:https://api.openai.com/v1:gpt-4o"] = {
            "consecutive_failures": 0,
            "open_until": 1020.0,
        }

        response = test_app.get(
            "/api/query/metrics?project_id=1&include_route_dimensions=true",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["llm_http_circuit"]["open_keys"] == 1
        assert data["llm_http_circuit"]["total_keys"] >= 1
        assert (
            data["llm_http_circuit"]["keys"]["openai:https://api.openai.com/v1:gpt-4o"]["state"]
            == "open"
        )
        llm_service.clear_llm_http_circuit_state()

    def test_route_dimensions_cap_event_history_per_project(self, test_app: TestClient, auth_headers: dict, monkeypatch):
        import services.ask_service as ask_service

        ask_service.clear_execution_metrics()
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_window_seconds", 3600)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_max_events_per_project", 1000)

        clock = {"now": 1000.0}
        monkeypatch.setattr(ask_service.time, "monotonic", lambda: clock["now"])

        for idx in range(1005):
            ask_service._emit_route_event(
                "sql_generation_retry",
                {
                    "reason": f"retry_{idx}",
                    "attempt": 1,
                    "max_retries": 3,
                    "generation_engine": "direct_llm",
                },
                project_id=1,
            )
            clock["now"] += 1.0

        response = test_app.get(
            "/api/query/metrics?project_id=1&include_route_dimensions=true",
            headers=auth_headers,
        )

        assert response.status_code == 200
        route_dimensions = response.json()["data"]["route_dimensions"]
        assert route_dimensions["events_total"] == 1000
        assert route_dimensions["generation_retry_reason"].get("retry_0", 0) == 0
        assert route_dimensions["generation_retry_reason"].get("retry_1", 0) == 0
        assert route_dimensions["generation_retry_reason"].get("retry_4", 0) == 0
        assert route_dimensions["generation_retry_reason"].get("retry_5", 0) == 1
        assert route_dimensions["generation_retry_reason"].get("retry_1004", 0) == 1

    def test_route_dimensions_can_recover_from_persisted_snapshot(self, test_app: TestClient, auth_headers: dict, monkeypatch):
        import services.ask_service as ask_service

        ask_service.clear_execution_metrics()
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_window_seconds", 1800)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_persist_enabled", True)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_persist_interval_seconds", 1)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_persist_event_delta", 1)

        clock = {"mono": 1000.0, "wall": 100000.0}
        monkeypatch.setattr(ask_service.time, "monotonic", lambda: clock["mono"])
        monkeypatch.setattr(ask_service.time, "time", lambda: clock["wall"])

        ask_service._emit_route_event(
            "sql_generation_retry",
            {
                "reason": "persisted_retry",
                "attempt": 1,
                "max_retries": 3,
                "generation_engine": "direct_llm",
            },
            project_id=1,
        )

        response = test_app.get(
            "/api/query/metrics?project_id=1&include_route_dimensions=true",
            headers=auth_headers,
        )
        assert response.status_code == 200
        route_dimensions = response.json()["data"]["route_dimensions"]
        assert route_dimensions["generation_retry_reason"].get("persisted_retry", 0) == 1

        with ask_service._route_dimension_metrics_lock:
            ask_service._route_dimension_metrics_by_project.clear()
            ask_service._route_dimension_events_by_project.clear()
            ask_service._route_observability_snapshot_state_by_project.clear()

        restored = test_app.get(
            "/api/query/metrics?project_id=1&include_route_dimensions=true",
            headers=auth_headers,
        )
        assert restored.status_code == 200
        restored_dims = restored.json()["data"]["route_dimensions"]
        assert restored_dims["generation_retry_reason"].get("persisted_retry", 0) == 1
        assert restored_dims["window_seconds"] == 1800

        clock["wall"] += 1900.0
        with ask_service._route_dimension_metrics_lock:
            ask_service._route_dimension_metrics_by_project.clear()
            ask_service._route_dimension_events_by_project.clear()
            ask_service._route_observability_snapshot_state_by_project.clear()

        expired = test_app.get(
            "/api/query/metrics?project_id=1&include_route_dimensions=true",
            headers=auth_headers,
        )
        assert expired.status_code == 200
        expired_dims = expired.json()["data"]["route_dimensions"]
        assert expired_dims["events_total"] == 0
        assert expired_dims["generation_retry_reason"].get("persisted_retry", 0) == 0

    def test_route_dimensions_persist_respects_interval_and_event_delta(self, monkeypatch):
        import services.ask_service as ask_service

        ask_service.clear_execution_metrics()
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_window_seconds", 1800)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_persist_enabled", True)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_persist_interval_seconds", 30)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_persist_event_delta", 3)

        clock = {"mono": 1000.0, "wall": 100000.0}
        monkeypatch.setattr(ask_service.time, "monotonic", lambda: clock["mono"])
        monkeypatch.setattr(ask_service.time, "time", lambda: clock["wall"])

        persist_calls: list[tuple[int, int, float]] = []

        def _capture_persist(project_id: int, snapshot: dict[str, object], captured_at_unix: float) -> None:
            persist_calls.append((project_id, int(snapshot.get("events_total") or 0), float(captured_at_unix)))

        monkeypatch.setattr(ask_service, "_persist_route_observability_snapshot", _capture_persist)

        for idx in range(5):
            ask_service._emit_route_event(
                "sql_generation_retry",
                {
                    "reason": f"persist_delta_{idx}",
                    "attempt": 1,
                    "max_retries": 3,
                    "generation_engine": "direct_llm",
                },
                project_id=1,
            )
            if idx == 0:
                assert len(persist_calls) == 1
                assert persist_calls[-1][1] == 1
            if idx == 1:
                clock["mono"] += 5.0
                clock["wall"] += 5.0
                assert len(persist_calls) == 1
            if idx == 2:
                clock["mono"] += 5.0
                clock["wall"] += 5.0
                assert len(persist_calls) == 1
            if idx == 3:
                assert len(persist_calls) == 2
                assert persist_calls[-1][1] == 4
            if idx == 4:
                clock["mono"] += 31.0
                clock["wall"] += 31.0

        ask_service._emit_route_event(
            "sql_generation_retry",
            {
                "reason": "persist_delta_interval",
                "attempt": 1,
                "max_retries": 3,
                "generation_engine": "direct_llm",
            },
            project_id=1,
        )

        assert len(persist_calls) == 3
        assert persist_calls[-1][1] == 6

    def test_strategy_trend_persist_uses_dedicated_thresholds(self, test_db, monkeypatch):
        import services.ask_service as ask_service

        ask_service.clear_execution_metrics()
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_window_seconds", 1800)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_persist_enabled", True)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_persist_interval_seconds", 3600)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_persist_event_delta", 9999)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_strategy_trend_max_points", 8)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_strategy_trend_persist_interval_seconds", 1)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_strategy_trend_persist_decision_delta", 1)

        clock = {"mono": 1000.0, "wall": 100000.0}
        monkeypatch.setattr(ask_service.time, "monotonic", lambda: clock["mono"])
        monkeypatch.setattr(ask_service.time, "time", lambda: clock["wall"])

        ask_service._emit_route_event(
            "generation_route_decision",
            {
                "generation_engine": "direct_llm",
                "strategy_selected_engine": "fewshot_cot",
                "strategy_mode": "adaptive_risk",
                "strategy_policy": "risk_consensus_fewshot",
                "strategy_risk_score": 6,
                "strategy_risk_level": "medium",
                "strict_json_mode": "json_schema",
                "fallback_count": 0,
            },
            project_id=1,
        )

        snapshot_key = ask_service._route_observability_snapshot_setting_key(1)
        trend_key = ask_service._route_observability_strategy_trend_setting_key(1)
        snapshot_row = test_db.execute("SELECT value FROM metadata.settings WHERE key = ?", [snapshot_key]).fetchone()
        trend_row = test_db.execute("SELECT value FROM metadata.settings WHERE key = ?", [trend_key]).fetchone()

        assert snapshot_row is None
        assert trend_row is not None

        trend_payload = trend_row[0]
        if isinstance(trend_payload, str):
            trend_payload = json.loads(trend_payload)
        assert isinstance(trend_payload, dict)
        points = trend_payload.get("points")
        assert isinstance(points, list)
        assert len(points) == 1
        point = points[0]
        assert int(point.get("decision_total") or 0) >= 1
        assert point.get("dominant_mode") == "adaptive_risk"
        assert point.get("dominant_policy") == "risk_consensus_fewshot"

    def test_clear_route_dimension_metrics_deletes_persisted_snapshot(self, test_db, monkeypatch):
        import services.ask_service as ask_service

        ask_service.clear_execution_metrics()
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_window_seconds", 1800)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_persist_enabled", True)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_persist_interval_seconds", 1)
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_persist_event_delta", 1)

        clock = {"mono": 1000.0, "wall": 100000.0}
        monkeypatch.setattr(ask_service.time, "monotonic", lambda: clock["mono"])
        monkeypatch.setattr(ask_service.time, "time", lambda: clock["wall"])

        ask_service._emit_route_event(
            "sql_generation_retry",
            {
                "reason": "persisted_for_clear",
                "attempt": 1,
                "max_retries": 3,
                "generation_engine": "direct_llm",
            },
            project_id=1,
        )
        ask_service._emit_route_event(
            "generation_route_decision",
            {
                "generation_engine": "direct_llm",
                "strategy_selected_engine": "direct_llm",
                "strategy_mode": "adaptive_risk",
                "strategy_policy": "risk_constrained_direct",
                "strategy_risk_score": 2,
                "strategy_risk_level": "low",
                "strict_json_mode": "none",
                "fallback_count": 0,
            },
            project_id=1,
        )

        snapshot_key = ask_service._route_observability_snapshot_setting_key(1)
        trend_key = ask_service._route_observability_strategy_trend_setting_key(1)
        before_clear = test_db.execute("SELECT value FROM metadata.settings WHERE key = ?", [snapshot_key]).fetchone()
        before_trend_clear = test_db.execute("SELECT value FROM metadata.settings WHERE key = ?", [trend_key]).fetchone()
        assert before_clear is not None
        assert before_trend_clear is not None

        ask_service.clear_route_dimension_metrics(project_id=1)

        after_clear = test_db.execute("SELECT value FROM metadata.settings WHERE key = ?", [snapshot_key]).fetchone()
        after_trend_clear = test_db.execute("SELECT value FROM metadata.settings WHERE key = ?", [trend_key]).fetchone()
        assert after_clear is None
        assert after_trend_clear is None

    def test_route_dimensions_handles_corrupted_persisted_snapshot(self, test_app: TestClient, auth_headers: dict, test_db, monkeypatch):
        import services.ask_service as ask_service

        ask_service.clear_execution_metrics()
        monkeypatch.setitem(ask_service.ROUTER_CONFIG, "route_observability_window_seconds", 1800)
        monkeypatch.setattr(ask_service.time, "time", lambda: 100500.0)

        with ask_service._route_dimension_metrics_lock:
            ask_service._route_dimension_metrics_by_project.clear()
            ask_service._route_dimension_events_by_project.clear()
            ask_service._route_observability_snapshot_state_by_project.clear()

        snapshot_key = ask_service._route_observability_snapshot_setting_key(1)
        test_db.execute(
            "INSERT OR REPLACE INTO metadata.settings (key, value) VALUES (?, ?::JSON)",
            [
                snapshot_key,
                json.dumps(
                    {
                        "captured_at_unix": 100000,
                        "window_seconds": 1800,
                        "events_total": "not-a-number",
                        "route_kind": "bad-map",
                        "generation_retry_reason": {"empty_llm_content": "3"},
                        "fallback_count_avg": "not-a-float",
                    }
                ),
            ],
        )

        response = test_app.get(
            "/api/query/metrics?project_id=1&include_route_dimensions=true",
            headers=auth_headers,
        )

        assert response.status_code == 200
        route_dimensions = response.json()["data"]["route_dimensions"]
        assert route_dimensions["events_total"] == 0
        assert route_dimensions["route_kind"] == {}
        assert route_dimensions["generation_retry_reason"].get("empty_llm_content") == 3
        assert route_dimensions["fallback_count_avg"] == 0.0


class TestDryPlan:
    def test_dry_plan(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/query/dry-plan",
            json={"sql": "SELECT 1", "project_id": 1},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["planned_sql"] == "SELECT 1"
        assert data["data"]["model_refs"] == []

    def test_dry_plan_missing_fields(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/query/dry-plan", json={}, headers=auth_headers
        )
        assert response.status_code == 422

    def test_dry_plan_rejects_mutating_sql(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/query/dry-plan",
            json={"sql": "DROP TABLE users", "project_id": 1},
            headers=auth_headers,
        )
        assert response.status_code == 400

    def test_dry_plan_rejects_project_zero(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/query/dry-plan",
            json={"sql": "SELECT 1", "project_id": 0},
            headers=auth_headers,
        )
        assert response.status_code == 400

    def test_dry_plan_recombines_split_read_only_sql(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/query/dry-plan",
            json={"sql": "WITH cte AS (SELECT 1 AS id); SELECT id FROM cte", "project_id": 1},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["planned_sql"] == "WITH cte AS (SELECT 1 AS id) SELECT id FROM cte"


def test_external_execution_supports_all_configured_types(monkeypatch):
    import services.ask_service as ask_service

    monkeypatch.setattr(ask_service, "_import_optional", lambda _name: None)
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
        result = ask_service._execute_external_raw_query(ds_type, {}, "SELECT 1", 1)
        warning = str(result.get("warning") or "").lower()
        assert "not implemented yet" not in warning


def test_apply_binding_limit_handles_oracle_without_limit_keyword():
    import services.ask_service as ask_service

    limited_sql = ask_service._apply_binding_limit("SELECT * FROM orders", "oracle", 25)

    assert "FETCH FIRST 25 ROWS ONLY" in limited_sql
    assert " limit " not in limited_sql.lower()


def test_execute_external_raw_query_normalizes_alias_types(monkeypatch):
    import services.ask_service as ask_service

    captured: list[str] = []

    def _fake_postgresql(props, sql, row_limit):
        captured.append("postgresql")
        return {"columns": ["n"], "tuples": [(1,)]}

    monkeypatch.setattr(ask_service, "_execute_postgresql", _fake_postgresql)

    result = ask_service._execute_external_raw_query("redshift", {}, "SELECT 1", 1)

    assert result["tuples"] == [(1,)]
    assert captured == ["postgresql"]


def test_cross_source_aggregate_materialization_includes_join_keys(monkeypatch):
    import time

    import services.ask_service as ask_service

    planned_sql = (
        "SELECT c.id, c.name, COUNT(DISTINCT d.id) AS department_count "
        "FROM company c "
        "LEFT JOIN department d ON c.id = d.emp_id "
        "GROUP BY c.id, c.name"
    )
    planned_limited_sql = ask_service._apply_limit(planned_sql, 20)
    referenced_by_binding = {
        1: [{"name": "company", "table_reference": "company", "source_binding_id": 1, "columns": ["id", "name"]}],
        2: [{"name": "department", "table_reference": "department", "source_binding_id": 2, "columns": ["id", "emp_id"]}],
    }
    binding_lookup = {
        1: ("postgresql", {}),
        2: ("mysql", {}),
    }
    captured_columns: dict[str, list[str]] = {}

    def _fake_model_source_select(model, ds_type, row_limit, where_clauses=None, select_columns=None):
        captured_columns[model["name"]] = list(select_columns or [])
        return "SELECT 1"

    def _fake_execute_binding_raw_query(ds_type, props, sql, row_limit, project_id, binding_id):
        model_name = "company" if binding_id == 1 else "department"
        selected = captured_columns.get(model_name) or ["id"]
        return {"columns": selected, "tuples": []}

    monkeypatch.setattr(ask_service, "_model_source_select", _fake_model_source_select)
    monkeypatch.setattr(ask_service, "_execute_binding_raw_query", _fake_execute_binding_raw_query)

    result = ask_service._execute_cross_source_query(
        planned_sql=planned_sql,
        planned_limited_sql=planned_limited_sql,
        project_id=1,
        row_limit=20,
        plan={"security": {"cls": []}},
        start=time.perf_counter(),
        referenced_by_binding=referenced_by_binding,
        binding_lookup=binding_lookup,
    )

    assert "warning" not in result
    assert "emp_id" in {column.lower() for column in captured_columns.get("department", [])}


def test_execute_query_routes_all_supported_external_types(
    test_app: TestClient,
    auth_headers: dict,
    seed_project: dict,
    seed_model: dict,
    test_db,
    monkeypatch,
):
    import services.ask_service as ask_service

    called: list[str] = []

    def _fake_external_query(ds_type: str, _props: dict, _sql: str, _row_limit: int):
        called.append(ds_type)
        return {"columns": ["n"], "tuples": [(1,)]}

    monkeypatch.setattr(ask_service, "_execute_external_raw_query", _fake_external_query)

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
    canonical_expected = {
        "postgresql": "postgresql",
        "redshift": "redshift",
        "mysql": "mysql",
        "mariadb": "mysql",
        "mssql": "mssql",
        "clickhouse": "clickhouse",
        "trino": "trino",
        "athena": "athena",
        "oracle": "oracle",
        "snowflake": "snowflake",
        "bigquery": "bigquery",
        "databricks": "databricks",
    }

    for index, ds_type in enumerate(supported_external_types, start=1):
        datasource_id = 100 + index
        binding_id = 200 + index
        test_db.execute(
            "INSERT INTO metadata.datasources (id, name, type, properties_encrypted) VALUES (?, ?, ?, '{}')",
            [datasource_id, f"ds-{ds_type}", ds_type],
        )
        test_db.execute(
            "INSERT INTO metadata.project_datasources (id, project_id, datasource_id, alias) VALUES (?, 1, ?, ?)",
            [binding_id, datasource_id, f"b-{ds_type}"],
        )
        test_db.execute("UPDATE metadata.models SET source_binding_id = ? WHERE id = 1", [binding_id])

        response = test_app.post(
            "/api/query",
            json={"sql": "SELECT COUNT(*) AS n FROM orders", "project_id": 1},
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json()["data"]["rows"] == [{"n": 1}]
        assert called[-1] == canonical_expected[ds_type]


def test_run_cross_source_fetch_jobs_uses_parallel_workers(monkeypatch):
    import services.ask_service as ask_service

    thread_names: list[str] = []

    def _fake_execute_binding_raw_query(ds_type, props, sql, row_limit, project_id, binding_id):
        thread_names.append(threading.current_thread().name)
        time.sleep(0.03)
        return {"columns": ["id"], "tuples": [(binding_id,)]}

    monkeypatch.setattr(ask_service, "_execute_binding_raw_query", _fake_execute_binding_raw_query)
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "cross_source_max_workers", 2)

    jobs = [
        {
            "binding_id": 1,
            "ds_type": "postgresql",
            "props": {},
            "model": {"name": "orders"},
            "source_sql": "SELECT 1",
            "effective_limit": 10,
        },
        {
            "binding_id": 2,
            "ds_type": "mysql",
            "props": {},
            "model": {"name": "customers"},
            "source_sql": "SELECT 2",
            "effective_limit": 10,
        },
    ]

    results = ask_service._run_cross_source_fetch_jobs(jobs, project_id=1)

    assert [item["binding_id"] for item in results] == [1, 2]
    assert any(name.startswith("cross-source-fetch") for name in thread_names)


def test_execute_postgresql_reuses_pooled_connection(monkeypatch):
    import services.ask_service as ask_service

    ask_service._clear_external_connection_pool()
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "external_connection_pool_enabled", True)

    connect_calls = {"count": 0}

    class FakeCursor:
        def __init__(self):
            self.description = [("n",)]

        def execute(self, _sql):
            return None

        def fetchmany(self, _limit):
            return [(1,)]

        def close(self):
            return None

    class FakeConnection:
        def __init__(self):
            self.closed = 0

        def cursor(self):
            return FakeCursor()

        def close(self):
            self.closed = 1

    class FakeDriver:
        def connect(self, **kwargs):
            connect_calls["count"] += 1
            return FakeConnection()

    fake_driver = FakeDriver()
    monkeypatch.setattr(
        ask_service,
        "_import_optional",
        lambda module_name: fake_driver if module_name == "psycopg" else None,
    )

    props = {
        "host": "localhost",
        "port": 5432,
        "database": "sales",
        "user": "analytics",
        "password": "secret",
    }
    result1 = ask_service._execute_postgresql(props, "SELECT 1", 5)
    result2 = ask_service._execute_postgresql(props, "SELECT 1", 5)

    assert result1["tuples"] == [(1,)]
    assert result2["tuples"] == [(1,)]
    assert connect_calls["count"] == 1
    ask_service._clear_external_connection_pool()


def test_postgresql_and_redshift_use_separate_connection_pools(monkeypatch):
    import services.ask_service as ask_service

    ask_service._clear_external_connection_pool()
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "external_connection_pool_enabled", True)

    connect_calls = {"count": 0}

    class FakeCursor:
        def __init__(self):
            self.description = [("n",)]

        def execute(self, _sql):
            return None

        def fetchmany(self, _limit):
            return [(1,)]

        def close(self):
            return None

    class FakeConnection:
        def __init__(self):
            self.closed = 0

        def cursor(self):
            return FakeCursor()

        def close(self):
            self.closed = 1

    class FakeDriver:
        def connect(self, **kwargs):
            connect_calls["count"] += 1
            return FakeConnection()

    fake_driver = FakeDriver()
    monkeypatch.setattr(
        ask_service,
        "_import_optional",
        lambda module_name: fake_driver if module_name == "psycopg" else None,
    )

    props = {
        "host": "localhost",
        "port": 5432,
        "database": "sales",
        "user": "analytics",
        "password": "secret",
    }
    result1 = ask_service._execute_external_raw_query("postgresql", props, "SELECT 1", 5)
    result2 = ask_service._execute_external_raw_query("redshift", props, "SELECT 1", 5)

    assert result1["tuples"] == [(1,)]
    assert result2["tuples"] == [(1,)]
    assert connect_calls["count"] == 2
    ask_service._clear_external_connection_pool()


def test_execute_trino_reuses_pooled_connection(monkeypatch):
    import services.ask_service as ask_service

    ask_service._clear_external_connection_pool()
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "external_connection_pool_enabled", True)

    connect_calls = {"count": 0}

    class FakeCursor:
        def __init__(self):
            self.description = [("n",)]

        def execute(self, _sql):
            return None

        def fetchmany(self, _limit):
            return [(1,)]

        def close(self):
            return None

    class FakeConnection:
        def __init__(self):
            self.closed = False

        def cursor(self):
            return FakeCursor()

        def close(self):
            self.closed = True

    class FakeTrinoModule:
        class auth:
            @staticmethod
            def BasicAuthentication(_user, _password):
                return ("basic",)

        class dbapi:
            @staticmethod
            def connect(**kwargs):
                connect_calls["count"] += 1
                return FakeConnection()

    monkeypatch.setattr(ask_service, "_import_optional", lambda module_name: FakeTrinoModule if module_name == "trino" else None)

    props = {
        "host": "trino.local",
        "port": 8080,
        "user": "analytics",
        "catalog": "hive",
        "schema": "default",
    }
    result1 = ask_service._execute_trino(props, "SELECT 1", 5)
    result2 = ask_service._execute_trino(props, "SELECT 1", 5)

    assert result1["tuples"] == [(1,)]
    assert result2["tuples"] == [(1,)]
    assert connect_calls["count"] == 1
    ask_service._clear_external_connection_pool()


def test_execute_mssql_reuses_pooled_connection_with_pymssql(monkeypatch):
    import services.ask_service as ask_service

    ask_service._clear_external_connection_pool()
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "external_connection_pool_enabled", True)

    connect_calls = {"count": 0}

    class FakeCursor:
        def __init__(self):
            self.description = [("n",)]

        def execute(self, _sql):
            return None

        def fetchmany(self, _limit):
            return [(1,)]

        def close(self):
            return None

    class FakeConnection:
        def __init__(self):
            self.closed = False

        def cursor(self):
            return FakeCursor()

        def close(self):
            self.closed = True

    class FakePymssqlModule:
        @staticmethod
        def connect(**kwargs):
            connect_calls["count"] += 1
            return FakeConnection()

    monkeypatch.setattr(
        ask_service,
        "_import_optional",
        lambda module_name: None if module_name == "pyodbc" else FakePymssqlModule if module_name == "pymssql" else None,
    )

    props = {
        "host": "mssql.local",
        "port": 1433,
        "database": "sales",
        "user": "analytics",
        "password": "secret",
    }
    result1 = ask_service._execute_mssql(props, "SELECT 1", 5)
    result2 = ask_service._execute_mssql(props, "SELECT 1", 5)

    assert result1["tuples"] == [(1,)]
    assert result2["tuples"] == [(1,)]
    assert connect_calls["count"] == 1
    ask_service._clear_external_connection_pool()


def test_external_execution_metrics_snapshot_tracks_success_and_error(monkeypatch):
    import services.ask_service as ask_service

    ask_service.clear_execution_metrics()
    monkeypatch.setattr(
        ask_service,
        "_execute_postgresql",
        lambda _props, _sql, _row_limit: {"columns": ["n"], "tuples": [(1,)]},
    )
    ask_service._execute_external_raw_query("postgresql", {}, "SELECT 1", 1)

    def _raise(*args, **kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(ask_service, "_execute_postgresql", _raise)
    with pytest.raises(RuntimeError):
        ask_service._execute_external_raw_query("postgresql", {}, "SELECT 1", 1)

    snapshot = ask_service.get_execution_metrics_snapshot()
    assert "postgresql" in snapshot
    assert snapshot["postgresql"]["total"] >= 2
    assert snapshot["postgresql"]["success"] >= 1
    assert snapshot["postgresql"]["error"] >= 1


def test_execution_metrics_snapshot_filters_by_project_id():
    import services.ask_service as ask_service

    ask_service.clear_execution_metrics()
    ask_service._record_execution_metric("postgresql", "success", 11.0, 2, project_id=1)
    ask_service._record_execution_metric("postgresql", "success", 22.0, 2, project_id=2)

    snapshot_project_1 = ask_service.get_execution_metrics_snapshot(project_id=1)
    snapshot_project_2 = ask_service.get_execution_metrics_snapshot(project_id=2)

    assert snapshot_project_1["postgresql"]["total"] == 1
    assert snapshot_project_2["postgresql"]["total"] == 1


def test_execute_project_sql_routed_normalizes_fullwidth_sql_before_planning(monkeypatch):
    import services.ask_service as ask_service

    captured: dict[str, str] = {}

    def _fake_plan(sql: str, project_id: int, user_id: int):
        captured["sql"] = sql
        return {"planned_sql": sql, "model_refs": [], "security": {"cls": []}}

    monkeypatch.setattr(ask_service, "plan_secured_sql", _fake_plan)
    monkeypatch.setattr(ask_service, "_binding_rows", lambda _project_id: [])

    result = ask_service._execute_project_sql_routed(
        "SELECT city， SUM(amount) FROM orders GROUP BY city；",
        project_id=1,
        user_id=1,
        limit=20,
    )

    assert captured["sql"] == "SELECT city, SUM(amount) FROM orders GROUP BY city"
    assert result["warning"] == "Project has no datasource bindings."


def test_execute_project_sql_routed_honors_query_limit_above_preview_range(monkeypatch):
    import services.ask_service as ask_service

    captured: dict[str, int] = {}

    monkeypatch.setattr(
        ask_service,
        "plan_secured_sql",
        lambda sql, _project_id, _user_id: {
            "planned_sql": sql,
            "model_refs": [],
            "security": {"cls": []},
        },
    )
    monkeypatch.setattr(ask_service, "_binding_rows", lambda _project_id: [])

    def _capture_apply_limit(sql: str, limit: int) -> str:
        captured["limit"] = int(limit)
        return f"SELECT * FROM ({sql}) AS prismbi_limited LIMIT {limit}"

    monkeypatch.setattr(ask_service, "_apply_limit", _capture_apply_limit)

    result = ask_service._execute_project_sql_routed(
        "SELECT 1",
        project_id=1,
        user_id=1,
        limit=500,
    )

    assert captured["limit"] == 500
    assert result["warning"] == "Project has no datasource bindings."


def test_execute_project_sql_routed_sanitizes_sensitive_execution_errors(monkeypatch):
    import services.ask_service as ask_service

    monkeypatch.setattr(
        ask_service,
        "plan_secured_sql",
        lambda sql, _project_id, _user_id: {
            "planned_sql": sql,
            "model_refs": ["orders"],
            "security": {"cls": []},
        },
    )
    monkeypatch.setattr(ask_service, "_binding_rows", lambda _project_id: [(1, "postgresql", {})])
    monkeypatch.setattr(
        ask_service,
        "_models_by_binding",
        lambda _project_id: {
            1: [{"name": "orders", "table_reference": "orders", "source_binding_id": 1, "columns": ["order_id"]}]
        },
    )
    monkeypatch.setattr(ask_service, "_rewrite_model_refs_for_source", lambda sql, _models, _ds_type: (sql, []))
    monkeypatch.setattr(ask_service, "_transpile_sql_for_dialect", lambda sql, _ds_type: (sql, None))

    def _raise_sensitive_error(_ds_type: str, _props: dict, _sql: str, _row_limit: int):
        raise RuntimeError(
            "connection failed password=supersecret token=abc123 "
            "url=postgres://alice:secret987@db.internal:5432/sales"
        )

    monkeypatch.setattr(ask_service, "_execute_external_raw_query", _raise_sensitive_error)

    try:
        ask_service._execute_project_sql_routed("SELECT order_id FROM orders", project_id=1, user_id=1, limit=10)
        raise AssertionError("Expected ValueError")
    except ValueError as exc:
        message = str(exc)

    assert "supersecret" not in message
    assert "abc123" not in message
    assert "secret987" not in message
    assert "[REDACTED]" in message


def test_execute_duckdb_semantic_query_runs_binder_preflight(monkeypatch, tmp_path):
    import services.ask_service as ask_service

    db_path = tmp_path / "mock.duckdb"
    db_path.write_text("", encoding="utf-8")

    executed_sql: list[str] = []

    class FakeResult:
        def __init__(self, rows: list[tuple[int]] | None = None):
            self.description = [("value", None, None, None, None, None, None)]
            self._rows = rows or [(1,)]

        def fetchmany(self, row_limit: int):
            return self._rows[:row_limit]

    class FakeConn:
        def execute(self, sql: str):
            executed_sql.append(sql)
            return FakeResult()

        def close(self):
            return None

    monkeypatch.setattr(
        ask_service,
        "_resolve_duckdb_path",
        lambda _props, _project_id, _binding_id: str(db_path),
    )
    monkeypatch.setattr(ask_service.duckdb, "connect", lambda _path: FakeConn())

    result = ask_service._execute_duckdb_semantic_query(
        project_id=1,
        planned_sql="SELECT 1 AS value",
        row_limit=10,
        plan={"security": {"cls": []}},
        start=time.perf_counter(),
        bindings=[(1, "duckdb", {})],
        models_by_binding={},
    )

    assert executed_sql
    assert executed_sql[0].startswith("EXPLAIN SELECT 1 AS value")
    assert "SELECT 1 AS value" in executed_sql
    assert result["rows"] == [{"value": 1}]


def test_execute_duckdb_semantic_query_rewrites_group_by_binder_error_during_preflight(monkeypatch, tmp_path):
    import services.ask_service as ask_service

    db_path = tmp_path / "mock.duckdb"
    db_path.write_text("", encoding="utf-8")

    executed_sql: list[str] = []

    class FakeResult:
        def __init__(self, rows: list[tuple[int, float]] | None = None):
            self.description = [
                ("product_id", None, None, None, None, None, None),
                ("total_revenue", None, None, None, None, None, None),
            ]
            self._rows = rows or [("p1", 100.0)]

        def fetchmany(self, row_limit: int):
            return self._rows[:row_limit]

    class FakeConn:
        def execute(self, sql: str):
            executed_sql.append(sql)
            lowered = sql.lower()
            if lowered.startswith("explain") and "order by oi.price desc" in lowered:
                raise RuntimeError(
                    "Binder Error: column \"total_revenue\" must appear in the GROUP BY clause or must be part of an aggregate function."
                )
            return FakeResult()

        def close(self):
            return None

    monkeypatch.setattr(
        ask_service,
        "_resolve_duckdb_path",
        lambda _props, _project_id, _binding_id: str(db_path),
    )
    monkeypatch.setattr(ask_service.duckdb, "connect", lambda _path: FakeConn())

    result = ask_service._execute_duckdb_semantic_query(
        project_id=1,
        planned_sql=(
            "SELECT oi.product_id, SUM(oi.price) AS total_revenue "
            "FROM olist_order_items_dataset oi "
            "GROUP BY oi.product_id "
            "ORDER BY oi.price DESC"
        ),
        row_limit=10,
        plan={"security": {"cls": []}},
        start=time.perf_counter(),
        bindings=[(1, "duckdb", {})],
        models_by_binding={},
    )

    explain_calls = [sql for sql in executed_sql if sql.lower().startswith("explain")]
    assert len(explain_calls) >= 2
    assert any("order by max(oi.price) desc" in sql.lower() for sql in explain_calls)
    assert any(
        (not sql.lower().startswith("explain")) and "order by max(oi.price) desc" in sql.lower()
        for sql in executed_sql
    )
    assert result["rows"] == [{"product_id": "p1", "total_revenue": 100.0}]
