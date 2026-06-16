from __future__ import annotations

import importlib
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional

_ask = importlib.import_module('services.ask_service')

LOGGER = logging.getLogger(__name__)

# shared mutable references
ROUTER_CONFIG = _ask.ROUTER_CONFIG

# decompose-merge module-level state
_decompose_merge_state_lock = threading.Lock()
_llm_budget_lock = threading.Lock()
_decompose_merge_state_by_project: dict[int, dict[str, float]] = {}

_TRANSIENT_FAILURE_REASONS = frozenset({"returned_none", "timed_out", "empty", "parse_error"})

# module-level bindings for all names referenced by moved functions
_CONNECTION_TIMEOUTS = _ask._CONNECTION_TIMEOUTS
LLMService = _ask.LLMService
parse_json_object = _ask.parse_json_object
_apply_group_by_completion_rules = _ask._apply_group_by_completion_rules
_apply_resolved_dimension_group_by_rules = _ask._apply_resolved_dimension_group_by_rules
_build_retry_user_message = _ask._build_retry_user_message
_build_sql_system_message = _ask._build_sql_system_message
_candidate_guard = _ask._candidate_guard
_contains_sql_placeholder_markers = _ask._contains_sql_placeholder_markers
_dialect_hint_for_project = _ask._dialect_hint_for_project
_extract_sql_examples_from_knowledge = _ask._extract_sql_examples_from_knowledge
_extract_sql_from_llm_text = _ask._extract_sql_from_llm_text
_fix_type_mismatch_multiply = _ask._fix_type_mismatch_multiply
_format_owner_lock_constraints_hint = _ask._format_owner_lock_constraints_hint
_is_sql_route_v2_enabled = _ask._is_sql_route_v2_enabled
_llm_chat_with_response_format_fallback = _ask._llm_chat_with_response_format_fallback
_normalize_analysis_string_list = _ask._normalize_analysis_string_list
_normalize_question_analysis = _ask._normalize_question_analysis
_normalize_sql_candidate = _ask._normalize_sql_candidate
_owner_preferences_from_issues = _ask._owner_preferences_from_issues
_prompt_profile_selection = _ask._prompt_profile_selection
_rehint_columns = _ask._rehint_columns
_render_project_prompt = _ask._render_project_prompt
_repair_sql = _ask._repair_sql
_sanitize_error_message = _ask._sanitize_error_message
_strict_json_capability = _ask._strict_json_capability
_validate_no_orphaned_cte = _ask._validate_no_orphaned_cte
_validate_sql_columns = _ask._validate_sql_columns
_validate_sql_group_by = _ask._validate_sql_group_by


def _is_decompose_merge_temporarily_disabled(project_id: int) -> bool:
    if not ROUTER_CONFIG.get("decompose_merge_circuit_enabled", True):
        return False
    now = time.monotonic()
    with _decompose_merge_state_lock:
        state = _decompose_merge_state_by_project.get(int(project_id))
        if not state:
            return False
        disabled_until = float(state.get("disabled_until") or 0.0)
        if disabled_until <= 0:
            return False
        if now >= disabled_until:
            state["disabled_until"] = 0.0
            state["failures"] = 0.0
            return False
        return True


def _record_decompose_merge_failure(project_id: int, reason: str | None = None) -> None:
    if not ROUTER_CONFIG.get("decompose_merge_circuit_enabled", True):
        return
    base_threshold = max(1, int(ROUTER_CONFIG.get("decompose_merge_failure_threshold", 1) or 1))
    normalized_reason = str(reason or "").strip().lower()
    is_transient = normalized_reason in _TRANSIENT_FAILURE_REASONS
    threshold = max(base_threshold, 3) if is_transient else base_threshold
    disable_seconds = max(30.0, float(ROUTER_CONFIG.get("decompose_merge_disable_seconds", 3600) or 3600))
    now = time.monotonic()
    with _decompose_merge_state_lock:
        state = _decompose_merge_state_by_project.setdefault(int(project_id), {"failures": 0.0, "disabled_until": 0.0})
        disabled_until = float(state.get("disabled_until") or 0.0)
        if disabled_until and now < disabled_until:
            return
        failures = int(state.get("failures") or 0) + 1
        if failures >= threshold:
            state["failures"] = 0.0
            state["disabled_until"] = now + disable_seconds
            LOGGER.warning(
                "Temporarily disabling decompose-merge for project_id=%d after %d failure(s)%s",
                project_id,
                failures,
                f" (reason={reason})" if reason else "",
            )
            return
        state["failures"] = float(failures)
    LOGGER.info(
        "Recorded decompose-merge failure for project_id=%d (%d/%d)%s",
        project_id,
        failures,
        threshold,
        f" (reason={reason})" if reason else "",
    )


def _record_decompose_merge_success(project_id: int) -> None:
    if not ROUTER_CONFIG.get("decompose_merge_circuit_enabled", True):
        return
    with _decompose_merge_state_lock:
        state = _decompose_merge_state_by_project.get(int(project_id))
        if not state:
            return
        state["failures"] = 0.0
        state["disabled_until"] = 0.0


def _decompose_merge_sql(
    question: str,
    project_id: int,
    analysis: dict,
    semantic_context: str,
    retrieved_tables: list[str],
    semantic_hits: dict,
    previous_questions: Optional[list[str]] = None,
    language: Optional[str] = None,
    knowledge_context: Optional[str] = None,
    resolved: Optional[dict] = None,
    schema_link_plan: Optional[dict[str, Any]] = None,
    cancel_check: Optional[Callable[[], None]] = None,
    failure_meta: Optional[dict[str, Any]] = None,
    stage_budget_s: Optional[float] = None,
) -> dict[str, Any] | None:
    normalized_analysis = _normalize_question_analysis(analysis)
    sub_questions = _normalize_analysis_string_list(normalized_analysis.get("sub_questions"))
    max_sub = ROUTER_CONFIG.get("max_sub_questions", 3)
    if len(sub_questions) > max_sub:
        LOGGER.warning("Truncating sub_questions from %d to %d for decompose-merge", len(sub_questions), max_sub)
        sub_questions = sub_questions[:max_sub]
    if not sub_questions:
        if isinstance(failure_meta, dict):
            failure_meta["reason"] = "returned_none"
        return None
    configured_stage_budget = stage_budget_s
    if configured_stage_budget is None:
        configured_stage_budget = ROUTER_CONFIG.get("decompose_merge_stage_budget_s", 150.0)
    try:
        stage_budget = float(configured_stage_budget)
    except Exception:
        stage_budget = 60.0
    if not math.isfinite(stage_budget):
        stage_budget = 60.0
    stage_budget = max(0.0, stage_budget)
    stage_started_at = time.monotonic()
    stage_deadline = stage_started_at + stage_budget
    subquery_cancel_event = threading.Event()

    def _remaining_stage_budget_s() -> float:
        if stage_budget <= 0.0:
            return 0.0
        return max(0.0, stage_deadline - time.monotonic())

    def _subquery_cancel_requested() -> bool:
        return subquery_cancel_event.is_set()

    def _subquery_cancel_check() -> None:
        if cancel_check:
            cancel_check()
        if _subquery_cancel_requested():
            raise RuntimeError("decompose_subquery_cancelled")

    _subquery_cancel_check()

    sql_examples = _extract_sql_examples_from_knowledge(knowledge_context or "")
    project_prompt = _render_project_prompt(project_id, semantic_context, sql_examples)
    dialect_hint = _dialect_hint_for_project(project_id)
    strict_json = _strict_json_capability()
    prompt_selection = _prompt_profile_selection(
        "sql_generation",
        strict_json_mode=strict_json.get("mode", "none"),
    )
    use_profile = _is_sql_route_v2_enabled(project_id) or bool(ROUTER_CONFIG.get("sql_route_shadow_mode", False))
    system_suffix = f"\n<PROFILE>{prompt_selection.system_suffix}</PROFILE>" if use_profile and prompt_selection.system_suffix else ""
    response_format = prompt_selection.response_format if (use_profile and prompt_selection.response_format) else "json"
    system = _build_sql_system_message(system_suffix, dialect_hint, language)
    llm = LLMService()
    if not llm.is_configured():
        return None
    hit_models = semantic_hits.get("models", [])
    guard = _candidate_guard()
    owner_lock_hint = _format_owner_lock_constraints_hint(resolved, schema_link_plan)
    _selected_owner_map = {
        str(k).strip().lower(): str(v).strip().lower()
        for k, v in ((schema_link_plan or {}).get("selected_owner_map") or {}).items()
        if str(k).strip() and str(v).strip()
    }
    _ambig_hint_lines = [f"  - '{c}' is owned by '{o}'" for c, o in sorted(_selected_owner_map.items())][:20]
    preventive_owner_hint = (
        "\nColumn-to-owner mapping (use these tables as column prefixes where ambiguous):\n"
        + "\n".join(_ambig_hint_lines)
        + "\n"
    ) if _ambig_hint_lines else ""
    subquery_failure_counts: dict[str, int] = {}
    merge_failure_counts: dict[str, int] = {}
    _llm_budget = int(ROUTER_CONFIG.get("decompose_llm_budget", 10))

    def _record_subquery_failure(reason: str, sq: str, detail: str | None = None) -> None:
        normalized_reason = str(reason or "returned_none").strip().lower() or "returned_none"
        subquery_failure_counts[normalized_reason] = int(subquery_failure_counts.get(normalized_reason) or 0) + 1
        if detail:
            LOGGER.info(
                "Decompose-merge sub-query failed reason=%s detail=%s sub_question=%s",
                normalized_reason,
                detail,
                sq,
            )
        else:
            LOGGER.info(
                "Decompose-merge sub-query failed reason=%s sub_question=%s",
                normalized_reason,
                sq,
            )

    def _set_failure(reason: str, detail: str | None = None) -> None:
        if not isinstance(failure_meta, dict):
            return
        normalized_reason = str(reason or "returned_none").strip().lower() or "returned_none"
        failure_meta["reason"] = normalized_reason
        failure_meta["elapsed_ms"] = round(max(0.0, (time.monotonic() - stage_started_at) * 1000.0), 3)
        failure_meta["stage_budget_s"] = float(stage_budget)
        if detail:
            failure_meta["detail"] = detail
        if subquery_failure_counts:
            failure_meta["reason_counts"] = dict(subquery_failure_counts)
        if merge_failure_counts:
            failure_meta["merge_reason_counts"] = dict(merge_failure_counts)

    def _stage_budget_exhausted(*, reason: str, detail: str | None = None) -> bool:
        remaining = _remaining_stage_budget_s()
        if remaining > 0.0:
            return False
        _set_failure("budget_exceeded", detail=detail or reason)
        LOGGER.warning(
            "Decompose-merge stage budget exhausted at %s (budget=%.2fs)",
            reason,
            stage_budget,
        )
        return True

    def _record_merge_failure(reason: str, detail: str | None = None) -> None:
        normalized_reason = str(reason or "returned_none").strip().lower() or "returned_none"
        merge_failure_counts[normalized_reason] = int(merge_failure_counts.get(normalized_reason) or 0) + 1
        if detail:
            LOGGER.info(
                "Decompose-merge merge candidate failed reason=%s detail=%s",
                normalized_reason,
                detail,
            )
        else:
            LOGGER.info("Decompose-merge merge candidate failed reason=%s", normalized_reason)

    if _stage_budget_exhausted(reason="start", detail="stage_budget_unavailable"):
        return None

    def _stabilize_decompose_candidate(candidate_sql: str, label: str) -> tuple[str | None, list[str], list[str], str | None]:
        if _contains_sql_placeholder_markers(candidate_sql):
            LOGGER.warning(
                "Decompose-merge %s contains placeholder SQL identifiers; rejecting candidate",
                label,
            )
            return None, [], [], "placeholder"
        stabilized = _fix_type_mismatch_multiply(candidate_sql, hit_models) if hit_models else candidate_sql
        inspected = guard.inspect(
            stabilized,
            dimensions=normalized_analysis.get("dimensions") or [],
            hit_models=hit_models,
            resolved=resolved,
            project_id=project_id,
        )
        group_issues = list(inspected.group_issues)
        if group_issues:
            group_candidate = _apply_group_by_completion_rules(stabilized)
            if group_candidate != stabilized:
                group_columns = _validate_sql_columns(group_candidate, hit_models)
                group_candidate_issues = _validate_sql_group_by(
                    group_candidate,
                    normalized_analysis.get("dimensions") or [],
                    hit_models=hit_models,
                    resolved=resolved,
                )
                if (group_columns is None or not group_columns) and not group_candidate_issues:
                    stabilized = group_candidate
                    group_issues = []
            if group_issues and resolved and resolved.get("dimensions_resolved"):
                resolved_candidate = _apply_resolved_dimension_group_by_rules(
                    stabilized,
                    resolved,
                    hit_models=hit_models,
                )
                if resolved_candidate != stabilized:
                    resolved_columns = _validate_sql_columns(resolved_candidate, hit_models)
                    resolved_issues = _validate_sql_group_by(
                        resolved_candidate,
                        normalized_analysis.get("dimensions") or [],
                        hit_models=hit_models,
                        resolved=resolved,
                    )
                    if (resolved_columns is None or not resolved_columns) and not resolved_issues:
                        stabilized = resolved_candidate
                        group_issues = []
        inspected = guard.inspect(
            stabilized,
            dimensions=normalized_analysis.get("dimensions") or [],
            hit_models=hit_models,
            resolved=resolved,
            project_id=project_id,
        )
        agg_issues = list(inspected.aggregation_issues)
        syntax_issues = list(inspected.syntax_issues)
        if syntax_issues:
            LOGGER.warning(
                "Decompose-merge %s syntax issues: %s",
                label,
                syntax_issues,
            )
            return None, group_issues, agg_issues, "syntax"
        if group_issues:
            LOGGER.warning(
                "Decompose-merge %s GROUP BY issues remain after local repair: %s",
                label,
                group_issues,
            )
            return None, group_issues, agg_issues, "group_by"
        return stabilized, group_issues, agg_issues, None

    def _subquery_guard_reason(candidate_sql: str) -> str | None:
        inspected = guard.inspect(
            candidate_sql,
            dimensions=[],
            hit_models=hit_models,
            resolved=resolved,
            project_id=project_id,
        )
        if inspected.syntax_issues:
            return "syntax"
        if inspected.aggregation_issues:
            return "aggregation"
        return None

    def _sub_sql(sq: str, attempt: int = 0, errors: Optional[list[str]] = None) -> tuple[str | None, str | None, str]:
        nonlocal _llm_budget
        if errors is None:
            errors = []
        try:
            if _subquery_cancel_requested():
                return None, None, "cancelled"
            _subquery_cancel_check()
            if _stage_budget_exhausted(reason="subquery_start", detail=f"sub_question={sq}"):
                return None, None, "budget_exceeded"
            with _llm_budget_lock:
                if _llm_budget <= 0:
                    return None, None, "budget_exceeded"
                _llm_budget -= 1
            remaining_for_subquery = _remaining_stage_budget_s()
            if remaining_for_subquery <= 0.0:
                return None, None, "budget_exceeded"
            _safe_sq = str(sq or "")[:1000]
            _safe_prev = [str(q or "")[:200] for q in (previous_questions or [])[-3:]]
            base_sub_question_prompt = (
                f"Previous questions: {_safe_prev}\n"
                f"Sub-question: {_safe_sq}\n"
                "Generate a SQL query that answers this sub-question. Use only the provided schema context. "
                "Note: Table names must be used exactly as given — never abbreviated."
                f"{owner_lock_hint}{preventive_owner_hint}"
            )
            sub_question_prompt = _build_retry_user_message(base_sub_question_prompt, errors, attempt, 3)
            result = _llm_chat_with_response_format_fallback(
                llm,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Project prompt:\n{project_prompt}\n\n{sub_question_prompt}"},
                ],
                response_format=response_format,
                stage="sql_generation_subquery",
                timeout=min(float(ROUTER_CONFIG.get("llm_sub_query_timeout_s", 90.0)), max(0.5, remaining_for_subquery)),
                cancel_check=_subquery_cancel_check,
            )
            parsed = parse_json_object(result.get("content", ""))
            sq_sql = _normalize_sql_candidate(parsed.get("sql"))
            if not sq_sql:
                if attempt < 2:
                    LOGGER.info("Decompose-merge sub-query empty on attempt %d; retrying", attempt)
                    errors.append(f"Attempt {attempt+1}: empty SQL")
                    return _sub_sql(sq, attempt=attempt + 1, errors=errors)
                return None, None, "empty"
            validated = _validate_sql_columns(sq_sql, hit_models)
            if validated is None:
                if attempt < 2:
                    LOGGER.info("Decompose-merge sub-query returned_none on attempt %d; retrying", attempt)
                    errors.append(f"Attempt {attempt+1}: column validation returned None")
                    return _sub_sql(sq, attempt=attempt + 1, errors=errors)
                return None, None, "returned_none"
            if validated is not None and not validated:
                guard_reason = _subquery_guard_reason(sq_sql)
                if guard_reason:
                    return None, None, guard_reason
                return sq_sql, parsed.get("summary", ""), "ok"
            if validated:
                owner_prefs = _owner_preferences_from_issues(
                    validated,
                    hit_models=hit_models,
                    schema_link_plan=schema_link_plan,
                    failed_sql=sq_sql,
                )
                rehinted = _rehint_columns(sq_sql, hit_models, bad_columns=validated, owner_preferences=owner_prefs)
                if rehinted != sq_sql:
                    rehinted_validated = _validate_sql_columns(rehinted, hit_models)
                    if rehinted_validated is not None and not rehinted_validated:
                        guard_reason = _subquery_guard_reason(rehinted)
                        if guard_reason:
                            return None, None, guard_reason
                        return rehinted, parsed.get("summary", ""), "ok"
            errors.append(f"Attempt {attempt+1}: bad columns ({validated[0] if validated else 'unknown'})")
            return None, None, "bad_columns"
        except Exception as e:
            if _subquery_cancel_requested():
                return None, None, "cancelled"
            if _remaining_stage_budget_s() <= 0.0:
                return None, None, "budget_exceeded"
            if attempt < 2:
                LOGGER.info("Decompose-merge sub-query exception on attempt %d; retrying", attempt)
                errors.append(f"Attempt {attempt+1}: {type(e).__name__}")
                return _sub_sql(sq, attempt=attempt + 1, errors=errors)
            return None, None, "returned_none"

    parallel_max = min(len(sub_questions), 3)
    sql_results: list[tuple[str | None, str | None, str]] = [None] * len(sub_questions)
    if parallel_max > 1:
        executor = ThreadPoolExecutor(max_workers=parallel_max, thread_name_prefix="sub-query")
        try:
            fut_to_idx = {executor.submit(_sub_sql, sq): i for i, sq in enumerate(sub_questions)}
            _subquery_cancel_check()
            remaining = _remaining_stage_budget_s()
            try:
                for fut in as_completed(fut_to_idx.keys(), timeout=max(0.0, remaining)):
                    idx = fut_to_idx[fut]
                    sql_results[idx] = fut.result()
            except TimeoutError:
                subquery_cancel_event.set()
                for pending_fut in fut_to_idx.keys():
                    if not pending_fut.done():
                        pending_fut.cancel()
            timeout_reason = (
                "budget_exceeded"
                if _remaining_stage_budget_s() <= 0.0
                else ("cancelled" if _subquery_cancel_requested() else "timed_out")
            )
            for idx in range(len(sql_results)):
                if sql_results[idx] is None:
                    sql_results[idx] = (None, None, timeout_reason)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
    else:
        sql_results = [_sub_sql(sq) for sq in sub_questions]
    if cancel_check:
        cancel_check()
    valid_results: list[tuple[str, str, str | None]] = []
    for sq, (sql, summary, reason) in zip(sub_questions, sql_results):
        if sql:
            valid_results.append((sq, sql, summary))
            continue
        _record_subquery_failure(reason, sq)
    if not valid_results:
        ranked_failures = sorted(subquery_failure_counts.items(), key=lambda item: (-item[1], item[0]))
        primary_reason = ranked_failures[0][0] if ranked_failures else "returned_none"
        _set_failure(primary_reason, detail=f"sub_query_failures={dict(subquery_failure_counts)}")
        LOGGER.warning(
            "Decompose-merge: all sub-queries failed (reasons=%s) — falling back to direct generation",
            subquery_failure_counts or {primary_reason: 1},
        )
        return None

    if len(valid_results) == 1:
        _, only_sql, only_summary = valid_results[0]
        orphan = _validate_no_orphaned_cte(only_sql)
        if orphan:
            if _stage_budget_exhausted(reason="single_subquery_repair", detail="orphan_cte"):
                return None
            only_sql = _repair_sql(
                question,
                only_sql,
                "; ".join(orphan),
                project_id,
                semantic_context,
                language,
                cancel_check=_subquery_cancel_check,
                timeout_cap_s=_remaining_stage_budget_s(),
            ).get("sql") or only_sql
        stabilized_sql, _group_issues, agg_issues, stabilize_reason = _stabilize_decompose_candidate(only_sql, "single sub-query")
        if not stabilized_sql:
            _set_failure(stabilize_reason or "returned_none", detail="single_subquery_unstable")
            return None
        if agg_issues:
            LOGGER.warning("Decompose-merge single sub-query aggregation issues: %s", agg_issues)
        return {
            "sql": stabilized_sql,
            "summary": only_summary or _ask._sql_msg("decompose_gen_sql", language),
            "reasoning": _ask._sql_msg("decompose_single_subq", language),
            "retrieved_tables": retrieved_tables,
            "configured": True,
            "sql_engine": "decompose_merge",
        }

    merge_system = _build_sql_system_message(
        system_suffix, _dialect_hint_for_project(project_id), language,
        extra_instructions=(
            "Merge-specific instructions:\n"
            "- Preserve ALL dimensions and metrics from every sub-query; do not drop any.\n"
            "- Generate a single master query with GROUP BY covering ALL dimensions.\n"
            "- Prefer a single flat query over multiple CTEs unless sub-queries share tables.\n"
            "- Return ONLY valid JSON with keys: sql, summary, reasoning. Do NOT include markdown fences, explanations, or any text outside the JSON object."
        ),
    )
    _safe_q = str(question or "")[:4000]
    merge_parts = [f"Original question: {_safe_q}\nSchema context:\n{semantic_context}{owner_lock_hint}{preventive_owner_hint}\n\nSub-queries to merge:"]
    for i, (sq, sq_sql, sq_summary) in enumerate(valid_results):
        _safe_sq = str(sq or "")[:500]
        _safe_sql = str(sq_sql or "")[:2000]
        _safe_summary = str(sq_summary or "")[:200]
        merge_parts.append(f"\n--- Sub-query {i+1}: {_safe_sq} ---\nSQL:\n```\n{_safe_sql}\n```\nSummary: {_safe_summary}")
    merge_parts.append("\nMerge these sub-queries into a single SQL query that answers the original question. "
                       "Note: Table names must be used exactly as given — never abbreviated. "
                       "Return ONLY valid JSON with keys: sql, summary, reasoning.")
    merge_user = "\n".join(merge_parts)

    merge_candidate_budget = max(1, min(3, int(ROUTER_CONFIG.get("tier3_max_retries", 3) or 3)))
    for merge_attempt in range(merge_candidate_budget):
        if _stage_budget_exhausted(reason="merge_start", detail=f"attempt={merge_attempt + 1}"):
            _record_merge_failure("budget_exceeded", detail=f"candidate {merge_attempt + 1}:stage_budget_exhausted")
            break
        with _llm_budget_lock:
            if _llm_budget <= 0:
                LOGGER.warning("Decompose-merge LLM budget exhausted after sub-queries; stopping merge attempts")
                break
            _llm_budget -= 1
        attempt_label = f"candidate {merge_attempt + 1}/{merge_candidate_budget}"
        try:
            _subquery_cancel_check()
            remaining_for_merge = _remaining_stage_budget_s()
            if remaining_for_merge <= 0.0:
                _record_merge_failure("budget_exceeded", detail=f"{attempt_label}:remaining_budget_zero")
                break
            attempt_prompt = merge_user
            if merge_candidate_budget > 1:
                attempt_prompt = (
                    f"{merge_user}\n"
                    f"Generate merge SQL {attempt_label}. Maintain semantic consistency while improving validity and stability."
                )
            result = _llm_chat_with_response_format_fallback(
                llm,
                [
                    {"role": "system", "content": merge_system},
                    {"role": "user", "content": f"Project prompt:\n{project_prompt}\n\n{attempt_prompt}"},
                ],
                response_format=response_format,
                stage="sql_generation_merge",
                timeout=min(float(ROUTER_CONFIG.get("llm_merge_timeout_s", 120.0)), max(0.5, remaining_for_merge)),
                cancel_check=_subquery_cancel_check,
            )
            raw_merge_content = result.get("content", "")
            try:
                parsed = parse_json_object(raw_merge_content)
            except Exception as parse_exc:
                fallback_sql = _extract_sql_from_llm_text(raw_merge_content)
                if not fallback_sql:
                    _record_merge_failure("parse_error", detail=f"{attempt_label}:{type(parse_exc).__name__}")
                    continue
                LOGGER.warning(
                    "Decompose-merge merge response JSON parse failed (%s); using plain-text SQL fallback",
                    parse_exc,
                )
                parsed = {
                    "sql": fallback_sql,
                    "summary": _ask._sql_msg("decompose_merged_sql", language),
                    "reasoning": _ask._sql_msg("decompose_merge_fallback_fmt", language).format(type(parse_exc).__name__),
                }
            merged_sql = _normalize_sql_candidate(parsed.get("sql"))
            if merged_sql and (validated := _validate_sql_columns(merged_sql, hit_models)) is not None and not validated:
                orphan = _validate_no_orphaned_cte(merged_sql)
                if orphan:
                    if _stage_budget_exhausted(reason="merge_orphan_repair", detail=attempt_label):
                        _record_merge_failure("budget_exceeded", detail=f"{attempt_label}:merge_orphan_repair")
                        break
                    merged_sql = _repair_sql(
                        question,
                        merged_sql,
                        "; ".join(orphan),
                        project_id,
                        semantic_context,
                        language,
                        cancel_check=_subquery_cancel_check,
                        timeout_cap_s=_remaining_stage_budget_s(),
                    ).get("sql") or merged_sql
                stabilized_sql, _group_issues, agg_issues, stabilize_reason = _stabilize_decompose_candidate(
                    merged_sql,
                    f"merge result ({attempt_label})",
                )
                if not stabilized_sql:
                    LOGGER.warning("Decompose-merge merge result %s is unstable", attempt_label)
                    _record_merge_failure(stabilize_reason or "returned_none", detail=f"{attempt_label}:merge_result_unstable")
                    continue
                if agg_issues:
                    LOGGER.warning("Decompose-merge SQL aggregation issues: %s", agg_issues)
                return {
                    "sql": stabilized_sql,
                    "summary": parsed.get("summary") or _ask._sql_msg("decompose_merged_sql", language),
                    "reasoning": parsed.get("reasoning") or _ask._sql_msg("decompose_merged_count_fmt", language).format(len(valid_results)),
                    "retrieved_tables": retrieved_tables,
                    "configured": True,
                    "sql_engine": "decompose_merge",
                }
            if not merged_sql:
                LOGGER.warning("Decompose-merge merge step returned empty SQL (%s)", attempt_label)
                _record_merge_failure("empty", detail=f"{attempt_label}:merge_sql_empty")
                continue
            owner_prefs = _owner_preferences_from_issues(
                validated,
                hit_models=hit_models,
                schema_link_plan=schema_link_plan,
                failed_sql=merged_sql,
            )
            rehinted = _rehint_columns(merged_sql, hit_models, bad_columns=validated, owner_preferences=owner_prefs)
            if rehinted != merged_sql:
                rehinted_validated = _validate_sql_columns(rehinted, hit_models)
                if rehinted_validated is not None and not rehinted_validated:
                    orphan = _validate_no_orphaned_cte(rehinted)
                    if orphan:
                        if _stage_budget_exhausted(reason="rehint_orphan_repair", detail=attempt_label):
                            _record_merge_failure("budget_exceeded", detail=f"{attempt_label}:rehint_orphan_repair")
                            break
                        rehinted = _repair_sql(
                            question,
                            rehinted,
                            "; ".join(orphan),
                            project_id,
                            semantic_context,
                            language,
                            cancel_check=_subquery_cancel_check,
                            timeout_cap_s=_remaining_stage_budget_s(),
                        ).get("sql") or rehinted
                    stabilized_rehinted, _rehint_group_issues, rehint_agg_issues, rehint_reason = _stabilize_decompose_candidate(
                        rehinted,
                        f"rehinted merge result ({attempt_label})",
                    )
                    if not stabilized_rehinted:
                        LOGGER.warning("Decompose-merge rehinted SQL %s is unstable", attempt_label)
                        _record_merge_failure(rehint_reason or "returned_none", detail=f"{attempt_label}:rehinted_merge_unstable")
                        continue
                    if rehint_agg_issues:
                        LOGGER.warning("Decompose-merge rehinted SQL aggregation issues: %s", rehint_agg_issues)
                    return {
                        "sql": stabilized_rehinted,
                        "summary": parsed.get("summary") or _ask._sql_msg("decompose_merged_auto_correct", language),
                        "reasoning": parsed.get("reasoning") or _ask._sql_msg("decompose_merged_count_fmt", language).format(len(valid_results)),
                        "retrieved_tables": retrieved_tables,
                        "configured": True,
                        "sql_engine": "decompose_merge_rehint",
                    }
            LOGGER.warning("Decompose-merge merged SQL has bad columns (%s)", attempt_label)
            _record_merge_failure("merged_bad_columns", detail=attempt_label)
            LOGGER.warning(
                "Decompose-merge merged bad columns is non-transient; skipping remaining merge candidates",
            )
            break
        except Exception as e:
            if _remaining_stage_budget_s() <= 0.0:
                _record_merge_failure("budget_exceeded", detail=f"{attempt_label}:exception_after_budget")
                break
            LOGGER.warning(
                "Decompose-merge merge %s failed: %s",
                attempt_label,
                _sanitize_error_message(e),
            )
            _record_merge_failure("returned_none", detail=f"{attempt_label}:{type(e).__name__}")

    ranked_merge_failures = sorted(merge_failure_counts.items(), key=lambda item: (-item[1], item[0]))
    primary_reason = ranked_merge_failures[0][0] if ranked_merge_failures else "returned_none"
    _set_failure(primary_reason, detail=f"merge_candidate_failures={dict(merge_failure_counts)}")
    LOGGER.warning(
        "Decompose-merge merge step exhausted %d candidate(s) (reasons=%s) — falling back to direct generation",
        merge_candidate_budget,
        merge_failure_counts or {primary_reason: 1},
    )
    return None
