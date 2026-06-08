from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class AskInput:
    question: str
    user_id: int
    project_id: int
    thread_id: int | None
    previous_questions: list[str] = field(default_factory=list)
    previous_answers: list[str] = field(default_factory=list)
    language: str | None = None
    preview_row_limit: int = 20

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MetadataHitContext:
    semantic_context: str
    retrieved_tables: list[str]
    semantic_hits: dict[str, Any]
    knowledge_context: str
    knowledge_hits: dict[str, Any]
    analysis: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GenerationRouteDecision:
    requires_sql: bool
    metadata_question_part: str
    non_metadata_question_part: str
    generation_engine: str
    prompt_profile_id: str
    prompt_profile_version: str
    strict_json_mode: str
    reasoning: str
    analysis_tier: str
    fallback_chain: list[str] = field(default_factory=list)

    def to_audit_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fallback_count"] = len(self.fallback_chain)
        return payload


@dataclass(slots=True)
class ExecutionRouteDecision:
    route_kind: str
    binding_ids: list[int]
    datasource_types: list[str]
    planned_sql: str
    final_execution_sql: str
    routing_stage: str
    warning: str | None = None
    model_refs: list[str] = field(default_factory=list)

    def to_audit_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["binding_count"] = len(self.binding_ids)
        payload["datasource_count"] = len(self.datasource_types)
        return payload
