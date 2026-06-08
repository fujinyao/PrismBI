from __future__ import annotations

from datetime import datetime, timezone

from scripts.unknown_columns_daily_report import build_daily_markdown


def test_build_daily_markdown_contains_summary_buckets_and_replay_cases():
    log_text = """
SQL references unknown columns ['t2.customer_city (belongs on: olist_customers_dataset)'] — attempting rehint then repair
SQL references unknown columns ['t3.order_id (belongs on: olist_order_items_dataset, olist_orders_dataset)'] — attempting rehint then repair
SQL references unknown columns ['t1.quantity (not found; did you mean customer_city on olist_customers_dataset?)'] — attempting rehint then repair
"""
    md = build_daily_markdown(log_text, top=5)

    assert "# Unknown Columns Daily Report" in md
    assert "## Summary" in md
    assert "unknown_lines: 3" in md
    assert "## Bucket Counts" in md
    assert "wrong_alias_owner: 1" in md
    assert "ambiguous_owner: 1" in md
    assert "fuzzy_miss: 1" in md
    assert "## Replay Cases" in md
    assert "suggested_action" in md


def test_build_daily_markdown_empty_when_no_unknown_lines():
    md = build_daily_markdown("INFO: all good", top=5)
    assert "unknown_lines: 0" in md
    assert "total_issues: 0" in md
    assert "- (none)" in md


def test_build_daily_markdown_since_hours_filters_timestamped_lines():
    log_text = """
2026-06-03 08:50:00 SQL references unknown columns ['t2.customer_city (belongs on: olist_customers_dataset)'] — attempting rehint then repair
2026-06-03 07:20:00 SQL references unknown columns ['t3.order_id (belongs on: olist_order_items_dataset, olist_orders_dataset)'] — attempting rehint then repair
2026-06-03 06:30:00 SQL references unknown columns ['t1.quantity (not found; did you mean customer_city on olist_customers_dataset?)'] — attempting rehint then repair
"""
    now = datetime(2026, 6, 3, 9, 0, 0, tzinfo=timezone.utc)
    md = build_daily_markdown(log_text, top=5, since_hours=2, now_utc=now)

    assert "Time window: last 2 hour(s)" in md
    assert "unknown_lines: 2" in md
    assert "wrong_alias_owner: 1" in md
    assert "ambiguous_owner: 1" in md


def test_build_daily_markdown_since_hours_keeps_lines_without_timestamp():
    log_text = """
INFO any line without timestamp
SQL references unknown columns ['t9.ghost (not found in any model)'] — attempting rehint then repair
"""
    now = datetime(2026, 6, 3, 9, 0, 0, tzinfo=timezone.utc)
    md = build_daily_markdown(log_text, top=5, since_hours=1, now_utc=now)
    assert "unknown_lines: 1" in md
    assert "hallucinated_column: 1" in md
