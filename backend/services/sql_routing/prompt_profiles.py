from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PromptStageTemplate:
    stage: str
    system_suffix: str
    requires_structured_json: bool = False


@dataclass(frozen=True, slots=True)
class PromptProfileVersion:
    profile_id: str
    version: str
    description: str
    stages: dict[str, PromptStageTemplate]


@dataclass(frozen=True, slots=True)
class PromptProfileSelection:
    profile_id: str
    profile_version: str
    stage: str
    strict_json_mode: str
    system_suffix: str
    response_format: Any


def _sql_generation_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "prismbi_sql_generation",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["sql", "summary", "reasoning"],
                "properties": {
                    "sql": {"type": "string"},
                    "summary": {"type": "string"},
                    "reasoning": {"type": "string"},
                },
            },
        },
    }


def _question_route_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "prismbi_question_route",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "requires_sql",
                    "metadata_question_part",
                    "non_metadata_question_part",
                    "reasoning",
                ],
                "properties": {
                    "requires_sql": {"type": "boolean"},
                    "metadata_question_part": {"type": "string"},
                    "non_metadata_question_part": {"type": "string"},
                    "reasoning": {"type": "string"},
                },
            },
        },
    }


class PromptProfileRouter:
    def __init__(
        self,
        default_profile_id: str = "prismbi.default",
        default_profile_version: str = "v1",
    ) -> None:
        self._default_profile_id = default_profile_id
        self._default_profile_version = default_profile_version
        self._profiles: dict[tuple[str, str], PromptProfileVersion] = {}
        self._load_defaults()

    def _load_defaults(self) -> None:
        v1 = PromptProfileVersion(
            profile_id="prismbi.default",
            version="v1",
            description="Baseline SQL routing profile with strict contracts.",
            stages={
                "question_categorization": PromptStageTemplate(
                    stage="question_categorization",
                    requires_structured_json=True,
                    system_suffix=(
                        "Prompt profile prismbi.default/v1: prioritize deterministic routing. "
                        "When metadata is matched but uncertain, prefer requires_sql=true."
                    ),
                ),
                "sql_generation": PromptStageTemplate(
                    stage="sql_generation",
                    requires_structured_json=True,
                    system_suffix=(
                        "Prompt profile prismbi.default/v1: keep one executable query, "
                        "preserve requested dimensions, and avoid non-deterministic formatting."
                    ),
                ),
                "sql_repair": PromptStageTemplate(
                    stage="sql_repair",
                    requires_structured_json=True,
                    system_suffix=(
                        "Prompt profile prismbi.default/v1: return only the repaired SQL JSON object."
                    ),
                ),
                "final_answer": PromptStageTemplate(
                    stage="final_answer",
                    requires_structured_json=False,
                    system_suffix=(
                        "Prompt profile prismbi.default/v1: summarize using query evidence first."
                    ),
                ),
            },
        )
        v2 = PromptProfileVersion(
            profile_id="prismbi.default",
            version="v2",
            description="Stricter profile with stronger multi-dimension enforcement.",
            stages={
                "question_categorization": PromptStageTemplate(
                    stage="question_categorization",
                    requires_structured_json=True,
                    system_suffix=(
                        "Prompt profile prismbi.default/v2: classify conservatively. "
                        "Any measurable business request must stay in SQL path."
                    ),
                ),
                "sql_generation": PromptStageTemplate(
                    stage="sql_generation",
                    requires_structured_json=True,
                    system_suffix=(
                        "Prompt profile prismbi.default/v2: prioritize full-dimension GROUP BY coverage, "
                        "single-query output, and dialect correctness."
                    ),
                ),
                "sql_repair": PromptStageTemplate(
                    stage="sql_repair",
                    requires_structured_json=True,
                    system_suffix=(
                        "Prompt profile prismbi.default/v2: preserve intent and apply minimal edits needed for execution."
                    ),
                ),
                "final_answer": PromptStageTemplate(
                    stage="final_answer",
                    requires_structured_json=False,
                    system_suffix=(
                        "Prompt profile prismbi.default/v2: explicitly call out uncertainty or warnings from execution."
                    ),
                ),
            },
        )
        for profile in (v1, v2):
            self.register(profile)

    def register(self, profile: PromptProfileVersion) -> None:
        key = (profile.profile_id, profile.version)
        self._profiles[key] = profile

    def resolve(self, profile_id: str | None = None, profile_version: str | None = None) -> PromptProfileVersion:
        requested_id = str(profile_id or self._default_profile_id)
        requested_version = str(profile_version or self._default_profile_version)
        key = (requested_id, requested_version)
        if key in self._profiles:
            return self._profiles[key]
        fallback_key = (self._default_profile_id, self._default_profile_version)
        if fallback_key in self._profiles:
            return self._profiles[fallback_key]
        return next(iter(self._profiles.values()))

    def _structured_response_format(self, stage: str, strict_json_mode: str) -> Any:
        mode = str(strict_json_mode or "").strip().lower()
        if mode == "json_schema":
            if stage == "question_categorization":
                return _question_route_schema()
            return _sql_generation_schema()
        return "json"

    def select(
        self,
        stage: str,
        *,
        strict_json_mode: str,
        profile_id: str | None = None,
        profile_version: str | None = None,
    ) -> PromptProfileSelection:
        profile = self.resolve(profile_id=profile_id, profile_version=profile_version)
        template = profile.stages.get(stage)
        if template is None:
            template = PromptStageTemplate(stage=stage, system_suffix="", requires_structured_json=False)
        response_format = None
        if template.requires_structured_json:
            response_format = self._structured_response_format(stage=stage, strict_json_mode=strict_json_mode)
        return PromptProfileSelection(
            profile_id=profile.profile_id,
            profile_version=profile.version,
            stage=stage,
            strict_json_mode=str(strict_json_mode or "none"),
            system_suffix=template.system_suffix,
            response_format=response_format,
        )
