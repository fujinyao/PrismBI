from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


NormalizeQuestionAnalysisFn = Callable[[dict[str, Any] | None], dict[str, Any]]
SemanticPromptFn = Callable[..., tuple[str, list[str], dict[str, Any]]]
ResolveAnalysisToSchemaFn = Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any]]
PruneSchemaFn = Callable[..., tuple[list[dict[str, Any]], list[dict[str, Any]]]]
ReformatSchemaContextFn = Callable[[list[dict[str, Any]], list[dict[str, Any]], list[str], str, bool], str]
BuildSchemaLinkingPlanFn = Callable[[str, dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]
BuildSqlPlanningArtifactFn = Callable[[str, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]
SelectSqlStrategyFn = Callable[[dict[str, Any], bool], dict[str, Any]]
EstimateComplexityFn = Callable[[dict[str, Any], dict[str, Any]], int]
StrictJsonCapabilityFn = Callable[[], dict[str, Any]]
PromptProfileSelectionFn = Callable[..., Any]
IsSqlRouteV2EnabledFn = Callable[[int | None], bool]
ResolveModelTierFn = Callable[[], str | None]


@dataclass(slots=True)
class GenerationPreparation:
    analysis: dict[str, Any]
    semantic_context: str
    retrieved_tables: list[str]
    semantic_hits: dict[str, Any]
    resolved: dict[str, Any]
    schema_link_plan: dict[str, Any]
    sql_plan: dict[str, Any]
    strategy: dict[str, Any]
    engine_label: str
    max_retries: int
    response_format: Any
    system_suffix: str


class GenerationPipeline:
    def __init__(
        self,
        *,
        normalize_question_analysis: NormalizeQuestionAnalysisFn,
        semantic_prompt: SemanticPromptFn,
        resolve_analysis_to_schema: ResolveAnalysisToSchemaFn,
        prune_schema: PruneSchemaFn,
        reformat_schema_context: ReformatSchemaContextFn,
        build_schema_linking_plan: BuildSchemaLinkingPlanFn,
        build_sql_planning_artifact: BuildSqlPlanningArtifactFn,
        select_sql_strategy: SelectSqlStrategyFn,
        estimate_sql_generation_complexity: EstimateComplexityFn,
        strict_json_capability: StrictJsonCapabilityFn,
        prompt_profile_selection: PromptProfileSelectionFn,
        is_sql_route_v2_enabled: IsSqlRouteV2EnabledFn,
        resolve_model_tier: ResolveModelTierFn | None = None,
    ) -> None:
        self._normalize_question_analysis = normalize_question_analysis
        self._semantic_prompt = semantic_prompt
        self._resolve_analysis_to_schema = resolve_analysis_to_schema
        self._prune_schema = prune_schema
        self._reformat_schema_context = reformat_schema_context
        self._build_schema_linking_plan = build_schema_linking_plan
        self._build_sql_planning_artifact = build_sql_planning_artifact
        self._select_sql_strategy = select_sql_strategy
        self._estimate_sql_generation_complexity = estimate_sql_generation_complexity
        self._strict_json_capability = strict_json_capability
        self._prompt_profile_selection = prompt_profile_selection
        self._is_sql_route_v2_enabled = is_sql_route_v2_enabled
        self._resolve_model_tier = resolve_model_tier

    @staticmethod
    def legacy_engine(analysis: dict[str, Any], has_knowledge: bool) -> str:
        tier = str((analysis or {}).get("tier") or "simple").strip().lower()
        if tier == "compound":
            return "decompose_merge"
        if tier == "multi_dimension":
            return "fewshot_cot"
        return "direct_llm"

    @staticmethod
    def normalize_engine(engine: str) -> str:
        value = str(engine or "").strip().lower()
        if value.startswith("decompose_merge"):
            return "decompose_merge"
        if value.startswith("fewshot_cot"):
            return "fewshot_cot"
        if value.startswith("direct_llm"):
            return "direct_llm"
        if value.startswith("llm_fallback"):
            return "direct_llm"
        return value or "unknown"

    def shadow_diff(
        self,
        *,
        analysis: dict[str, Any],
        has_knowledge: bool,
        generation_engine: str,
    ) -> dict[str, Any]:
        normalized_analysis = self._normalize_question_analysis(analysis)
        legacy = self.legacy_engine(normalized_analysis, has_knowledge)
        new = self.normalize_engine(generation_engine)
        return {
            "legacy_generation_engine": legacy,
            "new_generation_engine": new,
            "changed": legacy != new,
            "analysis_tier": str(normalized_analysis.get("tier") or "simple"),
            "has_knowledge": bool(has_knowledge),
        }

    def prepare_context(
        self,
        *,
        question: str,
        project_id: int,
        semantic_context: str | None,
        retrieved_tables: list[str] | None,
        semantic_hits: dict[str, Any] | None,
        knowledge_context: str | None,
        analysis: dict[str, Any] | None,
        router_config: dict[str, Any],
    ) -> tuple[GenerationPreparation | None, dict[str, Any] | None]:
        normalized_analysis = self._normalize_question_analysis(analysis)

        model_tier: str | None = None
        if callable(self._resolve_model_tier):
            try:
                resolved_tier = self._resolve_model_tier()
                if resolved_tier:
                    model_tier = str(resolved_tier)
            except Exception:
                model_tier = None

        strict_json = self._strict_json_capability()
        try:
            prompt_selection = self._prompt_profile_selection(
                "sql_generation",
                strict_json_mode=str(strict_json.get("mode") or "none"),
                model_tier=model_tier,
            )
        except TypeError:
            prompt_selection = self._prompt_profile_selection(
                "sql_generation",
                strict_json_mode=str(strict_json.get("mode") or "none"),
            )
        route_v2_enabled = self._is_sql_route_v2_enabled(project_id)
        shadow_mode = bool(router_config.get("sql_route_shadow_mode", False))
        use_profile = route_v2_enabled or shadow_mode
        response_format = (
            prompt_selection.response_format
            if use_profile and prompt_selection.response_format is not None
            else "json"
        )
        system_suffix = (
            f"\n<PROFILE>{getattr(prompt_selection, 'system_suffix', '')}</PROFILE>"
            if use_profile and getattr(prompt_selection, "system_suffix", "")
            else ""
        )

        if semantic_context is None or retrieved_tables is None or semantic_hits is None:
            semantic_context, retrieved_tables, semantic_hits = self._semantic_prompt(
                project_id,
                question,
                require_hits=True,
                analysis=normalized_analysis,
            )

        if not semantic_hits.get("has_hits"):
            return None, {
                "sql": None,
                "summary": "No project metadata matched this question, so SQL generation was skipped.",
                "reasoning": "No metadata hit.",
                "retrieved_tables": [],
                "configured": True,
                "sql_engine": "not_applicable",
            }

        resolved: dict[str, Any] = {}
        if normalized_analysis and semantic_hits.get("models"):
            resolved = self._resolve_analysis_to_schema(normalized_analysis, semantic_hits["models"])

        if (
            bool(router_config.get("schema_pruning_enabled", True))
            and normalized_analysis
            and semantic_hits.get("models")
        ):
            tier = str(normalized_analysis.get("tier") or "simple")
            key = "tier3" if tier == "compound" else "tier2" if tier == "multi_dimension" else "tier1"
            try:
                max_cols = int(router_config.get(f"{key}_max_columns_per_model", 15) or 15)
            except Exception:
                max_cols = 15
            pruned_models, pruned_relations = self._prune_schema(
                semantic_hits["models"],
                semantic_hits.get("relations", []),
                normalized_analysis,
                resolved,
                max_columns_per_model=max_cols,
                column_mapping=semantic_hits.get("column_mapping"),
            )
            if pruned_models:
                semantic_hits = {
                    **semantic_hits,
                    "models": pruned_models,
                    "relations": pruned_relations,
                }
                resolved = self._resolve_analysis_to_schema(normalized_analysis, pruned_models)
                semantic_context = self._reformat_schema_context(
                    pruned_models,
                    pruned_relations,
                    retrieved_tables,
                    question,
                    True,
                )

        schema_link_plan = self._build_schema_linking_plan(
            question,
            normalized_analysis,
            semantic_hits,
            resolved,
        )
        sql_plan = self._build_sql_planning_artifact(
            question,
            normalized_analysis,
            semantic_hits,
            resolved,
            schema_link_plan,
        )

        strategy = self._select_sql_strategy(normalized_analysis, bool(knowledge_context))
        engine_label = str(strategy.get("engine") or "direct_llm")

        tier = str(normalized_analysis.get("tier") or "simple")
        configured_max_retries = {
            "compound": int(router_config.get("tier3_max_retries", 3) or 3),
            "multi_dimension": int(router_config.get("tier2_max_retries", 2) or 2),
        }.get(tier, int(router_config.get("tier1_max_retries", 1) or 1))
        try:
            strategy_max_retries = int(strategy.get("max_retries") or 0)
        except Exception:
            strategy_max_retries = 0
        if strategy_max_retries > 0:
            max_retries = min(strategy_max_retries, configured_max_retries)
        else:
            max_retries = configured_max_retries
        complexity = int(self._estimate_sql_generation_complexity(normalized_analysis, semantic_hits) or 0)
        if complexity <= 1:
            max_retries = min(max_retries, 1)
        elif complexity <= 3:
            max_retries = min(max_retries, 2)

        return (
            GenerationPreparation(
                analysis=normalized_analysis,
                semantic_context=semantic_context,
                retrieved_tables=list(retrieved_tables or []),
                semantic_hits=semantic_hits,
                resolved=resolved,
                schema_link_plan=schema_link_plan,
                sql_plan=sql_plan,
                strategy=strategy,
                engine_label=engine_label,
                max_retries=max_retries,
                response_format=response_format,
                system_suffix=system_suffix,
            ),
            None,
        )
