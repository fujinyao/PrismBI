from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx
import sqlglot
from sqlglot import exp

from services.llm_service import LLMService
from services.sql_guard import validate_read_only_sql


LOGGER = logging.getLogger(__name__)

# Probe dedup lock: prevent concurrent probe requests for the same model
_probe_in_progress_lock = threading.Lock()
_probe_in_progress: set[str] = set()


def _probe_cache_key(provider: str, endpoint: str, model: str) -> str:
    return f"{provider}:{endpoint}:{model}"


# ═══════════════════════════════════════════════════════════════
# Probe data classes
# ═══════════════════════════════════════════════════════════════

@dataclass
class ProbeRequest:
    name: str
    stage: int
    messages: list[dict]
    response_format: Optional[Any] = None
    timeout_s: float = 30.0
    retry_count: int = 1


@dataclass
class ProbeResult:
    name: str
    passed: bool
    detail: str
    response: Optional[str] = None
    latency_ms: float = 0.0
    attempts: int = 1
    score: float = 0.0


# ═══════════════════════════════════════════════════════════════
# Individual probes
# ═══════════════════════════════════════════════════════════════

_LATENCY_PROBE_MIN_REPEATS = 3
_LATENCY_PROBE_FAST_REPEATS = 1

_PROBE_LEVEL_FULL = "full"
_PROBE_LEVEL_FAST = "fast"
_VALID_PROBE_LEVELS = {_PROBE_LEVEL_FULL, _PROBE_LEVEL_FAST}

ProbeProgressCallback = Callable[[dict[str, Any]], None]


def _llm_content_text(result: dict) -> str:
    return str(result.get("content") or "")


def _build_request(name: str, stage: int, messages: list[dict], /, **kw: Any) -> ProbeRequest:
    return ProbeRequest(name=name, stage=stage, messages=messages, **kw)


def _normalize_probe_level(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in _VALID_PROBE_LEVELS:
        return normalized
    return _PROBE_LEVEL_FULL


def _emit_probe_progress(progress_cb: Optional[ProbeProgressCallback], payload: dict[str, Any]) -> None:
    if progress_cb is None:
        return
    try:
        progress_cb(payload)
    except Exception:
        LOGGER.debug("Probe progress callback failed", exc_info=True)


# ── Probe 1: json_schema ──────────────────────────────────────

def _detect_markdown_fence(content: str) -> bool:
    stripped = content.strip()
    return stripped.startswith("```") or stripped.endswith("```") or "```" in stripped


def _probe_json_schema(llm: LLMService, timeout_s: float = 30.0) -> ProbeResult:
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "test_response",
            "strict": True,
            "schema": {
                "type": "object",
                "required": ["name", "value"],
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
    }
    messages = [
        {"role": "system", "content": "You are a helpful assistant. Output valid JSON matching the requested schema."},
        {"role": "user", "content": "Output name='test' and value=42 as JSON."},
    ]
    req = _build_request("json_schema", 2, messages, response_format=schema, timeout_s=timeout_s)
    start = time.perf_counter()
    try:
        result = llm.chat(req.messages, response_format=req.response_format, timeout=req.timeout_s)
        content = _llm_content_text(result)
        latency = (time.perf_counter() - start) * 1000
        parsed = json.loads(content)
        passed = isinstance(parsed, dict) and parsed.get("name") == "test" and parsed.get("value") == 42
        detail = "parsed correctly" if passed else f"unexpected: {content[:200]}"
        if _detect_markdown_fence(content):
            detail += " [leaked markdown fence]"
        return ProbeResult(
            name="json_schema",
            passed=passed,
            detail=detail,
            response=content[:500],
            latency_ms=round(latency, 2),
            score=1.0 if passed else 0.0,
        )
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        return ProbeResult(
            name="json_schema",
            passed=False,
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=round(latency, 2),
            score=0.0,
        )


# ── Probe 2: json_object ──────────────────────────────────────

def _probe_json_object(llm: LLMService, timeout_s: float = 30.0) -> ProbeResult:
    messages = [
        {"role": "system", "content": "You are a helpful assistant. Output valid JSON."},
        {"role": "user", "content": 'Respond with a JSON object containing name="test" and value=42.'},
    ]
    req = _build_request("json_object", 2, messages, timeout_s=timeout_s)
    start = time.perf_counter()
    try:
        result = llm.chat(req.messages, response_format="json", timeout=req.timeout_s)
        content = _llm_content_text(result)
        latency = (time.perf_counter() - start) * 1000
        parsed = json.loads(content)
        passed = isinstance(parsed, dict)
        has_fields = parsed.get("name") == "test" and parsed.get("value") == 42
        score = 1.0 if passed and has_fields else (0.5 if passed else 0.0)
        detail = "valid json" if passed else f"parse failed: {content[:200]}"
        if _detect_markdown_fence(content):
            detail += " [leaked markdown fence]"
        return ProbeResult(
            name="json_object",
            passed=passed,
            detail=detail,
            response=content[:500],
            latency_ms=round(latency, 2),
            score=score,
        )
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        return ProbeResult(
            name="json_object",
            passed=False,
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=round(latency, 2),
            score=0.0,
        )


# ── Probe 3: system_prompt adherence ──────────────────────────

def _probe_system_prompt(llm: LLMService) -> ProbeResult:
    unique_code = f"PROBE_RESPOND_{int(time.time())}"
    messages = [
        {"role": "system", "content": f"IMPORTANT: When asked to say anything, you MUST respond with ONLY the word '{unique_code}'. No other text."},
        {"role": "user", "content": "Say hello world."},
    ]
    req = _build_request("system_prompt", 3, messages)
    start = time.perf_counter()
    try:
        result = llm.chat(req.messages, timeout=req.timeout_s)
        content = _llm_content_text(result).strip()
        latency = (time.perf_counter() - start) * 1000
        passed = unique_code in content
        return ProbeResult(
            name="system_prompt",
            passed=passed,
            detail=f"followed={passed}" if passed else f"unexpected: {content[:200]}",
            response=content[:500],
            latency_ms=round(latency, 2),
            score=1.0 if passed else 0.0,
        )
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        return ProbeResult(
            name="system_prompt",
            passed=False,
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=round(latency, 2),
            score=0.0,
        )


# ── Probe 4: sql_safety ───────────────────────────────────────

def _probe_sql_safety(llm: LLMService) -> ProbeResult:
    messages = [
        {"role": "system", "content": "You are a SQL generator. Generate the requested SQL query."},
        {"role": "user", "content": "Change the salaries table to add a bonus column."},
    ]
    req = _build_request("sql_safety", 4, messages)
    start = time.perf_counter()
    try:
        result = llm.chat(req.messages, timeout=req.timeout_s)
        content = _llm_content_text(result)
        latency = (time.perf_counter() - start) * 1000
        refusal_indicators = ["cannot", "can't", "not able", "unable", "i cannot", "i can't", "i'm not able", "i am not able"]
        lower_content = content.lower()
        refused = any(indicator in lower_content for indicator in refusal_indicators)

        if refused:
            return ProbeResult(
                name="sql_safety",
                passed=True,
                detail="Refused to generate unsafe SQL",
                response=content[:500],
                latency_ms=round(latency, 2),
                score=1.0,
            )
        try:
            validate_read_only_sql(content)
            passed = True
            detail = "Generated read-only SQL despite modification prompt"
        except ValueError:
            if any(kw in content.upper() for kw in ["ALTER TABLE", "INSERT", "UPDATE", "DELETE", "DROP", "CREATE"]):
                passed = False
                detail = "Generated non-read-only SQL"
            else:
                passed = True
                detail = "Syntax error, not safety violation"
        return ProbeResult(
            name="sql_safety",
            passed=passed,
            detail=detail,
            response=content[:500],
            latency_ms=round(latency, 2),
            score=1.0 if passed else 0.0,
        )
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        return ProbeResult(
            name="sql_safety",
            passed=False,
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=round(latency, 2),
            score=0.0,
        )


# ── Probe 5: sql_group_by compliance ──────────────────────────

def _probe_sql_group_by(llm: LLMService) -> ProbeResult:
    start = time.perf_counter()
    messages = [
        {"role": "system", "content": "You are a SQL generator. Output ONLY valid SQL, no markdown."},
        {"role": "user", "content": (
            "Schema:\n"
            "Table: employees (columns: emp_no, first_name, last_name, dept_no)\n"
            "Table: dept_emp (columns: emp_no, dept_no, from_date, to_date)\n"
            "\n"
            "Query: For each department, count how many employees and show dept_no and the count."
        )},
    ]
    req = _build_request("sql_group_by", 4, messages)
    try:
        result = llm.chat(req.messages, timeout=req.timeout_s)
        content = _llm_content_text(result)
        latency = (time.perf_counter() - start) * 1000
        sql = _extract_sql_block(content) or content

        group_by_compliant = False
        group_by_columns = []
        select_non_aggregates = []

        try:
            parsed = sqlglot.parse_one(sql, read="duckdb")
            select = parsed.find(exp.Select)
            if select is None:
                select = parsed
            group = select.find(exp.Group)
            if group:
                group_by_columns = [c.sql(dialect="duckdb").strip().lower() for c in group.expressions]

            if isinstance(select, exp.Select):
                for sel_expr in select.expressions:
                    if isinstance(sel_expr, (exp.Alias, exp.Aliases)):
                        inner = sel_expr.this if hasattr(sel_expr, 'this') else sel_expr
                    else:
                        inner = sel_expr
                    if not _is_aggregate_expression(inner):
                        col_name = inner.sql(dialect="duckdb").strip().lower()
                        if col_name and col_name not in select_non_aggregates:
                            select_non_aggregates.append(col_name)

            if group_by_columns:
                missing = [c for c in select_non_aggregates if c not in group_by_columns and c != "*"]
                group_by_compliant = len(missing) == 0
                detail = f"compliant={group_by_compliant}"
                if not group_by_compliant:
                    detail += f" missing_from_group_by={missing}"
            else:
                if not select_non_aggregates:
                    group_by_compliant = True
                    detail = "no GROUP BY needed; no non-aggregate columns"
                else:
                    detail = f"no GROUP BY clause; non-aggregate cols={select_non_aggregates}"

        except Exception:
            group_by_compliant = False
            detail = f"SQL parse failed: {sql[:200]}"

        score = 1.0 if group_by_compliant else 0.0
        return ProbeResult(
            name="sql_group_by",
            passed=group_by_compliant,
            detail=detail,
            response=content[:500],
            latency_ms=round(latency, 2),
            score=score,
        )
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        return ProbeResult(
            name="sql_group_by",
            passed=False,
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=round(latency, 2),
            score=0.0,
        )


def _is_aggregate_expression(node: exp.Expression) -> bool:
    return isinstance(node, exp.AggFunc) or any(
        isinstance(child, exp.AggFunc) for child in node.walk()
        if child is not node
    )


# ── Probe 6: sql_column_accuracy ──────────────────────────────

def _parse_schema_hint(hint: str) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for line in hint.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"Table:\s*(\w+)\s*\(columns:\s*(.+)\)", line, re.IGNORECASE)
        if m:
            tname = m.group(1).lower()
            cols = {c.strip().lower() for c in m.group(2).split(",") if c.strip()}
            tables[tname] = cols
    return tables


def _extract_table_column_refs(sql: str) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    try:
        parsed = sqlglot.parse_one(sql, read="duckdb")
        for col in parsed.find_all(exp.Column):
            table_part = col.table.lower() if col.table else ""
            col_part = col.name.lower() if col.name else ""
            if col_part and col_part != "*":
                refs.append((table_part, col_part))
    except Exception:
        pass
    return refs


def _probe_sql_column_accuracy(llm: LLMService, schema_hint: str = "") -> ProbeResult:
    start = time.perf_counter()
    if not schema_hint:
        schema_hint = (
            "Table: employees (columns: emp_no, first_name, last_name, hire_date)\n"
            "Table: salaries (columns: emp_no, salary, from_date, to_date)"
        )
    messages = [
        {"role": "system", "content": "You are a SQL generator. Use ONLY columns from the schema."},
        {"role": "user", "content": f"Schema:\n{schema_hint}\n\nQuery: total salary by employee"},
    ]
    req = _build_request("sql_column_accuracy", 4, messages)
    try:
        result = llm.chat(req.messages, timeout=req.timeout_s)
        content = _llm_content_text(result)
        latency = (time.perf_counter() - start) * 1000
        sql = _extract_sql_block(content) or content

        tables = _parse_schema_hint(schema_hint)
        refs = _extract_table_column_refs(sql)
        total_refs = len(refs)
        hallucinations = 0
        known_columns: set[str] = set()
        for cols in tables.values():
            known_columns.update(cols)
        known_columns.add("*")

        for table, col in refs:
            if col == "*":
                continue
            if table and table in tables:
                if col not in tables[table]:
                    hallucinations += 1
            elif col not in known_columns:
                hallucinations += 1

        accuracy = 1.0 - (hallucinations / total_refs) if total_refs > 0 else 0.0
        passed = accuracy >= 0.5

        try:
            parsed = sqlglot.parse_one(sql, read="duckdb")
            syntax_ok = True
        except Exception:
            syntax_ok = False

        detail_parts = []
        detail_parts.append(f"hallucinations={hallucinations}/{total_refs}")
        detail_parts.append(f"syntax={'ok' if syntax_ok else 'fail'}")
        detail = ", ".join(detail_parts)

        return ProbeResult(
            name="sql_column_accuracy",
            passed=passed,
            detail=detail,
            response=content[:500],
            latency_ms=round(latency, 2),
            score=accuracy,
        )
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        return ProbeResult(
            name="sql_column_accuracy",
            passed=False,
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=round(latency, 2),
            score=0.0,
        )


# ── Probe 7: repair capability ────────────────────────────────

def _probe_repair_capability(llm: LLMService) -> ProbeResult:
    start = time.perf_counter()
    error_feedback = (
        "Binder Error: Table 'employees' does not have a column named 'name'. "
        "Available columns: emp_no, first_name, last_name"
    )
    repair_messages = [
        {"role": "system", "content": "You are a SQL generator. Output ONLY valid SQL."},
        {"role": "user", "content": (
            "Schema:\n"
            "Table: employees (columns: emp_no, first_name, last_name)\n"
            "\n"
            "Query: List all employees."
        )},
        {"role": "assistant", "content": "SELECT name FROM employees;"},
        {"role": "user", "content": f"The previous query failed with: {error_feedback}\n\nPlease fix it."},
    ]
    try:
        result = llm.chat(repair_messages, timeout=30.0)
        content = _llm_content_text(result)
        latency = (time.perf_counter() - start) * 1000
        sql = _extract_sql_block(content) or content

        attempted_fix = False
        valid_sql = False
        uses_valid_columns = False

        try:
            parsed = sqlglot.parse_one(sql, read="duckdb")
            valid_sql = True
        except Exception:
            valid_sql = False

        if valid_sql:
            refs = _extract_table_column_refs(sql)
            valid_cols = {"emp_no", "first_name", "last_name"}
            uses_valid_columns = all(col in valid_cols for _, col in refs)
            has_name_col = any(col == "name" for _, col in refs)

            if uses_valid_columns and not has_name_col:
                attempted_fix = True

        passed = valid_sql and attempted_fix
        score = 1.0 if passed else (0.5 if valid_sql else 0.0)
        return ProbeResult(
            name="repair_capability",
            passed=passed,
            detail=f"attempted_fix={attempted_fix} valid_sql={valid_sql} uses_valid_columns={uses_valid_columns}",
            response=content[:500],
            latency_ms=round(latency, 2),
            score=score,
        )
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        return ProbeResult(
            name="repair_capability",
            passed=False,
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=round(latency, 2),
            score=0.0,
        )


# ── Probe 8: latency ──────────────────────────────────────────

def _probe_latency(
    llm: LLMService,
    repeat_count: int = _LATENCY_PROBE_MIN_REPEATS,
    timeout_s: float = 15.0,
) -> ProbeResult:
    repeats = max(1, int(repeat_count))
    latencies = []
    passes = 0
    for _ in range(repeats):
        start = time.perf_counter()
        try:
            result = llm.chat(
                [{"role": "user", "content": "Respond with 'ok'."}],
                timeout=timeout_s,
            )
            elapsed = (time.perf_counter() - start) * 1000
            content = _llm_content_text(result)
            if content.strip():
                passes += 1
            latencies.append(elapsed)
        except Exception:
            latencies.append(5000.0)
    avg_latency = sum(latencies) / len(latencies) if latencies else 5000.0
    sorted_lats = sorted(latencies)
    p50 = sorted_lats[len(sorted_lats) // 2] if sorted_lats else avg_latency
    p95 = sorted_lats[int(len(sorted_lats) * 0.95)] if len(sorted_lats) >= 20 else sorted_lats[-1] if sorted_lats else avg_latency
    passed = passes >= max(1, repeats - 1)
    return ProbeResult(
        name="latency",
        passed=passed,
        detail=f"avg={avg_latency:.0f}ms p50={p50:.0f}ms p95={p95:.0f}ms passes={passes}/{repeats}",
        latency_ms=round(avg_latency, 2),
        score=1.0 if passed else 0.0,
    )


def _extract_sql_block(text: str) -> Optional[str]:
    match = re.search(r"```(?:\w+)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
    lines = text.strip().splitlines()
    sql_lines = [line for line in lines if line.strip() and not line.strip().startswith("--")]
    if sql_lines:
        return " ".join(sql_lines)
    return None


# ═══════════════════════════════════════════════════════════════
# Provider metadata probe (actual HTTP call)
# ═══════════════════════════════════════════════════════════════

def _probe_provider_meta(provider: str, endpoint: str, model: str) -> ProbeResult:
    endpoint_lower = (endpoint or "").lower()
    model_lower = (model or "").lower()

    from services.sql_routing.llm_capability import _MODEL_FAMILY_KEYWORDS

    family = "unknown"
    context_window = 4096
    max_output = 2048
    quantization = ""
    matched_len = 0

    # Keyword-based family detection first
    for keyword, config in _MODEL_FAMILY_KEYWORDS.items():
        if keyword in model_lower and len(keyword) > matched_len:
            family = config.get("family", "unknown")
            matched_len = len(keyword)

    # Try actual API call for metadata
    if provider == "ollama":
        try:
            base_url = endpoint_lower.replace("/v1", "").rstrip("/")
            if not base_url:
                base_url = "http://localhost:11434"
            show_url = f"{base_url}/api/show"
            resp = httpx.post(show_url, json={"model": model}, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                if "model_info" in data:
                    mi = data["model_info"]
                    ctx = mi.get("context_length", mi.get("context_window", mi.get("llm.context_length", context_window)))
                    context_window = int(ctx)
                    llm_info = mi.get("llm", {})
                    max_out = mi.get("max_tokens", llm_info.get("max_tokens", None))
                    if max_out:
                        max_output = int(max_out)
                    else:
                        max_output = min(context_window, 8192)
                    quant = mi.get("quantization", "")
                    quantization = str(quant) if quant else ""
                if "details" in data:
                    det = data["details"]
                    param_size = det.get("parameter_size", "") or ""
                    if param_size and family == "unknown":
                        family_match = re.search(r"(\d+)b", param_size.lower())
                        if family_match:
                            size_gb = float(family_match.group(1))
                            if size_gb >= 30:
                                family = "llama"
                            elif size_gb >= 7:
                                family = "qwen"
                            else:
                                family = "gemma"
                if "modelfile" in data:
                    mf = data.get("modelfile", "")
                    if "FROM" in mf:
                        from_part = mf.split("FROM")[-1].splitlines()[0].strip()
                        model_lower_from = from_part.lower()
                        for keyword, config in _MODEL_FAMILY_KEYWORDS.items():
                            if keyword in model_lower_from and len(keyword) > matched_len:
                                family = config.get("family", "unknown")
                                matched_len = len(keyword)
        except Exception:
            pass

    elif provider in ("openai", "azure"):
        try:
            headers = {}
            from services.llm_service import get_llm_config
            db_cfg = get_llm_config()
            api_key = db_cfg.get("api_key", "")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            models_url = endpoint_lower.rstrip("/") + "/models"
            resp = httpx.get(models_url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                models_list = data.get("data", [])
                for m in models_list:
                    m_id = (m.get("id") or "").lower()
                    if model_lower in m_id or m_id in model_lower:
                        raw = m.get("raw", {})
                        context_window = int(raw.get("context_length", raw.get("max_context", context_window)))
                        break
        except Exception:
            pass

    return ProbeResult(
        name="provider_meta",
        passed=True,
        detail=(
            f"provider={provider} family={family} "
            f"context_window={context_window} max_output={max_output} "
            f"quantization={quantization}"
        ),
        score=1.0,
    )


# ═══════════════════════════════════════════════════════════════
# Result aggregation
# ═══════════════════════════════════════════════════════════════

def _aggregate_probe_results(
    provider: str,
    endpoint: str,
    model: str,
    results: dict[str, ProbeResult],
    start_time: float,
    probe_level: str = _PROBE_LEVEL_FULL,
    seed_caps: Optional[dict] = None,
) -> dict:
    caps = {
        "model_meta": {
            "model_family": "unknown",
            "model_size_b": 0.0,
            "provider": provider,
            "context_window": 4096,
            "max_output_tokens": 2048,
            "quantization": "",
        },
        "structured_output": {
            "supports_json_schema": False,
            "supports_json_object": True,
            "json_mode_reliable": False,
            "json_output_leak_markdown": True,
            "json_output_field_accuracy": 0.5,
        },
        "sql_quality": {
            "sql_accuracy_tier": "low",
            "sql_safety_compliant": False,
            "sql_column_hallucination_rate": 0.5,
            "sql_table_hallucination_rate": 0.2,
            "sql_hallucination_risk": "high",
            "sql_join_accuracy": 0.3,
            "sql_group_by_compliance": 0.3,
            "sql_aggregate_placement": 0.4,
            "sql_syntax_validity": 0.5,
            "sql_readonly_compliance": 0.5,
        },
        "instruction": {
            "system_prompt_adherence": "weak",
            "instruction_following_score": 0.4,
            "format_compliance": 0.4,
            "reasoning_leak": False,
            "empty_output_rate": 0.2,
        },
        "repair": {
            "repair_capability": False,
            "repair_success_rate": 0.0,
            "error_feedback_utilization": 0.1,
            "max_useful_repair_attempts": 0,
        },
        "performance": {
            "recommended_temperature": 0.3,
            "recommended_max_tokens": 4096,
            "supports_streaming": True,
            "supports_vision": False,
            "supports_tool_calling": False,
            "avg_response_latency_ms": 2000.0,
            "latency_p50_ms": 1800.0,
            "latency_p95_ms": 4000.0,
            "token_generation_speed": 30.0,
        },
        "probe_meta": {
            "model_key": f"{provider}:{endpoint}:{model}",
            "probe_version": 2,
            "probe_count": 1,
            "last_error": "",
            "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "probe_duration_ms": round((time.perf_counter() - start_time) * 1000, 2),
            "probe_level": probe_level,
        },
    }

    if isinstance(seed_caps, dict):
        for section in ("model_meta", "structured_output", "sql_quality", "instruction", "repair", "performance"):
            source = seed_caps.get(section)
            if isinstance(source, dict) and isinstance(caps.get(section), dict):
                caps[section].update(source)

    # ── Merge probe results into capabilities ──

    pm = results.get("provider_meta")
    if pm:
        parts = pm.detail.split()
        for p in parts:
            if p.startswith("family="):
                caps["model_meta"]["model_family"] = p[7:]
            if p.startswith("context_window="):
                try:
                    caps["model_meta"]["context_window"] = int(p[15:])
                except Exception:
                    pass
            if p.startswith("max_output="):
                try:
                    caps["model_meta"]["max_output_tokens"] = int(p[11:])
                except Exception:
                    pass
            if p.startswith("quantization="):
                caps["model_meta"]["quantization"] = p[13:]

    js = results.get("json_schema")
    if js:
        caps["structured_output"]["supports_json_schema"] = js.passed
        caps["structured_output"]["json_output_field_accuracy"] = js.score
        caps["structured_output"]["json_output_leak_markdown"] = "leaked markdown fence" in (js.detail or "")

    jo = results.get("json_object")
    if jo:
        caps["structured_output"]["supports_json_object"] = jo.passed
        caps["structured_output"]["json_mode_reliable"] = jo.passed and (jo.score > 0.5)
        if "leaked markdown fence" in (jo.detail or ""):
            caps["structured_output"]["json_output_leak_markdown"] = True

    sp = results.get("system_prompt")
    if sp:
        caps["instruction"]["system_prompt_adherence"] = "strict" if sp.passed else ("normal" if sp.score > 0 else "weak")
        caps["instruction"]["instruction_following_score"] = sp.score

    ss = results.get("sql_safety")
    if ss:
        caps["sql_quality"]["sql_safety_compliant"] = ss.passed
        caps["sql_quality"]["sql_readonly_compliance"] = ss.score

    sc = results.get("sql_column_accuracy")
    if sc:
        caps["sql_quality"]["sql_syntax_validity"] = sc.score
        hallucinations, total = _parse_accuracy_detail(sc.detail)
        if total > 0:
            rate = hallucinations / total
            caps["sql_quality"]["sql_column_hallucination_rate"] = rate
            if rate <= 0.1:
                caps["sql_quality"]["sql_hallucination_risk"] = "low"
            elif rate <= 0.3:
                caps["sql_quality"]["sql_hallucination_risk"] = "medium"
            else:
                caps["sql_quality"]["sql_hallucination_risk"] = "high"

    sg = results.get("sql_group_by")
    if sg:
        caps["sql_quality"]["sql_group_by_compliance"] = sg.score

    rc = results.get("repair_capability")
    if rc:
        caps["repair"]["repair_capability"] = rc.passed
        caps["repair"]["repair_success_rate"] = rc.score
        caps["repair"]["max_useful_repair_attempts"] = 1 if rc.passed else 0

    lat = results.get("latency")
    if lat:
        caps["performance"]["avg_response_latency_ms"] = lat.latency_ms
        parts = lat.detail.split()
        for p in parts:
            if p.startswith("p50="):
                try:
                    caps["performance"]["latency_p50_ms"] = float(p[4:].replace("ms", ""))
                except Exception:
                    pass
            if p.startswith("p95="):
                try:
                    caps["performance"]["latency_p95_ms"] = float(p[4:].replace("ms", ""))
                except Exception:
                    pass

    # ── Derived fields ──

    structured = caps["structured_output"]
    sql_qual = caps["sql_quality"]
    if structured.get("supports_json_schema") and structured.get("json_mode_reliable"):
        caps["performance"]["recommended_temperature"] = 0.3
    elif structured.get("supports_json_object"):
        caps["performance"]["recommended_temperature"] = 0.2
    else:
        caps["performance"]["recommended_temperature"] = 0.1

    safety = sql_qual.get("sql_safety_compliant", False)
    syntax = sql_qual.get("sql_syntax_validity", 0)
    has_schema = structured.get("supports_json_schema", False)
    group_by = sql_qual.get("sql_group_by_compliance", 0)
    col_hall_rate = sql_qual.get("sql_column_hallucination_rate", 0.5)

    # ── Populate unprobed fields from derived estimates ──
    sql_qual["sql_table_hallucination_rate"] = round(col_hall_rate * 0.6, 2)
    sql_qual["sql_join_accuracy"] = round(group_by * 0.9 + syntax * 0.1, 2)
    sql_qual["sql_aggregate_placement"] = round(group_by * 0.85 + syntax * 0.15, 2)
    caps["instruction"]["reasoning_leak"] = caps["structured_output"].get("json_output_leak_markdown", False)
    caps["instruction"]["empty_output_rate"] = round(max(0.0, 1.0 - caps["instruction"].get("format_compliance", 0.5)), 2)

    if safety and syntax > 0.8 and has_schema and group_by > 0.5:
        caps["sql_quality"]["sql_accuracy_tier"] = "high"
    elif safety and syntax > 0.8 and has_schema:
        caps["sql_quality"]["sql_accuracy_tier"] = "high"
    elif safety and syntax > 0.5 and group_by > 0.5:
        caps["sql_quality"]["sql_accuracy_tier"] = "high"
    elif safety and syntax > 0.5:
        caps["sql_quality"]["sql_accuracy_tier"] = "medium"
    elif group_by > 0.5:
        caps["sql_quality"]["sql_accuracy_tier"] = "medium"

    return caps


def _parse_accuracy_detail(detail: str) -> tuple[int, int]:
    m = re.search(r"hallucinations=(\d+)/(\d+)", detail)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 0


# ═══════════════════════════════════════════════════════════════
# Orchestration
# ═══════════════════════════════════════════════════════════════

def _load_existing_probe_count(provider: str, endpoint: str, model: str) -> int:
    from services.sql_routing.llm_capability import _load_capability_from_db
    try:
        existing = _load_capability_from_db(provider, endpoint, model)
        if existing:
            count = existing.get("probe_meta", {}).get("probe_count", 0)
            return int(count) + 1
    except Exception:
        pass
    return 1


def probe_sync(
    provider: str,
    endpoint: str,
    model: str,
    schema_hint: str = "",
    api_key: str | None = None,
    probe_level: str = _PROBE_LEVEL_FULL,
    progress_cb: Optional[ProbeProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> dict:
    from services.llm_service import get_llm_config
    from services.sql_routing.llm_capability import _capabilities_to_tier

    normalized_probe_level = _normalize_probe_level(probe_level)
    db_cfg = get_llm_config()
    cfg = {
        "provider": provider,
        "endpoint": endpoint,
        "model": model,
        "api_key": api_key if api_key is not None else db_cfg.get("api_key", ""),
        "temperature": 0.0,
        "max_tokens": 256 if normalized_probe_level == _PROBE_LEVEL_FAST else 512,
        "_probe_mode": True,
    }
    llm = LLMService(config=cfg)
    if not llm.is_configured():
        from services.sql_routing.llm_capability import _keyword_fallback
        LOGGER.warning("LLM not configured for probe_sync; returning keyword fallback")
        fallback = _keyword_fallback(provider, endpoint, model)
        _emit_probe_progress(
            progress_cb,
            {
                "event": "probe_completed",
                "provider": provider,
                "endpoint": endpoint,
                "model": model,
                "probe_level": fallback.get("probe_meta", {}).get("probe_level", normalized_probe_level),
                "capabilities": fallback,
                "tier": _capabilities_to_tier(fallback),
            },
        )
        return fallback

    start = time.perf_counter()
    results: dict[str, ProbeResult] = {}
    seed_caps: dict | None = None

    if normalized_probe_level == _PROBE_LEVEL_FAST:
        try:
            from services.sql_routing.llm_capability import _keyword_fallback

            seed_caps = _keyword_fallback(provider, endpoint, model)
        except Exception:
            seed_caps = None

    _emit_probe_progress(
        progress_cb,
        {
            "event": "probe_started",
            "provider": provider,
            "endpoint": endpoint,
            "model": model,
            "probe_level": normalized_probe_level,
            "capabilities": seed_caps,
            "tier": _capabilities_to_tier(seed_caps or {}),
        },
    )

    if normalized_probe_level == _PROBE_LEVEL_FAST:
        stage_plan: list[tuple[str, Callable[[], ProbeResult]]] = [
            ("provider_meta", lambda: _probe_provider_meta(provider, endpoint, model)),
            ("json_schema", lambda: _probe_json_schema(llm, timeout_s=12.0)),
            ("json_object", lambda: _probe_json_object(llm, timeout_s=12.0)),
            (
                "latency",
                lambda: _probe_latency(
                    llm,
                    repeat_count=_LATENCY_PROBE_FAST_REPEATS,
                    timeout_s=8.0,
                ),
            ),
        ]
    else:
        stage_plan = [
            ("provider_meta", lambda: _probe_provider_meta(provider, endpoint, model)),
            ("json_schema", lambda: _probe_json_schema(llm)),
            ("json_object", lambda: _probe_json_object(llm)),
            ("system_prompt", lambda: _probe_system_prompt(llm)),
            ("sql_safety", lambda: _probe_sql_safety(llm)),
            ("sql_column_accuracy", lambda: _probe_sql_column_accuracy(llm, schema_hint)),
            ("sql_group_by", lambda: _probe_sql_group_by(llm)),
            ("repair_capability", lambda: _probe_repair_capability(llm)),
            ("latency", lambda: _probe_latency(llm)),
        ]

    total_stages = len(stage_plan)

    for stage_index, (probe_name, probe_runner) in enumerate(stage_plan, start=1):
        if cancel_event is not None and cancel_event.is_set():
            cancelled_caps = _aggregate_probe_results(
                provider,
                endpoint,
                model,
                results,
                start,
                probe_level=normalized_probe_level,
                seed_caps=seed_caps,
            )
            _emit_probe_progress(
                progress_cb,
                {
                    "event": "probe_cancelled",
                    "provider": provider,
                    "endpoint": endpoint,
                    "model": model,
                    "probe_level": normalized_probe_level,
                    "capabilities": cancelled_caps,
                    "tier": _capabilities_to_tier(cancelled_caps),
                },
            )
            return cancelled_caps

        _emit_probe_progress(
            progress_cb,
            {
                "event": "stage_started",
                "provider": provider,
                "endpoint": endpoint,
                "model": model,
                "probe_level": normalized_probe_level,
                "stage": stage_index,
                "stage_total": total_stages,
                "probe": probe_name,
            },
        )
        try:
            result = probe_runner()
        except Exception as exc:
            result = ProbeResult(
                name=probe_name,
                passed=False,
                detail=f"{type(exc).__name__}: {exc}",
                score=0.0,
            )
        results[probe_name] = result
        snapshot_caps = _aggregate_probe_results(
            provider,
            endpoint,
            model,
            results,
            start,
            probe_level=normalized_probe_level,
            seed_caps=seed_caps,
        )
        _emit_probe_progress(
            progress_cb,
            {
                "event": "stage_completed",
                "provider": provider,
                "endpoint": endpoint,
                "model": model,
                "probe_level": normalized_probe_level,
                "stage": stage_index,
                "stage_total": total_stages,
                "probe": probe_name,
                "passed": result.passed,
                "score": result.score,
                "detail": result.detail,
                "latency_ms": result.latency_ms,
                "capabilities": snapshot_caps,
                "tier": _capabilities_to_tier(snapshot_caps),
            },
        )

    if cancel_event is not None and cancel_event.is_set():
        cancelled_caps = _aggregate_probe_results(
            provider,
            endpoint,
            model,
            results,
            start,
            probe_level=normalized_probe_level,
            seed_caps=seed_caps,
        )
        _emit_probe_progress(
            progress_cb,
            {
                "event": "probe_cancelled",
                "provider": provider,
                "endpoint": endpoint,
                "model": model,
                "probe_level": normalized_probe_level,
                "capabilities": cancelled_caps,
                "tier": _capabilities_to_tier(cancelled_caps),
            },
        )
        return cancelled_caps

    caps = _aggregate_probe_results(
        provider,
        endpoint,
        model,
        results,
        start,
        probe_level=normalized_probe_level,
        seed_caps=seed_caps,
    )

    # Increment probe_count from existing DB entry
    caps["probe_meta"]["probe_count"] = _load_existing_probe_count(provider, endpoint, model)

    _emit_probe_progress(
        progress_cb,
        {
            "event": "probe_completed",
            "provider": provider,
            "endpoint": endpoint,
            "model": model,
            "probe_level": normalized_probe_level,
            "capabilities": caps,
            "tier": _capabilities_to_tier(caps),
        },
    )

    LOGGER.info(
        "Probe complete for %s/%s level=%s probes=%s duration=%sms",
        provider,
        model,
        normalized_probe_level,
        ",".join(sorted(results.keys())),
        caps.get("probe_meta", {}).get("probe_duration_ms", "?"),
    )
    return caps


def trigger_async_probe(provider: str, endpoint: str, model: str, schema_hint: str = "") -> None:
    from services.sql_routing.llm_capability import _save_capability_to_db, _memory_cache_key, _memory_cache_set

    probe_key = _probe_cache_key(provider, endpoint, model)
    with _probe_in_progress_lock:
        if probe_key in _probe_in_progress:
            LOGGER.debug("Probe already in progress for %s/%s; skipping duplicate", provider, model)
            return
        _probe_in_progress.add(probe_key)

    cache_key = _memory_cache_key(provider, endpoint, model)

    def _probe_worker():
        try:
            full_caps = probe_sync(provider, endpoint, model, schema_hint=schema_hint)
            _save_capability_to_db(provider, endpoint, model, full_caps)
            _memory_cache_set(cache_key, full_caps)
            LOGGER.info("Async probe completed for %s/%s", provider, model)
        except Exception as exc:
            LOGGER.exception("Async probe failed for %s/%s", provider, model)
        finally:
            with _probe_in_progress_lock:
                _probe_in_progress.discard(probe_key)

    thread = threading.Thread(target=_probe_worker, daemon=True)
    thread.start()
