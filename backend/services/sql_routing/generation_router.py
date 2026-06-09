from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .contracts import GenerationRouteDecision


@dataclass(frozen=True, slots=True)
class StrategySelection:
    engine: str
    max_retries: int
    use_examples: bool
    mode: str = "legacy_tier"
    policy: str = "tier_default"
    risk_score: int = 0
    risk_level: str = "low"
    signals: dict[str, int] = field(default_factory=dict)


class GenerationRouter:
    def __init__(self, config_getter: Callable[[], dict[str, Any]] | None = None) -> None:
        self._config_getter = config_getter
        self._strategies: dict[str, StrategySelection] = {
            "simple": StrategySelection(engine="direct_llm", max_retries=1, use_examples=True),
            "multi_dimension": StrategySelection(engine="fewshot_cot", max_retries=2, use_examples=True),
            "compound": StrategySelection(engine="decompose_merge", max_retries=3, use_examples=True),
        }

    @staticmethod
    def _coerce_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return default

    @staticmethod
    def _coerce_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except Exception:
            parsed = int(default)
        if parsed < minimum:
            return minimum
        if parsed > maximum:
            return maximum
        return parsed

    @staticmethod
    def _normalized_count(values: Any) -> int:
        if not isinstance(values, list):
            return 0
        return sum(1 for item in values if str(item or "").strip())

    def _runtime_config(self) -> dict[str, Any]:
        if not callable(self._config_getter):
            return {}
        try:
            config = self._config_getter()
            return config if isinstance(config, dict) else {}
        except Exception:
            return {}

    def select_strategy(self, analysis: dict[str, Any] | None, has_knowledge: bool) -> StrategySelection:
        normalized = analysis if isinstance(analysis, dict) else {}
        tier = str(normalized.get("tier") or "simple").strip().lower()
        if tier not in self._strategies:
            tier = "simple"

        config = self._runtime_config()
        adaptive_enabled = self._coerce_bool(config.get("adaptive_strategy_enabled", True), True)
        decompose_enabled = self._coerce_bool(config.get("decompose_merge_enabled", True), True)
        consensus_threshold = self._coerce_int(
            config.get("adaptive_strategy_consensus_risk_threshold", 4),
            4,
            1,
            20,
        )
        decompose_threshold = self._coerce_int(
            config.get("adaptive_strategy_decompose_risk_threshold", 7),
            7,
            1,
            20,
        )
        if decompose_threshold < consensus_threshold:
            decompose_threshold = consensus_threshold
        min_sub_questions_for_decompose = self._coerce_int(
            config.get("adaptive_strategy_min_subquestions_for_decompose", 2),
            2,
            1,
            10,
        )

        sub_question_count = self._normalized_count(normalized.get("sub_questions"))
        dimension_count = self._normalized_count(normalized.get("dimensions"))
        metric_count = self._normalized_count(normalized.get("metrics"))
        filter_count = self._normalized_count(normalized.get("filters"))

        clause_routing = normalized.get("clause_routing") if isinstance(normalized.get("clause_routing"), dict) else {}
        metadata_clause_count = self._coerce_int(clause_routing.get("metadata_clause_count", 0), 0, 0, 20)
        non_metadata_clause_count = self._coerce_int(clause_routing.get("non_metadata_clause_count", 0), 0, 0, 20)
        mixed_clauses = bool(clause_routing.get("mixed"))

        tier_risk = {"simple": 0, "multi_dimension": 2, "compound": 4}.get(tier, 0)
        sub_question_risk = 3 if sub_question_count >= 2 else 1 if sub_question_count == 1 else 0
        dimension_risk = 3 if dimension_count >= 3 else 2 if dimension_count == 2 else 1 if dimension_count == 1 else 0
        metric_risk = 1 if metric_count >= 2 else 0
        filter_risk = 1 if filter_count >= 2 else 0
        clause_risk = 2 if metadata_clause_count >= 3 else 1 if metadata_clause_count == 2 else 0
        mixed_clause_risk = 2 if mixed_clauses else 0
        no_knowledge_risk = 0 if has_knowledge else 1

        risk_score = (
            tier_risk
            + sub_question_risk
            + dimension_risk
            + metric_risk
            + filter_risk
            + clause_risk
            + mixed_clause_risk
            + no_knowledge_risk
        )

        selected_key = tier
        policy = "tier_default"
        mode = "legacy_tier"
        if adaptive_enabled:
            mode = "adaptive_risk"
            decompose_eligible = (
                sub_question_count >= min_sub_questions_for_decompose
                and (
                    tier == "compound"
                    or dimension_count >= 3
                    or metadata_clause_count >= min_sub_questions_for_decompose
                    or mixed_clauses
                )
            )
            if decompose_eligible and risk_score >= decompose_threshold:
                selected_key = "compound"
                policy = "risk_decompose_merge"
            elif risk_score >= consensus_threshold or tier in {"multi_dimension", "compound"} or dimension_count >= 2:
                selected_key = "multi_dimension"
                policy = "risk_consensus_fewshot"
            else:
                selected_key = "simple"
                policy = "risk_constrained_direct"

        if selected_key == "compound" and not decompose_enabled:
            should_use_fewshot = (
                tier in {"compound", "multi_dimension"}
                or sub_question_count >= min_sub_questions_for_decompose
                or dimension_count >= 2
                or metadata_clause_count >= 2
                or mixed_clauses
            )
            if should_use_fewshot:
                selected_key = "multi_dimension"
                policy = "decompose_disabled_fewshot"
            else:
                selected_key = "simple"
                policy = "decompose_disabled_direct"

        risk_level = "high" if risk_score >= decompose_threshold else "medium" if risk_score >= consensus_threshold else "low"
        selected = self._strategies.get(selected_key, self._strategies["simple"])
        signals: dict[str, int] = {
            "sub_questions": sub_question_count,
            "dimensions": dimension_count,
            "metrics": metric_count,
            "filters": filter_count,
            "metadata_clauses": metadata_clause_count,
            "non_metadata_clauses": non_metadata_clause_count,
            "mixed_clauses": 1 if mixed_clauses else 0,
            "has_knowledge": 1 if has_knowledge else 0,
            "tier_risk": tier_risk,
            "sub_question_risk": sub_question_risk,
            "dimension_risk": dimension_risk,
            "metric_risk": metric_risk,
            "filter_risk": filter_risk,
            "clause_risk": clause_risk,
            "mixed_clause_risk": mixed_clause_risk,
            "no_knowledge_risk": no_knowledge_risk,
            "consensus_threshold": consensus_threshold,
            "decompose_threshold": decompose_threshold,
            "decompose_min_sub_questions": min_sub_questions_for_decompose,
            "decompose_enabled": 1 if decompose_enabled else 0,
        }
        return StrategySelection(
            engine=selected.engine,
            max_retries=selected.max_retries,
            use_examples=bool(has_knowledge),
            mode=mode,
            policy=policy,
            risk_score=risk_score,
            risk_level=risk_level,
            signals=signals,
        )

    def build_decision(
        self,
        *,
        requires_sql: bool,
        metadata_question_part: str,
        non_metadata_question_part: str,
        generation_engine: str,
        prompt_profile_id: str,
        prompt_profile_version: str,
        strict_json_mode: str,
        reasoning: str,
        analysis_tier: str,
        fallback_chain: list[str] | None = None,
    ) -> GenerationRouteDecision:
        return GenerationRouteDecision(
            requires_sql=bool(requires_sql),
            metadata_question_part=str(metadata_question_part or ""),
            non_metadata_question_part=str(non_metadata_question_part or ""),
            generation_engine=str(generation_engine or "direct_llm"),
            prompt_profile_id=str(prompt_profile_id or "prismbi.default"),
            prompt_profile_version=str(prompt_profile_version or "v1"),
            strict_json_mode=str(strict_json_mode or "none"),
            reasoning=str(reasoning or ""),
            analysis_tier=str(analysis_tier or "simple"),
            fallback_chain=list(fallback_chain or []),
        )
