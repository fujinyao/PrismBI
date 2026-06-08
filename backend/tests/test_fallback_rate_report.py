from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.fallback_rate_report import summarize_fallback_rates


def test_summarize_fallback_rates_computes_project_level_rates_and_reasons():
    now = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
    events = [
        {
            "event_type": "generation_route_decision",
            "project_id": 1,
            "payload": {"generation_engine": "fewshot_cot"},
            "created_at": now - timedelta(seconds=10),
        },
        {
            "event_type": "generation_route_decision",
            "project_id": 1,
            "payload": {"generation_engine": "fewshot_cot"},
            "created_at": now - timedelta(seconds=9),
        },
        {
            "event_type": "generation_route_decision",
            "project_id": 1,
            "payload": {"generation_engine": "direct_llm"},
            "created_at": now - timedelta(seconds=8),
        },
        {
            "event_type": "generation_route_decision",
            "project_id": 1,
            "payload": {"generation_engine": "direct_llm"},
            "created_at": now - timedelta(seconds=7),
        },
        {
            "event_type": "schema_link_fallback",
            "project_id": 1,
            "payload": {"reason": "empty_content"},
            "created_at": now - timedelta(seconds=6),
        },
        {
            "event_type": "sql_generation_fallback",
            "project_id": 1,
            "payload": {"reason": "group_by"},
            "created_at": now - timedelta(seconds=5),
        },
        {
            "event_type": "sql_generation_fallback",
            "project_id": 1,
            "payload": {"reason": "single_sub_question_bypass"},
            "created_at": now - timedelta(seconds=4),
        },
        {
            "event_type": "final_answer_fallback",
            "project_id": 1,
            "payload": {"reason": "ungrounded_summary"},
            "created_at": now - timedelta(seconds=3),
        },
    ]

    report = summarize_fallback_rates(events, window_seconds=120, now=now)

    assert report["window_seconds"] == 120
    assert report["totals"]["generation_decision_total"] == 4
    assert len(report["projects"]) == 1
    project = report["projects"][0]
    assert project["project_id"] == 1
    assert project["schema_link_fallback_total"] == 1
    assert project["schema_link_fallback_reason"]["empty_content"] == 1
    assert project["schema_link_fallback_rate"] == 0.25
    assert project["sql_generation_fallback_total"] == 2
    assert project["sql_generation_fallback_reason"]["group_by"] == 1
    assert project["sql_generation_fallback_reason"]["single_sub_question_bypass"] == 1
    assert project["sql_generation_fallback_rate"] == 0.5
    assert project["final_answer_fallback_total"] == 1
    assert project["final_answer_fallback_reason"]["ungrounded_summary"] == 1
    assert project["final_answer_fallback_rate"] == 0.25


def test_summarize_fallback_rates_filters_out_of_window_and_invalid_payloads():
    now = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
    events = [
        {
            "event_type": "generation_route_decision",
            "project_id": 2,
            "payload": "{}",
            "created_at": now - timedelta(seconds=20),
        },
        {
            "event_type": "final_answer_fallback",
            "project_id": 2,
            "payload": "{invalid-json}",
            "created_at": now - timedelta(seconds=10),
        },
        {
            "event_type": "sql_generation_fallback",
            "project_id": 2,
            "payload": {"reason": "group_by"},
            "created_at": now - timedelta(seconds=5000),
        },
    ]

    report = summarize_fallback_rates(events, window_seconds=120, now=now)

    assert len(report["projects"]) == 1
    project = report["projects"][0]
    assert project["project_id"] == 2
    assert project["generation_decision_total"] == 1
    assert project["final_answer_fallback_total"] == 1
    assert project["final_answer_fallback_reason"]["unknown"] == 1
    assert project["sql_generation_fallback_total"] == 0
    assert project["sql_generation_fallback_rate"] == 0.0
