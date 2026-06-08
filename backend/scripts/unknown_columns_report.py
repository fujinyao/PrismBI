#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter
from pathlib import Path


_UNKNOWN_LINE_RE = re.compile(
    r"SQL references unknown columns\s+(\[.*\])\s+—\s+attempting rehint then repair"
)


def classify_issue(issue: str) -> str:
    text = (issue or "").lower()
    if "duplicate table alias" in text or "duplicate alias" in text or "alias already used" in text:
        return "duplicate_alias"
    if "table/alias not visible in current select scope" in text:
        return "alias_scope_leak"
    if "not projected by cte" in text:
        return "cte_projection_missing"
    if "belongs on:" in text:
        owners_part = text.split("belongs on:", 1)[-1]
        owners = [p.strip() for p in owners_part.split(",") if p.strip()]
        if len(owners) > 1:
            return "ambiguous_owner"
        return "wrong_alias_owner"
    if "did you mean" in text:
        return "fuzzy_miss"
    if "not found in any model" in text:
        return "hallucinated_column"
    return "other_unknown_column_issue"


def parse_issue_list(raw_list: str) -> list[str]:
    try:
        parsed = ast.literal_eval(raw_list)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except Exception:
        pass
    return []


def analyze_log(text: str) -> dict:
    issue_bucket_counter: Counter[str] = Counter()
    issue_text_counter: Counter[str] = Counter()
    column_ref_counter: Counter[str] = Counter()
    total_unknown_lines = 0

    for line in text.splitlines():
        m = _UNKNOWN_LINE_RE.search(line)
        if not m:
            continue
        total_unknown_lines += 1
        issues = parse_issue_list(m.group(1))
        for issue in issues:
            issue_text_counter[issue] += 1
            issue_bucket_counter[classify_issue(issue)] += 1
            column_ref = issue.split("(", 1)[0].strip()
            if column_ref:
                column_ref_counter[column_ref] += 1

    return {
        "unknown_lines": total_unknown_lines,
        "total_issues": sum(issue_bucket_counter.values()),
        "bucket_counts": dict(issue_bucket_counter),
        "top_columns": column_ref_counter.most_common(20),
        "top_issue_texts": issue_text_counter.most_common(20),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze unknown-columns logs and output issue buckets."
    )
    parser.add_argument("logfile", type=Path, help="Path to backend log file")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON only",
    )
    args = parser.parse_args()

    if not args.logfile.exists():
        raise SystemExit(f"Log file not found: {args.logfile}")

    text = args.logfile.read_text(encoding="utf-8", errors="ignore")
    report = analyze_log(text)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("Unknown Columns Log Report")
    print("==========================")
    print(f"unknown_lines: {report['unknown_lines']}")
    print(f"total_issues: {report['total_issues']}")
    print("\nBucket counts:")
    for bucket, count in sorted(report["bucket_counts"].items(), key=lambda item: item[1], reverse=True):
        print(f"- {bucket}: {count}")

    print("\nTop column refs:")
    for col, count in report["top_columns"][:10]:
        print(f"- {col}: {count}")

    print("\nTop issue texts:")
    for issue, count in report["top_issue_texts"][:10]:
        print(f"- ({count}) {issue}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
