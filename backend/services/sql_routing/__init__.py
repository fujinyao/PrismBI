from __future__ import annotations

from .contracts import (
    AskInput,
    ExecutionRouteDecision,
    GenerationRouteDecision,
    MetadataHitContext,
)
from .candidate_guard import CandidateGuard, CandidateValidationResult
from .datasource_registry import (
    DEFAULT_DATASOURCE_REGISTRY,
    apply_limit_for_datasource,
    dialect_for_datasource,
    normalize_datasource_type,
)
from .execution_router import ExecutionRouter
from .execution_pipeline import ExecutionPipeline, ExecutionPreparedPlan, ExecutionEarlyExit
from .generation_pipeline import GenerationPipeline, GenerationPreparation
from .generation_router import GenerationRouter
from .llm_capability import get_strict_json_capability
from .prompt_profiles import PromptProfileRouter

__all__ = [
    "AskInput",
    "MetadataHitContext",
    "GenerationRouteDecision",
    "ExecutionRouteDecision",
    "CandidateGuard",
    "CandidateValidationResult",
    "DEFAULT_DATASOURCE_REGISTRY",
    "normalize_datasource_type",
    "dialect_for_datasource",
    "apply_limit_for_datasource",
    "GenerationRouter",
    "ExecutionRouter",
    "ExecutionPipeline",
    "ExecutionPreparedPlan",
    "ExecutionEarlyExit",
    "GenerationPipeline",
    "GenerationPreparation",
    "PromptProfileRouter",
    "get_strict_json_capability",
]
