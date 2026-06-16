from __future__ import annotations

import json

from services.sql_routing.datasource_registry import (
    DEFAULT_DATASOURCE_REGISTRY,
    apply_limit_for_datasource,
    dialect_for_datasource,
    normalize_datasource_type,
)
from services.sql_routing.candidate_guard import CandidateGuard
from services.sql_routing.execution_pipeline import ExecutionPipeline
from services.sql_routing.execution_router import ExecutionRouter
from services.sql_routing.generation_pipeline import GenerationPipeline
from services.sql_routing.generation_router import GenerationRouter
from services.sql_routing.llm_capability import get_strict_json_capability
from services.sql_routing.prompt_profiles import PromptProfileRouter


def test_datasource_registry_covers_frontend_types():
    canonical = set(DEFAULT_DATASOURCE_REGISTRY.all_canonical_types())
    expected = {
        "postgresql",
        "redshift",
        "mysql",
        "clickhouse",
        "mssql",
        "trino",
        "athena",
        "oracle",
        "snowflake",
        "bigquery",
        "databricks",
        "duckdb",
        "sample",
    }
    assert expected.issubset(canonical)


def test_datasource_registry_normalizes_aliases_and_dialects():
    assert normalize_datasource_type("postgres") == "postgresql"
    assert normalize_datasource_type("mariadb") == "mysql"
    assert normalize_datasource_type("sqlserver") == "mssql"
    assert normalize_datasource_type("redshift") == "redshift"

    assert dialect_for_datasource("postgres") == "postgres"
    assert dialect_for_datasource("redshift") == "redshift"
    assert dialect_for_datasource("sqlserver") == "tsql"


def test_apply_limit_for_datasource_uses_style_rules():
    mssql_sql = apply_limit_for_datasource("SELECT * FROM orders", "mssql", 5)
    oracle_sql = apply_limit_for_datasource("SELECT * FROM orders", "oracle", 5)
    mysql_sql = apply_limit_for_datasource("SELECT * FROM orders", "mysql", 5)

    assert "SELECT TOP 5" in mssql_sql
    assert "FETCH FIRST 5 ROWS ONLY" in oracle_sql
    assert "LIMIT 5" in mysql_sql


def test_prompt_profile_router_selects_json_schema_for_structured_stage():
    router = PromptProfileRouter(default_profile_version="v2")

    sql_generation = router.select("sql_generation", strict_json_mode="json_schema")
    final_answer = router.select("final_answer", strict_json_mode="json_schema")

    assert sql_generation.profile_id == "prismbi.default"
    assert sql_generation.profile_version == "v2"
    assert isinstance(sql_generation.response_format, dict)
    assert sql_generation.response_format.get("type") == "json_schema"

    assert final_answer.response_format is None
    assert "[profile prismbi.default/v2]" in final_answer.system_suffix


def test_generation_router_decisions_by_tier():
    router = GenerationRouter()

    simple = router.select_strategy({"tier": "simple"}, has_knowledge=False)
    compound = router.select_strategy(
        {
            "tier": "compound",
            "sub_questions": ["top products", "city breakdown"],
            "dimensions": ["product", "city"],
            "metrics": ["sales", "orders"],
        },
        has_knowledge=True,
    )
    decision = router.build_decision(
        requires_sql=True,
        metadata_question_part="m",
        non_metadata_question_part="",
        generation_engine="decompose_merge",
        prompt_profile_id="prismbi.default",
        prompt_profile_version="v2",
        strict_json_mode="json_schema",
        reasoning="ok",
        analysis_tier="compound",
        fallback_chain=["decompose_merge", "direct_llm"],
    )

    assert simple.engine == "direct_llm"
    assert simple.use_examples is False
    assert simple.mode == "adaptive_risk"
    assert simple.policy == "risk_constrained_direct"
    assert compound.engine == "decompose_merge"
    assert compound.mode == "adaptive_risk"
    assert compound.policy == "risk_decompose_merge"
    assert compound.risk_level == "high"
    assert decision.to_audit_payload()["fallback_count"] == 2


def test_generation_router_adaptive_avoids_decompose_when_subquestions_do_not_support_it():
    router = GenerationRouter()

    selection = router.select_strategy(
        {
            "tier": "compound",
            "sub_questions": ["top products"],
            "dimensions": ["product", "city"],
            "metrics": ["sales"],
        },
        has_knowledge=True,
    )

    assert selection.engine == "fewshot_cot"
    assert selection.mode == "adaptive_risk"
    assert selection.policy == "risk_consensus_fewshot"


def test_generation_router_can_disable_adaptive_strategy_mode():
    router = GenerationRouter(config_getter=lambda: {"adaptive_strategy_enabled": False})

    selection = router.select_strategy({"tier": "compound"}, has_knowledge=True)

    assert selection.engine == "decompose_merge"
    assert selection.mode == "legacy_tier"
    assert selection.policy == "tier_default"


def test_generation_router_decompose_toggle_redirects_compound_strategy():
    router = GenerationRouter(config_getter=lambda: {"decompose_merge_enabled": False})

    selection = router.select_strategy(
        {
            "tier": "compound",
            "sub_questions": ["top products", "city breakdown"],
            "dimensions": ["product", "city"],
        },
        has_knowledge=True,
    )

    assert selection.engine == "fewshot_cot"
    assert selection.policy == "decompose_disabled_fewshot"
    assert selection.signals.get("decompose_enabled") == 0


def test_generation_pipeline_prepare_context_with_no_hits_returns_early_result():
    profile_router = PromptProfileRouter(default_profile_version="v2")
    pipeline = GenerationPipeline(
        normalize_question_analysis=lambda analysis: analysis or {"tier": "simple"},
        semantic_prompt=lambda *_args, **_kwargs: ("", [], {"has_hits": False, "models": [], "relations": []}),
        resolve_analysis_to_schema=lambda _analysis, _models: {},
        prune_schema=lambda models, relations, *_args, **_kwargs: (models, relations),
        reformat_schema_context=lambda *_args, **_kwargs: "",
        build_schema_linking_plan=lambda *_args, **_kwargs: {},
        build_sql_planning_artifact=lambda *_args, **_kwargs: {},
        select_sql_strategy=lambda _analysis, _has_knowledge: {"engine": "direct_llm", "max_retries": 1, "use_examples": False},
        estimate_sql_generation_complexity=lambda _analysis, _semantic_hits: 1,
        strict_json_capability=lambda: {"supported": True, "mode": "json_schema"},
        prompt_profile_selection=lambda stage, strict_json_mode: profile_router.select(stage, strict_json_mode=strict_json_mode),
        is_sql_route_v2_enabled=lambda _project_id: True,
    )

    prepared, early = pipeline.prepare_context(
        question="no match question",
        project_id=1,
        semantic_context=None,
        retrieved_tables=None,
        semantic_hits=None,
        knowledge_context=None,
        analysis={"tier": "simple"},
        router_config={"sql_route_shadow_mode": False},
    )

    assert prepared is None
    assert early is not None
    assert early["sql_engine"] == "not_applicable"


def test_generation_pipeline_prepare_context_builds_prepared_state():
    profile_router = PromptProfileRouter(default_profile_version="v2")
    semantic_hits = {
        "has_hits": True,
        "models": [{"name": "orders", "columns": [{"name": "order_id"}], "matched_columns": []}],
        "relations": [],
    }

    pipeline = GenerationPipeline(
        normalize_question_analysis=lambda analysis: analysis or {"tier": "multi_dimension", "dimensions": ["city"]},
        semantic_prompt=lambda *_args, **_kwargs: ("schema context", ["orders"], semantic_hits),
        resolve_analysis_to_schema=lambda _analysis, _models: {},
        prune_schema=lambda models, relations, *_args, **_kwargs: (models, relations),
        reformat_schema_context=lambda *_args, **_kwargs: "schema context",
        build_schema_linking_plan=lambda *_args, **_kwargs: {"owner_preferences": []},
        build_sql_planning_artifact=lambda *_args, **_kwargs: {"steps": []},
        select_sql_strategy=lambda _analysis, _has_knowledge: {"engine": "fewshot_cot", "max_retries": 2, "use_examples": True},
        estimate_sql_generation_complexity=lambda _analysis, _semantic_hits: 1,
        strict_json_capability=lambda: {"supported": True, "mode": "json_schema"},
        prompt_profile_selection=lambda stage, strict_json_mode: profile_router.select(stage, strict_json_mode=strict_json_mode),
        is_sql_route_v2_enabled=lambda _project_id: True,
    )

    prepared, early = pipeline.prepare_context(
        question="sales by city",
        project_id=1,
        semantic_context=None,
        retrieved_tables=None,
        semantic_hits=None,
        knowledge_context="examples",
        analysis={"tier": "multi_dimension", "dimensions": ["city"]},
        router_config={
            "sql_route_shadow_mode": False,
            "schema_pruning_enabled": True,
            "tier1_max_retries": 1,
            "tier2_max_retries": 2,
            "tier3_max_retries": 3,
            "tier1_max_columns_per_model": 12,
            "tier2_max_columns_per_model": 15,
            "tier3_max_columns_per_model": 20,
        },
    )

    assert early is None
    assert prepared is not None
    assert prepared.engine_label == "fewshot_cot"
    assert prepared.max_retries == 1
    assert isinstance(prepared.response_format, dict)
    assert prepared.response_format.get("type") == "json_schema"


def test_generation_pipeline_prepare_context_passes_model_tier_to_profile_selection():
    profile_router = PromptProfileRouter(default_profile_version="v2")
    semantic_hits = {
        "has_hits": True,
        "models": [{"name": "orders", "columns": [{"name": "order_id"}], "matched_columns": []}],
        "relations": [],
    }
    captured: dict[str, str | None] = {"model_tier": None}

    def _profile_selection(stage, strict_json_mode, model_tier=None):
        captured["model_tier"] = model_tier
        return profile_router.select(stage, strict_json_mode=strict_json_mode, model_tier=model_tier)

    pipeline = GenerationPipeline(
        normalize_question_analysis=lambda analysis: analysis or {"tier": "simple"},
        semantic_prompt=lambda *_args, **_kwargs: ("schema context", ["orders"], semantic_hits),
        resolve_analysis_to_schema=lambda _analysis, _models: {},
        prune_schema=lambda models, relations, *_args, **_kwargs: (models, relations),
        reformat_schema_context=lambda *_args, **_kwargs: "schema context",
        build_schema_linking_plan=lambda *_args, **_kwargs: {},
        build_sql_planning_artifact=lambda *_args, **_kwargs: {},
        select_sql_strategy=lambda _analysis, _has_knowledge: {"engine": "direct_llm", "max_retries": 1, "use_examples": False},
        estimate_sql_generation_complexity=lambda _analysis, _semantic_hits: 2,
        strict_json_capability=lambda: {"supported": True, "mode": "json_schema"},
        prompt_profile_selection=_profile_selection,
        is_sql_route_v2_enabled=lambda _project_id: True,
        resolve_model_tier=lambda: "weak",
    )

    prepared, early = pipeline.prepare_context(
        question="sales",
        project_id=1,
        semantic_context=None,
        retrieved_tables=None,
        semantic_hits=None,
        knowledge_context=None,
        analysis={"tier": "simple"},
        router_config={"sql_route_shadow_mode": False, "schema_pruning_enabled": False, "tier1_max_retries": 2},
    )

    assert early is None
    assert prepared is not None
    assert captured["model_tier"] == "weak"
    assert "CRITICAL - Output ONLY valid JSON" in prepared.system_suffix


def test_generation_pipeline_shadow_diff_detects_engine_change():
    profile_router = PromptProfileRouter(default_profile_version="v2")
    pipeline = GenerationPipeline(
        normalize_question_analysis=lambda analysis: analysis or {"tier": "simple"},
        semantic_prompt=lambda *_args, **_kwargs: ("", [], {"has_hits": True, "models": [], "relations": []}),
        resolve_analysis_to_schema=lambda _analysis, _models: {},
        prune_schema=lambda models, relations, *_args, **_kwargs: (models, relations),
        reformat_schema_context=lambda *_args, **_kwargs: "",
        build_schema_linking_plan=lambda *_args, **_kwargs: {},
        build_sql_planning_artifact=lambda *_args, **_kwargs: {},
        select_sql_strategy=lambda _analysis, _has_knowledge: {"engine": "direct_llm", "max_retries": 1, "use_examples": False},
        estimate_sql_generation_complexity=lambda _analysis, _semantic_hits: 1,
        strict_json_capability=lambda: {"supported": True, "mode": "json_schema"},
        prompt_profile_selection=lambda stage, strict_json_mode: profile_router.select(stage, strict_json_mode=strict_json_mode),
        is_sql_route_v2_enabled=lambda _project_id: True,
    )

    diff = pipeline.shadow_diff(
        analysis={"tier": "compound"},
        has_knowledge=True,
        generation_engine="direct_llm_repair",
    )

    assert diff["legacy_generation_engine"] == "decompose_merge"
    assert diff["new_generation_engine"] == "direct_llm"
    assert diff["changed"] is True


def test_generation_pipeline_prepare_context_uses_json_when_route_profile_disabled():
    profile_router = PromptProfileRouter(default_profile_version="v2")
    semantic_hits = {
        "has_hits": True,
        "models": [{"name": "orders", "columns": [{"name": "order_id"}], "matched_columns": []}],
        "relations": [],
    }
    pipeline = GenerationPipeline(
        normalize_question_analysis=lambda analysis: analysis or {"tier": "simple"},
        semantic_prompt=lambda *_args, **_kwargs: ("schema context", ["orders"], semantic_hits),
        resolve_analysis_to_schema=lambda _analysis, _models: {},
        prune_schema=lambda models, relations, *_args, **_kwargs: (models, relations),
        reformat_schema_context=lambda *_args, **_kwargs: "schema context",
        build_schema_linking_plan=lambda *_args, **_kwargs: {},
        build_sql_planning_artifact=lambda *_args, **_kwargs: {},
        select_sql_strategy=lambda _analysis, _has_knowledge: {"engine": "direct_llm", "max_retries": 1, "use_examples": False},
        estimate_sql_generation_complexity=lambda _analysis, _semantic_hits: 2,
        strict_json_capability=lambda: {"supported": True, "mode": "json_schema"},
        prompt_profile_selection=lambda stage, strict_json_mode: profile_router.select(stage, strict_json_mode=strict_json_mode),
        is_sql_route_v2_enabled=lambda _project_id: False,
    )

    prepared, early = pipeline.prepare_context(
        question="sales",
        project_id=1,
        semantic_context=None,
        retrieved_tables=None,
        semantic_hits=None,
        knowledge_context=None,
        analysis={"tier": "simple"},
        router_config={"sql_route_shadow_mode": False, "schema_pruning_enabled": False, "tier1_max_retries": 3},
    )

    assert early is None
    assert prepared is not None
    assert prepared.response_format == "json"
    assert prepared.system_suffix == ""


def test_generation_pipeline_prefers_strategy_retry_budget_before_complexity_cap():
    profile_router = PromptProfileRouter(default_profile_version="v2")
    semantic_hits = {
        "has_hits": True,
        "models": [{"name": "orders", "columns": [{"name": "order_id"}], "matched_columns": []}],
        "relations": [],
    }
    pipeline = GenerationPipeline(
        normalize_question_analysis=lambda analysis: analysis or {"tier": "simple"},
        semantic_prompt=lambda *_args, **_kwargs: ("schema context", ["orders"], semantic_hits),
        resolve_analysis_to_schema=lambda _analysis, _models: {},
        prune_schema=lambda models, relations, *_args, **_kwargs: (models, relations),
        reformat_schema_context=lambda *_args, **_kwargs: "schema context",
        build_schema_linking_plan=lambda *_args, **_kwargs: {},
        build_sql_planning_artifact=lambda *_args, **_kwargs: {},
        select_sql_strategy=lambda _analysis, _has_knowledge: {"engine": "direct_llm", "max_retries": 3, "use_examples": False},
        estimate_sql_generation_complexity=lambda _analysis, _semantic_hits: 5,
        strict_json_capability=lambda: {"supported": True, "mode": "json_schema"},
        prompt_profile_selection=lambda stage, strict_json_mode: profile_router.select(stage, strict_json_mode=strict_json_mode),
        is_sql_route_v2_enabled=lambda _project_id: True,
    )

    prepared, early = pipeline.prepare_context(
        question="sales",
        project_id=1,
        semantic_context=None,
        retrieved_tables=None,
        semantic_hits=None,
        knowledge_context=None,
        analysis={"tier": "simple"},
        router_config={"sql_route_shadow_mode": False, "schema_pruning_enabled": False, "tier1_max_retries": 10},
    )

    assert early is None
    assert prepared is not None
    assert prepared.max_retries == 3


def test_execution_router_route_kinds():
    router = ExecutionRouter()

    no_binding = router.decide(
        planned_sql="SELECT 1",
        final_execution_sql="SELECT 1",
        routing_stage="security_plan",
        referenced_by_binding={},
        binding_lookup={},
    )
    duckdb = router.decide(
        planned_sql="SELECT 1",
        final_execution_sql="SELECT 1",
        routing_stage="duckdb_binding_1",
        referenced_by_binding={1: [{"name": "orders"}]},
        binding_lookup={1: ("duckdb", {})},
    )
    cross = router.decide(
        planned_sql="SELECT 1",
        final_execution_sql="SELECT 1",
        routing_stage="cross_source",
        referenced_by_binding={1: [{"name": "orders"}], 2: [{"name": "customers"}]},
        binding_lookup={1: ("postgresql", {}), 2: ("mysql", {})},
    )

    assert no_binding.route_kind == "no_binding"
    assert duckdb.route_kind == "single_duckdb"
    assert cross.route_kind == "cross_source"


def test_llm_capability_reads_summary_strict_json(monkeypatch, tmp_path):
    report = {
        "summary": {
            "strict_json": {
                "supported": True,
                "mode": "json_schema",
                "detail": "probe ok",
            }
        }
    }
    report_path = tmp_path / "llm_capability_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("PRISMBI_LLM_CAPABILITY_REPORT_PATH", str(report_path))

    capability = get_strict_json_capability(force_refresh=True)

    assert capability["supported"] is True
    assert capability["mode"] == "json_schema"


def test_candidate_guard_collects_all_issue_types():
    guard = CandidateGuard(
        validate_sql_columns=lambda _sql, _models: ["unknown column"],
        validate_sql_group_by=lambda _sql, _dimensions, **_kwargs: ["missing group by column"],
        validate_sql_aggregation=lambda _sql: ["aggregation mismatch"],
        validate_sql_syntax_for_project=lambda _sql, _project_id: ["syntax error"],
    )

    result = guard.inspect(
        "SELECT 1",
        dimensions=["city"],
        hit_models=[],
        resolved=None,
        project_id=1,
    )

    assert result.bad_columns == ["unknown column"]
    assert result.group_issues == ["missing group by column"]
    assert result.aggregation_issues == ["aggregation mismatch"]
    assert result.syntax_issues == ["syntax error"]
    assert result.is_valid is False


def test_candidate_guard_marks_columns_inconclusive_when_validator_returns_none():
    guard = CandidateGuard(
        validate_sql_columns=lambda _sql, _models: None,
        validate_sql_group_by=lambda _sql, _dimensions, **_kwargs: [],
        validate_sql_aggregation=lambda _sql: [],
        validate_sql_syntax_for_project=lambda _sql, _project_id: [],
    )

    result = guard.inspect(
        "SELECT 1",
        dimensions=[],
        hit_models=[],
        resolved=None,
        project_id=1,
    )

    assert result.columns_inconclusive is True
    assert result.bad_columns == []
    assert result.is_valid is False


def test_execution_pipeline_prepare_and_shadow_diff():
    pipeline = ExecutionPipeline(
        plan_secured_sql=lambda sql, _project_id, _user_id: {"planned_sql": sql, "model_refs": ["orders"], "security": {"cls": []}},
        binding_rows=lambda _project_id: [(1, "postgresql", {})],
        models_by_binding=lambda _project_id: {1: [{"name": "orders", "source_binding_id": 1}]},
        models_for_project=lambda _project_id: [{"name": "orders", "source_binding_id": 1}],
        normalize_sql_candidate=lambda value: str(value or "").strip(),
        apply_limit=lambda sql, limit: f"SELECT * FROM ({sql}) AS t LIMIT {limit}",
        normalize_row_limit=lambda value: int(value or 20),
        default_sql_rows=200,
    )

    prepared, early = pipeline.prepare(
        input_sql="SELECT * FROM orders",
        project_id=1,
        user_id=1,
        limit=10,
    )

    assert early is None
    assert prepared is not None
    assert prepared.row_limit == 10
    assert prepared.referenced_by_binding

    diff = pipeline.shadow_diff(
        planned_sql=prepared.planned_sql,
        final_execution_sql=prepared.planned_limited_sql,
        routing_stage="external_binding_1",
        referenced_by_binding=prepared.referenced_by_binding,
        binding_lookup=prepared.binding_lookup,
    )

    assert diff["legacy_route_kind"] == "single_external"
    assert diff["new_route_kind"] == "single_external"
    assert diff["changed"] is False


def test_execution_pipeline_prepare_early_exit_without_bindings():
    pipeline = ExecutionPipeline(
        plan_secured_sql=lambda sql, _project_id, _user_id: {"planned_sql": sql, "model_refs": [], "security": {"cls": []}},
        binding_rows=lambda _project_id: [],
        models_by_binding=lambda _project_id: {},
        models_for_project=lambda _project_id: [],
        normalize_sql_candidate=lambda value: str(value or "").strip(),
        apply_limit=lambda sql, limit: f"SELECT * FROM ({sql}) AS t LIMIT {limit}",
        normalize_row_limit=lambda value: int(value or 20),
        default_sql_rows=200,
    )

    prepared, early = pipeline.prepare(
        input_sql="SELECT 1",
        project_id=1,
        user_id=1,
        limit=None,
    )

    assert prepared is None
    assert early is not None
    assert early.warning == "Project has no datasource bindings."


def test_execution_pipeline_model_matching_is_case_sensitive_by_default():
    pipeline = ExecutionPipeline(
        plan_secured_sql=lambda sql, _project_id, _user_id: {
            "planned_sql": sql,
            "model_refs": ["OrdersModel"],
            "security": {"cls": []},
        },
        binding_rows=lambda _project_id: [(1, "postgresql", {})],
        models_by_binding=lambda _project_id: {1: [{"name": "ordersmodel", "source_binding_id": 1}]},
        models_for_project=lambda _project_id: [{"name": "ordersmodel", "source_binding_id": 1}],
        normalize_sql_candidate=lambda value: str(value or "").strip(),
        apply_limit=lambda sql, limit: f"SELECT * FROM ({sql}) AS t LIMIT {limit}",
        normalize_row_limit=lambda value: int(value or 20),
        default_sql_rows=200,
    )

    prepared, early = pipeline.prepare(
        input_sql="SELECT * FROM OrdersModel",
        project_id=1,
        user_id=1,
        limit=10,
    )

    assert prepared is None
    assert early is not None
    assert "SQL must reference semantic model names" in early.warning


def test_execution_pipeline_can_disable_case_sensitive_model_matching():
    pipeline = ExecutionPipeline(
        plan_secured_sql=lambda sql, _project_id, _user_id: {
            "planned_sql": sql,
            "model_refs": ["OrdersModel"],
            "model_ref_case_sensitive": False,
            "security": {"cls": []},
        },
        binding_rows=lambda _project_id: [(1, "postgresql", {})],
        models_by_binding=lambda _project_id: {1: [{"name": "ordersmodel", "source_binding_id": 1}]},
        models_for_project=lambda _project_id: [{"name": "ordersmodel", "source_binding_id": 1}],
        normalize_sql_candidate=lambda value: str(value or "").strip(),
        apply_limit=lambda sql, limit: f"SELECT * FROM ({sql}) AS t LIMIT {limit}",
        normalize_row_limit=lambda value: int(value or 20),
        default_sql_rows=200,
    )

    prepared, early = pipeline.prepare(
        input_sql="SELECT * FROM OrdersModel",
        project_id=1,
        user_id=1,
        limit=10,
    )

    assert early is None
    assert prepared is not None
    assert prepared.referenced_by_binding
