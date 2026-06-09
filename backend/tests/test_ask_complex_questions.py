from __future__ import annotations

import json

import services.ask_service as ask_service


def _patch_prompt_helpers(monkeypatch):
    monkeypatch.setattr(ask_service, "_render_system_prompt", lambda *args, **kwargs: "SYSTEM")
    monkeypatch.setattr(ask_service, "_render_project_prompt", lambda *args, **kwargs: "PROJECT_PROMPT")
    monkeypatch.setattr(ask_service, "_dialect_hint_for_project", lambda *args, **kwargs: "Use DuckDB SQL")


def _olist_hits() -> dict:
    return {
        "has_hits": True,
        "models": [
            {
                "name": "olist_order_items_dataset",
                "table_reference": "olist_order_items_dataset",
                "columns": [
                    {"name": "order_id", "type": "VARCHAR"},
                    {"name": "product_id", "type": "VARCHAR"},
                    {"name": "seller_id", "type": "VARCHAR"},
                    {"name": "price", "type": "DOUBLE"},
                ],
            },
            {
                "name": "olist_products_dataset",
                "table_reference": "olist_products_dataset",
                "columns": [
                    {"name": "product_id", "type": "VARCHAR"},
                    {"name": "product_category_name", "type": "VARCHAR"},
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
                    {"name": "seller_state", "type": "VARCHAR"},
                ],
            },
        ],
        "relations": [],
    }


def _hr_hits() -> dict:
    return {
        "has_hits": True,
        "models": [
            {
                "name": "employees",
                "table_reference": "employees",
                "columns": [
                    {"name": "emp_no", "type": "INTEGER"},
                    {"name": "first_name", "type": "VARCHAR"},
                    {"name": "last_name", "type": "VARCHAR"},
                ],
            },
            {
                "name": "dept_emp",
                "table_reference": "dept_emp",
                "columns": [
                    {"name": "emp_no", "type": "INTEGER"},
                    {"name": "dept_no", "type": "VARCHAR"},
                    {"name": "to_date", "type": "DATE"},
                ],
            },
            {
                "name": "departments",
                "table_reference": "departments",
                "columns": [
                    {"name": "dept_no", "type": "VARCHAR"},
                    {"name": "dept_name", "type": "VARCHAR"},
                ],
            },
            {
                "name": "salaries",
                "table_reference": "salaries",
                "columns": [
                    {"name": "emp_no", "type": "INTEGER"},
                    {"name": "salary", "type": "INTEGER"},
                    {"name": "to_date", "type": "DATE"},
                ],
            },
            {
                "name": "titles",
                "table_reference": "titles",
                "columns": [
                    {"name": "emp_no", "type": "INTEGER"},
                    {"name": "title", "type": "VARCHAR"},
                    {"name": "to_date", "type": "DATE"},
                ],
            },
        ],
        "relations": [],
    }


def test_generate_sql_complex_olist_multidimension(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    expected_sql = (
        "SELECT t.product_category_name_english, s.seller_city, s.seller_state, "
        "SUM(oi.price) AS total_sales, COUNT(DISTINCT oi.order_id) AS order_cnt "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "JOIN olist_sellers_dataset s ON oi.seller_id = s.seller_id "
        "GROUP BY t.product_category_name_english, s.seller_city, s.seller_state "
        "ORDER BY total_sales DESC LIMIT 15"
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json"):
            return {"content": json.dumps({"sql": expected_sql, "summary": "ok", "reasoning": "ok"})}

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._generate_sql(
        question="按产品英文类目+卖家城市+州统计销售额和订单数，按销售额排名前15",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=[
            "olist_order_items_dataset",
            "olist_products_dataset",
            "product_category_name_translation",
            "olist_sellers_dataset",
        ],
        semantic_hits=_olist_hits(),
        analysis={
            "tier": "multi_dimension",
            "dimensions": ["Product", "City", "State"],
            "metrics": ["Sales", "Order Count"],
            "entities": [],
            "sub_questions": [],
        },
        language="zh",
    )

    assert result["sql"] == expected_sql
    assert "GROUP BY t.product_category_name_english, s.seller_city, s.seller_state" in result["sql"]


def test_generate_sql_complex_hr_current_top_earners(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    expected_sql = (
        "WITH current_salary AS ("
        "SELECT emp_no, salary FROM salaries WHERE to_date = DATE '9999-01-01'"
        "), current_title AS ("
        "SELECT emp_no, title FROM titles WHERE to_date = DATE '9999-01-01'"
        "), ranked AS ("
        "SELECT d.dept_name, e.emp_no, e.first_name, e.last_name, ct.title, cs.salary, "
        "ROW_NUMBER() OVER (PARTITION BY d.dept_name ORDER BY cs.salary DESC) AS rn "
        "FROM dept_emp de "
        "JOIN departments d ON de.dept_no = d.dept_no "
        "JOIN employees e ON de.emp_no = e.emp_no "
        "JOIN current_salary cs ON e.emp_no = cs.emp_no "
        "JOIN current_title ct ON e.emp_no = ct.emp_no "
        "WHERE de.to_date = DATE '9999-01-01'"
        ") "
        "SELECT dept_name, emp_no, first_name, last_name, title, salary FROM ranked WHERE rn <= 3 "
        "ORDER BY dept_name, salary DESC"
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json"):
            return {"content": json.dumps({"sql": expected_sql, "summary": "ok", "reasoning": "ok"})}

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._generate_sql(
        question="找出每个部门当前薪资最高的前三名员工及其头衔",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=["departments", "dept_emp", "employees", "salaries", "titles"],
        semantic_hits=_hr_hits(),
        analysis={
            "tier": "multi_dimension",
            "dimensions": ["Department", "Employee", "Title"],
            "metrics": ["Salary"],
            "entities": [],
            "sub_questions": [],
        },
        language="zh",
    )

    assert result["sql"] == expected_sql
    assert "ROW_NUMBER() OVER (PARTITION BY d.dept_name ORDER BY cs.salary DESC)" in result["sql"]


def test_generate_sql_compound_decompose_merge_with_complex_questions(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    sub_sql_1 = (
        "SELECT t.product_category_name_english, SUM(oi.price) AS total_sales "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "GROUP BY t.product_category_name_english"
    )
    sub_sql_2 = (
        "SELECT s.seller_city, COUNT(DISTINCT oi.order_id) AS order_cnt "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_sellers_dataset s ON oi.seller_id = s.seller_id "
        "GROUP BY s.seller_city"
    )
    merged_sql = (
        "SELECT t.product_category_name_english, s.seller_city, "
        "SUM(oi.price) AS total_sales, COUNT(DISTINCT oi.order_id) AS order_cnt "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "JOIN olist_sellers_dataset s ON oi.seller_id = s.seller_id "
        "GROUP BY t.product_category_name_english, s.seller_city "
        "ORDER BY total_sales DESC"
    )

    class FakeLLM:
        def __init__(self):
            self.call_idx = 0

        def is_configured(self):
            return True

        def chat(self, messages, response_format="json"):
            self.call_idx += 1
            user_text = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
            if "Sub-question:" in user_text:
                if self.call_idx == 1:
                    sql = sub_sql_1
                else:
                    sql = sub_sql_2
                return {"content": json.dumps({"sql": sql, "summary": "sub", "reasoning": "sub"})}
            return {"content": json.dumps({"sql": merged_sql, "summary": "merged", "reasoning": "merged"})}

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._generate_sql(
        question="先看产品类目销售额，再看城市订单量，并合并成一个结果",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=[
            "olist_order_items_dataset",
            "olist_products_dataset",
            "product_category_name_translation",
            "olist_sellers_dataset",
        ],
        semantic_hits=_olist_hits(),
        analysis={
            "tier": "compound",
            "dimensions": ["Product", "City"],
            "metrics": ["Sales", "Order Count"],
            "entities": [],
            "sub_questions": [
                "每个产品类目的销售额",
                "每个城市的订单量",
            ],
        },
        language="zh",
    )

    assert result["sql"] == merged_sql
    assert result["sql_engine"] in {"decompose_merge", "decompose_merge_rehint"}


def test_generate_sql_compound_decompose_merge_single_subquery_supports_cjk_dimensions_with_display_names(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    sub_sql = (
        "SELECT t.product_category_name_english, s.seller_city, SUM(oi.price) AS total_sales "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "JOIN olist_sellers_dataset s ON oi.seller_id = s.seller_id "
        "GROUP BY t.product_category_name_english, s.seller_city"
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json"):
            user_text = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
            if "Sub-question:" in user_text:
                return {"content": json.dumps({"sql": sub_sql, "summary": "sub", "reasoning": "sub"})}
            return {"content": json.dumps({"sql": sub_sql, "summary": "direct", "reasoning": "direct"})}

    semantic_hits = _olist_hits()
    for model in semantic_hits["models"]:
        if model.get("name") == "product_category_name_translation":
            for column in model.get("columns", []):
                if column.get("name") == "product_category_name_english":
                    column["display_name"] = "产品"
        if model.get("name") == "olist_sellers_dataset":
            for column in model.get("columns", []):
                if column.get("name") == "seller_city":
                    column["display_name"] = "城市"

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)
    monkeypatch.setattr(
        ask_service,
        "_select_sql_strategy",
        lambda analysis, has_knowledge: {"engine": "decompose_merge", "max_retries": 2, "use_examples": False},
    )

    result = ask_service._generate_sql(
        question="按产品和城市统计销售额",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=[
            "olist_order_items_dataset",
            "olist_products_dataset",
            "product_category_name_translation",
            "olist_sellers_dataset",
        ],
        semantic_hits=semantic_hits,
        analysis={
            "tier": "compound",
            "dimensions": ["产品", "城市"],
            "metrics": ["销售额"],
            "entities": [],
            "sub_questions": ["按产品和城市统计销售额"],
        },
        language="zh",
    )

    assert result["sql"] == sub_sql
    assert result["sql_engine"] == "fewshot_cot"


def test_generate_sql_with_requested_question_product_city_performance(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    expected_sql = (
        "SELECT t.product_category_name_english, s.seller_city, "
        "SUM(oi.price) AS total_sales, COUNT(DISTINCT oi.order_id) AS order_cnt "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "JOIN olist_sellers_dataset s ON oi.seller_id = s.seller_id "
        "GROUP BY t.product_category_name_english, s.seller_city "
        "ORDER BY total_sales DESC"
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json"):
            return {"content": json.dumps({"sql": expected_sql, "summary": "ok", "reasoning": "ok"})}

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._generate_sql(
        question="订单中哪些产品销售的比较好、这些产品在不同的城市表现怎样",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=[
            "olist_order_items_dataset",
            "olist_products_dataset",
            "product_category_name_translation",
            "olist_sellers_dataset",
        ],
        semantic_hits=_olist_hits(),
        analysis={
            "tier": "multi_dimension",
            "dimensions": ["Product", "City"],
            "metrics": ["Sales", "Order Count"],
            "entities": [],
            "sub_questions": [],
        },
        language="zh",
    )

    assert result["sql"] == expected_sql
    assert "GROUP BY t.product_category_name_english, s.seller_city" in result["sql"]


def test_generate_sql_with_requested_question_department_or_title_salary(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    expected_sql = (
        "SELECT d.dept_name, t.title, AVG(s.salary) AS avg_salary, MAX(s.salary) AS max_salary "
        "FROM employees e "
        "JOIN dept_emp de ON e.emp_no = de.emp_no "
        "JOIN departments d ON de.dept_no = d.dept_no "
        "JOIN salaries s ON e.emp_no = s.emp_no "
        "JOIN titles t ON e.emp_no = t.emp_no "
        "WHERE de.to_date = DATE '9999-01-01' AND s.to_date = DATE '9999-01-01' AND t.to_date = DATE '9999-01-01' "
        "GROUP BY d.dept_name, t.title "
        "ORDER BY avg_salary DESC"
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json"):
            return {"content": json.dumps({"sql": expected_sql, "summary": "ok", "reasoning": "ok"})}

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._generate_sql(
        question="在这些雇员中哪些部门或者工作岗位的薪资比较高",
        project_id=1,
        previous_questions=["找出每个部门当前薪资最高的前三名员工及其头衔"],
        semantic_context="ctx",
        retrieved_tables=["employees", "dept_emp", "departments", "salaries", "titles"],
        semantic_hits=_hr_hits(),
        analysis={
            "tier": "multi_dimension",
            "dimensions": ["Department", "Title"],
            "metrics": ["Salary"],
            "entities": ["Employees"],
            "sub_questions": [],
        },
        language="zh",
    )

    assert result["sql"] == expected_sql
    assert "GROUP BY d.dept_name, t.title" in result["sql"]


def test_generate_sql_compound_decompose_merge_accepts_plain_text_merge_sql(monkeypatch):
    _patch_prompt_helpers(monkeypatch)

    sub_sql_1 = (
        "SELECT t.product_category_name_english, SUM(oi.price) AS total_sales "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "GROUP BY t.product_category_name_english"
    )
    sub_sql_2 = (
        "SELECT s.seller_city, COUNT(DISTINCT oi.order_id) AS order_cnt "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_sellers_dataset s ON oi.seller_id = s.seller_id "
        "GROUP BY s.seller_city"
    )
    merged_sql = (
        "SELECT t.product_category_name_english, s.seller_city, "
        "SUM(oi.price) AS total_sales, COUNT(DISTINCT oi.order_id) AS order_cnt "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "JOIN olist_sellers_dataset s ON oi.seller_id = s.seller_id "
        "GROUP BY t.product_category_name_english, s.seller_city"
    )

    class FakeLLM:
        def __init__(self):
            self.sub_count = 0

        def is_configured(self):
            return True

        def chat(self, messages, response_format="json"):
            user_text = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
            if "Sub-question:" in user_text:
                self.sub_count += 1
                sql = sub_sql_1 if self.sub_count == 1 else sub_sql_2
                return {"content": json.dumps({"sql": sql, "summary": "sub", "reasoning": "sub"})}
            return {
                "content": (
                    "I merged the SQL as requested.\n"
                    "```sql\n"
                    f"{merged_sql};\n"
                    "```"
                )
            }

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._generate_sql(
        question="先看产品类目销售额，再看城市订单量，并合并成一个结果",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=[
            "olist_order_items_dataset",
            "olist_products_dataset",
            "product_category_name_translation",
            "olist_sellers_dataset",
        ],
        semantic_hits=_olist_hits(),
        analysis={
            "tier": "compound",
            "dimensions": ["Product", "City"],
            "metrics": ["Sales", "Order Count"],
            "entities": [],
            "sub_questions": ["每个产品类目的销售额", "每个城市的订单量"],
        },
        language="zh",
    )

    assert result["sql"] == merged_sql
    assert result["sql_engine"] in {"decompose_merge", "decompose_merge_rehint"}


def test_generate_sql_compound_decompose_merge_retries_merge_candidate_after_placeholder_sql(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    ask_service._decompose_merge_state_by_project.clear()
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "tier3_max_retries", 3)

    sub_sql_1 = (
        "SELECT t.product_category_name_english, SUM(oi.price) AS total_sales "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "GROUP BY t.product_category_name_english"
    )
    sub_sql_2 = (
        "SELECT s.seller_city, COUNT(DISTINCT oi.order_id) AS order_cnt "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_sellers_dataset s ON oi.seller_id = s.seller_id "
        "GROUP BY s.seller_city"
    )
    placeholder_merge_sql = "SELECT * FROM [schema].[table]"
    merged_sql = (
        "SELECT t.product_category_name_english, s.seller_city, "
        "SUM(oi.price) AS total_sales, COUNT(DISTINCT oi.order_id) AS order_cnt "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "JOIN olist_sellers_dataset s ON oi.seller_id = s.seller_id "
        "GROUP BY t.product_category_name_english, s.seller_city"
    )

    class FakeLLM:
        def __init__(self):
            self.sub_count = 0
            self.merge_count = 0

        def is_configured(self):
            return True

        def chat(self, messages, response_format="json"):
            user_text = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
            if "Sub-question:" in user_text:
                self.sub_count += 1
                sql = sub_sql_1 if self.sub_count == 1 else sub_sql_2
                return {"content": json.dumps({"sql": sql, "summary": "sub", "reasoning": "sub"})}
            if "Combine these into a single SQL query" in user_text:
                self.merge_count += 1
                sql = placeholder_merge_sql if self.merge_count == 1 else merged_sql
                return {"content": json.dumps({"sql": sql, "summary": "merged", "reasoning": "merged"})}
            return {"content": json.dumps({"sql": merged_sql, "summary": "direct", "reasoning": "direct"})}

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._generate_sql(
        question="先看产品类目销售额，再看城市订单量，并合并成一个结果",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=[
            "olist_order_items_dataset",
            "olist_products_dataset",
            "product_category_name_translation",
            "olist_sellers_dataset",
        ],
        semantic_hits=_olist_hits(),
        analysis={
            "tier": "compound",
            "dimensions": ["Product", "City"],
            "metrics": ["Sales", "Order Count"],
            "entities": [],
            "sub_questions": ["每个产品类目的销售额", "每个城市的订单量"],
        },
        language="zh",
    )

    assert result["sql"] == merged_sql
    assert result["sql_engine"] in {"decompose_merge", "decompose_merge_rehint"}
    ask_service._decompose_merge_state_by_project.clear()


def test_generate_sql_compound_decompose_merge_circuit_breaker_skips_unstable_merge(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    ask_service._decompose_merge_state_by_project.clear()

    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "decompose_merge_circuit_enabled", True)
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "decompose_merge_failure_threshold", 2)
    monkeypatch.setitem(ask_service.ROUTER_CONFIG, "decompose_merge_disable_seconds", 120)

    calls = {"decompose": 0}

    def fake_decompose(*args, **kwargs):
        calls["decompose"] += 1
        return None

    monkeypatch.setattr(ask_service, "_decompose_merge_sql", fake_decompose)

    direct_sql = (
        "SELECT t.product_category_name_english, s.seller_city, SUM(oi.price) AS total_sales "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "JOIN olist_sellers_dataset s ON oi.seller_id = s.seller_id "
        "GROUP BY t.product_category_name_english, s.seller_city"
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json"):
            return {"content": json.dumps({"sql": direct_sql, "summary": "ok", "reasoning": "direct"})}

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    analysis = {
        "tier": "compound",
        "dimensions": ["Product", "City"],
        "metrics": ["Sales"],
        "entities": [],
        "sub_questions": ["按产品", "按城市"],
    }

    for _ in range(3):
        result = ask_service._generate_sql(
            question="先看产品类目销售额，再看城市订单量，并合并成一个结果",
            project_id=1,
            semantic_context="ctx",
            retrieved_tables=[
                "olist_order_items_dataset",
                "olist_products_dataset",
                "product_category_name_translation",
                "olist_sellers_dataset",
            ],
            semantic_hits=_olist_hits(),
            analysis=analysis,
            language="zh",
        )
        assert result["sql"] == direct_sql

    assert calls["decompose"] == 2
    assert ask_service._is_decompose_merge_temporarily_disabled(1) is True
    ask_service._decompose_merge_state_by_project.clear()


def test_generate_sql_compound_propagates_decompose_failure_reason(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    ask_service._decompose_merge_state_by_project.clear()

    captured = {"reason": None}

    def fake_decompose(*args, **kwargs):
        failure_meta = kwargs.get("failure_meta")
        if isinstance(failure_meta, dict):
            failure_meta["reason"] = "bad_columns"
            failure_meta["reason_counts"] = {"bad_columns": 2}
        return None

    def fake_record_failure(_project_id: int, reason: str | None = None):
        captured["reason"] = reason

    direct_sql = (
        "SELECT t.product_category_name_english, s.seller_city, SUM(oi.price) AS total_sales "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "JOIN olist_sellers_dataset s ON oi.seller_id = s.seller_id "
        "GROUP BY t.product_category_name_english, s.seller_city"
    )

    class FakeLLM:
        def is_configured(self):
            return True

        def chat(self, messages, response_format="json"):
            return {"content": json.dumps({"sql": direct_sql, "summary": "ok", "reasoning": "direct"})}

    monkeypatch.setattr(ask_service, "_decompose_merge_sql", fake_decompose)
    monkeypatch.setattr(ask_service, "_record_decompose_merge_failure", fake_record_failure)
    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._generate_sql(
        question="先看产品类目销售额，再看城市订单量，并合并成一个结果",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=[
            "olist_order_items_dataset",
            "olist_products_dataset",
            "product_category_name_translation",
            "olist_sellers_dataset",
        ],
        semantic_hits=_olist_hits(),
        analysis={
            "tier": "compound",
            "dimensions": ["Product", "City"],
            "metrics": ["Sales", "Order Count"],
            "entities": [],
            "sub_questions": ["按产品", "按城市"],
        },
        language="zh",
    )

    assert result["sql"] == direct_sql
    assert str(result["sql_engine"]).startswith("direct_llm")
    assert captured["reason"] == "bad_columns"


def test_generate_sql_compound_decompose_merge_falls_back_when_groupby_unstable(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    ask_service._decompose_merge_state_by_project.clear()

    sub_sql_1 = (
        "SELECT t.product_category_name_english, SUM(oi.price) AS total_sales "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "GROUP BY t.product_category_name_english"
    )
    sub_sql_2 = (
        "SELECT s.seller_city, COUNT(DISTINCT oi.order_id) AS order_cnt "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_sellers_dataset s ON oi.seller_id = s.seller_id "
        "GROUP BY s.seller_city"
    )
    merged_bad_sql = (
        "SELECT t.product_category_name_english, s.seller_city "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "JOIN olist_sellers_dataset s ON oi.seller_id = s.seller_id"
    )
    direct_sql = (
        "SELECT t.product_category_name_english, s.seller_city, SUM(oi.price) AS total_sales "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "JOIN olist_sellers_dataset s ON oi.seller_id = s.seller_id "
        "GROUP BY t.product_category_name_english, s.seller_city"
    )

    class FakeLLM:
        def __init__(self):
            self.sub_count = 0

        def is_configured(self):
            return True

        def chat(self, messages, response_format="json"):
            user_text = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
            if "Sub-question:" in user_text:
                self.sub_count += 1
                sql = sub_sql_1 if self.sub_count == 1 else sub_sql_2
                return {"content": json.dumps({"sql": sql, "summary": "sub", "reasoning": "sub"})}
            if "Combine these into a single SQL query" in user_text:
                return {"content": json.dumps({"sql": merged_bad_sql, "summary": "merged", "reasoning": "merged"})}
            return {"content": json.dumps({"sql": direct_sql, "summary": "direct", "reasoning": "direct"})}

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._generate_sql(
        question="先看产品类目销售额，再看城市订单量，并合并成一个结果",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=[
            "olist_order_items_dataset",
            "olist_products_dataset",
            "product_category_name_translation",
            "olist_sellers_dataset",
        ],
        semantic_hits=_olist_hits(),
        analysis={
            "tier": "compound",
            "dimensions": ["Product", "City"],
            "metrics": ["Sales", "Order Count"],
            "entities": [],
            "sub_questions": ["每个产品类目的销售额", "每个城市的订单量"],
        },
        language="zh",
    )

    assert result["sql"] == direct_sql
    assert result["sql_engine"].startswith("direct_llm")
    assert (ask_service._decompose_merge_state_by_project.get(1) or {}).get("failures", 0) >= 1
    ask_service._decompose_merge_state_by_project.clear()


def test_generate_sql_compound_decompose_merge_falls_back_when_merge_sql_has_parser_issue(monkeypatch):
    _patch_prompt_helpers(monkeypatch)
    ask_service._decompose_merge_state_by_project.clear()

    sub_sql_1 = (
        "SELECT t.product_category_name_english, SUM(oi.price) AS total_sales "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "GROUP BY t.product_category_name_english"
    )
    sub_sql_2 = (
        "SELECT t.product_category_name_english, COUNT(DISTINCT oi.order_id) AS order_cnt "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "GROUP BY t.product_category_name_english"
    )
    merged_bad_sql = (
        "SELECT t.product_category_name_english, SUM(oi.price) AS total_sales "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "GROUP BY t.product_category_name_english "
        "ORDER BY total_sales DESC "
        "UNION ALL "
        "SELECT t.product_category_name_english, COUNT(DISTINCT oi.order_id) AS total_sales "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "GROUP BY t.product_category_name_english"
    )
    direct_sql = (
        "SELECT t.product_category_name_english, SUM(oi.price) AS total_sales "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "JOIN product_category_name_translation t ON p.product_category_name = t.product_category_name "
        "GROUP BY t.product_category_name_english"
    )

    class FakeLLM:
        def __init__(self):
            self.sub_count = 0

        def is_configured(self):
            return True

        def chat(self, messages, response_format="json"):
            user_text = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
            if "Sub-question:" in user_text:
                self.sub_count += 1
                sql = sub_sql_1 if self.sub_count == 1 else sub_sql_2
                return {"content": json.dumps({"sql": sql, "summary": "sub", "reasoning": "sub"})}
            if "Combine these into a single SQL query" in user_text:
                return {"content": json.dumps({"sql": merged_bad_sql, "summary": "merged", "reasoning": "merged"})}
            return {"content": json.dumps({"sql": direct_sql, "summary": "direct", "reasoning": "direct"})}

    monkeypatch.setattr(ask_service, "LLMService", FakeLLM)

    result = ask_service._generate_sql(
        question="先看产品类目销售额，再看订单量，并合并成一个结果",
        project_id=1,
        semantic_context="ctx",
        retrieved_tables=[
            "olist_order_items_dataset",
            "olist_products_dataset",
            "product_category_name_translation",
        ],
        semantic_hits=_olist_hits(),
        analysis={
            "tier": "compound",
            "dimensions": ["Product"],
            "metrics": ["Sales", "Order Count"],
            "entities": [],
            "sub_questions": ["每个产品类目的销售额", "每个产品类目的订单量"],
        },
        language="zh",
    )

    assert result["sql"] == direct_sql
    assert result["sql_engine"].startswith("direct_llm")
    assert (ask_service._decompose_merge_state_by_project.get(1) or {}).get("failures", 0) >= 1
    ask_service._decompose_merge_state_by_project.clear()
