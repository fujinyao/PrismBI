#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

try:
    from scripts.unknown_columns_report import analyze_log
    from scripts.unknown_columns_replay import build_replay_cases
except Exception:  # pragma: no cover - fallback for direct script execution
    from unknown_columns_report import analyze_log
    from unknown_columns_replay import build_replay_cases


_LINE_TS_RE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)


def _extract_line_timestamp(line: str) -> datetime | None:
    m = _LINE_TS_RE.search(line)
    if not m:
        return None
    raw = m.group("ts").replace(",", ".")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if len(raw) >= 5 and raw[-5] in {"+", "-"} and raw[-3] != ":":
        raw = raw[:-2] + ":" + raw[-2:]
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _filter_log_text_by_since_hours(log_text: str, since_hours: float | None, now_utc: datetime) -> tuple[str, datetime | None]:
    if since_hours is None:
        return log_text, None
    cutoff = now_utc - timedelta(hours=max(0.0, since_hours))
    filtered_lines: list[str] = []
    for line in log_text.splitlines():
        ts = _extract_line_timestamp(line)
        if ts is None or ts >= cutoff:
            filtered_lines.append(line)
    return "\n".join(filtered_lines), cutoff


def build_daily_markdown(
    log_text: str,
    top: int = 10,
    since_hours: float | None = None,
    now_utc: datetime | None = None,
) -> str:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    filtered_text, cutoff = _filter_log_text_by_since_hours(log_text, since_hours, now)
    report = analyze_log(filtered_text)
    replay_cases = build_replay_cases(filtered_text, top=max(1, top))

    lines: list[str] = []
    lines.append("# Unknown Columns Daily Report")
    lines.append("")
    lines.append(f"Generated at: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    if cutoff is not None:
        lines.append(f"Time window: last {since_hours:g} hour(s) (since {cutoff.strftime('%Y-%m-%d %H:%M:%S')} UTC)")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- unknown_lines: {report.get('unknown_lines', 0)}")
    lines.append(f"- total_issues: {report.get('total_issues', 0)}")
    lines.append("")

    lines.append("## Bucket Counts")
    lines.append("")
    bucket_counts = report.get("bucket_counts", {}) or {}
    if not bucket_counts:
        lines.append("- (none)")
    else:
        for bucket, count in sorted(bucket_counts.items(), key=lambda item: item[1], reverse=True):
            lines.append(f"- {bucket}: {count}")
    lines.append("")

    lines.append("## Top Column Refs")
    lines.append("")
    top_columns = report.get("top_columns", []) or []
    if not top_columns:
        lines.append("- (none)")
    else:
        for col, count in top_columns[:top]:
            lines.append(f"- {col}: {count}")
    lines.append("")

    lines.append("## Replay Cases")
    lines.append("")
    if not replay_cases:
        lines.append("- (none)")
    else:
        for idx, case in enumerate(replay_cases[:top], start=1):
            owners = ", ".join(case.get("owners") or []) or "-"
            lines.append(f"### Case {idx}")
            lines.append(f"- issue: `{case.get('issue', '')}`")
            lines.append(f"- bucket: `{case.get('bucket', '')}`")
            lines.append(f"- count: {case.get('count', 0)}")
            lines.append(f"- owners: {owners}")
            lines.append(f"- suggested_action: `{case.get('suggested_action', '')}`")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate unknown-columns daily markdown report from logs.")
    parser.add_argument("logfile", type=Path, help="Path to backend log file")
    parser.add_argument("--top", type=int, default=10, help="Top N entries")
    parser.add_argument(
        "--since-hours",
        type=float,
        default=None,
        help="Only include timestamped lines within the last N hours",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output markdown path (default: stdout)",
    )
    args = parser.parse_args()

    if not args.logfile.exists():
        raise SystemExit(f"Log file not found: {args.logfile}")

    log_text = args.logfile.read_text(encoding="utf-8", errors="ignore")
    markdown = build_daily_markdown(
        log_text,
        top=max(1, args.top),
        since_hours=args.since_hours,
    )

    if args.output is None:
        print(markdown)
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown, encoding="utf-8")
    print(f"Wrote report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
