#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter
from pathlib import Path

try:
    from scripts.unknown_columns_report import classify_issue
except Exception:  # pragma: no cover - fallback for direct script execution
    from unknown_columns_report import classify_issue


_UNKNOWN_LINE_RE = re.compile(
    r"SQL references unknown columns\s+(\[.*\])\s+—\s+attempting rehint then repair"
)
_ISSUE_RE = re.compile(r"^(?P<column>[A-Za-z0-9_\.]+)\s*\((?P<detail>.*)\)$")


def _parse_issue_list(raw_list: str) -> list[str]:
    try:
        parsed = ast.literal_eval(raw_list)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except Exception:
        pass
    return []


def _extract_issues(text: str) -> list[str]:
    issues: list[str] = []
    for line in text.splitlines():
        m = _UNKNOWN_LINE_RE.search(line)
        if not m:
            continue
        issues.extend(_parse_issue_list(m.group(1)))
    return issues


def _owners_from_issue(issue_detail: str) -> list[str]:
    detail = issue_detail.lower()
    if "belongs on:" not in detail:
        return []
    owners_part = detail.split("belongs on:", 1)[-1]
    owners = [o.strip(" )") for o in owners_part.split(",") if o.strip()]
    return owners


def _suggested_action(bucket: str) -> str:
    return {
        "duplicate_alias": "rename_conflicting_aliases",
        "alias_scope_leak": "rewrite_to_visible_scope_alias",
        "wrong_alias_owner": "fix_alias_owner",
        "ambiguous_owner": "disambiguate_owner_with_join_path",
        "fuzzy_miss": "avoid_unrelated_fuzzy_remap",
        "hallucinated_column": "drop_or_replace_hallucinated_column",
        "cte_projection_missing": "project_column_in_cte_or_rewire_outer_ref",
    }.get(bucket, "manual_review")


def build_replay_cases(text: str, top: int = 20) -> list[dict]:
    issue_counter: Counter[str] = Counter(_extract_issues(text))
    cases: list[dict] = []
    for issue, count in issue_counter.most_common(top):
        m = _ISSUE_RE.match(issue.strip())
        column_ref = issue
        detail = ""
        if m:
            column_ref = m.group("column")
            detail = m.group("detail")
        bucket = classify_issue(issue)
        owners = _owners_from_issue(detail)
        case = {
            "issue": issue,
            "count": count,
            "column_ref": column_ref,
            "bucket": bucket,
            "owners": owners,
            "suggested_action": _suggested_action(bucket),
            "repair_payload_template": {
                "failed_sql": "-- fill with captured failed SQL for this issue",
                "error": f"Unknown columns: ['{issue}']",
            },
        }
        cases.append(case)
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build replay samples from unknown-columns logs."
    )
    parser.add_argument("logfile", type=Path, help="Path to backend log file")
    parser.add_argument("--top", type=int, default=20, help="Top N issues to keep")
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    args = parser.parse_args()

    if not args.logfile.exists():
        raise SystemExit(f"Log file not found: {args.logfile}")

    text = args.logfile.read_text(encoding="utf-8", errors="ignore")
    cases = build_replay_cases(text, top=max(1, args.top))

    if args.json:
        print(json.dumps(cases, ensure_ascii=False, indent=2))
        return 0

    print("Unknown Columns Replay Cases")
    print("============================")
    print(f"total_cases: {len(cases)}")
    for idx, case in enumerate(cases, start=1):
        owners = ", ".join(case["owners"]) if case["owners"] else "-"
        print(f"\n[{idx}] {case['column_ref']} x{case['count']}")
        print(f" bucket: {case['bucket']}")
        print(f" owners: {owners}")
        print(f" action: {case['suggested_action']}")
        print(f" issue: {case['issue']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
