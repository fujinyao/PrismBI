#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import duckdb

try:
    from db import DEFAULT_SYSTEM_DB_PATH
except Exception:  # pragma: no cover - fallback for direct execution context
    DEFAULT_SYSTEM_DB_PATH = str(Path(__file__).resolve().parents[1] / "data" / "prismbi.duckdb")


_FALLBACK_EVENT_TO_FIELDS = {
    "schema_link_fallback": ("schema_link_fallback_total", "schema_link_fallback_reason"),
    "sql_generation_fallback": (
        "sql_generation_fallback_total",
        "sql_generation_fallback_reason",
    ),
    "final_answer_fallback": (
        "final_answer_fallback_total",
        "final_answer_fallback_reason",
    ),
}


def _parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _parse_created_at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _new_project_bucket(project_id: int) -> dict[str, Any]:
    return {
        "project_id": int(project_id),
        "events_total": 0,
        "generation_decision_total": 0,
        "schema_link_fallback_total": 0,
        "schema_link_fallback_reason": Counter(),
        "sql_generation_fallback_total": 0,
        "sql_generation_fallback_reason": Counter(),
        "final_answer_fallback_total": 0,
        "final_answer_fallback_reason": Counter(),
    }


def summarize_fallback_rates(
    events: Iterable[dict[str, Any]],
    *,
    window_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    effective_now = now or datetime.now(timezone.utc)
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=timezone.utc)
    cutoff = effective_now - timedelta(seconds=max(1, int(window_seconds)))

    by_project: dict[int, dict[str, Any]] = {}
    for event in events:
        try:
            project_id = int(event.get("project_id"))
        except Exception:
            continue
        created_at = _parse_created_at(event.get("created_at"))
        if created_at is not None and created_at < cutoff:
            continue

        bucket = by_project.setdefault(project_id, _new_project_bucket(project_id))
        bucket["events_total"] = int(bucket["events_total"] or 0) + 1

        event_type = str(event.get("event_type") or "").strip()
        payload = _parse_payload(event.get("payload"))
        if event_type == "generation_route_decision":
            bucket["generation_decision_total"] = int(bucket["generation_decision_total"] or 0) + 1
            continue
        fallback_field = _FALLBACK_EVENT_TO_FIELDS.get(event_type)
        if not fallback_field:
            continue
        total_field, reason_field = fallback_field
        bucket[total_field] = int(bucket[total_field] or 0) + 1
        reason = str(payload.get("reason") or "unknown")
        bucket[reason_field][reason] += 1

    projects: list[dict[str, Any]] = []
    totals = {
        "events_total": 0,
        "generation_decision_total": 0,
        "schema_link_fallback_total": 0,
        "sql_generation_fallback_total": 0,
        "final_answer_fallback_total": 0,
    }

    for project_id in sorted(by_project):
        bucket = by_project[project_id]
        denom = int(bucket.get("generation_decision_total") or 0)
        schema_total = int(bucket.get("schema_link_fallback_total") or 0)
        generation_total = int(bucket.get("sql_generation_fallback_total") or 0)
        final_total = int(bucket.get("final_answer_fallback_total") or 0)
        project_entry = {
            "project_id": int(project_id),
            "events_total": int(bucket.get("events_total") or 0),
            "generation_decision_total": denom,
            "schema_link_fallback_total": schema_total,
            "schema_link_fallback_reason": dict(bucket.get("schema_link_fallback_reason") or {}),
            "schema_link_fallback_rate": round(schema_total / denom, 4) if denom > 0 else 0.0,
            "sql_generation_fallback_total": generation_total,
            "sql_generation_fallback_reason": dict(bucket.get("sql_generation_fallback_reason") or {}),
            "sql_generation_fallback_rate": round(generation_total / denom, 4) if denom > 0 else 0.0,
            "final_answer_fallback_total": final_total,
            "final_answer_fallback_reason": dict(bucket.get("final_answer_fallback_reason") or {}),
            "final_answer_fallback_rate": round(final_total / denom, 4) if denom > 0 else 0.0,
        }
        projects.append(project_entry)
        for key in totals:
            totals[key] = int(totals.get(key) or 0) + int(project_entry.get(key) or 0)

    denom = int(totals.get("generation_decision_total") or 0)
    totals["schema_link_fallback_rate"] = (
        round(int(totals.get("schema_link_fallback_total") or 0) / denom, 4) if denom > 0 else 0.0
    )
    totals["sql_generation_fallback_rate"] = (
        round(int(totals.get("sql_generation_fallback_total") or 0) / denom, 4) if denom > 0 else 0.0
    )
    totals["final_answer_fallback_rate"] = (
        round(int(totals.get("final_answer_fallback_total") or 0) / denom, 4) if denom > 0 else 0.0
    )

    return {
        "window_seconds": int(max(1, int(window_seconds))),
        "generated_at": effective_now.isoformat(),
        "projects": projects,
        "totals": totals,
    }


def load_events_from_db(db_path: Path, project_id: int | None = None) -> list[dict[str, Any]]:
    if not db_path.exists():
        raise FileNotFoundError(f"DuckDB file not found: {db_path}")
    connection = None
    should_close = False
    try:
        connection = duckdb.connect(str(db_path), read_only=True)
        should_close = True
    except Exception:
        try:
            connection = duckdb.connect(str(db_path), read_only=False)
            should_close = True
        except Exception:
            runtime_path = Path(os.getenv("PRISMBI_DB_PATH") or DEFAULT_SYSTEM_DB_PATH)
            if db_path.resolve() != runtime_path.resolve():
                raise
            from db import get_connection  # lazy import to avoid script import side effects

            connection = get_connection()
            should_close = False
    try:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'metadata' AND table_name = 'sql_route_events'
            """
        ).fetchone()
        if not row or int(row[0] or 0) == 0:
            return []

        if project_id is None:
            rows = connection.execute(
                """
                SELECT event_type, project_id, payload, created_at
                FROM metadata.sql_route_events
                ORDER BY created_at DESC
                """
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT event_type, project_id, payload, created_at
                FROM metadata.sql_route_events
                WHERE project_id = ?
                ORDER BY created_at DESC
                """,
                [int(project_id)],
            ).fetchall()
        return [
            {
                "event_type": str(item[0] or ""),
                "project_id": item[1],
                "payload": item[2],
                "created_at": item[3],
            }
            for item in rows
        ]
    finally:
        if should_close and connection is not None:
            connection.close()


def _render_human_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Fallback Rate Report")
    lines.append("====================")
    lines.append(f"window_seconds: {report.get('window_seconds')}")
    totals = report.get("totals") or {}
    lines.append(
        "totals: "
        f"events={totals.get('events_total', 0)} "
        f"generation_decisions={totals.get('generation_decision_total', 0)} "
        f"schema_link_rate={totals.get('schema_link_fallback_rate', 0.0):.4f} "
        f"generation_fallback_rate={totals.get('sql_generation_fallback_rate', 0.0):.4f} "
        f"final_answer_rate={totals.get('final_answer_fallback_rate', 0.0):.4f}"
    )

    projects = report.get("projects") or []
    if not projects:
        lines.append("\nNo matching events found in the selected window.")
        return "\n".join(lines)

    lines.append("\nPer project:")
    for project in projects:
        lines.append(
            "- "
            f"project_id={project.get('project_id')} "
            f"generation_decisions={project.get('generation_decision_total', 0)} "
            f"schema_link_rate={project.get('schema_link_fallback_rate', 0.0):.4f} "
            f"generation_fallback_rate={project.get('sql_generation_fallback_rate', 0.0):.4f} "
            f"final_answer_rate={project.get('final_answer_fallback_rate', 0.0):.4f}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Report schema/decompose/final-answer fallback rates from sql_route_events")
    parser.add_argument("--project-id", type=int, default=None, help="Optional project id filter")
    parser.add_argument("--window-seconds", type=int, default=1800, help="Time window for analysis")
    parser.add_argument("--db-path", type=Path, default=None, help="Path to prismbi.duckdb (default: PRISMBI_DB_PATH or backend default)")
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    args = parser.parse_args()

    db_path = args.db_path
    if db_path is None:
        env_path = os.getenv("PRISMBI_DB_PATH")
        db_path = Path(env_path) if env_path else Path(DEFAULT_SYSTEM_DB_PATH)

    events = load_events_from_db(db_path, project_id=args.project_id)
    report = summarize_fallback_rates(
        events,
        window_seconds=max(1, int(args.window_seconds)),
    )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    print(_render_human_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
