from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .contracts import ExecutionRouteDecision
from .execution_router import ExecutionRouter


PlanSecuredSqlFn = Callable[[str, int, int], dict[str, Any]]
BindingRowsFn = Callable[[int], list[tuple[int, str, dict[str, Any]]]]
ModelsByBindingFn = Callable[[int], dict[int, list[dict[str, Any]]]]
ModelsForProjectFn = Callable[[int], list[dict[str, Any]]]
NormalizeSqlFn = Callable[[Any], str]
ApplyLimitFn = Callable[[str, int], str]
NormalizeRowLimitFn = Callable[[Optional[int]], int]


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 1:
            return True
        if value == 0:
            return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


@dataclass(slots=True)
class ExecutionPreparedPlan:
    plan: dict[str, Any]
    planned_sql: str
    planned_limited_sql: str
    row_limit: int
    start: float
    bindings: list[tuple[int, str, dict[str, Any]]]
    binding_lookup: dict[int, tuple[str, dict[str, Any]]]
    models_by_binding: dict[int, list[dict[str, Any]]]
    referenced_models: set[str] = field(default_factory=set)
    referenced_by_binding: dict[int, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionEarlyExit:
    warning: str
    routing_stage: str
    final_execution_sql: str
    plan: dict[str, Any]
    planned_sql: str
    planned_limited_sql: str
    start: float | None
    binding_lookup: dict[int, tuple[str, dict[str, Any]]] = field(default_factory=dict)
    referenced_by_binding: dict[int, list[dict[str, Any]]] = field(default_factory=dict)


class ExecutionPipeline:
    def __init__(
        self,
        *,
        plan_secured_sql: PlanSecuredSqlFn,
        binding_rows: BindingRowsFn,
        models_by_binding: ModelsByBindingFn,
        models_for_project: ModelsForProjectFn,
        normalize_sql_candidate: NormalizeSqlFn,
        apply_limit: ApplyLimitFn,
        normalize_row_limit: NormalizeRowLimitFn,
        default_sql_rows: int,
        execution_router: ExecutionRouter | None = None,
    ) -> None:
        self._plan_secured_sql = plan_secured_sql
        self._binding_rows = binding_rows
        self._models_by_binding = models_by_binding
        self._models_for_project = models_for_project
        self._normalize_sql_candidate = normalize_sql_candidate
        self._apply_limit = apply_limit
        self._normalize_row_limit = normalize_row_limit
        self._default_sql_rows = int(default_sql_rows)
        self._execution_router = execution_router or ExecutionRouter()

    def prepare(
        self,
        *,
        input_sql: str,
        project_id: int,
        user_id: int,
        limit: Optional[int],
    ) -> tuple[ExecutionPreparedPlan | None, ExecutionEarlyExit | None]:
        plan = self._plan_secured_sql(input_sql, project_id, user_id)
        planned_sql = self._normalize_sql_candidate(plan.get("planned_sql") or input_sql)
        row_limit = self._normalize_row_limit(limit) if limit is not None else self._default_sql_rows
        planned_limited_sql = self._apply_limit(planned_sql, row_limit)
        final_execution_sql = planned_limited_sql

        bindings = self._binding_rows(project_id)
        if not bindings:
            return None, ExecutionEarlyExit(
                warning="Project has no datasource bindings.",
                routing_stage="no_binding",
                final_execution_sql=final_execution_sql,
                plan=plan,
                planned_sql=planned_sql,
                planned_limited_sql=planned_limited_sql,
                start=None,
            )

        binding_lookup = {binding_id: (ds_type, props) for binding_id, ds_type, props in bindings}
        models_by_binding = self._models_by_binding(project_id)
        model_ref_case_sensitive = _coerce_bool(plan.get("model_ref_case_sensitive"), True)
        referenced_models = {
            str(model_name or "").strip()
            for model_name in (plan.get("model_refs") or [])
            if str(model_name or "").strip()
        }
        referenced_models_for_compare = (
            referenced_models
            if model_ref_case_sensitive
            else {model_name.lower() for model_name in referenced_models}
        )
        referenced_by_binding: dict[int, list[dict[str, Any]]] = {}
        for binding_id, models in models_by_binding.items():
            matched_models = []
            for model in models:
                model_name = str(model.get("name") or "").strip()
                if not model_name:
                    continue
                candidate = model_name if model_ref_case_sensitive else model_name.lower()
                if candidate in referenced_models_for_compare:
                    matched_models.append(model)
            if matched_models:
                referenced_by_binding[binding_id] = matched_models
        referenced_by_binding = {
            binding_id: models
            for binding_id, models in referenced_by_binding.items()
            if models
        }
        start = time.perf_counter()

        if not referenced_by_binding:
            all_models = self._models_for_project(project_id)
            if all_models:
                model_names_with_binding = {
                    (
                        str(model.get("name") or "").strip()
                        if model_ref_case_sensitive
                        else str(model.get("name") or "").strip().lower()
                    )
                    for model in all_models
                    if model.get("source_binding_id") is not None
                }
                model_names_without_binding = {
                    (
                        str(model.get("name") or "").strip()
                        if model_ref_case_sensitive
                        else str(model.get("name") or "").strip().lower()
                    )
                    for model in all_models
                    if model.get("source_binding_id") is None
                }
                missing_binding = referenced_models_for_compare - model_names_with_binding

                if missing_binding and model_names_without_binding:
                    warning = (
                        f"SQL references models that are not mapped to a datasource binding: {', '.join(sorted(missing_binding))}. "
                        f"Please ensure all models have a valid source_binding_id in the modeling settings."
                    )
                    return None, ExecutionEarlyExit(
                        warning=warning,
                        routing_stage="no_binding",
                        final_execution_sql=final_execution_sql,
                        plan=plan,
                        planned_sql=planned_sql,
                        planned_limited_sql=planned_limited_sql,
                        start=start,
                        binding_lookup=binding_lookup,
                        referenced_by_binding=referenced_by_binding,
                    )

            warning = (
                "SQL must reference semantic model names. Direct physical table queries are disabled for project isolation and security policy enforcement."
            )
            return None, ExecutionEarlyExit(
                warning=warning,
                routing_stage="no_binding",
                final_execution_sql=final_execution_sql,
                plan=plan,
                planned_sql=planned_sql,
                planned_limited_sql=planned_limited_sql,
                start=start,
                binding_lookup=binding_lookup,
                referenced_by_binding=referenced_by_binding,
            )

        return ExecutionPreparedPlan(
            plan=plan,
            planned_sql=planned_sql,
            planned_limited_sql=planned_limited_sql,
            row_limit=row_limit,
            start=start,
            bindings=bindings,
            binding_lookup=binding_lookup,
            models_by_binding=models_by_binding,
            referenced_models=referenced_models,
            referenced_by_binding=referenced_by_binding,
        ), None

    def build_decision(
        self,
        *,
        planned_sql: str,
        final_execution_sql: str,
        routing_stage: str,
        referenced_by_binding: dict[int, list[dict[str, Any]]],
        binding_lookup: dict[int, tuple[str, dict[str, Any]]],
        warning: str | None = None,
        model_refs: list[str] | None = None,
    ) -> ExecutionRouteDecision:
        return self._execution_router.decide(
            planned_sql=planned_sql,
            final_execution_sql=final_execution_sql,
            routing_stage=routing_stage,
            referenced_by_binding=referenced_by_binding,
            binding_lookup=binding_lookup,
            warning=warning,
            model_refs=model_refs,
        )

    def legacy_route_kind(
        self,
        referenced_by_binding: dict[int, list[dict[str, Any]]],
        binding_lookup: dict[int, tuple[str, dict[str, Any]]],
    ) -> str:
        binding_ids = sorted(int(binding_id) for binding_id in referenced_by_binding.keys())
        if not binding_ids:
            return "no_binding"
        if len(binding_ids) == 1:
            binding_id = binding_ids[0]
            ds_type = str((binding_lookup.get(binding_id) or ("", {}))[0] or "").lower()
            return "single_duckdb" if ds_type in {"duckdb", "sample"} else "single_external"
        return "cross_source"

    def shadow_diff(
        self,
        *,
        planned_sql: str,
        final_execution_sql: str,
        routing_stage: str,
        referenced_by_binding: dict[int, list[dict[str, Any]]],
        binding_lookup: dict[int, tuple[str, dict[str, Any]]],
        warning: str | None = None,
        model_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        decision = self.build_decision(
            planned_sql=planned_sql,
            final_execution_sql=final_execution_sql,
            routing_stage=routing_stage,
            referenced_by_binding=referenced_by_binding,
            binding_lookup=binding_lookup,
            warning=warning,
            model_refs=model_refs,
        )
        legacy = self.legacy_route_kind(referenced_by_binding, binding_lookup)
        return {
            "legacy_route_kind": legacy,
            "new_route_kind": decision.route_kind,
            "changed": legacy != decision.route_kind,
            "binding_ids": decision.binding_ids,
            "datasource_types": decision.datasource_types,
            "routing_stage": routing_stage,
        }
