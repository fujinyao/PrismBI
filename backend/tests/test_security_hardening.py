"""Tests for security hardening: _safe_identifier, _next_id allowlist,
_validate_init_sql, and SSE token auth."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import pytest

from services.security_policy_service import _safe_identifier, _sql_literal


class TestSafeIdentifier:
    def test_valid_simple(self):
        assert _safe_identifier("orders") == "orders"

    def test_valid_dotted(self):
        assert _safe_identifier("schema.table") == "schema.table"

    def test_valid_with_underscores(self):
        assert _safe_identifier("my_table") == "my_table"

    def test_valid_dotted_with_underscore(self):
        assert _safe_identifier("metadata.users") == "metadata.users"

    def test_reject_empty(self):
        with pytest.raises(ValueError, match="Unsafe identifier"):
            _safe_identifier("")

    def test_reject_none_like(self):
        with pytest.raises(ValueError):
            _safe_identifier(None)

    def test_reject_sql_injection_spaces(self):
        with pytest.raises(ValueError):
            _safe_identifier("drop table")

    def test_reject_sql_injection_semicolon(self):
        with pytest.raises(ValueError):
            _safe_identifier("users; DROP TABLE")

    def test_reject_special_chars(self):
        with pytest.raises(ValueError):
            _safe_identifier("user'table")

    def test_reject_dashes(self):
        with pytest.raises(ValueError):
            _safe_identifier("my-table")

    def test_reject_leading_dot(self):
        with pytest.raises(ValueError):
            _safe_identifier(".table")

    def test_reject_trailing_dot(self):
        with pytest.raises(ValueError):
            _safe_identifier("table.")

    def test_reject_double_dot(self):
        with pytest.raises(ValueError):
            _safe_identifier("schema..table")

    def test_reject_star(self):
        with pytest.raises(ValueError):
            _safe_identifier("*")

    def test_reject_lowercase_sql_injection(self):
        with pytest.raises(ValueError):
            _safe_identifier("1; DROP TABLE users")


class TestSqlLiteral:
    def test_none(self):
        assert _sql_literal(None) == "NULL"

    def test_bool_true(self):
        assert _sql_literal(True) == "TRUE"

    def test_bool_false(self):
        assert _sql_literal(False) == "FALSE"

    def test_int(self):
        assert _sql_literal(42) == "42"

    def test_float(self):
        assert _sql_literal(3.14) == "3.14"

    def test_string_quotes(self):
        assert _sql_literal("hello") == "'hello'"

    def test_string_escape_single_quote(self):
        assert _sql_literal("it's") == "'it''s'"



class TestNextIdAllowlist:
    def test_admin_next_id_rejects_unknown_table(self):
        from routers.admin import _next_id, _ALLOWED_ADMIN_TABLES
        import duckdb
        con = duckdb.connect(":memory:")
        con.execute("CREATE SCHEMA metadata")
        con.execute("CREATE TABLE metadata.users (id INTEGER PRIMARY KEY, username VARCHAR)")
        with pytest.raises(ValueError, match="Unknown table"):
            _next_id(con, "metadata.nonexistent_table")
        con.close()

    def test_admin_next_id_accepts_allowed_table(self):
        from routers.admin import _next_id
        import duckdb
        con = duckdb.connect(":memory:")
        con.execute("CREATE SCHEMA metadata")
        con.execute("CREATE TABLE metadata.users (id INTEGER PRIMARY KEY, username VARCHAR)")
        result = _next_id(con, "metadata.users")
        assert result == 1
        con.close()

    def test_admin_next_id_increments(self):
        from routers.admin import _next_id
        import duckdb
        con = duckdb.connect(":memory:")
        con.execute("CREATE SCHEMA metadata")
        con.execute("CREATE TABLE metadata.users (id INTEGER PRIMARY KEY, username VARCHAR)")
        con.execute("INSERT INTO metadata.users VALUES (1, 'a')")
        result = _next_id(con, "metadata.users")
        assert result == 2
        con.close()

    def test_allowlist_does_not_contain_dangerous_tables(self):
        from routers.admin import _ALLOWED_ADMIN_TABLES
        for dangerous in ["metadata.datasources", "metadata.projects", "metadata.api_tokens", "pg_catalog"]:
            assert dangerous not in _ALLOWED_ADMIN_TABLES


class TestValidateInitSql:
    def _validate(self, sql: str):
        from routers.datasources import _validate_init_sql
        _validate_init_sql(sql)

    def test_empty_string_passes(self):
        self._validate("")

    def test_whitespace_passes(self):
        self._validate("   \n\t  ")

    def test_select_passes(self):
        self._validate("SELECT 1")

    def test_with_cte_passes(self):
        self._validate("WITH cte AS (SELECT 1) SELECT * FROM cte")

    def test_comment_passes(self):
        self._validate("COMMENT ON TABLE foo IS 'bar'")

    def test_create_table_as_select_passes(self):
        self._validate("CREATE TABLE t AS SELECT 1")

    def test_create_view_as_select_passes(self):
        self._validate("CREATE VIEW v AS SELECT 1")

    def test_create_table_if_not_exists_passes(self):
        self._validate("CREATE TABLE IF NOT EXISTS t AS SELECT 1")

    def test_create_view_if_not_exists_passes(self):
        self._validate("CREATE VIEW IF NOT EXISTS v AS SELECT 1")

    def test_multi_statement_passes(self):
        self._validate("CREATE TABLE t AS SELECT 1; SELECT * FROM t")

    def test_reject_drop(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._validate("DROP TABLE users")
        assert exc_info.value.status_code == 400
        assert "DROP" in exc_info.value.detail or "must not contain" in exc_info.value.detail

    def test_reject_insert(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("INSERT INTO t VALUES (1)")

    def test_reject_update(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("UPDATE t SET x=1")

    def test_reject_delete(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("DELETE FROM t")

    def test_reject_truncate(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("TRUNCATE TABLE t")

    def test_reject_alter(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("ALTER TABLE t ADD COLUMN x INT")

    def test_reject_copy(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("COPY t TO '/tmp/out.csv'")

    def test_reject_attach(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("ATTACH 'test.db' AS test")

    def test_reject_pragma(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("PRAGMA database_list")

    def test_reject_read_csv_in_select(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("SELECT * FROM read_csv('/etc/passwd')")

    def test_reject_read_parquet_in_select(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("SELECT * FROM read_parquet('/etc/passwd')")

    def test_reject_read_csv_auto_in_select(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("SELECT * FROM read_csv_auto('/etc/passwd')")

    def test_allow_read_csv_in_create_table_as_select(self):
        self._validate("CREATE TABLE t AS SELECT * FROM read_csv('data.csv', header=true)")

    def test_allow_read_parquet_in_create_table_as_select(self):
        self._validate("CREATE TABLE t AS SELECT * FROM read_parquet('data.parquet')")

    def test_allow_read_csv_auto_in_create_table_as_select(self):
        self._validate("CREATE TABLE t AS SELECT * FROM read_csv_auto('data.csv')")

    def test_reject_httpfs_scan(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("SELECT * FROM httpfs_scan('http://evil.com/data')")

    def test_reject_parquet_scan(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("SELECT * FROM parquet_scan('data.parquet')")

    def test_reject_sqlite_scan(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("SELECT * FROM sqlite_scan('db', 't')")

    def test_reject_postgres_scan(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("SELECT * FROM postgres_scan('host', 'db', 't')")

    def test_reject_mysql_scan(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("SELECT * FROM mysql_scan('host', 'db', 't')")

    def test_allow_read_ndjson(self):
        self._validate("SELECT * FROM read_ndjson('data.ndjson')")

    def test_allow_read_xlsx(self):
        self._validate("SELECT * FROM read_xlsx('data.xlsx')")

    def test_reject_read_json_auto(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("SELECT * FROM read_json_auto('data.json')")

    def test_reject_metadata_reference(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("SELECT * FROM metadata.users")

    def test_reject_glob(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("SELECT * FROM glob('*.csv')")

    def test_reject_listdir(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("SELECT * FROM listdir('/tmp')")

    def test_reject_create_without_as_select(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._validate("CREATE TABLE t (id INT)")
        assert "CREATE TABLE ... AS SELECT" in exc_info.value.detail or "CREATE VIEW" in exc_info.value.detail

    def test_reject_unknown_statement(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("EXPLAIN SELECT 1")

    def test_case_insensitive_keyword_block(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("drop table users")

    def test_case_insensitive_pattern_block(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            self._validate("SELECT * FROM Read_Blob('f.bin')")


class TestSSETokenAuth:
    def test_sse_bearer_auth_success(self, test_app, auth_headers):
        resp = test_app.post("/api/ask/stream", json={
            "question": "hi", "temporary": True,
        }, headers=auth_headers)
        assert resp.status_code != 401

    def test_sse_no_auth_returns_401(self, test_app):
        resp = test_app.post("/api/ask/stream", json={
            "question": "hi", "temporary": True,
        })
        assert resp.status_code == 401

    def test_sse_query_param_token(self, test_app, auth_headers):
        token = auth_headers["Authorization"].replace("Bearer ", "")
        resp = test_app.post(
            f"/api/ask/stream?token={token}",
            json={"question": "hi", "temporary": True},
        )
        assert resp.status_code != 401

    def test_sse_invalid_token_returns_401(self, test_app):
        resp = test_app.post("/api/ask/stream?token=invalid_token", json={
            "question": "hi", "temporary": True,
        })
        assert resp.status_code == 401

    def test_sse_invalid_bearer_returns_401(self, test_app):
        resp = test_app.post("/api/ask/stream", json={
            "question": "hi", "temporary": True,
        }, headers={"Authorization": "Bearer invalid_token"})
        assert resp.status_code == 401

    def test_sse_query_credentials_are_redacted_in_api_history(self, test_app, auth_headers, test_db):
        token = auth_headers["Authorization"].replace("Bearer ", "")
        resp = test_app.post(
            f"/api/ask/stream?token={token}&ticket=temporary-ticket",
            json={"question": "hi", "temporary": True},
        )
        assert resp.status_code != 401

        row = test_db.execute(
            "SELECT headers FROM metadata.api_history ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert row and row[0] is not None

        headers_payload = row[0]
        if isinstance(headers_payload, str):
            headers_payload = json.loads(headers_payload)
        query = str((headers_payload or {}).get("query") or "")
        parsed_query = parse_qs(query, keep_blank_values=True)
        assert parsed_query.get("token") == ["[REDACTED]"]
        assert parsed_query.get("ticket") == ["[REDACTED]"]


class TestAnalysisCache:
    def test_cache_key_format(self):
        from services.ask_service import _analysis_cache_key
        key = _analysis_cache_key("what is sales?", 5)
        assert key == "5::what is sales?::"

    def test_cache_key_with_previous(self):
        from services.ask_service import _analysis_cache_key
        import hashlib, json
        key = _analysis_cache_key("compare A and B", 3, ["show sales"])
        expected_hash = hashlib.sha256(json.dumps(["show sales"], sort_keys=True).encode()).hexdigest()[:16]
        assert key == f"3::compare A and B::{expected_hash}"

    def test_cache_key_collision_resistant(self):
        from services.ask_service import _analysis_cache_key
        key1 = _analysis_cache_key("q", 1, ["a|b"])
        key2 = _analysis_cache_key("q", 1, ["a", "b"])
        assert key1 != key2

    def test_cache_key_different_projects(self):
        from services.ask_service import _analysis_cache_key
        k1 = _analysis_cache_key("q", 1)
        k2 = _analysis_cache_key("q", 2)
        assert k1 != k2

    def test_cache_stores_timestamp_tuple(self):
        import time
        import services.ask_service as mod
        mod._analysis_cache.clear()
        try:
            now = time.monotonic()
            mod._analysis_cache["k1"] = ({"tier": "simple"}, now)
            entry = mod._analysis_cache["k1"]
            assert isinstance(entry, tuple)
            assert len(entry) == 2
            assert entry[0]["tier"] == "simple"
            assert isinstance(entry[1], float)
        finally:
            mod._analysis_cache.clear()

    def test_cache_ttl_expired_entry_not_returned(self):
        import time
        import services.ask_service as mod
        mod._analysis_cache.clear()
        old_time = time.monotonic() - mod._CACHE_TTL_SECONDS - 60
        try:
            mod._analysis_cache["oldkey"] = ({"tier": "simple"}, old_time)
            from services.ask_service import _analyze_question, _analysis_cache_key
            cached = mod._analysis_cache.get("oldkey")
            assert cached is not None
            ts = cached[1]
            assert time.monotonic() - ts > mod._CACHE_TTL_SECONDS
        finally:
            mod._analysis_cache.clear()

    def test_clear_analysis_cache_by_project(self):
        import services.ask_service as mod
        mod._analysis_cache.clear()
        try:
            mod._analysis_cache["1::q1::"] = ({"tier": "simple"}, 0)
            mod._analysis_cache["1::q2::"] = ({"tier": "compound"}, 0)
            mod._analysis_cache["2::q1::"] = ({"tier": "simple"}, 0)
            mod.clear_analysis_cache(project_id=1)
            assert "1::q1::" not in mod._analysis_cache
            assert "1::q2::" not in mod._analysis_cache
            assert "2::q1::" in mod._analysis_cache
        finally:
            mod._analysis_cache.clear()

    def test_clear_analysis_cache_all(self):
        import services.ask_service as mod
        mod._analysis_cache.clear()
        try:
            mod._analysis_cache["1::q1::"] = ({"tier": "simple"}, 0)
            mod._analysis_cache["2::q2::"] = ({"tier": "compound"}, 0)
            mod.clear_analysis_cache()
            assert len(mod._analysis_cache) == 0
        finally:
            mod._analysis_cache.clear()

    def test_purge_expired_cache_entries(self):
        import time
        import services.ask_service as mod
        mod._analysis_cache.clear()
        try:
            old_time = time.monotonic() - mod._CACHE_TTL_SECONDS - 60
            recent_time = time.monotonic()
            mod._analysis_cache["expired"] = ({"tier": "simple"}, old_time)
            mod._analysis_cache["valid"] = ({"tier": "compound"}, recent_time)
            mod._purge_expired_cache_entries()
            assert "expired" not in mod._analysis_cache
            assert "valid" in mod._analysis_cache
        finally:
            mod._analysis_cache.clear()


class TestLikeWildcardEscaping:
    def _escape(self, s: str) -> str:
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def test_percent_escaped(self):
        assert self._escape("100%") == r"100\%"

    def test_underscore_escaped(self):
        assert self._escape("a_b") == r"a\_b"

    def test_backslash_escaped(self):
        assert self._escape(r"path\name") == r"path\\name"

    def test_no_special_chars(self):
        assert self._escape("hello") == "hello"

    def test_combined(self):
        assert self._escape("a%b_c\\d") == r"a\%b\_c\\d"
