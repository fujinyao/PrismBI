#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import tempfile
from typing import Any

import duckdb

try:
    from scripts.unknown_columns_report import classify_issue
except Exception:  # pragma: no cover - fallback for direct script execution
    from unknown_columns_report import classify_issue


def _default_db_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "prismbi.duckdb"


def _parse_json_map(raw: Any) -> dict[str, int]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        out: dict[str, int] = {}
        for k, v in raw.items():
            try:
                out[str(k)] = int(v)
            except Exception:
                continue
        return out
    text = str(raw).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out = {}
    for k, v in parsed.items():
        try:
            out[str(k)] = int(v)
        except Exception:
            continue
    return out


def _parse_json_obj(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    text = str(raw).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _buckets_from_error(error_text: str, stage: str) -> dict[str, int]:
    stage_lower = str(stage or "").lower()
    if stage_lower in {"validate_group_by", "repair_group_by"}:
        return {"group_by_missing": 1}
    if "unknown columns:" not in str(error_text or "").lower():
        return {}
    payload = str(error_text or "")
    _, _, raw_list = payload.partition(":")
    raw_list = raw_list.strip()
    try:
        parsed = ast.literal_eval(raw_list)
    except Exception:
        return {}
    if not isinstance(parsed, list):
        return {}
    out: dict[str, int] = {}
    for issue in parsed:
        bucket = classify_issue(str(issue or ""))
        out[bucket] = out.get(bucket, 0) + 1
    return out


def _row_bucket_counts(issue_buckets_raw: Any, error_text: str, stage: str) -> dict[str, int]:
    parsed = _parse_json_map(issue_buckets_raw)
    if parsed:
        return parsed
    return _buckets_from_error(error_text, stage)


def _row_plan_segment(schema_link_snapshot_raw: Any, sql_plan_snapshot_raw: Any) -> str:
    schema_snap = _parse_json_obj(schema_link_snapshot_raw)
    plan_snap = _parse_json_obj(sql_plan_snapshot_raw)
    plan_tier = str(plan_snap.get("tier") or schema_snap.get("analysis_tier") or "unknown").lower()
    facts = plan_snap.get("facts") if isinstance(plan_snap.get("facts"), list) else []
    group_cols = plan_snap.get("group_by_columns") if isinstance(plan_snap.get("group_by_columns"), list) else []
    join_hints = plan_snap.get("join_path_hints") if isinstance(plan_snap.get("join_path_hints"), list) else []
    has_owner_preferences = bool((schema_snap.get("selected_owner_map") or {}))
    return (
        f"tier={plan_tier}"
        f"|facts={len(facts)}"
        f"|group_by={len(group_cols)}"
        f"|joins={1 if join_hints else 0}"
        f"|owner_map={1 if has_owner_preferences else 0}"
    )


def evaluate_failures_rows(rows: list[tuple[Any, ...]], *, segment_top: int = 10) -> dict[str, Any]:
    validate_counts: dict[str, int] = {}
    resolved_counts: dict[str, int] = {}
    segment_validate: dict[str, dict[str, int]] = {}
    segment_resolved: dict[str, dict[str, int]] = {}
    validate_rows = 0
    resolved_rows = 0

    for row in rows:
        stage = str(row[0] or "")
        issue_buckets = row[1]
        error_text = str(row[2] or "")
        resolved_flag = bool(row[3]) if row[3] is not None else False
        schema_link_snapshot = row[4] if len(row) > 4 else None
        sql_plan_snapshot = row[5] if len(row) > 5 else None
        stage_lower = stage.lower()
        segment = _row_plan_segment(schema_link_snapshot, sql_plan_snapshot)

        bucket_counts = _row_bucket_counts(issue_buckets, error_text, stage)
        if not bucket_counts:
            continue

        if stage_lower.startswith("validate_"):
            validate_rows += 1
            seg_validate_bucket = segment_validate.setdefault(segment, {})
            for bucket, count in bucket_counts.items():
                validate_counts[bucket] = validate_counts.get(bucket, 0) + int(count)
                seg_validate_bucket[bucket] = seg_validate_bucket.get(bucket, 0) + int(count)
            continue

        if stage_lower.startswith("repair_") and resolved_flag:
            resolved_rows += 1
            seg_resolved_bucket = segment_resolved.setdefault(segment, {})
            for bucket, count in bucket_counts.items():
                resolved_counts[bucket] = resolved_counts.get(bucket, 0) + int(count)
                seg_resolved_bucket[bucket] = seg_resolved_bucket.get(bucket, 0) + int(count)

    all_buckets = sorted(set(validate_counts.keys()) | set(resolved_counts.keys()))
    bucket_stats = []
    validate_total = sum(validate_counts.values())
    resolved_total = sum(resolved_counts.values())
    for bucket in all_buckets:
        validate_n = int(validate_counts.get(bucket, 0))
        resolved_n = int(resolved_counts.get(bucket, 0))
        success_rate = (resolved_n / validate_n) if validate_n > 0 else 0.0
        bucket_stats.append(
            {
                "bucket": bucket,
                "validate_total": validate_n,
                "resolved_total": resolved_n,
                "success_rate": round(success_rate, 4),
            }
        )
    bucket_stats.sort(key=lambda item: item["validate_total"], reverse=True)

    segment_stats = []
    all_segments = sorted(set(segment_validate.keys()) | set(segment_resolved.keys()))
    for segment in all_segments:
        seg_validate_counts = segment_validate.get(segment, {})
        seg_resolved_counts = segment_resolved.get(segment, {})
        seg_validate_total = sum(seg_validate_counts.values())
        seg_resolved_total = sum(seg_resolved_counts.values())
        seg_success = (seg_resolved_total / seg_validate_total) if seg_validate_total > 0 else 0.0
        seg_bucket_stats = []
        for bucket in sorted(set(seg_validate_counts.keys()) | set(seg_resolved_counts.keys())):
            b_validate = int(seg_validate_counts.get(bucket, 0))
            b_resolved = int(seg_resolved_counts.get(bucket, 0))
            b_success = (b_resolved / b_validate) if b_validate > 0 else 0.0
            seg_bucket_stats.append(
                {
                    "bucket": bucket,
                    "validate_total": b_validate,
                    "resolved_total": b_resolved,
                    "success_rate": round(b_success, 4),
                }
            )
        seg_bucket_stats.sort(key=lambda item: item["validate_total"], reverse=True)
        segment_stats.append(
            {
                "segment": segment,
                "validate_total": seg_validate_total,
                "resolved_total": seg_resolved_total,
                "success_rate": round(seg_success, 4),
                "bucket_stats": seg_bucket_stats,
            }
        )
    segment_stats.sort(key=lambda item: item["validate_total"], reverse=True)
    if segment_top > 0:
        segment_stats = segment_stats[:segment_top]

    overall_success_rate = (resolved_total / validate_total) if validate_total > 0 else 0.0
    return {
        "validate_rows": validate_rows,
        "resolved_rows": resolved_rows,
        "validate_total": validate_total,
        "resolved_total": resolved_total,
        "overall_success_rate": round(overall_success_rate, 4),
        "bucket_stats": bucket_stats,
        "plan_segment_stats": segment_stats,
    }


def evaluate_failures_db(
    db_path: Path,
    *,
    since_hours: float | None = None,
    now_utc: datetime | None = None,
    segment_top: int = 10,
) -> dict[str, Any]:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = None
    if since_hours is not None:
        cutoff = now - timedelta(hours=max(0.0, since_hours))

    if not db_path.exists():
        return {
            "error": f"DB file not found: {db_path}",
            "validate_rows": 0,
            "resolved_rows": 0,
            "validate_total": 0,
            "resolved_total": 0,
            "overall_success_rate": 0.0,
            "bucket_stats": [],
            "plan_segment_stats": [],
            "since_hours": since_hours,
        }

    copied_temp_file: str | None = None
    conn = None
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
    except Exception as exc:
        lock_msg = str(exc).lower()
        if "could not set lock" not in lock_msg and "conflicting lock" not in lock_msg:
            conn = duckdb.connect(str(db_path))
        else:
            try:
                tmp = tempfile.NamedTemporaryFile(prefix="prismbi_eval_", suffix=".duckdb", delete=False)
                tmp.close()
                copied_temp_file = tmp.name
                shutil.copy2(db_path, copied_temp_file)
                conn = duckdb.connect(copied_temp_file, read_only=True)
            except Exception as copy_exc:
                if copied_temp_file:
                    try:
                        Path(copied_temp_file).unlink(missing_ok=True)
                    except Exception:
                        pass
                return {
                    "error": f"Failed to open DB due to lock: {copy_exc}",
                    "validate_rows": 0,
                    "resolved_rows": 0,
                    "validate_total": 0,
                    "resolved_total": 0,
                    "overall_success_rate": 0.0,
                    "bucket_stats": [],
                    "plan_segment_stats": [],
                    "since_hours": since_hours,
                }
    try:
        table_exists = conn.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'metadata' AND table_name = 'sql_generation_failures'
            """
        ).fetchone()[0]
        if int(table_exists or 0) <= 0:
            result = {
                "validate_rows": 0,
                "resolved_rows": 0,
                "validate_total": 0,
                "resolved_total": 0,
                "overall_success_rate": 0.0,
                "bucket_stats": [],
                "plan_segment_stats": [],
                "since_hours": since_hours,
            }
            return result

        column_rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'metadata' AND table_name = 'sql_generation_failures'
            """
        ).fetchall()
        available_columns = {str(r[0] or "").lower() for r in column_rows}
        schema_link_expr = "schema_link_snapshot" if "schema_link_snapshot" in available_columns else "NULL::JSON AS schema_link_snapshot"
        sql_plan_expr = "sql_plan_snapshot" if "sql_plan_snapshot" in available_columns else "NULL::JSON AS sql_plan_snapshot"
        select_sql = (
            "SELECT stage, issue_buckets, error_text, resolved, "
            f"{schema_link_expr}, {sql_plan_expr} "
            "FROM metadata.sql_generation_failures"
        )

        if cutoff is None:
            rows = conn.execute(
                f"{select_sql} ORDER BY id DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                f"{select_sql} WHERE created_at >= ? ORDER BY id DESC",
                [cutoff.replace(tzinfo=None)],
            ).fetchall()
    finally:
        if conn is not None:
            conn.close()
        if copied_temp_file:
            try:
                Path(copied_temp_file).unlink(missing_ok=True)
            except Exception:
                pass

    result = evaluate_failures_rows(rows, segment_top=segment_top)
    result["since_hours"] = since_hours
    return result


def build_evaluation_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Unknown Columns Repair Evaluation")
    lines.append("")
    lines.append(f"Generated at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    since_hours = report.get("since_hours")
    if since_hours is not None:
        lines.append(f"Window: last {since_hours:g} hour(s)")
    lines.append("")
    if report.get("error"):
        lines.append(f"- error: {report['error']}")
        return "\n".join(lines).strip() + "\n"

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- validate_rows: {report.get('validate_rows', 0)}")
    lines.append(f"- resolved_rows: {report.get('resolved_rows', 0)}")
    lines.append(f"- validate_total: {report.get('validate_total', 0)}")
    lines.append(f"- resolved_total: {report.get('resolved_total', 0)}")
    lines.append(f"- overall_success_rate: {report.get('overall_success_rate', 0.0):.2%}")
    lines.append("")

    lines.append("## Bucket Success Rates")
    lines.append("")
    bucket_stats = report.get("bucket_stats") or []
    if not bucket_stats:
        lines.append("- (none)")
    else:
        for item in bucket_stats:
            lines.append(
                f"- {item['bucket']}: {item['resolved_total']}/{item['validate_total']} ({item['success_rate']:.2%})"
            )
    lines.append("")

    lines.append("## Plan Segment Success Rates")
    lines.append("")
    segment_stats = report.get("plan_segment_stats") or []
    if not segment_stats:
        lines.append("- (none)")
    else:
        for item in segment_stats:
            lines.append(
                f"- {item['segment']}: {item['resolved_total']}/{item['validate_total']} ({item['success_rate']:.2%})"
            )
            top_bucket = (item.get("bucket_stats") or [])[:2]
            if top_bucket:
                details = ", ".join(
                    f"{b['bucket']} {b['resolved_total']}/{b['validate_total']} ({b['success_rate']:.2%})"
                    for b in top_bucket
                )
                lines.append(f"  top_buckets: {details}")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate replay->repair success rate by unknown-column bucket.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=_default_db_path(),
        help="Path to prismbi DuckDB file",
    )
    parser.add_argument(
        "--since-hours",
        type=float,
        default=None,
        help="Only include samples within the last N hours",
    )
    parser.add_argument(
        "--segment-top",
        type=int,
        default=10,
        help="Top N plan segments to include",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    parser.add_argument("--output", type=Path, default=None, help="Optional output path")
    args = parser.parse_args()

    report = evaluate_failures_db(
        args.db_path,
        since_hours=args.since_hours,
        segment_top=max(0, args.segment_top),
    )

    if args.json:
        content = json.dumps(report, ensure_ascii=False, indent=2)
    else:
        content = build_evaluation_markdown(report)

    if args.output is None:
        print(content)
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(f"Wrote evaluation: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
