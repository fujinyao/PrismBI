from __future__ import annotations

from typing import Any

from .contracts import ExecutionRouteDecision


class ExecutionRouter:
    def decide(
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
        binding_ids = sorted(int(binding_id) for binding_id in referenced_by_binding.keys())
        datasource_types: list[str] = []
        for binding_id in binding_ids:
            entry = binding_lookup.get(binding_id)
            if not entry:
                continue
            datasource_types.append(str(entry[0] or "unknown").lower())

        if not binding_ids:
            route_kind = "no_binding"
        elif len(binding_ids) == 1:
            ds_type = datasource_types[0] if datasource_types else "unknown"
            route_kind = "single_duckdb" if ds_type in {"duckdb", "sample"} else "single_external"
        else:
            route_kind = "cross_source"

        return ExecutionRouteDecision(
            route_kind=route_kind,
            binding_ids=binding_ids,
            datasource_types=datasource_types,
            planned_sql=str(planned_sql or ""),
            final_execution_sql=str(final_execution_sql or planned_sql or ""),
            routing_stage=str(routing_stage or "unknown"),
            warning=str(warning or "") or None,
            model_refs=list(model_refs or []),
        )
