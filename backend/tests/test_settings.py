from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


class TestGetSettings:
    def test_get_settings(self, test_app: TestClient, auth_headers: dict):
        response = test_app.get("/api/settings", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["settings"]["app_name"] == "PrismBI"

    def test_get_settings_unauthenticated(self, test_app: TestClient):
        response = test_app.get("/api/settings")
        assert response.status_code == 401
        assert response.json()["detail"] == "Missing authorization header"

    def test_get_public_settings_includes_default_language(self, test_app: TestClient):
        response = test_app.get("/api/settings/public")
        assert response.status_code == 200
        settings = response.json()["data"]["settings"]
        assert settings["app_name"] == "PrismBI"
        assert settings["language"] in {"en", "zh"}


class TestUpdateSetting:
    def test_update_branding(self, test_app: TestClient, auth_headers: dict):
        import db

        response = test_app.put(
            "/api/settings/branding",
            json={"app_name": "MyApp", "logo": "https://example.com/logo.png"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["success"] is True

        audit = db.get_connection().execute(
            "SELECT event_type, detail FROM metadata.audit_logs WHERE event_type = 'SETTINGS_BRANDING_UPDATE' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None
        detail = audit[1]
        if isinstance(detail, str):
            detail = json.loads(detail)
        assert isinstance(detail, dict)
        assert {"app_name", "logo"}.issubset(set(detail.get("changed_fields") or []))

    def test_update_theme(self, test_app: TestClient, auth_headers: dict):
        import db

        response = test_app.put(
            "/api/settings/theme",
            json={"mode": "dark", "primary_color": "#1890ff"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["data"]["success"] is True

        audit = db.get_connection().execute(
            "SELECT event_type, detail FROM metadata.audit_logs WHERE event_type = 'SETTINGS_THEME_UPDATE' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None
        detail = audit[1]
        if isinstance(detail, str):
            detail = json.loads(detail)
        assert isinstance(detail, dict)
        assert {"mode", "primary_color"}.issubset(set(detail.get("changed_fields") or []))

    def test_update_llm(self, test_app: TestClient, auth_headers: dict):
        from services.crypto_service import is_encrypted_value

        response = test_app.put(
            "/api/settings/llm",
            json={"provider": "openai", "model": "gpt-4", "api_key": "sk-test"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["data"]["success"] is True

        settings = test_app.get("/api/settings", headers=auth_headers).json()["data"]["settings"]
        assert settings["llm_api_key"] == "********"

        import db

        stored = db.get_connection().execute(
            "SELECT value FROM metadata.settings WHERE key = 'llm_api_key'"
        ).fetchone()[0]
        assert is_encrypted_value(stored)

        audit = db.get_connection().execute(
            "SELECT event_type, detail FROM metadata.audit_logs WHERE event_type = 'SETTINGS_LLM_UPDATE' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None
        detail = audit[1]
        if isinstance(detail, str):
            detail = json.loads(detail)
        assert isinstance(detail, dict)
        changed_fields = set(detail.get("changed_fields") or [])
        assert {"provider", "model", "api_key"}.issubset(changed_fields)
        assert "sk-test" not in json.dumps(detail)

    def test_get_and_update_llm_advanced_includes_http_circuit_fields(self, test_app: TestClient, auth_headers: dict):
        import db
        import services.llm_service as llm_service

        initial = test_app.get("/api/settings/llm/advanced", headers=auth_headers)
        assert initial.status_code == 200
        initial_data = initial.json()["data"]
        assert "http_circuit_enabled" in initial_data
        assert "http_circuit_failure_threshold" in initial_data
        assert "http_circuit_open_seconds" in initial_data

        response = test_app.put(
            "/api/settings/llm/advanced",
            json={
                "max_retries": 4,
                "retry_base_delay_s": 0.5,
                "retry_max_delay_s": 8.0,
                "http_circuit_enabled": False,
                "http_circuit_failure_threshold": 5,
                "http_circuit_open_seconds": 45.0,
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["data"]["success"] is True

        refreshed_policy = llm_service.get_llm_http_resilience_policy_snapshot()
        assert refreshed_policy["max_retries"] == 4
        assert float(refreshed_policy["retry_base_delay_s"]) == 0.5
        assert float(refreshed_policy["retry_max_delay_s"]) == 8.0
        assert refreshed_policy["circuit_enabled"] is False
        assert refreshed_policy["circuit_failure_threshold"] == 5
        assert float(refreshed_policy["circuit_open_seconds"]) == 45.0

        after = test_app.get("/api/settings/llm/advanced", headers=auth_headers)
        assert after.status_code == 200
        data = after.json()["data"]
        assert int(data["max_retries"]) == 4
        assert float(data["retry_base_delay_s"]) == 0.5
        assert float(data["retry_max_delay_s"]) == 8.0
        assert data["http_circuit_enabled"] is False
        assert int(data["http_circuit_failure_threshold"]) == 5
        assert float(data["http_circuit_open_seconds"]) == 45.0

        audit = db.get_connection().execute(
            "SELECT event_type, detail FROM metadata.audit_logs WHERE event_type = 'SETTINGS_LLM_ADVANCED_UPDATE' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None
        detail = audit[1]
        if isinstance(detail, str):
            detail = json.loads(detail)
        assert isinstance(detail, dict)
        changed_fields = set(detail.get("changed_fields") or [])
        assert {
            "max_retries",
            "retry_base_delay_s",
            "retry_max_delay_s",
            "http_circuit_enabled",
            "http_circuit_failure_threshold",
            "http_circuit_open_seconds",
        }.issubset(changed_fields)

    def test_update_llm_advanced_rejects_invalid_http_circuit_open_seconds(self, test_app: TestClient, auth_headers: dict):
        response = test_app.put(
            "/api/settings/llm/advanced",
            json={"http_circuit_open_seconds": 0.5},
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_update_security_writes_audit_log(self, test_app: TestClient, auth_headers: dict):
        import db

        response = test_app.put(
            "/api/settings/security",
            json={"rate_limit_max": 123},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["data"]["success"] is True

        audit = db.get_connection().execute(
            "SELECT event_type, detail FROM metadata.audit_logs WHERE event_type = 'SETTINGS_SECURITY_UPDATE' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None
        detail = audit[1]
        if isinstance(detail, str):
            detail = json.loads(detail)
        assert isinstance(detail, dict)
        assert "rate_limit_max" in set(detail.get("changed_fields") or [])

    def test_update_security_rejects_empty_payload_without_audit(self, test_app: TestClient, auth_headers: dict):
        import db

        con = db.get_connection()
        before = con.execute(
            "SELECT COUNT(*) FROM metadata.audit_logs WHERE event_type = 'SETTINGS_SECURITY_UPDATE'"
        ).fetchone()[0]

        response = test_app.put(
            "/api/settings/security",
            json={},
            headers=auth_headers,
        )

        assert response.status_code == 400
        after = con.execute(
            "SELECT COUNT(*) FROM metadata.audit_logs WHERE event_type = 'SETTINGS_SECURITY_UPDATE'"
        ).fetchone()[0]
        assert after == before

    def test_update_general(self, test_app: TestClient, auth_headers: dict):
        response = test_app.put(
            "/api/settings/general",
            json={"language": "en", "telemetry": False},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["data"]["success"] is True

    def test_update_general_rejects_empty_payload(self, test_app: TestClient, auth_headers: dict):
        import db

        con = db.get_connection()
        before = con.execute(
            "SELECT COUNT(*) FROM metadata.audit_logs WHERE event_type = 'SETTINGS_GENERAL_UPDATE'"
        ).fetchone()[0]
        response = test_app.put(
            "/api/settings/general",
            json={},
            headers=auth_headers,
        )
        assert response.status_code == 400
        after = con.execute(
            "SELECT COUNT(*) FROM metadata.audit_logs WHERE event_type = 'SETTINGS_GENERAL_UPDATE'"
        ).fetchone()[0]
        assert after == before

    def test_update_router_route_observability_window_refreshes_runtime_config(self, test_app: TestClient, auth_headers: dict):
        import db
        import services.ask_service as ask_service

        ask_service.ROUTER_CONFIG["route_observability_window_seconds"] = 1800

        response = test_app.put(
            "/api/settings/router",
            json={"route_observability_window_seconds": 2700},
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json()["data"]["success"] is True
        assert ask_service.ROUTER_CONFIG["route_observability_window_seconds"] == 2700

        stored = db.get_connection().execute(
            "SELECT value FROM metadata.settings WHERE key = 'router_route_observability_window_seconds'"
        ).fetchone()
        assert stored is not None
        assert int(stored[0]) == 2700

    def test_update_router_applies_timeout_and_persist_fields_in_single_request(self, test_app: TestClient, auth_headers: dict):
        import db
        import services.ask_service as ask_service

        ask_service.ROUTER_CONFIG["route_observability_persist_enabled"] = True
        ask_service.ROUTER_CONFIG["route_observability_persist_interval_seconds"] = 30
        ask_service.ROUTER_CONFIG["route_observability_persist_event_delta"] = 20
        ask_service.ROUTER_CONFIG["model_ref_case_sensitive"] = True

        response = test_app.put(
            "/api/settings/router",
            json={
                "request_timeout_ms": 91000,
                "llm_read_timeout_s": 240,
                "db_connect_timeout_s": 17,
                "route_observability_persist_enabled": False,
                "route_observability_persist_interval_seconds": 45,
                "route_observability_persist_event_delta": 7,
                "model_ref_case_sensitive": False,
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json()["data"]["success"] is True

        con = db.get_connection()
        request_timeout = con.execute(
            "SELECT value FROM metadata.settings WHERE key = 'timeout_request_ms'"
        ).fetchone()
        llm_read_timeout = con.execute(
            "SELECT value FROM metadata.settings WHERE key = 'timeout_llm_read_s'"
        ).fetchone()
        db_connect_timeout = con.execute(
            "SELECT value FROM metadata.settings WHERE key = 'timeout_db_connect_s'"
        ).fetchone()
        persist_enabled = con.execute(
            "SELECT value FROM metadata.settings WHERE key = 'router_route_observability_persist_enabled'"
        ).fetchone()
        persist_interval = con.execute(
            "SELECT value FROM metadata.settings WHERE key = 'router_route_observability_persist_interval_seconds'"
        ).fetchone()
        persist_event_delta = con.execute(
            "SELECT value FROM metadata.settings WHERE key = 'router_route_observability_persist_event_delta'"
        ).fetchone()
        model_ref_case_sensitive = con.execute(
            "SELECT value FROM metadata.settings WHERE key = 'router_model_ref_case_sensitive'"
        ).fetchone()

        assert request_timeout is not None and int(request_timeout[0]) == 91000
        assert llm_read_timeout is not None and int(llm_read_timeout[0]) == 240
        assert db_connect_timeout is not None and int(db_connect_timeout[0]) == 17
        assert persist_enabled is not None and str(persist_enabled[0]).strip('"').lower() == "false"
        assert persist_interval is not None and float(persist_interval[0]) == 45.0
        assert persist_event_delta is not None and int(persist_event_delta[0]) == 7
        assert model_ref_case_sensitive is not None and str(model_ref_case_sensitive[0]).strip('"').lower() == "false"

        assert ask_service.ROUTER_CONFIG["route_observability_persist_enabled"] is False
        assert float(ask_service.ROUTER_CONFIG["route_observability_persist_interval_seconds"]) == 45.0
        assert int(ask_service.ROUTER_CONFIG["route_observability_persist_event_delta"]) == 7
        assert ask_service.ROUTER_CONFIG["model_ref_case_sensitive"] is False

        audit = con.execute(
            "SELECT event_type, resource_type, resource_id, action, detail FROM metadata.audit_logs WHERE event_type = 'SETTINGS_ROUTER_UPDATE' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None
        assert audit[0] == "SETTINGS_ROUTER_UPDATE"
        assert audit[1] == "settings"
        assert audit[2] == "router"
        assert audit[3] == "update"
        detail = audit[4]
        if isinstance(detail, str):
            detail = json.loads(detail)
        assert isinstance(detail, dict)
        changed_fields = set(detail.get("changed_fields") or [])
        assert {
            "request_timeout_ms",
            "llm_read_timeout_s",
            "db_connect_timeout_s",
            "route_observability_persist_enabled",
            "route_observability_persist_interval_seconds",
            "route_observability_persist_event_delta",
            "model_ref_case_sensitive",
        }.issubset(changed_fields)

    def test_update_general_rejects_invalid_combined_payload_without_partial_writes(self, test_app: TestClient, auth_headers: dict):
        import db

        con = db.get_connection()
        con.execute(
            "INSERT OR REPLACE INTO metadata.settings (key, value) VALUES ('timeout_request_ms', ?::JSON)",
            ["120000"],
        )

        response = test_app.put(
            "/api/settings/router",
            json={
                "request_timeout_ms": 91000,
                "route_observability_persist_event_delta": 0,
            },
            headers=auth_headers,
        )

        assert response.status_code == 422
        request_timeout_after = con.execute(
            "SELECT value FROM metadata.settings WHERE key = 'timeout_request_ms'"
        ).fetchone()
        assert request_timeout_after is not None
        assert int(request_timeout_after[0]) == 120000

    def test_update_router_settings_rejects_out_of_range_timeout_value(self, test_app: TestClient, auth_headers: dict):
        import db

        con = db.get_connection()

        response = test_app.put(
            "/api/settings/router",
            json={"request_timeout_ms": 999},
            headers=auth_headers,
        )

        assert response.status_code == 422

    def test_update_router_settings_refreshes_runtime_config(self, test_app: TestClient, auth_headers: dict):
        import services.ask_service as ask_service

        ask_service.ROUTER_CONFIG["tier1_max_retries"] = 9
        ask_service.ROUTER_CONFIG["sql_route_profile_id"] = "legacy.profile"
        ask_service.ROUTER_CONFIG["model_ref_case_sensitive"] = True

        response = test_app.put(
            "/api/settings/router",
            json={
                "tier1_max_retries": 1,
                "adaptive_strategy_enabled": False,
                "adaptive_strategy_consensus_risk_threshold": 6,
                "adaptive_strategy_decompose_risk_threshold": 5,
                "adaptive_strategy_min_subquestions_for_decompose": 3,
                "cross_source_max_workers": 3,
                "decompose_merge_enabled": False,
                "route_observability_max_events_per_project": 15000,
                "route_observability_persist_enabled": True,
                "route_observability_persist_interval_seconds": 15,
                "route_observability_persist_event_delta": 5,
                "route_observability_strategy_trend_max_points": 36,
                "route_observability_strategy_trend_persist_interval_seconds": 20,
                "route_observability_strategy_trend_persist_decision_delta": 3,
                "route_alert_repair_timeout_short_circuit_warning_rate": 0.3,
                "route_alert_repair_timeout_short_circuit_critical_rate": 0.25,
                "route_alert_repair_timeout_short_circuit_min_warning_events": 9,
                "route_alert_repair_timeout_short_circuit_min_critical_events": 6,
                "route_alert_repair_budget_low_short_circuit_warning_rate": 0.18,
                "route_alert_repair_budget_low_short_circuit_critical_rate": 0.14,
                "route_alert_repair_budget_low_short_circuit_min_warning_events": 7,
                "route_alert_repair_budget_low_short_circuit_min_critical_events": 4,
                "route_alert_json_reask_warning_rate": 0.22,
                "route_alert_json_reask_critical_rate": 0.18,
                "route_alert_json_reask_min_warning_decisions": 11,
                "route_alert_json_reask_min_critical_decisions": 9,
                "route_alert_decompose_cancelled_warning_rate": 0.17,
                "route_alert_decompose_cancelled_critical_rate": 0.12,
                "route_alert_decompose_cancelled_min_warning_events": 8,
                "route_alert_decompose_cancelled_min_critical_events": 5,
                "sql_route_v2_enabled": True,
                "sql_route_shadow_mode": True,
                "sql_route_allowlist_projects": [1, 2],
                "model_ref_case_sensitive": False,
                "sql_route_profile_id": "prismbi.default",
                "sql_route_profile_version": "v2",
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json()["data"]["success"] is True
        assert ask_service.ROUTER_CONFIG["tier1_max_retries"] == 1
        assert ask_service.ROUTER_CONFIG["adaptive_strategy_enabled"] is False
        assert ask_service.ROUTER_CONFIG["adaptive_strategy_consensus_risk_threshold"] == 6
        assert ask_service.ROUTER_CONFIG["adaptive_strategy_decompose_risk_threshold"] == 6
        assert ask_service.ROUTER_CONFIG["adaptive_strategy_min_subquestions_for_decompose"] == 3
        assert ask_service.ROUTER_CONFIG["cross_source_max_workers"] == 3
        assert ask_service.ROUTER_CONFIG["decompose_merge_enabled"] is False
        assert ask_service.ROUTER_CONFIG["route_observability_max_events_per_project"] == 15000
        assert ask_service.ROUTER_CONFIG["route_observability_persist_enabled"] is True
        assert ask_service.ROUTER_CONFIG["route_observability_persist_interval_seconds"] == 15
        assert ask_service.ROUTER_CONFIG["route_observability_persist_event_delta"] == 5
        assert ask_service.ROUTER_CONFIG["route_observability_strategy_trend_max_points"] == 36
        assert ask_service.ROUTER_CONFIG["route_observability_strategy_trend_persist_interval_seconds"] == 20
        assert ask_service.ROUTER_CONFIG["route_observability_strategy_trend_persist_decision_delta"] == 3
        assert ask_service.ROUTER_CONFIG["route_alert_repair_timeout_short_circuit_warning_rate"] == 0.3
        assert ask_service.ROUTER_CONFIG["route_alert_repair_timeout_short_circuit_critical_rate"] == 0.3
        assert ask_service.ROUTER_CONFIG["route_alert_repair_timeout_short_circuit_min_warning_events"] == 9
        assert ask_service.ROUTER_CONFIG["route_alert_repair_timeout_short_circuit_min_critical_events"] == 9
        assert ask_service.ROUTER_CONFIG["route_alert_repair_budget_low_short_circuit_warning_rate"] == 0.18
        assert ask_service.ROUTER_CONFIG["route_alert_repair_budget_low_short_circuit_critical_rate"] == 0.18
        assert ask_service.ROUTER_CONFIG["route_alert_repair_budget_low_short_circuit_min_warning_events"] == 7
        assert ask_service.ROUTER_CONFIG["route_alert_repair_budget_low_short_circuit_min_critical_events"] == 7
        assert ask_service.ROUTER_CONFIG["route_alert_json_reask_warning_rate"] == 0.22
        assert ask_service.ROUTER_CONFIG["route_alert_json_reask_critical_rate"] == 0.22
        assert ask_service.ROUTER_CONFIG["route_alert_json_reask_min_warning_decisions"] == 11
        assert ask_service.ROUTER_CONFIG["route_alert_json_reask_min_critical_decisions"] == 11
        assert ask_service.ROUTER_CONFIG["route_alert_decompose_cancelled_warning_rate"] == 0.17
        assert ask_service.ROUTER_CONFIG["route_alert_decompose_cancelled_critical_rate"] == 0.17
        assert ask_service.ROUTER_CONFIG["route_alert_decompose_cancelled_min_warning_events"] == 8
        assert ask_service.ROUTER_CONFIG["route_alert_decompose_cancelled_min_critical_events"] == 8
        assert ask_service.ROUTER_CONFIG["model_ref_case_sensitive"] is False
        assert ask_service.ROUTER_CONFIG["sql_route_v2_enabled"] is True
        assert ask_service.ROUTER_CONFIG["sql_route_shadow_mode"] is True
        assert ask_service.ROUTER_CONFIG["sql_route_allowlist_projects"] == [1, 2]
        assert ask_service.ROUTER_CONFIG["sql_route_profile_id"] == "prismbi.default"
        assert ask_service.ROUTER_CONFIG["sql_route_profile_version"] == "v2"

    def test_get_router_settings_uses_runtime_fallback_when_db_key_missing(
        self,
        test_app: TestClient,
        auth_headers: dict,
    ):
        import db
        import services.ask_service as ask_service

        ask_service.refresh_runtime_router_settings(force=True)
        db.get_connection().execute(
            "DELETE FROM metadata.settings WHERE key = 'router_sql_route_profile_version'"
        )

        response = test_app.get("/api/settings/router", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["sql_route_profile_version"] == ask_service.ROUTER_CONFIG["sql_route_profile_version"]

    def test_reload_router_runtime_settings_applies_direct_db_changes(self, test_app: TestClient, auth_headers: dict):
        import db
        import services.ask_service as ask_service

        ask_service.ROUTER_CONFIG["sql_route_shadow_mode"] = False
        db.get_connection().execute(
            "INSERT OR REPLACE INTO metadata.settings (key, value) VALUES ('router_sql_route_shadow_mode', ?::JSON)",
            ["true"],
        )

        response = test_app.post("/api/settings/router/reload", headers=auth_headers)

        assert response.status_code == 200
        payload = response.json()["data"]
        assert payload["success"] is True
        assert payload["runtime"]["sql_route_shadow_mode"] is True
        assert ask_service.ROUTER_CONFIG["sql_route_shadow_mode"] is True

        audit = db.get_connection().execute(
            "SELECT event_type, resource_type, resource_id, action FROM metadata.audit_logs WHERE event_type = 'SETTINGS_ROUTER_RELOAD' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None
        assert audit[0] == "SETTINGS_ROUTER_RELOAD"
        assert audit[1] == "settings"
        assert audit[2] == "router"
        assert audit[3] == "reload"

    def test_get_settings_audit_summary_aggregates_by_scope(self, test_app: TestClient, auth_headers: dict):
        response_branding = test_app.put(
            "/api/settings/branding",
            json={"app_name": "Audit Scope App", "logo": "https://example.com/logo-audit.png"},
            headers=auth_headers,
        )
        assert response_branding.status_code == 200

        response_router = test_app.put(
            "/api/settings/router",
            json={"request_timeout_ms": 95000},
            headers=auth_headers,
        )
        assert response_router.status_code == 200

        response_reload = test_app.post("/api/settings/router/reload", headers=auth_headers)
        assert response_reload.status_code == 200

        summary_response = test_app.get(
            "/api/settings/audit-summary?latest_limit=20&max_events=500",
            headers=auth_headers,
        )
        assert summary_response.status_code == 200

        data = summary_response.json()["data"]
        assert int(data["scanned_events"]) >= 3
        assert int(data["matched_events"]) >= 3
        by_scope = data["by_scope"]
        assert "branding" in by_scope
        assert "router" in by_scope
        assert int(by_scope["branding"]["events"]) >= 1
        assert int(by_scope["router"]["events"]) >= 1
        assert int(by_scope["branding"]["changed_fields"].get("app_name", 0)) >= 1

        latest = data["latest"]
        assert isinstance(latest, list)
        assert any(item.get("scope") == "branding" for item in latest)

    def test_get_settings_audit_summary_supports_scope_and_latest_offset(self, test_app: TestClient, auth_headers: dict):
        response_router_1 = test_app.put(
            "/api/settings/router",
            json={"request_timeout_ms": 96100},
            headers=auth_headers,
        )
        assert response_router_1.status_code == 200

        response_router_2 = test_app.put(
            "/api/settings/router",
            json={"llm_read_timeout_s": 242},
            headers=auth_headers,
        )
        assert response_router_2.status_code == 200

        response_branding = test_app.put(
            "/api/settings/branding",
            json={"app_name": "ScopeFilterBranding"},
            headers=auth_headers,
        )
        assert response_branding.status_code == 200

        latest_first = test_app.get(
            "/api/settings/audit-summary?scope=router&latest_limit=1&latest_offset=0&max_events=1000",
            headers=auth_headers,
        )
        assert latest_first.status_code == 200
        first_data = latest_first.json()["data"]
        assert first_data["scope"] == "router"
        assert int(first_data["matched_events"]) >= 2
        assert set(first_data["by_scope"].keys()) == {"router"}
        assert len(first_data["latest"]) == 1
        first_changed = set(first_data["latest"][0].get("changed_fields") or [])
        assert "llm_read_timeout_s" in first_changed

        latest_second = test_app.get(
            "/api/settings/audit-summary?scope=router&latest_limit=1&latest_offset=1&max_events=1000",
            headers=auth_headers,
        )
        assert latest_second.status_code == 200
        second_data = latest_second.json()["data"]
        assert second_data["scope"] == "router"
        assert len(second_data["latest"]) == 1
        second_changed = set(second_data["latest"][0].get("changed_fields") or [])
        assert "request_timeout_ms" in second_changed

    def test_get_settings_audit_summary_filters_exact_from_to_window(self, test_app: TestClient, auth_headers: dict):
        import db

        con = db.get_connection()
        next_id = int(con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.audit_logs").fetchone()[0])

        entries = [
            (0, "2026-01-01 09:59:59", "language"),
            (1, "2026-01-01 10:00:00", "timezone"),
            (2, "2026-01-01 10:00:01", "date_format"),
        ]
        for offset, created_at, changed_field in entries:
            con.execute(
                "INSERT INTO metadata.audit_logs (id, user_id, event_type, resource_type, resource_id, action, detail, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?::JSON, ?, ?::TIMESTAMP)",
                [
                    next_id + offset,
                    1,
                    "SETTINGS_GENERAL_UPDATE",
                    "settings",
                    "general",
                    "update",
                    json.dumps({"scope": "general", "changed_fields": [changed_field]}),
                    "SUCCESS",
                    created_at,
                ],
            )

        response = test_app.get(
            "/api/settings/audit-summary",
            params={
                "scope": "general",
                "from": "2026-01-01T10:00:00",
                "to": "2026-01-01T10:00:00",
                "latest_limit": 20,
                "max_events": 500,
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert int(data["scanned_events"]) == 1
        assert int(data["matched_events"]) == 1
        assert data["scope"] == "general"
        assert set(data["by_scope"].keys()) == {"general"}
        assert int(data["by_scope"]["general"]["events"]) == 1
        assert int(data["by_scope"]["general"]["changed_fields"].get("timezone", 0)) == 1
        assert len(data["latest"]) == 1
        assert set(data["latest"][0].get("changed_fields") or []) == {"timezone"}

    def test_get_settings_audit_summary_respects_max_events_scan_cap(self, test_app: TestClient, auth_headers: dict):
        import db

        con = db.get_connection()
        next_id = int(con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.audit_logs").fetchone()[0])

        for offset in range(150):
            con.execute(
                "INSERT INTO metadata.audit_logs (id, user_id, event_type, resource_type, resource_id, action, detail, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?::JSON, ?)",
                [
                    next_id + offset,
                    1,
                    "SETTINGS_GENERAL_UPDATE",
                    "settings",
                    "general",
                    "update",
                    json.dumps({"scope": "general", "changed_fields": ["request_timeout_ms"]}),
                    "SUCCESS",
                ],
            )

        response = test_app.get(
            "/api/settings/audit-summary",
            params={
                "scope": "general",
                "max_events": 100,
                "latest_limit": 20,
                "latest_offset": 0,
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert int(data["scanned_events"]) == 100
        assert int(data["matched_events"]) == 100
        assert int(data["by_scope"]["general"]["events"]) == 100
        assert int(data["by_scope"]["general"]["changed_fields"].get("request_timeout_ms", 0)) == 100
        assert len(data["latest"]) == 20
        assert int(data["matched_events"]) < 150

    def test_get_settings_audit_summary_rejects_invalid_latest_offset(self, test_app: TestClient, auth_headers: dict):
        response = test_app.get(
            "/api/settings/audit-summary?latest_offset=-1",
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_get_settings_audit_summary_rejects_invalid_timestamp_filters(self, test_app: TestClient, auth_headers: dict):
        response = test_app.get(
            "/api/settings/audit-summary?from=not-a-timestamp",
            headers=auth_headers,
        )
        assert response.status_code == 422
        assert "from" in str(response.json().get("detail", ""))

    def test_get_settings_audit_summary_rejects_from_after_to(self, test_app: TestClient, auth_headers: dict):
        response = test_app.get(
            "/api/settings/audit-summary?from=2026-01-01T10:00:01&to=2026-01-01T10:00:00",
            headers=auth_headers,
        )
        assert response.status_code == 422
        assert "from" in str(response.json().get("detail", ""))

    def test_update_ask_settings_refreshes_runtime_limits(
        self,
        test_app: TestClient,
        auth_headers: dict,
        seed_project: dict,
    ):
        import services.ask_service as ask_service

        restore_payload = {
            "max_sql_rows": 200,
            "default_preview_row_limit": 20,
            "min_preview_row_limit": 5,
            "max_preview_row_limit": 100,
            "max_source_materialization_rows": 5000,
            "analysis_cache_max": 128,
            "analysis_cache_ttl_s": 300,
        }
        created_thread_id = None
        response = test_app.put(
            "/api/settings/ask",
            json={
                "max_sql_rows": 333,
                "default_preview_row_limit": 17,
                "min_preview_row_limit": 11,
                "max_preview_row_limit": 22,
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["data"]["success"] is True
        assert ask_service.MAX_SQL_ROWS == 333
        assert ask_service.DEFAULT_PREVIEW_ROW_LIMIT == 17
        assert ask_service.MIN_PREVIEW_ROW_LIMIT == 11
        assert ask_service.MAX_PREVIEW_ROW_LIMIT == 22
        assert ask_service._normalize_preview_row_limit(5) == 11
        assert ask_service._normalize_preview_row_limit(100) == 22

        thread_response = test_app.post(
            "/api/threads",
            json={"project_id": 1, "summary": "runtime-limit-test", "preview_row_limit": 3},
            headers=auth_headers,
        )
        assert thread_response.status_code == 200
        thread_data = thread_response.json()["data"]
        created_thread_id = thread_data["id"]
        assert thread_data["preview_row_limit"] == 11

        test_app.put("/api/settings/ask", json=restore_payload, headers=auth_headers)
        if created_thread_id is not None:
            test_app.delete(f"/api/threads/{created_thread_id}", headers=auth_headers)

    def test_update_ask_settings_rejects_out_of_range_values(
        self,
        test_app: TestClient,
        auth_headers: dict,
    ):
        response = test_app.put(
            "/api/settings/ask",
            json={"max_sql_rows": 0},
            headers=auth_headers,
        )

        assert response.status_code == 422

    def test_update_router_settings_rejects_invalid_allowlist_projects(
        self,
        test_app: TestClient,
        auth_headers: dict,
    ):
        import db

        con = db.get_connection()
        before = con.execute(
            "SELECT COUNT(*) FROM metadata.audit_logs WHERE event_type = 'SETTINGS_ROUTER_UPDATE'"
        ).fetchone()[0]

        response = test_app.put(
            "/api/settings/router",
            json={"sql_route_allowlist_projects": [1, -2]},
            headers=auth_headers,
        )

        assert response.status_code == 422
        after = con.execute(
            "SELECT COUNT(*) FROM metadata.audit_logs WHERE event_type = 'SETTINGS_ROUTER_UPDATE'"
        ).fetchone()[0]
        assert after == before


class TestAppInfo:
    def test_get_app_info(self, test_app: TestClient, auth_headers: dict):
        response = test_app.get("/api/settings/app-info", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["version"] == "1.0.0"
        assert data["data"]["platforms"] == ["web"]


class TestLLMTest:
    def test_llm_test(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/settings/llm/test",
            json={"provider": "openai", "model": "gpt-4", "api_key": "sk-test"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["data"]["success"] is False


class TestUnauthorizedAccess:
    def test_get_settings_without_auth(self, test_app: TestClient):
        response = test_app.get("/api/settings")
        assert response.status_code == 401

    def test_update_branding_without_auth(self, test_app: TestClient):
        response = test_app.put(
            "/api/settings/branding",
            json={"app_name": "EvilApp"},
        )
        assert response.status_code == 401

    def test_create_project_without_auth(self, test_app: TestClient):
        response = test_app.post(
            "/api/projects",
            json={"name": "unauth-project"},
        )
        assert response.status_code == 401

    def test_list_projects_without_auth(self, test_app: TestClient):
        response = test_app.get("/api/projects")
        assert response.status_code == 401

    def test_execute_query_without_auth(self, test_app: TestClient):
        response = test_app.post(
            "/api/query",
            json={"sql": "SELECT 1", "project_id": 1},
        )
        assert response.status_code == 401

    def test_reload_router_runtime_without_auth(self, test_app: TestClient):
        response = test_app.post("/api/settings/router/reload")
        assert response.status_code == 401

    def test_get_settings_audit_summary_without_auth(self, test_app: TestClient):
        response = test_app.get("/api/settings/audit-summary")
        assert response.status_code == 401
