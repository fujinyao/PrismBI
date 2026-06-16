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


def _question_analysis_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "prismbi_question_analysis",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tier", "sub_questions", "entities", "metrics", "dimensions", "filters", "reasoning"],
                "properties": {
                    "tier": {"type": "string"},
                    "sub_questions": {"type": "array", "items": {"type": "string"}},
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "metrics": {"type": "array", "items": {"type": "string"}},
                    "dimensions": {"type": "array", "items": {"type": "string"}},
                    "filters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["field", "operator", "value"],
                            "properties": {
                                "field": {"type": "string"},
                                "operator": {"type": "string"},
                                "value": {"type": "string"},
                            },
                        },
                    },
                    "reasoning": {"type": "string"},
                },
            },
        },
    }


def _semantic_link_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "prismbi_semantic_link",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["matched_models", "column_mapping", "reasoning"],
                "properties": {
                    "matched_models": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "matched_columns", "relevance"],
                            "properties": {
                                "name": {"type": "string"},
                                "matched_columns": {"type": "array", "items": {"type": "string"}},
                                "relevance": {"type": "string"},
                            },
                        },
                    },
                    "column_mapping": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["question_term", "model_name", "column_name", "confidence"],
                            "properties": {
                                "question_term": {"type": "string"},
                                "model_name": {"type": "string"},
                                "column_name": {"type": "string"},
                                "confidence": {"type": "string"},
                            },
                        },
                    },
                    "reasoning": {"type": "string"},
                },
            },
        },
    }


class PromptProfileRouter:
    def __init__(
        self,
        default_profile_id: str = "prismbi.default",
        default_profile_version: str = "v2",
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
                        "[profile prismbi.default/v1] When metadata is matched but uncertain, prefer requires_sql=true. Route conservatively to SQL path."
                    ),
                ),
                "question_analysis": PromptStageTemplate(
                    stage="question_analysis",
                    requires_structured_json=True,
                    system_suffix=(
                        "[profile prismbi.default/v1] Classify the question tier accurately. For compound questions, list each sub-question separately."
                    ),
                ),
                "semantic_link": PromptStageTemplate(
                    stage="semantic_link",
                    requires_structured_json=True,
                    system_suffix=(
                        "[profile prismbi.default/v1] Map user terms to exact metadata names. Include PK and join columns. Prefer high confidence for direct matches."
                    ),
                ),
                "sql_generation": PromptStageTemplate(
                    stage="sql_generation",
                    requires_structured_json=True,
                    system_suffix=(
                        "[profile prismbi.default/v1] Generate one executable query. Retain ALL requested dimensions in GROUP BY. Avoid non-deterministic formatting."
                    ),
                ),
                "sql_repair": PromptStageTemplate(
                    stage="sql_repair",
                    requires_structured_json=True,
                    system_suffix=(
                        "[profile prismbi.default/v1] Apply minimal changes to fix errors while preserving query intent. Return only the repaired SQL JSON."
                    ),
                ),
                "final_answer": PromptStageTemplate(
                    stage="final_answer",
                    requires_structured_json=False,
                    system_suffix=(
                        "[profile prismbi.default/v1] Base the answer primarily on SQL result evidence. Reference specific rows and values."
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
                        "[profile prismbi.default/v2] Conservative classification. ANY quantifiable business request MUST route to SQL path."
                    ),
                ),
                "question_analysis": PromptStageTemplate(
                    stage="question_analysis",
                    requires_structured_json=True,
                    system_suffix=(
                        "[profile prismbi.default/v2] Strict tier classification. ANY quantifiable request should be classified multi_dimension or compound — never non_metadata."
                    ),
                ),
                "semantic_link": PromptStageTemplate(
                    stage="semantic_link",
                    requires_structured_json=True,
                    system_suffix=(
                        "[profile prismbi.default/v2] Exhaustive column mapping. Include all PK/FK columns for JOIN resolution. Confidence should never be omitted."
                    ),
                ),
                "sql_generation": PromptStageTemplate(
                    stage="sql_generation",
                    requires_structured_json=True,
                    system_suffix=(
                        "[profile prismbi.default/v2] Full-coverage GROUP BY, single-query output, dialect correctness. Prefer flat queries over CTEs."
                    ),
                ),
                "sql_repair": PromptStageTemplate(
                    stage="sql_repair",
                    requires_structured_json=True,
                    system_suffix=(
                        "[profile prismbi.default/v2] Preserve query intent. Apply minimal modifications to make it executable."
                    ),
                ),
                "final_answer": PromptStageTemplate(
                    stage="final_answer",
                    requires_structured_json=False,
                    system_suffix=(
                        "[profile prismbi.default/v2] Explicitly note any uncertainties, warnings, or data quality issues in the result."
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
            if stage == "question_analysis":
                return _question_analysis_schema()
            if stage == "semantic_link":
                return _semantic_link_schema()
            return _sql_generation_schema()
        return "json"

    def select(
        self,
        stage: str,
        *,
        strict_json_mode: str,
        profile_id: str | None = None,
        profile_version: str | None = None,
        model_tier: str | None = None,
    ) -> PromptProfileSelection:
        profile = self.resolve(profile_id=profile_id, profile_version=profile_version)
        template = profile.stages.get(stage)
        if template is None:
            template = PromptStageTemplate(stage=stage, system_suffix="", requires_structured_json=False)
        response_format = None
        if template.requires_structured_json:
            response_format = self._structured_response_format(stage=stage, strict_json_mode=strict_json_mode)
        system_suffix = template.system_suffix
        if model_tier and model_tier != "strong":
            from services.sql_routing.llm_capability import _get_tier_system_suffix
            extra = _get_tier_system_suffix(model_tier)
            if extra:
                system_suffix = (system_suffix + extra) if system_suffix else extra
        return PromptProfileSelection(
            profile_id=profile.profile_id,
            profile_version=profile.version,
            stage=stage,
            strict_json_mode=str(strict_json_mode or "none"),
            system_suffix=system_suffix,
            response_format=response_format,
        )
