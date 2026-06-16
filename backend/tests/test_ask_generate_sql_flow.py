from __future__ import annotations

import json
import httpx
from types import SimpleNamespace

import services.ask_service as ask_service


def _semantic_hits_models() -> dict:
    models = [
        {
            "name": "olist_order_items_dataset",
            "table_reference": "olist_order_items_dataset",
            "columns": [
                {"name": "product_id", "type": "VARCHAR"},
                {"name": "seller_id", "type": "VARCHAR"},
                {"name": "price", "type": "DOUBLE"},
            ],
        },
        {
            "name": "product_category_name_translation",
            "table_reference": "product_category_name_translation",
            "columns": [
                {"name": "product_category_name", "type": "VARCHAR"},
                {"name": "product_category_name_english", "type": "VARCHAR"},
            ],
        },
        {
            "name": "olist_sellers_dataset",
            "table_reference": "olist_sellers_dataset",
            "columns": [
                {"name": "seller_id", "type": "VARCHAR"},
                {"name": "seller_city", "type": "VARCHAR"},
            ],
        },
    ]
    return {"models": models, "relations": [], "has_hits": True}


def _patch_prompt_helpers(monkeypatch):
    monkeypatch.setattr(ask_service, "_render_system_prompt", lambda *args, **kwargs: "SYSTEM")
    monkeypatch.setattr(ask_service, "_render_project_prompt", lambda *args, **kwargs: "PROJECT_PROMPT")
    monkeypatch.setattr(ask_service, "_dialect_hint_for_project", lambda *args, **kwargs: "Use DuckDB SQL")


def test_generate_sql_repairs_unknown_columns_from_bad_alias(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    bad_sql = (
        "SELECT T2.product_category_name_english, T3.seller_city, SUM(T1.price) AS total_sales "
        "FROM olist_order_items_dataset T1 "
        "JOIN olist_sellers_dataset T3 ON T1.seller_id = T3.seller_id "
        "JOIN olist_order_items_dataset T2 ON T1.product_id = T2.product_id "
        "GROUP BY T3.seller_city"
    )
    repaired_sql = (
        "SELECT t.product_category_name_english, s.seller_city, SUM(o.price) AS total_sales "
        "FROM olist_order_items_dataset o "
        "JOIN product_category_name_translation t ON o.product_id = t.product_category_name "
        "JOIN olist_sellers_dataset s ON o.seller_id = s.seller_id "
        "GROUP BY t.product_category_name_english, s.seller_city"
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {
                "content": json.dumps(
                    {
                        "sql": bad_sql,
                        "summary": "bad sql generated",
                        "reasoning": "initial",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    def fake_repair(*args, **kwargs):
        return {
            "sql": repaired_sql,
            "summary": "repaired",
            "reasoning": "fixed unknown columns",
            "configured": True,
        }

    monkeypatch.setattr(ask_service, "_repair_sql", fake_repair)

    result = ask_service._generate_sql(
        question="按 Product 和 City 统计总销售额",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["olist_order_items_dataset", "product_category_name_translation", "olist_sellers_dataset"],
        semantic_hits=_semantic_hits_models(),
        language="zh",
        analysis={"tier": "simple", "dimensions": ["Product", "City"], "metrics": ["total_sales"], "entities": [], "sub_questions": []},
    )

    assert result.get("sql") == repaired_sql
    assert result.get("sql_engine") in {"direct_llm_repair", "fewshot_cot_repair", "decompose_merge_repair"}


def test_generate_sql_skips_second_generation_after_successful_column_repair(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    bad_sql = "SELECT o.product_category_name_english FROM olist_order_items_dataset o"
    repaired_sql = "SELECT t.product_category_name_english FROM product_category_name_translation t"
    llm_calls = {"count": 0}

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            llm_calls["count"] += 1
            return {
                "content": json.dumps(
                    {
                        "sql": bad_sql,
                        "summary": "bad sql generated",
                        "reasoning": "initial",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)
    monkeypatch.setattr(
        ask_service,
        "_repair_sql",
        lambda *args, **kwargs: {
            "sql": repaired_sql,
            "summary": "repaired",
            "reasoning": "fixed unknown columns",
            "configured": True,
        },
    )

    result = ask_service._generate_sql(
        question="查看商品英文类目",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["olist_order_items_dataset", "product_category_name_translation"],
        semantic_hits=_semantic_hits_models(),
        language="zh",
        analysis={"tier": "multi_dimension", "dimensions": [], "metrics": [], "entities": [], "sub_questions": [], "filters": []},
    )

    assert result.get("sql") == repaired_sql
    assert result.get("sql_engine") == "fewshot_cot_repair"
    assert llm_calls["count"] == 1


def test_generate_sql_skips_second_generation_after_orphan_cte_repair(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    bad_sql = (
        "WITH t1 AS (SELECT seller_id FROM olist_sellers_dataset) "
        "SELECT seller_id FROM olist_sellers_dataset"
    )
    repaired_sql = "SELECT seller_id FROM olist_sellers_dataset"
    llm_calls = {"count": 0}

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            llm_calls["count"] += 1
            return {
                "content": json.dumps(
                    {
                        "sql": bad_sql,
                        "summary": "orphan cte sql",
                        "reasoning": "initial",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)
    monkeypatch.setattr(
        ask_service,
        "_repair_sql",
        lambda *args, **kwargs: {
            "sql": repaired_sql,
            "summary": "repaired",
            "reasoning": "removed orphan cte",
            "configured": True,
        },
    )

    result = ask_service._generate_sql(
        question="查看卖家ID",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["olist_sellers_dataset"],
        semantic_hits=_semantic_hits_models(),
        language="zh",
        analysis={"tier": "multi_dimension", "dimensions": ["seller_id"], "metrics": [], "entities": [], "sub_questions": [], "filters": []},
    )

    assert result.get("sql") == repaired_sql
    assert result.get("sql_engine") == "fewshot_cot_repair"
    assert llm_calls["count"] == 1


def test_generate_sql_auto_completes_single_cte_without_llm_repair(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    cte_only_sql = "WITH employee_salary_agg AS (SELECT 1 AS avg_salary)"

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {
                "content": json.dumps(
                    {
                        "sql": cte_only_sql,
                        "summary": "cte only",
                        "reasoning": "initial",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)
    monkeypatch.setattr(
        ask_service,
        "_validate_sql_syntax_for_project",
        lambda sql, _project_id: ["Failed to parse any statement following CTE"] if ask_service._normalize_sql_candidate(sql) == cte_only_sql else [],
    )

    def fail_repair(*args, **kwargs):
        raise AssertionError("_repair_sql should not be called when local CTE completion succeeds")

    monkeypatch.setattr(ask_service, "_repair_sql", fail_repair)

    result = ask_service._generate_sql(
        question="统计平均薪资",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["employees"],
        semantic_hits=_semantic_hits_models(),
        language="zh",
        analysis={"tier": "simple", "dimensions": [], "metrics": [], "entities": [], "sub_questions": []},
    )

    normalized_sql = str(result.get("sql") or "").lower()
    assert "with employee_salary_agg as" in normalized_sql
    assert "select * from \"employee_salary_agg\"" in normalized_sql
    assert str(result.get("sql_engine") or "").endswith("_repair")


def test_generate_sql_repairs_syntax_issues_before_return(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    invalid_sql = "WITH employee_salary_agg AS (SELECT 1 AS avg_salary), salary_top AS (SELECT avg_salary FROM employee_salary_agg)"
    repaired_sql = (
        "WITH employee_salary_agg AS (SELECT 1 AS avg_salary), "
        "salary_top AS (SELECT avg_salary FROM employee_salary_agg) "
        "SELECT * FROM salary_top"
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {
                "content": json.dumps(
                    {
                        "sql": invalid_sql,
                        "summary": "invalid cte",
                        "reasoning": "initial",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)
    monkeypatch.setattr(
        ask_service,
        "_validate_sql_syntax_for_project",
        lambda sql, _project_id: ["Failed to parse any statement following CTE"] if ask_service._normalize_sql_candidate(sql) == invalid_sql else [],
    )

    captured = {"error": ""}

    def fake_repair(_question, _failed_sql, error, *_args, **_kwargs):
        captured["error"] = error
        return {
            "sql": repaired_sql,
            "summary": "syntax repaired",
            "reasoning": "added final select",
            "configured": True,
        }

    monkeypatch.setattr(ask_service, "_repair_sql", fake_repair)

    result = ask_service._generate_sql(
        question="统计平均薪资",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["employees"],
        semantic_hits=_semantic_hits_models(),
        language="zh",
        analysis={"tier": "simple", "dimensions": [], "metrics": [], "entities": [], "sub_questions": []},
    )

    assert result.get("sql") == repaired_sql
    assert str(result.get("sql_engine") or "").endswith("_repair")
    assert "SQL syntax issues" in captured["error"]


def test_generate_sql_groupby_repair_includes_aggregation_hint(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    incomplete_group_sql = (
        "SELECT t.product_category_name_english, s.seller_city, SUM(o.price) AS total_sales "
        "FROM olist_order_items_dataset o "
        "JOIN product_category_name_translation t ON o.product_id = t.product_category_name "
        "JOIN olist_sellers_dataset s ON o.seller_id = s.seller_id "
        "GROUP BY s.seller_city"
    )
    repaired_sql = (
        "SELECT t.product_category_name_english, s.seller_city, SUM(o.price) AS total_sales "
        "FROM olist_order_items_dataset o "
        "JOIN product_category_name_translation t ON o.product_id = t.product_category_name "
        "JOIN olist_sellers_dataset s ON o.seller_id = s.seller_id "
        "GROUP BY t.product_category_name_english, s.seller_city"
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {
                "content": json.dumps(
                    {
                        "sql": incomplete_group_sql,
                        "summary": "incomplete group by",
                        "reasoning": "initial",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    captured = {"error": ""}

    def fake_repair(question, failed_sql, error, *args, **kwargs):
        captured["error"] = error
        return {
            "sql": repaired_sql,
            "summary": "repaired group by",
            "reasoning": "fixed group by",
            "configured": True,
        }

    monkeypatch.setattr(ask_service, "_repair_sql", fake_repair)

    result = ask_service._generate_sql(
        question="按 Product 和 City 统计总销售额",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["olist_order_items_dataset", "product_category_name_translation", "olist_sellers_dataset"],
        semantic_hits=_semantic_hits_models(),
        language="zh",
        analysis={"tier": "simple", "dimensions": ["Product", "City"], "metrics": ["total_sales"], "entities": [], "sub_questions": []},
    )

    sql_text = str(result.get("sql") or "")
    assert sql_text
    if captured["error"]:
        assert "Aggregation consistency issues:" in captured["error"]
    normalized_sql = sql_text.lower()
    assert "group by" in normalized_sql
    assert "t.product_category_name_english" in normalized_sql
    assert "s.seller_city" in normalized_sql


def test_generate_sql_groupby_auto_completion_skips_llm_repair(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    incomplete_group_sql = (
        "SELECT t.product_category_name_english, s.seller_city, SUM(o.price) AS total_sales "
        "FROM olist_order_items_dataset o "
        "JOIN product_category_name_translation t ON o.product_id = t.product_category_name "
        "JOIN olist_sellers_dataset s ON o.seller_id = s.seller_id "
        "GROUP BY s.seller_city"
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {
                "content": json.dumps(
                    {
                        "sql": incomplete_group_sql,
                        "summary": "incomplete group by",
                        "reasoning": "initial",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    def fail_repair(*args, **kwargs):
        raise AssertionError("_repair_sql should not be called when local GROUP BY completion succeeds")

    monkeypatch.setattr(ask_service, "_repair_sql", fail_repair)

    result = ask_service._generate_sql(
        question="按 Product 和 City 统计总销售额",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["olist_order_items_dataset", "product_category_name_translation", "olist_sellers_dataset"],
        semantic_hits=_semantic_hits_models(),
        language="zh",
        analysis={"tier": "simple", "dimensions": ["Product", "City"], "metrics": ["total_sales"], "entities": [], "sub_questions": []},
    )

    normalized_sql = str(result.get("sql") or "").lower()
    assert "group by" in normalized_sql
    assert "t.product_category_name_english" in normalized_sql
    assert "s.seller_city" in normalized_sql


def test_generate_sql_opens_unknown_issue_circuit_on_repeated_alias_scope_leak(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "unknown_issue_bucket_circuit_threshold", 2)

    problematic_sql = (
        "WITH sales_cte AS ("
        "SELECT oi.product_id, SUM(oi.price) AS total_sales "
        "FROM olist_order_items_dataset oi "
        "GROUP BY oi.product_id"
        ") "
        "SELECT oi.product_id, oi.price, SUM(sales_cte.total_sales) AS grand_total "
        "FROM sales_cte "
        "GROUP BY oi.product_id, oi.price"
    )

    semantic_hits = {
        "has_hits": True,
        "models": [
            {
                "name": "olist_order_items_dataset",
                "table_reference": "olist_order_items_dataset",
                "columns": [
                    {"name": "product_id", "type": "VARCHAR"},
                    {"name": "price", "type": "DOUBLE"},
                ],
            },
        ],
        "relations": [],
    }

    llm_calls = {"count": 0}

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            llm_calls["count"] += 1
            return {
                "content": json.dumps(
                    {
                        "sql": problematic_sql,
                        "summary": "bad alias scope",
                        "reasoning": "initial",
                    }
                )
            }

    repair_calls = {"count": 0}

    def fake_repair(*args, **kwargs):
        repair_calls["count"] += 1
        return {
            "sql": None,
            "summary": "repair failed",
            "reasoning": "no fix",
            "configured": True,
        }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)
    monkeypatch.setattr(ask_service, "_repair_sql", fake_repair)
    monkeypatch.setattr(
        ask_service,
        "_select_sql_strategy",
        lambda analysis, has_knowledge: {"engine": "fewshot_cot", "max_retries": 3, "use_examples": False},
    )

    result = ask_service._generate_sql(
        question="测试别名作用域修复回路",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=[
            "olist_order_items_dataset",
        ],
        semantic_hits=semantic_hits,
        language="zh",
        analysis={
            "tier": "multi_dimension",
            "dimensions": ["City"],
            "metrics": ["Sales"],
            "entities": [],
            "sub_questions": [],
        },
    )

    assert repair_calls["count"] == 1
    assert llm_calls["count"] >= 2
    assert result.get("sql") is None
    assert str(result.get("sql_engine") or "").endswith("_validation_circuit_open")
    assert "repeated unknown-column bucket" in str(result.get("reasoning") or "").lower()


def test_generate_sql_opens_unknown_issue_circuit_on_mixed_circuitable_buckets(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "unknown_issue_bucket_circuit_threshold", 2)

    generated_sql = "SELECT o.order_id FROM olist_orders_dataset o"
    semantic_hits = {
        "has_hits": True,
        "models": [
            {
                "name": "olist_orders_dataset",
                "table_reference": "olist_orders_dataset",
                "columns": [{"name": "order_id", "type": "VARCHAR"}],
            }
        ],
        "relations": [],
    }

    llm_calls = {"count": 0}

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            llm_calls["count"] += 1
            return {
                "content": json.dumps(
                    {
                        "sql": generated_sql,
                        "summary": "bad owner",
                        "reasoning": "initial",
                    }
                )
            }

    guard_calls = {"count": 0}

    class FakeGuard:
        def inspect(self, *_args, **_kwargs):
            guard_calls["count"] += 1
            if guard_calls["count"] == 1:
                bad_columns = ["o.order_id (belongs on: olist_orders_dataset, olist_order_items_dataset)"]
            else:
                bad_columns = ["o.order_id (belongs on: olist_orders_dataset)"]
            return SimpleNamespace(
                syntax_issues=[],
                columns_inconclusive=False,
                bad_columns=bad_columns,
            )

    repair_calls = {"count": 0}

    def fake_repair(*args, **kwargs):
        repair_calls["count"] += 1
        return {
            "sql": None,
            "summary": "repair failed",
            "reasoning": "no fix",
            "configured": True,
        }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)
    monkeypatch.setattr(ask_service, "_candidate_guard", lambda: FakeGuard())
    monkeypatch.setattr(ask_service, "_repair_sql", fake_repair)
    monkeypatch.setattr(
        ask_service,
        "_select_sql_strategy",
        lambda analysis, has_knowledge: {"engine": "fewshot_cot", "max_retries": 3, "use_examples": False},
    )
    monkeypatch.setattr(ask_service, "_apply_owner_selector_rules", lambda sql, *_args, **_kwargs: sql)
    monkeypatch.setattr(ask_service, "_apply_hallucinated_column_rewrite_rules", lambda sql, *_args, **_kwargs: sql)
    monkeypatch.setattr(ask_service, "_apply_alias_scope_rewrite_rules", lambda sql, *_args, **_kwargs: sql)
    monkeypatch.setattr(ask_service, "_validate_sql_columns", lambda _sql, _hit_models: ["o.order_id still unknown"])
    monkeypatch.setattr(ask_service, "_enforce_group_by_constraints", lambda sql, *_args, **_kwargs: (sql, []))

    result = ask_service._generate_sql(
        question="测试混合列归属错误回路",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["olist_orders_dataset"],
        semantic_hits=semantic_hits,
        language="zh",
        analysis={
            "tier": "multi_dimension",
            "dimensions": ["order_id"],
            "metrics": ["count"],
            "entities": [],
            "sub_questions": [],
        },
    )

    assert repair_calls["count"] == 1
    assert llm_calls["count"] == 2
    assert result.get("sql") is None
    assert str(result.get("sql_engine") or "").endswith("_validation_circuit_open")
    assert "repeated unknown-column bucket" in str(result.get("reasoning") or "").lower()


def test_repair_sql_includes_ambiguous_owner_hint_in_prompt(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    captured_prompt = {"user": ""}

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            captured_prompt["user"] = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
            return {
                "content": json.dumps(
                    {
                        "sql": "SELECT o.order_id FROM olist_orders_dataset o",
                        "summary": "repaired",
                        "reasoning": "used owner hint",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)
    monkeypatch.setattr(ask_service, "_dialect_hint_for_project", lambda *args, **kwargs: "Use DuckDB SQL")

    repair = ask_service._repair_sql(
        question="测试歧义列归属",
        failed_sql="SELECT t3.order_id FROM olist_orders_dataset t3",
        error="T3.order_id (belongs on: olist_order_items_dataset, olist_orders_dataset)",
        project_id=1,
        semantic_context="Project semantic model: ...",
        language="zh",
        hit_models=[
            {"name": "olist_orders_dataset", "table_reference": "olist_orders_dataset", "columns": [{"name": "order_id"}]},
            {"name": "olist_order_items_dataset", "table_reference": "olist_order_items_dataset", "columns": [{"name": "order_id"}]},
        ],
    )

    assert repair["sql"]
    assert "Ambiguous owner resolution hints:" in captured_prompt["user"]
    assert "prefer owner 'olist_orders_dataset'" in captured_prompt["user"]
    assert "ORDER BY and HAVING columns must be GROUP BY keys or wrapped in aggregate functions" in captured_prompt["user"]


def test_repair_sql_local_preflight_prunes_orphan_cte_without_llm_roundtrip(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "sql_repair_local_preflight_enabled", True)
    monkeypatch.setattr(ask_service, "_validate_sql_syntax_for_project", lambda _sql, _project_id: [])

    chat_calls = {"count": 0}

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            chat_calls["count"] += 1
            raise AssertionError("LLM chat should not be called when local preflight resolves orphan CTE")

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    repair = ask_service._repair_sql(
        question="查看员工编号",
        failed_sql=(
            "WITH employees_roles AS (SELECT emp_no FROM employees) "
            "SELECT emp_no FROM employees"
        ),
        error="CTE(s) defined but never referenced: employees_roles",
        project_id=1,
        semantic_context="Project semantic model: employees(emp_no)",
        language="zh",
    )

    normalized_sql = str(repair.get("sql") or "").lower()
    assert normalized_sql == "select emp_no from employees"
    assert chat_calls["count"] == 0
    assert "local repair preflight" in str(repair.get("reasoning") or "").lower()


def test_repair_sql_local_preflight_rewrites_dotted_alias_without_llm_roundtrip(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "sql_repair_local_preflight_enabled", True)

    def _fake_validate(sql: str, _project_id: int) -> list[str]:
        return [] if "department_dept_no" in sql else ["Parser Error: syntax error at or near '.'"]

    monkeypatch.setattr(ask_service, "_validate_sql_syntax_for_project", _fake_validate)

    chat_calls = {"count": 0}

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            chat_calls["count"] += 1
            raise AssertionError("LLM chat should not be called when local preflight resolves dotted alias syntax")

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    repair = ask_service._repair_sql(
        question="按部门查看编号",
        failed_sql="SELECT T1.dept_no AS department.dept_no FROM dept_emp T1",
        error=(
            "Parser Error: syntax error at or near \".\"\n"
            "LINE 1: SELECT T1.dept_no AS department.dept_no FROM dept_emp T1"
        ),
        project_id=1,
        semantic_context="Project semantic model: dept_emp(dept_no)",
        language="zh",
    )

    normalized_sql = str(repair.get("sql") or "").lower()
    assert "as department_dept_no" in normalized_sql
    assert chat_calls["count"] == 0
    assert "local repair preflight" in str(repair.get("reasoning") or "").lower()


def test_repair_sql_skips_llm_when_remaining_budget_is_too_low(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "sql_repair_local_preflight_enabled", False)
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "sql_repair_skip_if_remaining_budget_below_s", 8.0)

    chat_calls = {"count": 0}

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            chat_calls["count"] += 1
            raise AssertionError("LLM chat should be skipped when remaining repair budget is too low")

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    repair = ask_service._repair_sql(
        question="测试预算不足",
        failed_sql="SELECT t1.unknown_column FROM dept_emp t1",
        error="Unknown columns: t1.unknown_column",
        project_id=1,
        semantic_context="Project semantic model: dept_emp(emp_no, dept_no)",
        language="zh",
        timeout_cap_s=1.0,
    )

    assert repair.get("sql") is None
    assert "low remaining generation budget" in str(repair.get("reasoning") or "").lower()
    assert chat_calls["count"] == 0


def test_repair_sql_does_not_retry_after_timeout_error(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "sql_repair_local_preflight_enabled", False)
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "sql_repair_skip_if_remaining_budget_below_s", 0.5)

    chat_calls = {"count": 0}

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            chat_calls["count"] += 1
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    import services.sql_routing.llm_capability as llm_capability

    monkeypatch.setattr(
        llm_capability,
        "_get_repair_config",
        lambda _tier: {
            "max_repair_attempts": 2,
            "json_parse_retries": 1,
            "skip_repair_if_json_empty": False,
            "repair_timeout_s": 60,
            "retry_on_binder_error": True,
        },
    )

    repair = ask_service._repair_sql(
        question="测试超时",
        failed_sql="SELECT t1.order_id FROM olist_orders_dataset t1",
        error="column not found",
        project_id=1,
        semantic_context="Project semantic model: olist_orders_dataset(order_id)",
        language="zh",
        timeout_cap_s=30.0,
    )

    assert repair.get("sql") is None
    assert chat_calls["count"] == 1
    assert "readtimeout" in str(repair.get("reasoning") or "").lower()


def test_repair_sql_opens_unknown_issue_circuit_on_mixed_circuitable_buckets(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "unknown_issue_bucket_circuit_threshold", 2)
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "sql_repair_local_preflight_enabled", False)
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "sql_repair_skip_if_remaining_budget_below_s", 0.5)

    import services.sql_routing.llm_capability as llm_capability

    monkeypatch.setattr(
        llm_capability,
        "_get_repair_config",
        lambda _tier: {
            "max_repair_attempts": 3,
            "json_parse_retries": 1,
            "skip_repair_if_json_empty": False,
            "repair_timeout_s": 60,
            "retry_on_binder_error": True,
        },
    )

    llm_calls = {"count": 0}

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            llm_calls["count"] += 1
            if llm_calls["count"] == 1:
                sql = "SELECT t3.order_id FROM olist_sellers_dataset t3"
            else:
                sql = "SELECT t3.product_id FROM olist_sellers_dataset t3"
            return {
                "content": json.dumps(
                    {
                        "sql": sql,
                        "summary": "repair candidate",
                        "reasoning": "candidate",
                    }
                )
            }

    validation_calls = {"count": 0}

    def fake_validate(_sql: str, _models: list[dict[str, object]]):
        validation_calls["count"] += 1
        if validation_calls["count"] == 1:
            return ["t3.order_id (belongs on: olist_orders_dataset, olist_order_items_dataset)"]
        return ["t3.product_id (belongs on: olist_order_items_dataset)"]

    events: list[tuple[str, dict, int | None]] = []

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)
    monkeypatch.setattr(ask_service, "_validate_sql_columns", fake_validate)
    monkeypatch.setattr(
        ask_service,
        "_emit_route_event",
        lambda event_type, payload, project_id=None: events.append((event_type, payload, project_id)),
    )

    repair = ask_service._repair_sql(
        question="测试 repair 混合 issue 回路",
        failed_sql="SELECT t1.order_id FROM olist_sellers_dataset t1",
        error="Unknown columns: t1.order_id",
        project_id=1,
        semantic_context="Project semantic model: ...",
        language="zh",
        hit_models=[
            {"name": "olist_sellers_dataset", "table_reference": "olist_sellers_dataset", "columns": [{"name": "seller_id"}]},
            {"name": "olist_orders_dataset", "table_reference": "olist_orders_dataset", "columns": [{"name": "order_id"}]},
            {"name": "olist_order_items_dataset", "table_reference": "olist_order_items_dataset", "columns": [{"name": "order_id"}, {"name": "product_id"}]},
        ],
    )

    assert repair.get("sql") is None
    assert "repeated unknown-column bucket" in str(repair.get("reasoning") or "").lower()
    assert llm_calls["count"] == 2
    assert validation_calls["count"] == 2
    short_circuit = [item for item in events if item[0] == "sql_repair_short_circuit"]
    assert short_circuit
    _event_type, payload, _pid = short_circuit[-1]
    assert payload.get("reason") == "repeated_issue_bucket"
    assert int(payload.get("circuitable_issue_bucket_streak") or 0) >= 2


def test_generate_sql_includes_schema_link_and_structured_plan_hints(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    captured = {"users": []}

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            captured["users"].append("\n".join(m.get("content", "") for m in messages if m.get("role") == "user"))
            return {
                "content": json.dumps(
                    {
                        "sql": "SELECT s.seller_city, SUM(o.price) AS total_sales FROM olist_order_items_dataset o JOIN olist_sellers_dataset s ON o.seller_id = s.seller_id GROUP BY s.seller_city",
                        "summary": "ok",
                        "reasoning": "ok",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._generate_sql(
        question="按城市统计销售额",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["olist_order_items_dataset", "olist_sellers_dataset"],
        semantic_hits=_semantic_hits_models(),
        language="zh",
        analysis={"tier": "multi_dimension", "dimensions": ["City"], "metrics": ["Sales"], "entities": [], "sub_questions": []},
    )

    assert result.get("sql")
    all_user_prompts = "\n\n".join(captured["users"])
    assert "Schema linking owner preferences:" in all_user_prompts
    assert "Structured SQL plan:" in all_user_prompts


def test_generate_sql_includes_owner_lock_constraints_in_initial_prompt(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    captured = {"user_prompt": ""}

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            captured["user_prompt"] = "\n".join(
                m.get("content", "") for m in messages if m.get("role") == "user"
            )
            return {
                "content": json.dumps(
                    {
                        "sql": "SELECT s.seller_city, SUM(o.price) AS total_sales FROM olist_order_items_dataset o JOIN olist_sellers_dataset s ON o.seller_id = s.seller_id GROUP BY s.seller_city",
                        "summary": "ok",
                        "reasoning": "ok",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)
    monkeypatch.setattr(
        ask_service,
        "_resolve_analysis_to_schema",
        lambda analysis, models: {
            "dimensions_resolved": [{"column": "seller_city", "model": "olist_sellers_dataset"}],
            "metrics_resolved": [{"column": "price", "model": "olist_order_items_dataset"}],
            "entities_resolved": [],
        },
    )

    result = ask_service._generate_sql(
        question="按城市统计销售额",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["olist_order_items_dataset", "olist_sellers_dataset"],
        semantic_hits=_semantic_hits_models(),
        language="zh",
        analysis={"tier": "multi_dimension", "dimensions": ["城市"], "metrics": ["销售额"], "entities": [], "sub_questions": []},
    )

    assert result.get("sql")
    assert "Owner lock constraints:" in captured["user_prompt"]
    assert "seller_city -> olist_sellers_dataset" in captured["user_prompt"]


def test_generate_sql_prefers_resolved_dimensions_when_raw_cjk_labels_are_unmapped(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    expected_sql = (
        "SELECT t.product_category_name_english, s.seller_city, SUM(o.price) AS total_sales "
        "FROM olist_order_items_dataset o "
        "JOIN product_category_name_translation t ON o.product_id = t.product_category_name "
        "JOIN olist_sellers_dataset s ON o.seller_id = s.seller_id "
        "GROUP BY t.product_category_name_english, s.seller_city"
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {
                "content": json.dumps(
                    {
                        "sql": expected_sql,
                        "summary": "ok",
                        "reasoning": "resolved first",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)
    monkeypatch.setattr(
        ask_service,
        "_select_sql_strategy",
        lambda analysis, has_knowledge: {"engine": "fewshot_cot", "max_retries": 1, "use_examples": False},
    )
    monkeypatch.setattr(
        ask_service,
        "_resolve_analysis_to_schema",
        lambda _analysis, _models: {
            "dimensions_resolved": [
                {"column": "product_category_name_english", "model": "product_category_name_translation"},
                {"column": "seller_city", "model": "olist_sellers_dataset"},
            ],
            "metrics_resolved": [{"column": "price", "model": "olist_order_items_dataset"}],
            "entities_resolved": [],
        },
    )

    def fail_repair(*args, **kwargs):
        raise AssertionError("_repair_sql should not be called when resolved dimensions already satisfy GROUP BY")

    monkeypatch.setattr(ask_service, "_repair_sql", fail_repair)

    result = ask_service._generate_sql(
        question="按产品和城市统计销售额",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["olist_order_items_dataset", "product_category_name_translation", "olist_sellers_dataset"],
        semantic_hits=_semantic_hits_models(),
        language="zh",
        analysis={
            "tier": "multi_dimension",
            "dimensions": ["产品维度标签", "城市维度标签"],
            "metrics": ["销售额"],
            "entities": [],
            "sub_questions": [],
        },
    )

    assert result.get("sql") == expected_sql
    assert result.get("sql_engine") == "fewshot_cot"


def test_generate_sql_alias_scope_local_rewrite_skips_llm_repair(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    bad_sql = (
        "SELECT departments.dept_name, titles.title, AVG(salaries.salary) AS avg_salary "
        "FROM departments d "
        "JOIN dept_emp de ON d.dept_no = de.dept_no "
        "JOIN employees e ON de.emp_no = e.emp_no "
        "JOIN salaries s ON e.emp_no = s.emp_no "
        "JOIN titles t ON e.emp_no = t.emp_no "
        "GROUP BY departments.dept_name, titles.title"
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {
                "content": json.dumps(
                    {
                        "sql": bad_sql,
                        "summary": "alias scope issue",
                        "reasoning": "initial",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)
    monkeypatch.setattr(
        ask_service,
        "_select_sql_strategy",
        lambda analysis, has_knowledge: {"engine": "fewshot_cot", "max_retries": 1, "use_examples": False},
    )

    def fail_repair(*args, **kwargs):
        raise AssertionError("_repair_sql should not be called when alias-scope local rewrite succeeds")

    monkeypatch.setattr(ask_service, "_repair_sql", fail_repair)

    semantic_hits = {
        "has_hits": True,
        "relations": [],
        "models": [
            {
                "name": "departments",
                "table_reference": "departments",
                "columns": [
                    {"name": "dept_no", "type": "VARCHAR"},
                    {"name": "dept_name", "type": "VARCHAR"},
                ],
            },
            {
                "name": "dept_emp",
                "table_reference": "dept_emp",
                "columns": [
                    {"name": "emp_no", "type": "INTEGER"},
                    {"name": "dept_no", "type": "VARCHAR"},
                ],
            },
            {
                "name": "employees",
                "table_reference": "employees",
                "columns": [
                    {"name": "emp_no", "type": "INTEGER"},
                ],
            },
            {
                "name": "salaries",
                "table_reference": "salaries",
                "columns": [
                    {"name": "emp_no", "type": "INTEGER"},
                    {"name": "salary", "type": "DOUBLE"},
                ],
            },
            {
                "name": "titles",
                "table_reference": "titles",
                "columns": [
                    {"name": "emp_no", "type": "INTEGER"},
                    {"name": "title", "type": "VARCHAR"},
                ],
            },
        ],
    }

    result = ask_service._generate_sql(
        question="在这些雇员中哪些部门或者工作岗位的薪资比较高",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["departments", "dept_emp", "employees", "salaries", "titles"],
        semantic_hits=semantic_hits,
        language="zh",
        analysis={"tier": "multi_dimension", "dimensions": ["部门", "岗位"], "metrics": ["薪资"], "entities": [], "sub_questions": []},
    )

    sql_text = str(result.get("sql") or "")
    assert sql_text
    assert "d.dept_name" in sql_text
    assert "t.title" in sql_text
    assert "s.salary" in sql_text
    assert "group by incomplete" not in str(result.get("reasoning") or "").lower()


def test_generate_sql_local_rewrite_handles_ambiguous_owner_and_hallucinated_quantity(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    bad_sql = (
        "SELECT g.customer_id, SUM(o.quantity) AS total_quantity "
        "FROM olist_order_items_dataset o "
        "JOIN olist_orders_dataset ord ON o.order_id = ord.order_id "
        "JOIN olist_customers_dataset c ON ord.customer_id = c.customer_id "
        "JOIN olist_geolocation_dataset g ON c.customer_zip_code_prefix = g.geolocation_zip_code_prefix "
        "GROUP BY g.customer_id"
    )

    llm_calls = {"count": 0}

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            llm_calls["count"] += 1
            return {
                "content": json.dumps(
                    {
                        "sql": bad_sql,
                        "summary": "owner and quantity issue",
                        "reasoning": "initial",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)
    monkeypatch.setattr(
        ask_service,
        "_select_sql_strategy",
        lambda analysis, has_knowledge: {"engine": "fewshot_cot", "max_retries": 1, "use_examples": False},
    )

    def fail_repair(*args, **kwargs):
        raise AssertionError("_repair_sql should not be called when local ambiguous-owner and hallucinated-column rewrites succeed")

    monkeypatch.setattr(ask_service, "_repair_sql", fail_repair)

    semantic_hits = {
        "has_hits": True,
        "relations": [],
        "models": [
            {
                "name": "olist_customers_dataset",
                "table_reference": "olist_customers_dataset",
                "columns": [
                    {"name": "customer_id", "type": "VARCHAR"},
                    {"name": "customer_zip_code_prefix", "type": "VARCHAR"},
                ],
            },
            {
                "name": "olist_orders_dataset",
                "table_reference": "olist_orders_dataset",
                "columns": [
                    {"name": "order_id", "type": "VARCHAR"},
                    {"name": "customer_id", "type": "VARCHAR"},
                ],
            },
            {
                "name": "olist_order_items_dataset",
                "table_reference": "olist_order_items_dataset",
                "columns": [
                    {"name": "order_id", "type": "VARCHAR"},
                    {"name": "order_item_id", "type": "INTEGER"},
                ],
            },
            {
                "name": "olist_geolocation_dataset",
                "table_reference": "olist_geolocation_dataset",
                "columns": [
                    {"name": "geolocation_zip_code_prefix", "type": "VARCHAR"},
                ],
            },
        ],
    }

    result = ask_service._generate_sql(
        question="按客户统计销量",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=[
            "olist_order_items_dataset",
            "olist_orders_dataset",
            "olist_customers_dataset",
            "olist_geolocation_dataset",
        ],
        semantic_hits=semantic_hits,
        language="zh",
        analysis={"tier": "simple", "dimensions": ["customer_id"], "metrics": ["quantity"], "entities": [], "sub_questions": []},
    )

    sql_text = str(result.get("sql") or "").lower()
    assert llm_calls["count"] == 1
    assert str(result.get("sql_engine") or "").endswith("_rehint")
    assert "c.customer_id" in sql_text
    assert "g.customer_id" not in sql_text
    assert "sum(o.quantity)" not in sql_text
    assert "count(o.order_item_id)" in sql_text


def test_generate_sql_skips_decompose_merge_when_only_one_sub_question(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    llm_calls = {"count": 0}

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            llm_calls["count"] += 1
            return {
                "content": json.dumps(
                    {
                        "sql": (
                            "SELECT de.dept_no, AVG(s.salary) AS avg_salary "
                            "FROM dept_emp de "
                            "JOIN salaries s ON de.emp_no = s.emp_no "
                            "GROUP BY de.dept_no"
                        ),
                        "summary": "ok",
                        "reasoning": "ok",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)
    monkeypatch.setattr(
        ask_service,
        "_select_sql_strategy",
        lambda analysis, has_knowledge: {"engine": "decompose_merge", "max_retries": 1, "use_examples": False},
    )

    def fail_decompose(*args, **kwargs):
        raise AssertionError("_decompose_merge_sql should be bypassed when only one sub-question exists")

    monkeypatch.setattr(ask_service, "_decompose_merge_sql", fail_decompose)

    semantic_hits = {
        "has_hits": True,
        "relations": [],
        "models": [
            {
                "name": "dept_emp",
                "table_reference": "dept_emp",
                "columns": [
                    {"name": "dept_no", "type": "VARCHAR"},
                    {"name": "emp_no", "type": "INTEGER"},
                ],
            },
            {
                "name": "salaries",
                "table_reference": "salaries",
                "columns": [
                    {"name": "emp_no", "type": "INTEGER"},
                    {"name": "salary", "type": "DOUBLE"},
                ],
            },
        ],
    }

    result = ask_service._generate_sql(
        question="按部门统计平均薪资",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["dept_emp", "salaries"],
        semantic_hits=semantic_hits,
        language="zh",
        analysis={
            "tier": "compound",
            "dimensions": ["dept_no"],
            "metrics": ["avg_salary"],
            "entities": [],
            "sub_questions": ["按部门统计平均薪资"],
        },
    )

    assert llm_calls["count"] == 1
    assert result.get("sql")
    assert str(result.get("sql_engine") or "") == "fewshot_cot"


def test_generate_sql_auto_repairs_non_grouped_order_by_aggregation_clause(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    problematic_sql = (
        "SELECT oi.product_id, SUM(oi.price) AS total_revenue "
        "FROM olist_order_items_dataset oi "
        "GROUP BY oi.product_id "
        "ORDER BY oi.price DESC"
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {
                "content": json.dumps(
                    {
                        "sql": problematic_sql,
                        "summary": "ok",
                        "reasoning": "initial",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)
    monkeypatch.setattr(
        ask_service,
        "_select_sql_strategy",
        lambda analysis, has_knowledge: {"engine": "fewshot_cot", "max_retries": 1, "use_examples": False},
    )

    result = ask_service._generate_sql(
        question="按产品统计销售额并按销售额排序",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["olist_order_items_dataset"],
        semantic_hits={
            "models": [
                {
                    "name": "olist_order_items_dataset",
                    "table_reference": "olist_order_items_dataset",
                    "columns": [
                        {"name": "product_id", "type": "VARCHAR"},
                        {"name": "price", "type": "DOUBLE"},
                    ],
                }
            ],
            "relations": [],
            "has_hits": True,
        },
        language="zh",
        analysis={
            "tier": "multi_dimension",
            "dimensions": ["Product"],
            "metrics": ["Sales"],
            "entities": [],
            "sub_questions": [],
        },
    )

    assert result.get("sql")
    assert "ORDER BY MAX(oi.price) DESC" in str(result.get("sql") or "")
    assert "Aggregation issues" not in str(result.get("reasoning") or "")


def test_classify_question_route_normalizes_structured_filters_and_mixed_terms(monkeypatch):
    captured = {"user_prompt": ""}

    monkeypatch.setattr(
        ask_service,
        "_semantic_prompt",
        lambda *args, **kwargs: ("semantic context", ["orders"], {"has_hits": True, "models": [], "relations": []}),
    )
    monkeypatch.setattr(
        ask_service,
        "_knowledge_context",
        lambda *args, **kwargs: ("knowledge context", {"instructions": [], "sql_pairs": []}),
    )
    monkeypatch.setattr(ask_service, "_augment_context_with_knowledge", lambda semantic, knowledge: f"{semantic}\n{knowledge}")
    monkeypatch.setattr(
        ask_service,
        "_build_metadata_summary",
        lambda *args, **kwargs: {"summary": "- model orders", "models_count": 1, "suggested_questions": []},
    )
    monkeypatch.setattr(ask_service, "_project_meta", lambda *args, **kwargs: {"name": "demo", "description": "demo project"})
    monkeypatch.setattr(ask_service, "_render_system_prompt", lambda *args, **kwargs: "SYSTEM")

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            captured["user_prompt"] = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
            return {
                "content": json.dumps(
                    {
                        "requires_sql": True,
                        "metadata_question_part": "按城市查看销售额",
                        "non_metadata_question_part": "",
                        "reasoning": "ok",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    route = ask_service._classify_question_route(
        question="按城市查看销售额",
        project_id=1,
        previous_questions=["先看订单"],
        analysis={
            "tier": "compound",
            "sub_questions": [{"question": "先看销售额"}, 2],
            "entities": [{"name": "Orders"}, "Customers"],
            "metrics": [{"text": "Revenue"}],
            "dimensions": [{"name": "City"}],
            "filters": [
                {"field": "order_status", "operator": "=", "value": "paid"},
                {"column": "created_at", "op": ">=", "value": "2024-01-01"},
                "country=US",
            ],
        },
    )

    assert route["requires_sql"] is True
    assert route["analysis"]["sub_questions"] == ["先看销售额", "2"]
    assert route["analysis"]["entities"] == ["Orders", "Customers"]
    assert route["analysis"]["filters"][0] == {"field": "order_status", "operator": "=", "value": "paid"}
    assert "Filters: order_status = paid, created_at >= 2024-01-01, country=US" in captured["user_prompt"]


def test_classify_question_route_parse_failure_defaults_to_sql_path(monkeypatch):
    monkeypatch.setattr(
        ask_service,
        "_semantic_prompt",
        lambda *args, **kwargs: ("semantic context", ["orders"], {"has_hits": True, "models": [], "relations": []}),
    )
    monkeypatch.setattr(
        ask_service,
        "_knowledge_context",
        lambda *args, **kwargs: ("knowledge context", {"instructions": [], "sql_pairs": []}),
    )
    monkeypatch.setattr(ask_service, "_augment_context_with_knowledge", lambda semantic, knowledge: f"{semantic}\n{knowledge}")
    monkeypatch.setattr(
        ask_service,
        "_build_metadata_summary",
        lambda *args, **kwargs: {"summary": "- model orders", "models_count": 1, "suggested_questions": []},
    )
    monkeypatch.setattr(ask_service, "_project_meta", lambda *args, **kwargs: {"name": "demo", "description": "demo project"})
    monkeypatch.setattr(ask_service, "_render_system_prompt", lambda *args, **kwargs: "SYSTEM")

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {"content": "NOT_JSON"}

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    route = ask_service._classify_question_route(
        question="按城市查看销售额",
        project_id=1,
        previous_questions=["先看订单"],
        analysis={"tier": "simple", "sub_questions": [], "entities": [], "metrics": [], "dimensions": [], "filters": []},
    )

    assert route["requires_sql"] is True
    assert route["metadata_question_part"] == "按城市查看销售额"
    assert route["non_metadata_question_part"] == ""
    assert "defaulted to SQL path" in (route.get("reasoning") or "")


def test_summarize_query_result_handles_mixed_sub_question_types(monkeypatch):
    monkeypatch.setattr(ask_service, "_render_system_prompt", lambda *args, **kwargs: "SYSTEM")
    monkeypatch.setattr(ask_service, "_language_instruction", lambda *args, **kwargs: "")

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {"content": "By city, Beijing has total_sales 100."}

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    summary = ask_service._summarize_query_result(
        question="按城市统计销售额",
        sql="SELECT city, SUM(amount) AS total_sales FROM orders GROUP BY city",
        query_result={
            "columns": ["city", "total_sales"],
            "rows": [{"city": "Beijing", "total_sales": 100}],
            "total_rows": 1,
        },
        generated_summary="ok",
        language="en",
        analysis={
            "sub_questions": [{"question": "先按城市"}, 2],
            "entities": [{"name": "Orders"}],
        },
    )

    assert isinstance(summary, str)
    assert summary


def test_repair_sql_falls_back_to_plain_text_sql_when_json_parse_fails(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {
                "content": (
                    "I fixed the query.\n"
                    "```sql\n"
                    "SELECT o.order_id FROM olist_orders_dataset o;\n"
                    "```\n"
                    "Reasoning: use the correct model alias."
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    repair = ask_service._repair_sql(
        question="测试修复 SQL",
        failed_sql="SELECT t1.order_id FROM olist_orders_dataset t1",
        error="column not found",
        project_id=1,
        semantic_context="Project semantic model: olist_orders_dataset(order_id)",
        language="zh",
    )

    assert repair["sql"] == "SELECT o.order_id FROM olist_orders_dataset o"
    assert "plain-text fallback" in (repair.get("reasoning") or "")


def test_generate_sql_uses_strict_json_reask_then_repair_for_non_json_sql_payload(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    llm_calls = {"count": 0}
    repair_calls = {"count": 0}
    repaired_sql = "SELECT SUM(o.price) AS total_sales FROM olist_order_items_dataset o"

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            llm_calls["count"] += 1
            if llm_calls["count"] == 1:
                return {
                    "content": (
                        "```sql\n"
                        "SELECT SUM(o.price) AS total_sales FROM olist_order_items_dataset o\n"
                        "```"
                    )
                }
            return {"content": "SELECT SUM(o.price) AS total_sales FROM olist_order_items_dataset o"}

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    def fake_repair(*args, **kwargs):
        repair_calls["count"] += 1
        return {
            "sql": repaired_sql,
            "summary": "repaired",
            "reasoning": "strict json re-ask fallback repair",
            "configured": True,
        }

    monkeypatch.setattr(ask_service, "_repair_sql", fake_repair)

    result = ask_service._generate_sql(
        question="统计总销售额",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["olist_order_items_dataset"],
        semantic_hits=_semantic_hits_models(),
        language="zh",
        analysis={"tier": "simple", "dimensions": [], "metrics": ["total_sales"], "entities": [], "sub_questions": []},
    )

    assert llm_calls["count"] == 2
    assert repair_calls["count"] == 1
    assert result["sql"] == repaired_sql
    assert str(result.get("sql_engine") or "").endswith("_repair")


def test_llm_chat_with_response_format_fallback_honors_cancel_before_plain_retry():
    class FakeLLM:
        def __init__(self):
            self.calls = 0
            self.config = {
                "provider": "test",
                "endpoint": "https://example.invalid/v1",
                "model": "cancel-check",
            }

        def chat(self, messages, response_format="json", **kwargs):
            self.calls += 1
            return {"content": ""}

    llm = FakeLLM()
    breaker_key = ask_service._json_empty_content_circuit_key(llm)
    ask_service._reset_json_empty_content_breaker(breaker_key)
    cancel_checks = {"count": 0}

    def _cancel_check() -> None:
        cancel_checks["count"] += 1
        if cancel_checks["count"] >= 2:
            raise RuntimeError("cancelled")

    raised: RuntimeError | None = None
    try:
        ask_service._llm_chat_with_response_format_fallback(
            llm,
            [{"role": "user", "content": "Generate SQL"}],
            response_format="json",
            stage="sql_generation",
            timeout=5.0,
            cancel_check=_cancel_check,
        )
    except RuntimeError as exc:
        raised = exc

    assert raised is not None
    assert str(raised) == "cancelled"
    assert llm.calls == 1


def test_normalize_question_analysis_handles_non_dict_payload():
    normalized = ask_service._normalize_question_analysis(["unexpected", "payload"])  # type: ignore[arg-type]

    assert normalized["tier"] == "simple"
    assert normalized["sub_questions"] == []
    assert normalized["filters"] == []


def test_llm_schema_link_handles_non_object_json_payload():
    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {"content": "[]"}

    result = ask_service._llm_schema_link(
        question="show orders",
        project_id=1,
        models=[{"name": "orders", "table_reference": "orders", "columns": [{"name": "order_id", "type": "INTEGER"}]}],
        relations=[],
        llm=FakeLLM(),
    )

    assert result is None


def test_llm_schema_link_returns_none_on_empty_llm_content():
    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {"content": "   "}

    result = ask_service._llm_schema_link(
        question="show orders",
        project_id=1,
        models=[{"name": "orders", "table_reference": "orders", "columns": [{"name": "order_id", "type": "INTEGER"}]}],
        relations=[],
        llm=FakeLLM(),
    )

    assert result is None


def test_llm_semantic_matching_tolerates_non_string_column_items():
    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {
                "content": json.dumps(
                    {
                        "matched_models": [
                            {"name": "orders", "matched_columns": [{"name": "order_id"}, "customer_id", 123]},
                            "orders",
                        ]
                    }
                )
            }

    result = ask_service._llm_semantic_matching(
        question="show orders",
        project_id=1,
        models=[
            {
                "name": "orders",
                "table_reference": "orders",
                "columns": [
                    {"name": "order_id", "type": "INTEGER"},
                    {"name": "customer_id", "type": "INTEGER"},
                ],
            }
        ],
        relations=[],
        llm=FakeLLM(),
    )

    assert result is not None
    assert result.get("has_hits") is True
    assert result.get("models")


def test_generate_sql_retries_when_llm_returns_empty_content(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    class FakeLLM:
        call_count = 0

        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            type(self).call_count += 1
            if type(self).call_count == 1:
                return {"content": ""}
            return {
                "content": json.dumps(
                    {
                        "sql": (
                            "SELECT s.seller_city, SUM(o.price) AS total_sales "
                            "FROM olist_order_items_dataset o "
                            "JOIN olist_sellers_dataset s ON o.seller_id = s.seller_id "
                            "GROUP BY s.seller_city"
                        ),
                        "summary": "ok",
                        "reasoning": "ok",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._generate_sql(
        question="按城市统计销售额",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["olist_order_items_dataset", "olist_sellers_dataset"],
        semantic_hits=_semantic_hits_models(),
        language="zh",
        analysis={"tier": "multi_dimension", "dimensions": ["City"], "metrics": ["total_sales"], "entities": [], "sub_questions": []},
    )

    assert FakeLLM.call_count == 2
    assert "seller_city" in str(result.get("sql") or "").lower()


def test_generate_sql_uses_json_schema_response_format_when_supported(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    captured = {"response_format": None}

    monkeypatch.setattr(
        ask_service,
        "_strict_json_capability",
        lambda: {"supported": True, "mode": "json_schema", "detail": "ok"},
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            captured["response_format"] = response_format
            return {
                "content": json.dumps(
                    {
                        "sql": "SELECT SUM(o.price) AS total_sales FROM olist_order_items_dataset o",
                        "summary": "ok",
                        "reasoning": "ok",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._generate_sql(
        question="总销售额是多少",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["olist_order_items_dataset"],
        semantic_hits=_semantic_hits_models(),
        language="zh",
        analysis={"tier": "simple", "dimensions": [], "metrics": ["total_sales"], "entities": [], "sub_questions": []},
    )

    assert result.get("sql")
    assert isinstance(captured["response_format"], dict)
    assert captured["response_format"]["type"] == "json_schema"


def test_classify_route_uses_json_schema_response_format_when_supported(monkeypatch):
    captured = {"response_format": None}

    monkeypatch.setattr(
        ask_service,
        "_semantic_prompt",
        lambda *args, **kwargs: ("semantic context", ["orders"], {"has_hits": True, "models": [], "relations": []}),
    )
    monkeypatch.setattr(
        ask_service,
        "_knowledge_context",
        lambda *args, **kwargs: ("knowledge context", {"instructions": [], "sql_pairs": []}),
    )
    monkeypatch.setattr(ask_service, "_augment_context_with_knowledge", lambda semantic, knowledge: f"{semantic}\n{knowledge}")
    monkeypatch.setattr(
        ask_service,
        "_build_metadata_summary",
        lambda *args, **kwargs: {"summary": "- model orders", "models_count": 1, "suggested_questions": []},
    )
    monkeypatch.setattr(ask_service, "_project_meta", lambda *args, **kwargs: {"name": "demo", "description": "demo project"})
    monkeypatch.setattr(ask_service, "_render_system_prompt", lambda *args, **kwargs: "SYSTEM")
    monkeypatch.setattr(
        ask_service,
        "_strict_json_capability",
        lambda: {"supported": True, "mode": "json_schema", "detail": "ok"},
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            captured["response_format"] = response_format
            return {
                "content": json.dumps(
                    {
                        "requires_sql": True,
                        "metadata_question_part": "按城市查看销售额",
                        "non_metadata_question_part": "",
                        "reasoning": "ok",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    route = ask_service._classify_question_route(
        question="按城市查看销售额",
        project_id=1,
        previous_questions=["先看订单"],
        analysis={"tier": "simple", "sub_questions": [], "entities": [], "metrics": [], "dimensions": [], "filters": []},
    )

    assert route["requires_sql"] is True
    assert isinstance(captured["response_format"], dict)
    assert captured["response_format"]["type"] == "json_schema"


def test_generate_sql_falls_back_to_json_when_json_schema_rejected(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    calls: list[object] = []

    monkeypatch.setattr(
        ask_service,
        "_strict_json_capability",
        lambda: {"supported": True, "mode": "json_schema", "detail": "probe"},
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            calls.append(response_format)
            if isinstance(response_format, dict):
                raise RuntimeError("response_format json_schema is unsupported")
            return {
                "content": json.dumps(
                    {
                        "sql": "SELECT SUM(o.price) AS total_sales FROM olist_order_items_dataset o",
                        "summary": "ok",
                        "reasoning": "fallback json",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._generate_sql(
        question="总销售额是多少",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["olist_order_items_dataset"],
        semantic_hits=_semantic_hits_models(),
        language="zh",
        analysis={"tier": "simple", "dimensions": [], "metrics": ["total_sales"], "entities": [], "sub_questions": []},
    )

    assert result.get("sql")
    assert len(calls) >= 2
    assert isinstance(calls[0], dict)
    assert calls[1] == "json"


def test_repair_sql_falls_back_to_json_when_json_schema_rejected(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    calls: list[object] = []

    monkeypatch.setattr(
        ask_service,
        "_strict_json_capability",
        lambda: {"supported": True, "mode": "json_schema", "detail": "probe"},
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            calls.append(response_format)
            if isinstance(response_format, dict):
                raise RuntimeError("invalid_request_error: response_format json_schema")
            return {
                "content": json.dumps(
                    {
                        "sql": "SELECT o.order_id FROM olist_orders_dataset o",
                        "summary": "repaired",
                        "reasoning": "fallback json",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    repair = ask_service._repair_sql(
        question="测试修复 SQL",
        failed_sql="SELECT t1.order_id FROM olist_orders_dataset t1",
        error="column not found",
        project_id=1,
        semantic_context="Project semantic model: olist_orders_dataset(order_id)",
        language="zh",
    )

    assert repair["sql"] == "SELECT o.order_id FROM olist_orders_dataset o"
    assert len(calls) >= 2
    assert isinstance(calls[0], dict)
    assert calls[1] == "json"


def test_generate_sql_uses_json_when_project_not_in_route_allowlist(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    captured = {"response_format": None}

    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "sql_route_v2_enabled", True)
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "sql_route_allowlist_projects", [999])
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "sql_route_shadow_mode", False)
    monkeypatch.setattr(
        ask_service,
        "_strict_json_capability",
        lambda: {"supported": True, "mode": "json_schema", "detail": "probe"},
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            captured["response_format"] = response_format
            return {
                "content": json.dumps(
                    {
                        "sql": "SELECT SUM(o.price) AS total_sales FROM olist_order_items_dataset o",
                        "summary": "ok",
                        "reasoning": "allowlist fallback",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._generate_sql(
        question="总销售额是多少",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["olist_order_items_dataset"],
        semantic_hits=_semantic_hits_models(),
        language="zh",
        analysis={"tier": "simple", "dimensions": [], "metrics": ["total_sales"], "entities": [], "sub_questions": []},
    )

    assert result.get("sql")
    assert captured["response_format"] == "json"


def test_generation_shadow_diff_helpers_map_engines_consistently():
    assert ask_service._legacy_generation_engine({"tier": "simple"}, False) == "direct_llm"
    assert ask_service._legacy_generation_engine({"tier": "multi_dimension"}, True) == "fewshot_cot"
    assert ask_service._legacy_generation_engine({"tier": "compound"}, True) == "decompose_merge"

    assert ask_service._normalize_generation_engine_name("direct_llm_repair") == "direct_llm"
    assert ask_service._normalize_generation_engine_name("fewshot_cot_failed") == "fewshot_cot"
    assert ask_service._normalize_generation_engine_name("decompose_merge_retry") == "decompose_merge"
    assert ask_service._normalize_generation_engine_name("llm_fallback_parse_error") == "direct_llm"


def test_classify_question_route_splits_mixed_clauses_and_refines_sql_context(monkeypatch):
    semantic_calls: list[tuple[str, bool]] = []
    knowledge_calls: list[str] = []

    monkeypatch.setattr(
        ask_service,
        "_models_for_project",
        lambda *args, **kwargs: [{"name": "orders", "table_reference": "orders", "columns": [{"name": "amount"}]}],
    )
    monkeypatch.setattr(ask_service, "_relations_for_project", lambda *args, **kwargs: [])

    def fake_semantic_hits(clause: str, models, relations):
        has_hits = "销售额" in clause
        return {
            "has_hits": has_hits,
            "models": [{"name": "orders", "table_reference": "orders", "columns": [{"name": "amount"}]}] if has_hits else [],
            "relations": [],
            "score": 10 if has_hits else 0,
        }

    def fake_semantic_prompt(project_id: int, q: str, require_hits: bool = False, analysis=None, language=None):
        semantic_calls.append((q, require_hits))
        has_hits = "销售额" in q
        if not has_hits and require_hits:
            has_hits = True
        return (
            f"semantic::{q}",
            ["orders"] if has_hits else [],
            {
                "has_hits": has_hits,
                "models": [{"name": "orders", "table_reference": "orders", "columns": [{"name": "amount"}]}] if has_hits else [],
                "relations": [],
            },
        )

    def fake_knowledge_context(project_id: int, q: str):
        knowledge_calls.append(q)
        return (f"knowledge::{q}", {"instructions": [], "sql_pairs": []})

    monkeypatch.setattr(ask_service, "_semantic_hits", fake_semantic_hits)
    monkeypatch.setattr(ask_service, "_semantic_prompt", fake_semantic_prompt)
    monkeypatch.setattr(ask_service, "_knowledge_context", fake_knowledge_context)
    monkeypatch.setattr(ask_service, "_augment_context_with_knowledge", lambda semantic, knowledge: f"{semantic}\n{knowledge}")
    monkeypatch.setattr(
        ask_service,
        "_build_metadata_summary",
        lambda *args, **kwargs: {"summary": "- model orders", "models_count": 1, "suggested_questions": []},
    )
    monkeypatch.setattr(ask_service, "_project_meta", lambda *args, **kwargs: {"name": "demo", "description": "demo project"})
    monkeypatch.setattr(ask_service, "_render_system_prompt", lambda *args, **kwargs: "SYSTEM")

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {
                "content": json.dumps(
                    {
                        "requires_sql": True,
                        "metadata_question_part": "按城市统计销售额，并解释为什么销量会波动",
                        "non_metadata_question_part": "",
                        "reasoning": "ok",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    route = ask_service._classify_question_route(
        question="按城市统计销售额，并解释为什么销量会波动",
        project_id=1,
        previous_questions=["先看订单"],
        analysis={"tier": "simple", "sub_questions": [], "entities": [], "metrics": ["销售额"], "dimensions": ["城市"], "filters": []},
    )

    assert route["requires_sql"] is True
    assert route["metadata_question_part"] == "按城市统计销售额"
    assert route["non_metadata_question_part"] == "解释为什么销量会波动"
    assert route["semantic_context"] == "semantic::按城市统计销售额"
    assert route["combined_context"] == "semantic::按城市统计销售额\nknowledge::按城市统计销售额"
    assert route["clause_routing"]["mixed"] is True
    assert any(q == "按城市统计销售额" for q, _ in semantic_calls)
    assert "按城市统计销售额" in knowledge_calls


def test_generate_sql_includes_clause_routing_context_hint(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    captured = {"user_prompt": ""}

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            captured["user_prompt"] = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
            return {
                "content": json.dumps(
                    {
                        "sql": "SELECT SUM(o.price) AS total_sales FROM olist_order_items_dataset o",
                        "summary": "ok",
                        "reasoning": "ok",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._generate_sql(
        question="按城市统计销售额",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["olist_order_items_dataset"],
        semantic_hits=_semantic_hits_models(),
        language="zh",
        analysis={
            "tier": "simple",
            "sub_questions": [],
            "entities": [],
            "metrics": ["sales"],
            "dimensions": [],
            "filters": [],
            "metadata_question_part": "按城市统计销售额",
            "non_metadata_question_part": "解释为什么销量会波动",
            "clause_routing": {
                "clauses": [
                    {"text": "按城市统计销售额", "route": "sql", "semantic_hit": True, "data_intent": True},
                    {"text": "解释为什么销量会波动", "route": "general", "semantic_hit": False, "data_intent": False},
                ]
            },
        },
    )

    assert result.get("sql")
    assert "Clause routing context:" in captured["user_prompt"]
    assert "Non-SQL question part" in captured["user_prompt"]


def test_summarize_query_result_includes_clause_focus_hints(monkeypatch):
    captured = {"user_prompt": ""}
    monkeypatch.setattr(ask_service, "_render_system_prompt", lambda *args, **kwargs: "SYSTEM")
    monkeypatch.setattr(ask_service, "_language_instruction", lambda *args, **kwargs: "")

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            captured["user_prompt"] = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
            return {"content": "By city, Beijing total_sales is 100."}

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    summary = ask_service._summarize_query_result(
        question="按城市统计销售额",
        sql="SELECT city, SUM(amount) AS total_sales FROM orders GROUP BY city",
        query_result={
            "columns": ["city", "total_sales"],
            "rows": [{"city": "Beijing", "total_sales": 100}],
            "total_rows": 1,
        },
        generated_summary="ok",
        language="en",
        analysis={
            "tier": "simple",
            "sub_questions": [],
            "entities": [],
            "metrics": ["sales"],
            "dimensions": ["city"],
            "filters": [],
            "metadata_question_part": "按城市统计销售额",
            "non_metadata_question_part": "解释为什么销量会波动",
            "clause_routing": {
                "clauses": [
                    {"text": "按城市统计销售额", "route": "sql", "semantic_hit": True, "data_intent": True},
                    {"text": "解释为什么销量会波动", "route": "general", "semantic_hit": False, "data_intent": False},
                ]
            },
        },
    )

    assert isinstance(summary, str)
    assert summary
    assert "SQL-related question part: 按城市统计销售额" in captured["user_prompt"]
    assert "Non-SQL question part" in captured["user_prompt"]
    assert "Clause routing details:" in captured["user_prompt"]


def test_strip_sql_json_leak_filters_thinking_process_only_output():
    cleaned = ask_service._strip_sql_json_leak(
        """
Thinking Process:
Analyze the request and reconcile constraints.
Generate ranking and comparison statements.
"""
    )

    assert "thinking process" not in cleaned.lower()
    assert "could not safely answer" in cleaned.lower()


def test_strip_sql_json_leak_extracts_answer_after_think_block():
    cleaned = ask_service._strip_sql_json_leak(
        """
<think>
Need to compare department visits and revenue.
</think>
Final Answer: 昨天接诊量最高的是马亚英医生，共 110 人次，收入 8748.75 元。
"""
    )

    assert cleaned.startswith("昨天接诊量最高")
    assert "<think>" not in cleaned
    assert "final answer" not in cleaned.lower()


def test_summarize_query_result_falls_back_when_llm_returns_thinking_trace(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    monkeypatch.setattr(ask_service, "_language_instruction", lambda *args, **kwargs: "")

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {
                "content": (
                    "Thinking Process:\n"
                    "Analyze the request and list constraints.\n"
                    "Use SQL data only and compare rankings."
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    summary = ask_service._summarize_query_result(
        question="昨天哪些医生接诊较高",
        sql="SELECT doctor_name, jzrc, jine FROM zhibiao_linchuang_keshi_day LIMIT 10",
        query_result={
            "columns": ["doctor_name", "jzrc", "jine"],
            "rows": [
                {"doctor_name": "马亚英", "jzrc": 110, "jine": 8748.75},
                {"doctor_name": "高旭宏", "jzrc": 97, "jine": 10184.58},
            ],
            "total_rows": 2,
        },
        generated_summary="ok",
        language="zh",
    )

    assert "Thinking Process" not in summary
    assert "结论" in summary


def test_summarize_query_result_emits_fallback_event_on_exception(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    monkeypatch.setattr(ask_service, "_language_instruction", lambda *args, **kwargs: "")
    events: list[tuple[str, dict, int | None]] = []

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            raise RuntimeError("summary failed")

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)
    monkeypatch.setattr(
        ask_service,
        "_emit_route_event",
        lambda event_type, payload, project_id=None: events.append((event_type, payload, project_id)),
    )

    summary = ask_service._summarize_query_result(
        question="按城市统计销售额",
        sql="SELECT city, SUM(amount) AS total_sales FROM orders GROUP BY city",
        query_result={
            "columns": ["city", "total_sales"],
            "rows": [{"city": "Beijing", "total_sales": 100}],
            "total_rows": 1,
        },
        generated_summary="ok",
        language="en",
        project_id=7,
    )

    assert isinstance(summary, str)
    assert summary
    final_answer_events = [item for item in events if item[0] == "final_answer_fallback"]
    assert final_answer_events
    event_type, payload, pid = final_answer_events[-1]
    assert event_type == "final_answer_fallback"
    assert pid == 7
    assert payload.get("reason") == "summary_exception"
    assert payload.get("mode") == "deterministic_row_summary"


def test_classify_question_route_emits_clause_routing_details(monkeypatch):
    events: list[tuple[str, dict, int | None]] = []

    monkeypatch.setattr(
        ask_service,
        "_models_for_project",
        lambda *args, **kwargs: [{"name": "orders", "table_reference": "orders", "columns": [{"name": "amount"}]}],
    )
    monkeypatch.setattr(ask_service, "_relations_for_project", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        ask_service,
        "_semantic_hits",
        lambda clause, _models, _relations: {
            "has_hits": "销售额" in clause,
            "models": [{"name": "orders", "table_reference": "orders", "columns": [{"name": "amount"}]}] if "销售额" in clause else [],
            "relations": [],
            "score": 8 if "销售额" in clause else 0,
        },
    )
    monkeypatch.setattr(
        ask_service,
        "_semantic_prompt",
        lambda _project_id, q, require_hits=False, analysis=None, language=None: (
            f"semantic::{q}",
            ["orders"] if ("销售额" in q or require_hits) else [],
            {"has_hits": ("销售额" in q or require_hits), "models": [], "relations": []},
        ),
    )
    monkeypatch.setattr(
        ask_service,
        "_knowledge_context",
        lambda _project_id, q: (f"knowledge::{q}", {"instructions": [], "sql_pairs": []}),
    )
    monkeypatch.setattr(ask_service, "_augment_context_with_knowledge", lambda semantic, knowledge: f"{semantic}\n{knowledge}")
    monkeypatch.setattr(
        ask_service,
        "_build_metadata_summary",
        lambda *args, **kwargs: {"summary": "- model orders", "models_count": 1, "suggested_questions": []},
    )
    monkeypatch.setattr(ask_service, "_project_meta", lambda *args, **kwargs: {"name": "demo", "description": "demo"})
    monkeypatch.setattr(ask_service, "_render_system_prompt", lambda *args, **kwargs: "SYSTEM")
    monkeypatch.setattr(ask_service, "_is_sql_route_v2_enabled", lambda _pid: True)
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "sql_route_shadow_mode", False)
    monkeypatch.setattr(
        ask_service,
        "_emit_route_event",
        lambda event_type, payload, project_id=None: events.append((event_type, payload, project_id)),
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {
                "content": json.dumps(
                    {
                        "requires_sql": True,
                        "metadata_question_part": "按城市统计销售额，并解释为什么销量会波动",
                        "non_metadata_question_part": "",
                        "reasoning": "ok",
                    }
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    route = ask_service._classify_question_route(
        question="按城市统计销售额，并解释为什么销量会波动",
        project_id=1,
        previous_questions=["先看订单"],
        analysis={"tier": "simple", "sub_questions": [], "entities": [], "metrics": ["销售额"], "dimensions": ["城市"], "filters": []},
    )

    assert route["clause_routing"]["mixed"] is True
    q_event = next(payload for event, payload, _pid in events if event == "question_route_decision")
    assert q_event["clause_mixed"] is True
    assert q_event["metadata_clause_count"] == 1
    assert q_event["non_metadata_clause_count"] == 1
    assert q_event["clause_routing"]["clauses"][0]["text"].startswith("按城市统计销售额")


def test_ask_question_route_events_include_clause_summary(monkeypatch):
    events: list[tuple[str, dict, int | None]] = []

    monkeypatch.setattr(ask_service, "refresh_runtime_router_settings", lambda force=False: {})
    monkeypatch.setattr(ask_service, "get_thread_project_id", lambda _thread_id, _user_id: 1)
    monkeypatch.setattr(ask_service, "get_user_default_project_id", lambda _user_id: 1)
    monkeypatch.setattr(ask_service, "ensure_thread", lambda _project_id, _user_id, _question, _thread_id, _preview_row_limit: 101)
    monkeypatch.setattr(ask_service, "update_auto_thread_summary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ask_service, "get_thread_preview_row_limit", lambda _thread_id, _user_id: 20)
    monkeypatch.setattr(ask_service, "_project_has_context", lambda _project_id: True)
    monkeypatch.setattr(ask_service, "_looks_like_general_chat", lambda _question: False)
    monkeypatch.setattr(
        ask_service,
        "_analyze_question",
        lambda *_args, **_kwargs: {
            "tier": "simple",
            "sub_questions": [],
            "entities": [],
            "metrics": ["sales"],
            "dimensions": ["city"],
            "filters": [],
        },
    )
    monkeypatch.setattr(
        ask_service,
        "_classify_question_route",
        lambda *_args, **_kwargs: {
            "requires_sql": False,
            "metadata_question_part": "按城市统计销售额",
            "non_metadata_question_part": "解释为什么销量会波动",
            "reasoning": "mixed route",
            "semantic_context": "semantic",
            "retrieved_tables": ["orders"],
            "semantic_hits": {"has_hits": True, "models": [{"name": "orders"}], "relations": []},
            "knowledge_context": "knowledge",
            "knowledge_hits": {"instructions": [], "sql_pairs": []},
            "combined_context": "semantic\nknowledge",
            "metadata_summary": {"summary": "- model orders", "models_count": 1, "suggested_questions": []},
            "analysis": {"tier": "simple", "sub_questions": [], "entities": [], "metrics": ["sales"], "dimensions": ["city"], "filters": []},
            "clause_routing": {
                "metadata_clause_count": 1,
                "non_metadata_clause_count": 1,
                "mixed": True,
                "clauses": [
                    {"text": "按城市统计销售额", "route": "sql", "semantic_hit": True, "data_intent": True, "semantic_models": 1},
                    {"text": "解释为什么销量会波动", "route": "general", "semantic_hit": False, "data_intent": False, "semantic_models": 0},
                ],
            },
        },
    )
    monkeypatch.setattr(ask_service, "_project_general_chat", lambda *_args, **_kwargs: {"configured": True, "content": "general answer"})
    monkeypatch.setattr(ask_service, "create_thread_response", lambda *_args, **_kwargs: {"id": 1})
    monkeypatch.setattr(ask_service, "_is_sql_route_v2_enabled", lambda _pid: True)
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "sql_route_shadow_mode", False)
    monkeypatch.setattr(ask_service, "_strict_json_capability", lambda: {"supported": False, "mode": "none", "detail": "off"})
    monkeypatch.setattr(
        ask_service,
        "_prompt_profile_selection",
        lambda _stage, strict_json_mode="none": SimpleNamespace(
            profile_id="prismbi.default",
            profile_version="v2",
            response_format="json",
            system_suffix="",
        ),
    )
    monkeypatch.setattr(
        ask_service,
        "_emit_route_event",
        lambda event_type, payload, project_id=None: events.append((event_type, payload, project_id)),
    )

    result = ask_service.ask_question(
        question="按城市统计销售额，并解释为什么销量会波动",
        user_id=1,
        thread_id=99,
        previous_questions=["上一问"],
        previous_answers=["上一答"],
        language="zh",
        preview_row_limit=20,
        temporary=False,
    )

    assert result["summary"] == "general answer"
    gen_event = next(payload for event, payload, _pid in events if event == "generation_route_decision")
    assert gen_event["clause_mixed"] is True
    assert gen_event["metadata_clause_count"] == 1
    assert gen_event["non_metadata_clause_count"] == 1

    terminal_event = next(payload for event, payload, _pid in events if event == "ask_route_success")
    assert terminal_event["clause_mixed"] is True
    assert terminal_event["metadata_clause_count"] == 1
    assert terminal_event["non_metadata_clause_count"] == 1
    assert terminal_event["clause_routing"]["clauses"][0]["route"] == "sql"
    assert terminal_event["repair_path"] == "none"
    assert terminal_event["attempt_count"] == 0
    assert terminal_event["fallback_chain"] == []
    assert set(terminal_event["stage_durations_ms"].keys()) == {
        "understand",
        "retrieve",
        "generate",
        "execute",
        "answer",
    }
    assert float(terminal_event["stage_durations_ms"]["generate"]) >= 0.0
    assert float(terminal_event["duration_ms"]) >= 0.0


def test_ask_question_terminal_event_marks_generation_rehint_path(monkeypatch):
    events: list[tuple[str, dict, int | None]] = []

    monkeypatch.setattr(ask_service, "refresh_runtime_router_settings", lambda force=False: {})
    monkeypatch.setattr(ask_service, "get_thread_project_id", lambda _thread_id, _user_id: 1)
    monkeypatch.setattr(ask_service, "get_user_default_project_id", lambda _user_id: 1)
    monkeypatch.setattr(ask_service, "ensure_thread", lambda _project_id, _user_id, _question, _thread_id, _preview_row_limit: 112)
    monkeypatch.setattr(ask_service, "update_auto_thread_summary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ask_service, "get_thread_preview_row_limit", lambda _thread_id, _user_id: 20)
    monkeypatch.setattr(ask_service, "_project_has_context", lambda _project_id: True)
    monkeypatch.setattr(ask_service, "_looks_like_general_chat", lambda _question: False)
    monkeypatch.setattr(
        ask_service,
        "_analyze_question",
        lambda *_args, **_kwargs: {
            "tier": "simple",
            "sub_questions": [],
            "entities": [],
            "metrics": ["orders"],
            "dimensions": ["customer_id"],
            "filters": [],
        },
    )
    monkeypatch.setattr(
        ask_service,
        "_classify_question_route",
        lambda *_args, **_kwargs: {
            "requires_sql": True,
            "metadata_question_part": "按客户统计订单数",
            "non_metadata_question_part": "",
            "reasoning": "sql route",
            "semantic_context": "semantic",
            "retrieved_tables": ["orders"],
            "semantic_hits": {
                "has_hits": True,
                "models": [{"name": "orders", "table_reference": "orders", "columns": [{"name": "customer_id"}, {"name": "order_id"}]}],
                "relations": [],
            },
            "knowledge_context": "",
            "knowledge_hits": {"instructions": [], "sql_pairs": []},
            "combined_context": "semantic",
            "metadata_summary": {"summary": "- model orders", "models_count": 1, "suggested_questions": []},
            "analysis": {"tier": "simple", "sub_questions": [], "entities": [], "metrics": ["orders"], "dimensions": ["customer_id"], "filters": []},
            "clause_routing": {
                "metadata_clause_count": 1,
                "non_metadata_clause_count": 0,
                "mixed": False,
                "clauses": [{"text": "按客户统计订单数", "route": "sql", "semantic_hit": True, "data_intent": True, "semantic_models": 1}],
            },
        },
    )
    monkeypatch.setattr(
        ask_service,
        "_generate_sql",
        lambda *_args, **_kwargs: {
            "sql": "SELECT customer_id, COUNT(order_id) AS orders FROM orders GROUP BY customer_id",
            "summary": "generated",
            "reasoning": "rehinted",
            "retrieved_tables": ["orders"],
            "configured": True,
            "sql_engine": "fewshot_cot_rehint",
        },
    )
    monkeypatch.setattr(
        ask_service,
        "execute_project_sql",
        lambda _sql, _project_id, _user_id, _limit=None: {
            "columns": ["customer_id", "orders"],
            "rows": [["c1", 5]],
            "total_rows": 1,
            "execution_time_ms": 1.2,
            "warning": None,
            "security_plan": {},
        },
    )
    monkeypatch.setattr(ask_service, "_summarize_query_result", lambda *_args, **_kwargs: "summary")
    monkeypatch.setattr(ask_service, "_compose_final_answer", lambda *_args, **_kwargs: "final")
    monkeypatch.setattr(
        ask_service,
        "create_thread_response",
        lambda thread_id, user_id, question, sql, asking_task, answer_detail, breakdown_detail=None: {
            "id": 11,
            "threadId": thread_id,
            "question": question,
            "sql": sql,
            "askingTask": asking_task,
            "answerDetail": answer_detail,
            "breakdownDetail": breakdown_detail,
        },
    )
    monkeypatch.setattr(ask_service, "_is_sql_route_v2_enabled", lambda _pid: True)
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "sql_route_shadow_mode", False)
    monkeypatch.setattr(ask_service, "_strict_json_capability", lambda: {"supported": False, "mode": "none", "detail": "off"})
    monkeypatch.setattr(
        ask_service,
        "_prompt_profile_selection",
        lambda _stage, strict_json_mode="none": SimpleNamespace(
            profile_id="prismbi.default",
            profile_version="v2",
            response_format="json",
            system_suffix="",
        ),
    )
    monkeypatch.setattr(
        ask_service,
        "_emit_route_event",
        lambda event_type, payload, project_id=None: events.append((event_type, payload, project_id)),
    )

    result = ask_service.ask_question(
        question="按客户统计订单数",
        user_id=1,
        thread_id=222,
        previous_questions=[],
        previous_answers=[],
        language="zh",
        preview_row_limit=20,
        temporary=False,
    )

    assert result["summary"] == "final"
    terminal_event = next(payload for event, payload, _pid in events if event == "ask_route_success")
    assert terminal_event["repair_path"] == "generation_rehint"
    assert terminal_event["attempt_count"] == 1
    assert "generation_rehint" in terminal_event["fallback_chain"]
    assert set(terminal_event["stage_durations_ms"].keys()) == {
        "understand",
        "retrieve",
        "generate",
        "execute",
        "answer",
    }
    assert float(terminal_event["duration_ms"]) >= 0.0


def test_ask_question_skips_reexecute_when_repair_sql_has_duplicate_alias(monkeypatch):
    initial_sql = "SELECT o.order_id FROM olist_orders_dataset o"
    repaired_sql = (
        "SELECT T1.order_id "
        "FROM olist_orders_dataset AS T1 "
        "JOIN olist_order_items_dataset AS T1 ON T1.order_id = T1.order_id"
    )
    execute_calls = {"count": 0}

    monkeypatch.setattr(ask_service, "refresh_runtime_router_settings", lambda force=False: {})
    monkeypatch.setattr(ask_service, "get_thread_project_id", lambda _thread_id, _user_id: 1)
    monkeypatch.setattr(ask_service, "get_user_default_project_id", lambda _user_id: 1)
    monkeypatch.setattr(ask_service, "ensure_thread", lambda _project_id, _user_id, _question, _thread_id, _preview_row_limit: 101)
    monkeypatch.setattr(ask_service, "update_auto_thread_summary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ask_service, "get_thread_preview_row_limit", lambda _thread_id, _user_id: 20)
    monkeypatch.setattr(ask_service, "_project_has_context", lambda _project_id: True)
    monkeypatch.setattr(ask_service, "_looks_like_general_chat", lambda _question: False)
    monkeypatch.setattr(
        ask_service,
        "_analyze_question",
        lambda *_args, **_kwargs: {
            "tier": "simple",
            "sub_questions": [],
            "entities": [],
            "metrics": ["orders"],
            "dimensions": [],
            "filters": [],
        },
    )
    monkeypatch.setattr(
        ask_service,
        "_classify_question_route",
        lambda *_args, **_kwargs: {
            "requires_sql": True,
            "metadata_question_part": "订单数量",
            "non_metadata_question_part": "",
            "reasoning": "sql route",
            "semantic_context": "semantic",
            "retrieved_tables": ["olist_orders_dataset", "olist_order_items_dataset"],
            "semantic_hits": {
                "has_hits": True,
                "models": [
                    {
                        "name": "olist_orders_dataset",
                        "table_reference": "olist_orders_dataset",
                        "columns": [{"name": "order_id", "type": "VARCHAR"}],
                    },
                    {
                        "name": "olist_order_items_dataset",
                        "table_reference": "olist_order_items_dataset",
                        "columns": [{"name": "order_id", "type": "VARCHAR"}],
                    },
                ],
                "relations": [],
            },
            "knowledge_context": "",
            "knowledge_hits": {"instructions": [], "sql_pairs": []},
            "combined_context": "semantic",
            "metadata_summary": {"summary": "- model orders", "models_count": 2, "suggested_questions": []},
            "analysis": {"tier": "simple", "sub_questions": [], "entities": [], "metrics": ["orders"], "dimensions": [], "filters": []},
            "clause_routing": {"metadata_clause_count": 1, "non_metadata_clause_count": 0, "mixed": False, "clauses": []},
        },
    )
    monkeypatch.setattr(
        ask_service,
        "_generate_sql",
        lambda *_args, **_kwargs: {
            "sql": initial_sql,
            "summary": "generated",
            "reasoning": "generated",
            "retrieved_tables": ["olist_orders_dataset"],
            "configured": True,
            "sql_engine": "direct_llm",
        },
    )

    def fake_execute_project_sql(_sql, _project_id, _user_id, _limit=None):
        execute_calls["count"] += 1
        if execute_calls["count"] > 1:
            raise AssertionError("execute_project_sql should not be retried when repaired SQL is still invalid")
        raise ValueError("Binder Error: initial execution failed")

    monkeypatch.setattr(ask_service, "execute_project_sql", fake_execute_project_sql)
    monkeypatch.setattr(
        ask_service,
        "_repair_sql",
        lambda *_args, **_kwargs: {
            "sql": repaired_sql,
            "summary": "repair",
            "reasoning": "repair output",
            "configured": True,
        },
    )
    monkeypatch.setattr(ask_service, "_validate_sql_syntax_for_project", lambda _sql, _project_id: [])
    monkeypatch.setattr(
        ask_service,
        "create_thread_response",
        lambda thread_id, user_id, question, sql, asking_task, answer_detail, breakdown_detail=None: {
            "id": 1,
            "threadId": thread_id,
            "question": question,
            "sql": sql,
            "askingTask": asking_task,
            "answerDetail": answer_detail,
            "breakdownDetail": breakdown_detail,
        },
    )
    monkeypatch.setattr(ask_service, "_is_sql_route_v2_enabled", lambda _pid: False)

    result = ask_service.ask_question(
        question="订单数量",
        user_id=1,
        thread_id=100,
        previous_questions=[],
        previous_answers=[],
        language="zh",
        preview_row_limit=20,
        temporary=False,
    )

    assert execute_calls["count"] == 1
    assert result["sql"] == initial_sql
    assert result["response"]["askingTask"]["invalidSql"] == initial_sql
    assert "duplicate table alias" in (result["response"]["answerDetail"].get("error") or "").lower()


def test_ask_question_skips_reexecute_when_repair_sql_has_wrong_column_owner(monkeypatch):
    initial_sql = "SELECT o.order_id FROM olist_orders_dataset o"
    repaired_sql = (
        "SELECT o.product_id "
        "FROM olist_orders_dataset o "
        "JOIN olist_order_items_dataset oi ON o.order_id = oi.order_id"
    )
    execute_calls = {"count": 0}

    monkeypatch.setattr(ask_service, "refresh_runtime_router_settings", lambda force=False: {})
    monkeypatch.setattr(ask_service, "get_thread_project_id", lambda _thread_id, _user_id: 1)
    monkeypatch.setattr(ask_service, "get_user_default_project_id", lambda _user_id: 1)
    monkeypatch.setattr(ask_service, "ensure_thread", lambda _project_id, _user_id, _question, _thread_id, _preview_row_limit: 102)
    monkeypatch.setattr(ask_service, "update_auto_thread_summary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ask_service, "get_thread_preview_row_limit", lambda _thread_id, _user_id: 20)
    monkeypatch.setattr(ask_service, "_project_has_context", lambda _project_id: True)
    monkeypatch.setattr(ask_service, "_looks_like_general_chat", lambda _question: False)
    monkeypatch.setattr(
        ask_service,
        "_analyze_question",
        lambda *_args, **_kwargs: {
            "tier": "simple",
            "sub_questions": [],
            "entities": [],
            "metrics": ["orders"],
            "dimensions": [],
            "filters": [],
        },
    )
    monkeypatch.setattr(
        ask_service,
        "_classify_question_route",
        lambda *_args, **_kwargs: {
            "requires_sql": True,
            "metadata_question_part": "订单商品",
            "non_metadata_question_part": "",
            "reasoning": "sql route",
            "semantic_context": "semantic",
            "retrieved_tables": ["olist_orders_dataset", "olist_order_items_dataset"],
            "semantic_hits": {
                "has_hits": True,
                "models": [
                    {
                        "name": "olist_orders_dataset",
                        "table_reference": "olist_orders_dataset",
                        "columns": [{"name": "order_id", "type": "VARCHAR"}],
                    },
                    {
                        "name": "olist_order_items_dataset",
                        "table_reference": "olist_order_items_dataset",
                        "columns": [
                            {"name": "order_id", "type": "VARCHAR"},
                            {"name": "product_id", "type": "VARCHAR"},
                        ],
                    },
                ],
                "relations": [],
            },
            "knowledge_context": "",
            "knowledge_hits": {"instructions": [], "sql_pairs": []},
            "combined_context": "semantic",
            "metadata_summary": {"summary": "- model orders", "models_count": 2, "suggested_questions": []},
            "analysis": {"tier": "simple", "sub_questions": [], "entities": [], "metrics": ["orders"], "dimensions": [], "filters": []},
            "clause_routing": {"metadata_clause_count": 1, "non_metadata_clause_count": 0, "mixed": False, "clauses": []},
        },
    )
    monkeypatch.setattr(
        ask_service,
        "_generate_sql",
        lambda *_args, **_kwargs: {
            "sql": initial_sql,
            "summary": "generated",
            "reasoning": "generated",
            "retrieved_tables": ["olist_orders_dataset"],
            "configured": True,
            "sql_engine": "direct_llm",
        },
    )

    def fake_execute_project_sql(_sql, _project_id, _user_id, _limit=None):
        execute_calls["count"] += 1
        if execute_calls["count"] > 1:
            raise AssertionError("execute_project_sql should not be retried when repaired SQL has wrong owners")
        raise ValueError("Binder Error: initial execution failed")

    monkeypatch.setattr(ask_service, "execute_project_sql", fake_execute_project_sql)
    monkeypatch.setattr(
        ask_service,
        "_repair_sql",
        lambda *_args, **_kwargs: {
            "sql": repaired_sql,
            "summary": "repair",
            "reasoning": "repair output",
            "configured": True,
        },
    )
    monkeypatch.setattr(ask_service, "_validate_sql_syntax_for_project", lambda _sql, _project_id: [])
    monkeypatch.setattr(
        ask_service,
        "create_thread_response",
        lambda thread_id, user_id, question, sql, asking_task, answer_detail, breakdown_detail=None: {
            "id": 2,
            "threadId": thread_id,
            "question": question,
            "sql": sql,
            "askingTask": asking_task,
            "answerDetail": answer_detail,
            "breakdownDetail": breakdown_detail,
        },
    )
    monkeypatch.setattr(ask_service, "_is_sql_route_v2_enabled", lambda _pid: False)

    result = ask_service.ask_question(
        question="订单商品",
        user_id=1,
        thread_id=101,
        previous_questions=[],
        previous_answers=[],
        language="zh",
        preview_row_limit=20,
        temporary=False,
    )

    assert execute_calls["count"] == 1
    assert result["sql"] == initial_sql
    assert result["response"]["askingTask"]["invalidSql"] == initial_sql
    assert "repair returned unresolved sql references" in (result["response"]["answerDetail"].get("error") or "").lower()


def test_general_chat_fallback_when_llm_returns_empty_content(monkeypatch):
    monkeypatch.setattr(ask_service, "_render_system_prompt", lambda *_args, **_kwargs: "SYSTEM")

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {
                "content": "",
                "configured": True,
                "latency_ms": 1,
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._general_chat("你好，你是谁", language="zh")

    assert result["configured"] is True
    assert "PrismBI 助手" in result["content"]
    assert result["content"].strip()


def test_generate_sql_respects_total_budget_before_llm_generation(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "sql_generation_total_budget_s", 0.0)

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            raise AssertionError("LLM chat should not run when total generation budget is exhausted")

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._generate_sql(
        question="统计订单总数",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["olist_orders_dataset"],
        semantic_hits=_semantic_hits_models(),
        language="zh",
        analysis={"tier": "simple", "dimensions": [], "metrics": ["Order Count"], "entities": [], "sub_questions": []},
    )

    assert result.get("sql") is None
    assert result.get("sql_engine") == "llm_fallback_budget_exceeded"


def test_temporary_ask_general_chat_uses_non_empty_fallback_summary(monkeypatch):
    monkeypatch.setattr(ask_service, "refresh_runtime_router_settings", lambda force=False: {})
    monkeypatch.setattr(ask_service, "_render_system_prompt", lambda *_args, **_kwargs: "SYSTEM")

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json", **kwargs):
            return {
                "content": "",
                "configured": True,
                "latency_ms": 1,
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service.ask_question(
        question="你好，你是谁",
        user_id=1,
        thread_id=12345,
        language="zh",
        temporary=True,
    )

    assert result["summary"]
    assert "PrismBI 助手" in result["summary"]
    assert result["response"]["answerDetail"]["content"] == result["summary"]
