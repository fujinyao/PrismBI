from __future__ import annotations

from contextlib import contextmanager
import json

import services.ask_service as ask_service


@contextmanager
def _noop_lock():
    yield


def test_record_sql_generation_failure_persists_row(monkeypatch, test_db):
    monkeypatch.setattr(ask_service, "get_connection", lambda: test_db)
    monkeypatch.setattr(ask_service, "connection_lock", lambda: _noop_lock())

    ask_service._record_sql_generation_failure(
        project_id=1,
        question="测试 unknown columns",
        failed_sql="SELECT t1.foo FROM x t1",
        error_text="Unknown columns: ['t1.foo (not found in any model)']",
        stage="validate_sql_columns",
        sql_engine="direct_llm",
        attempt=1,
        issue_buckets={"hallucinated_column": 1},
        repaired_sql=None,
        resolved=False,
        schema_link_snapshot={"selected_owner_map": {"order_id": "orders"}},
        sql_plan_snapshot={"group_by_columns": ["city"]},
    )

    row = test_db.execute(
        """
        SELECT project_id, question, failed_sql, error_text, stage, sql_engine, attempt, issue_buckets, schema_link_snapshot, sql_plan_snapshot, resolved
        FROM metadata.sql_generation_failures
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    assert row is not None
    assert row[0] == 1
    assert row[4] == "validate_sql_columns"
    assert row[5] == "direct_llm"
    assert row[6] == 1
    bucket_json = json.loads(row[7])
    assert bucket_json["hallucinated_column"] == 1
    schema_json = json.loads(row[8])
    plan_json = json.loads(row[9])
    assert schema_json["selected_owner_map"]["order_id"] == "orders"
    assert plan_json["group_by_columns"] == ["city"]
    assert row[10] is False


def test_record_sql_generation_failure_stores_repair_fields(monkeypatch, test_db):
    monkeypatch.setattr(ask_service, "get_connection", lambda: test_db)
    monkeypatch.setattr(ask_service, "connection_lock", lambda: _noop_lock())

    ask_service._record_sql_generation_failure(
        project_id=1,
        question="测试 group by 修复",
        failed_sql="SELECT city, SUM(amount) FROM t",
        error_text="GROUP BY issues: ['Dimension(s) missing from GROUP BY: City']",
        stage="repair_group_by",
        sql_engine="fewshot_cot_repair",
        attempt=2,
        issue_buckets=None,
        repaired_sql="SELECT city, SUM(amount) FROM t GROUP BY city",
        resolved=True,
        schema_link_snapshot={"selected_owner_map": {}},
        sql_plan_snapshot={"facts": ["orders"]},
    )

    row = test_db.execute(
        """
        SELECT stage, sql_engine, attempt, schema_link_snapshot, sql_plan_snapshot, repaired_sql, resolved
        FROM metadata.sql_generation_failures
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    assert row is not None
    assert row[0] == "repair_group_by"
    assert row[1] == "fewshot_cot_repair"
    assert row[2] == 2
    assert json.loads(row[3])["selected_owner_map"] == {}
    assert json.loads(row[4])["facts"] == ["orders"]
    assert row[5] == "SELECT city, SUM(amount) FROM t GROUP BY city"
    assert row[6] is True
