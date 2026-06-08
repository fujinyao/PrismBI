from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import duckdb

from scripts.unknown_columns_evaluator import (
    build_evaluation_markdown,
    evaluate_failures_db,
    evaluate_failures_rows,
)


def test_evaluate_failures_rows_calculates_bucket_success_rates():
    rows = [
        ("validate_sql_columns", json.dumps({"ambiguous_owner": 2}), "Unknown columns: ...", False, None, None),
        ("validate_sql_columns", json.dumps({"wrong_alias_owner": 1}), "Unknown columns: ...", False, None, None),
        ("repair_sql_columns", json.dumps({"ambiguous_owner": 1}), "Unknown columns: ...", True, None, None),
        ("repair_sql_columns", json.dumps({"wrong_alias_owner": 1}), "Unknown columns: ...", True, None, None),
        ("repair_sql_columns", json.dumps({"ambiguous_owner": 1}), "Unknown columns: ...", False, None, None),
    ]
    report = evaluate_failures_rows(rows)
    assert report["validate_total"] == 3
    assert report["resolved_total"] == 2
    assert report["overall_success_rate"] == round(2 / 3, 4)

    by_bucket = {item["bucket"]: item for item in report["bucket_stats"]}
    assert by_bucket["ambiguous_owner"]["validate_total"] == 2
    assert by_bucket["ambiguous_owner"]["resolved_total"] == 1
    assert by_bucket["wrong_alias_owner"]["validate_total"] == 1
    assert by_bucket["wrong_alias_owner"]["resolved_total"] == 1
    assert report["plan_segment_stats"]


def test_evaluate_failures_rows_fallback_parses_error_text_when_issue_buckets_empty():
    rows = [
        (
            "validate_sql_columns",
            "{}",
            "Unknown columns: ['t1.foo (not found in any model)']",
            False,
            None,
            None,
        ),
        (
            "repair_sql_columns",
            "{}",
            "Unknown columns: ['t1.foo (not found in any model)']",
            True,
            None,
            None,
        ),
    ]
    report = evaluate_failures_rows(rows)
    by_bucket = {item["bucket"]: item for item in report["bucket_stats"]}
    assert by_bucket["hallucinated_column"]["validate_total"] == 1
    assert by_bucket["hallucinated_column"]["resolved_total"] == 1


def test_evaluate_failures_db_respects_since_hours_filter(tmp_path):
    now = datetime(2026, 6, 3, 10, 0, 0, tzinfo=timezone.utc)
    old_ts = now - timedelta(hours=5)
    new_ts = now - timedelta(minutes=20)
    db_path = tmp_path / "eval.duckdb"
    conn = duckdb.connect(str(db_path))

    conn.execute("CREATE SCHEMA IF NOT EXISTS metadata")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata.sql_generation_failures (
            id INTEGER PRIMARY KEY,
            project_id INTEGER,
            question_hash VARCHAR,
            question TEXT,
            failed_sql TEXT,
            error_text TEXT,
            stage VARCHAR,
            sql_engine VARCHAR,
            attempt INTEGER,
            issue_buckets JSON,
            schema_link_snapshot JSON,
            sql_plan_snapshot JSON,
            repaired_sql TEXT,
            resolved BOOLEAN,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        INSERT INTO metadata.sql_generation_failures
        (id, project_id, question_hash, question, failed_sql, error_text, stage, sql_engine, attempt, issue_buckets, schema_link_snapshot, sql_plan_snapshot, repaired_sql, resolved, created_at)
        VALUES
        (1, 1, 'h1', 'q1', 's1', 'Unknown columns: []', 'validate_sql_columns', 'direct_llm', 1, ?::JSON, ?::JSON, ?::JSON, NULL, FALSE, ?),
        (2, 1, 'h2', 'q2', 's2', 'Unknown columns: []', 'validate_sql_columns', 'direct_llm', 1, ?::JSON, ?::JSON, ?::JSON, NULL, FALSE, ?),
        (3, 1, 'h3', 'q3', 's3', 'Unknown columns: []', 'repair_sql_columns', 'direct_llm_repair', 1, ?::JSON, ?::JSON, ?::JSON, 's3_fix', TRUE, ?)
        """,
        [
            json.dumps({"ambiguous_owner": 1}), json.dumps({"selected_owner_map": {"order_id": "orders"}}), json.dumps({"tier": "simple", "facts": ["orders"], "group_by_columns": ["city"]}), old_ts.replace(tzinfo=None),
            json.dumps({"ambiguous_owner": 1}), json.dumps({"selected_owner_map": {"order_id": "orders"}}), json.dumps({"tier": "simple", "facts": ["orders"], "group_by_columns": ["city"]}), new_ts.replace(tzinfo=None),
            json.dumps({"ambiguous_owner": 1}), json.dumps({"selected_owner_map": {"order_id": "orders"}}), json.dumps({"tier": "simple", "facts": ["orders"], "group_by_columns": ["city"]}), new_ts.replace(tzinfo=None),
        ],
    )
    conn.close()
    report = evaluate_failures_db(
        db_path=db_path,
        since_hours=2,
        now_utc=now,
    )
    assert report["validate_total"] == 1
    assert report["resolved_total"] == 1
    assert report["plan_segment_stats"]


def test_build_evaluation_markdown_contains_summary_and_buckets():
    report = {
        "validate_rows": 2,
        "resolved_rows": 1,
        "validate_total": 3,
        "resolved_total": 2,
        "overall_success_rate": 0.6667,
        "bucket_stats": [
            {"bucket": "ambiguous_owner", "validate_total": 2, "resolved_total": 1, "success_rate": 0.5},
            {"bucket": "wrong_alias_owner", "validate_total": 1, "resolved_total": 1, "success_rate": 1.0},
        ],
        "plan_segment_stats": [
            {
                "segment": "tier=simple|facts=1|group_by=1|joins=1|owner_map=1",
                "validate_total": 2,
                "resolved_total": 1,
                "success_rate": 0.5,
                "bucket_stats": [
                    {"bucket": "ambiguous_owner", "validate_total": 2, "resolved_total": 1, "success_rate": 0.5},
                ],
            }
        ],
        "since_hours": 24,
    }
    md = build_evaluation_markdown(report)
    assert "# Unknown Columns Repair Evaluation" in md
    assert "overall_success_rate: 66.67%" in md
    assert "ambiguous_owner: 1/2 (50.00%)" in md
    assert "wrong_alias_owner: 1/1 (100.00%)" in md
    assert "## Plan Segment Success Rates" in md
    assert "tier=simple|facts=1|group_by=1|joins=1|owner_map=1: 1/2 (50.00%)" in md


def test_evaluate_failures_rows_builds_segment_level_success_rates():
    rows = [
        (
            "validate_sql_columns",
            json.dumps({"ambiguous_owner": 1}),
            "Unknown columns: ...",
            False,
            json.dumps({"selected_owner_map": {"order_id": "orders"}}),
            json.dumps({"tier": "simple", "facts": ["orders"], "group_by_columns": ["city"], "join_path_hints": ["a=b"]}),
        ),
        (
            "repair_sql_columns",
            json.dumps({"ambiguous_owner": 1}),
            "Unknown columns: ...",
            True,
            json.dumps({"selected_owner_map": {"order_id": "orders"}}),
            json.dumps({"tier": "simple", "facts": ["orders"], "group_by_columns": ["city"], "join_path_hints": ["a=b"]}),
        ),
    ]
    report = evaluate_failures_rows(rows)
    assert report["plan_segment_stats"]
    seg = report["plan_segment_stats"][0]
    assert seg["segment"].startswith("tier=simple")
    assert seg["success_rate"] == 1.0
