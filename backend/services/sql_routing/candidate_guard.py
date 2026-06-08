from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


ValidateColumnsFn = Callable[[str, list[dict[str, Any]]], list[str] | None]
ValidateGroupByFn = Callable[..., list[str]]
ValidateAggregationFn = Callable[[str], list[str]]
ValidateSyntaxFn = Callable[[str, int], list[str]]


@dataclass(slots=True)
class CandidateValidationResult:
    sql: str
    bad_columns: list[str] = field(default_factory=list)
    columns_inconclusive: bool = False
    group_issues: list[str] = field(default_factory=list)
    aggregation_issues: list[str] = field(default_factory=list)
    syntax_issues: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        if self.columns_inconclusive:
            return False
        return not (self.bad_columns or self.group_issues or self.aggregation_issues or self.syntax_issues)


class CandidateGuard:
    def __init__(
        self,
        *,
        validate_sql_columns: ValidateColumnsFn,
        validate_sql_group_by: ValidateGroupByFn,
        validate_sql_aggregation: ValidateAggregationFn,
        validate_sql_syntax_for_project: ValidateSyntaxFn,
    ) -> None:
        self._validate_sql_columns = validate_sql_columns
        self._validate_sql_group_by = validate_sql_group_by
        self._validate_sql_aggregation = validate_sql_aggregation
        self._validate_sql_syntax_for_project = validate_sql_syntax_for_project

    def inspect(
        self,
        sql: str,
        *,
        dimensions: list[str],
        hit_models: list[dict[str, Any]],
        resolved: dict[str, Any] | None,
        project_id: int,
    ) -> CandidateValidationResult:
        column_result = self._validate_sql_columns(sql, hit_models)
        columns_inconclusive = column_result is None
        bad_columns = [] if columns_inconclusive else list(column_result)
        group_issues = list(
            self._validate_sql_group_by(
                sql,
                dimensions,
                hit_models=hit_models,
                resolved=resolved,
            )
            or []
        )
        aggregation_issues = list(self._validate_sql_aggregation(sql) or [])
        syntax_issues = list(self._validate_sql_syntax_for_project(sql, project_id) or [])
        return CandidateValidationResult(
            sql=str(sql or ""),
            bad_columns=bad_columns,
            columns_inconclusive=columns_inconclusive,
            group_issues=group_issues,
            aggregation_issues=aggregation_issues,
            syntax_issues=syntax_issues,
        )
