from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import GenerationRouteDecision


@dataclass(frozen=True, slots=True)
class StrategySelection:
    engine: str
    max_retries: int
    use_examples: bool


class GenerationRouter:
    def __init__(self) -> None:
        self._strategies: dict[str, StrategySelection] = {
            "simple": StrategySelection(engine="direct_llm", max_retries=1, use_examples=True),
            "multi_dimension": StrategySelection(engine="fewshot_cot", max_retries=2, use_examples=True),
            "compound": StrategySelection(engine="decompose_merge", max_retries=3, use_examples=True),
        }

    def select_strategy(self, analysis: dict[str, Any] | None, has_knowledge: bool) -> StrategySelection:
        tier = str((analysis or {}).get("tier") or "simple").strip().lower()
        selected = self._strategies.get(tier, self._strategies["simple"])
        return StrategySelection(
            engine=selected.engine,
            max_retries=selected.max_retries,
            use_examples=bool(has_knowledge),
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
