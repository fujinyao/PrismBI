from __future__ import annotations

from services.ask_service import (
    _apply_alias_scope_rewrite_rules,
    _apply_group_by_aggregation_rewrite_rules,
    _apply_group_by_completion_rules,
    _apply_owner_selector_rules,
    _apply_resolved_dimension_group_by_rules,
    _build_ambiguous_owner_hint,
    _build_schema_linking_plan,
    _build_sql_planning_artifact,
    _classify_unknown_column_issue,
    _estimate_sql_generation_complexity,
    _fix_type_mismatch_multiply,
    _fuzzy_column_match,
    _enforce_group_by_constraints,
    _normalize_sql_text,
    _rehint_columns,
    _summarize_unknown_column_issues,
    _validate_sql_alias_scope,
    _validate_sql_aggregation,
    _validate_sql_group_by,
    _validate_sql_columns,
    _validate_duckdb_sql_syntax,
)


def _models() -> list[dict]:
    return [
        {
            "name": "olist_order_items_dataset",
            "table_reference": "olist_order_items_dataset",
            "columns": [
                {"name": "order_id", "type": "VARCHAR"},
                {"name": "product_id", "type": "VARCHAR"},
                {"name": "price", "type": "DOUBLE"},
                {"name": "seller_id", "type": "VARCHAR"},
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


def test_validate_sql_columns_cte_projection_rejects_non_projected_column():
    sql = (
        "WITH t1 AS (SELECT seller_id FROM olist_sellers_dataset) "
        "SELECT t1.seller_city FROM t1"
    )
    issues = _validate_sql_columns(sql, _models())
    assert issues
    assert any("not projected by CTE t1" in issue for issue in issues)


def test_validate_sql_columns_cte_projection_accepts_projected_column():
    sql = (
        "WITH t1 AS (SELECT seller_id, seller_city FROM olist_sellers_dataset) "
        "SELECT t1.seller_city FROM t1"
    )
    issues = _validate_sql_columns(sql, _models())
    assert issues == []


def test_validate_sql_columns_rejects_unqualified_column_not_projected_by_cte():
    sql = (
        "WITH product_city_sales AS ("
        "SELECT oi.product_id FROM olist_order_items_dataset oi"
        ") "
        "SELECT SUM(price) AS total_sales FROM product_city_sales"
    )

    issues = _validate_sql_columns(sql, _models())

    assert issues
    assert any("not visible in current select scope" in issue.lower() for issue in issues)


def test_validate_sql_columns_rejects_cte_alias_scope_leakage():
    sql = (
        "WITH sales_cte AS ("
        "SELECT oi.product_id, s.seller_city, oi.price "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_sellers_dataset s ON oi.seller_id = s.seller_id"
        ") "
        "SELECT oi.product_id, s.seller_city, SUM(sales_cte.price) AS total_sales "
        "FROM sales_cte "
        "GROUP BY oi.product_id, s.seller_city"
    )

    issues = _validate_sql_columns(sql, _models())

    assert issues
    assert any("not visible in current select scope" in issue.lower() for issue in issues)


def test_validate_sql_alias_scope_detects_duplicate_aliases():
    sql = (
        "SELECT T1.order_id "
        "FROM olist_order_items_dataset AS T1 "
        "JOIN olist_sellers_dataset AS T1 ON T1.seller_id = T1.seller_id"
    )

    issues = _validate_sql_alias_scope(sql)

    assert issues
    assert any("duplicate table alias" in issue.lower() for issue in issues)


def test_validate_sql_columns_reports_duplicate_aliases():
    sql = (
        "SELECT T1.order_id "
        "FROM olist_order_items_dataset AS T1 "
        "JOIN olist_sellers_dataset AS T1 ON T1.seller_id = T1.seller_id"
    )

    issues = _validate_sql_columns(sql, _models())

    assert issues
    assert any("duplicate table alias" in issue.lower() for issue in issues)


def test_group_by_completion_rules_rewrite_outer_columns_to_single_cte_alias():
    sql = (
        "WITH sales_cte AS ("
        "SELECT oi.product_id, s.seller_city, oi.price "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_sellers_dataset s ON oi.seller_id = s.seller_id"
        ") "
        "SELECT oi.product_id, s.seller_city, SUM(sales_cte.price) AS total_sales "
        "FROM sales_cte "
        "GROUP BY oi.product_id, s.seller_city"
    )

    rewritten = _apply_group_by_completion_rules(sql).lower()

    assert "select sales_cte.product_id, sales_cte.seller_city" in rewritten
    assert "group by sales_cte.product_id, sales_cte.seller_city" in rewritten
    assert "group by oi.product_id" not in rewritten


def test_apply_alias_scope_rewrite_rules_rewrites_full_model_refs_to_visible_aliases():
    models = [
        {
            "name": "olist_order_items_dataset",
            "table_reference": "olist_order_items_dataset",
            "columns": [
                {"name": "order_id", "type": "VARCHAR"},
                {"name": "product_id", "type": "VARCHAR"},
                {"name": "price", "type": "DOUBLE"},
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
            "name": "olist_customers_dataset",
            "table_reference": "olist_customers_dataset",
            "columns": [
                {"name": "customer_id", "type": "VARCHAR"},
                {"name": "customer_city", "type": "VARCHAR"},
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
    ]
    sql = (
        "SELECT olist_products_dataset.product_category_name, "
        "olist_customers_dataset.customer_city, "
        "SUM(olist_order_items_dataset.price) AS total_sales "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_orders_dataset o ON oi.order_id = o.order_id "
        "JOIN olist_customers_dataset c ON o.customer_id = c.customer_id "
        "JOIN olist_products_dataset p ON oi.product_id = p.product_id "
        "GROUP BY olist_products_dataset.product_category_name, olist_customers_dataset.customer_city"
    )
    issues = [
        "olist_products_dataset.product_category_name (table/alias not visible in current SELECT scope; available: c, o, oi, p)",
        "olist_customers_dataset.customer_city (table/alias not visible in current SELECT scope; available: c, o, oi, p)",
        "olist_order_items_dataset.price (table/alias not visible in current SELECT scope; available: c, o, oi, p)",
    ]

    rewritten = _apply_alias_scope_rewrite_rules(sql, issues, hit_models=models).lower()

    assert "p.product_category_name" in rewritten
    assert "c.customer_city" in rewritten
    assert "sum(oi.price)" in rewritten
    assert "olist_products_dataset.product_category_name" not in rewritten
    assert "olist_customers_dataset.customer_city" not in rewritten


def test_apply_alias_scope_rewrite_rules_qualifies_ambiguous_unqualified_column_with_owner_hint():
    sql = (
        "WITH dept_salary AS ("
        "SELECT de.dept_no, AVG(s.salary) AS avg_salary "
        "FROM dept_emp de "
        "JOIN salaries s ON de.emp_no = s.emp_no "
        "GROUP BY de.dept_no"
        "), "
        "title_salary AS ("
        "SELECT t.title, de.dept_no, AVG(s.salary) AS avg_salary "
        "FROM titles t "
        "JOIN dept_emp de ON t.emp_no = de.emp_no "
        "JOIN salaries s ON de.emp_no = s.emp_no "
        "GROUP BY t.title, de.dept_no"
        ") "
        "SELECT ds.dept_no, ts.title, avg_salary "
        "FROM dept_salary ds "
        "JOIN title_salary ts ON ds.dept_no = ts.dept_no "
        "ORDER BY avg_salary DESC"
    )
    issues = [
        "avg_salary (not visible in current SELECT scope; ambiguous unqualified column, candidates: ds, ts)",
    ]

    rewritten = _apply_alias_scope_rewrite_rules(
        sql,
        issues,
        hit_models=[],
        schema_link_plan={"selected_owner_map": {"avg_salary": "dept_salary"}},
    ).lower()

    assert "select ds.dept_no, ts.title, ds.avg_salary" in rewritten
    assert "order by avg_salary desc" in rewritten


def test_validate_sql_group_by_dedupes_join_key_dimensions_across_joined_models():
    hit_models = [
        {
            "name": "dept_emp",
            "table_reference": "dept_emp",
            "columns": [
                {"name": "dept_no", "type": "VARCHAR"},
                {"name": "emp_no", "type": "INTEGER"},
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
            "name": "dept_manager",
            "table_reference": "dept_manager",
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
    ]
    resolved = {
        "dimensions_resolved": [
            {"column": "dept_no", "model": "dept_emp"},
            {"column": "dept_no", "model": "departments"},
            {"column": "dept_no", "model": "dept_manager"},
            {"column": "dept_name", "model": "departments"},
        ],
    }
    sql = (
        "SELECT de.dept_no, d.dept_name, AVG(s.salary) AS avg_salary "
        "FROM dept_emp de "
        "JOIN departments d ON de.dept_no = d.dept_no "
        "JOIN dept_manager dm ON dm.dept_no = de.dept_no "
        "JOIN salaries s ON de.emp_no = s.emp_no "
        "GROUP BY de.dept_no, d.dept_name"
    )

    issues = _validate_sql_group_by(
        sql,
        ["dept_no", "dept_name"],
        hit_models=hit_models,
        resolved=resolved,
    )

    assert issues == []


def test_enforce_group_by_constraints_adds_missing_resolved_dimension_columns():
    hit_models = [
        {
            "name": "product_category_name_translation",
            "table_reference": "product_category_name_translation",
            "columns": [
                {"name": "product_category_name_english", "display_name": "产品", "type": "VARCHAR"},
            ],
        },
        {
            "name": "olist_customers_dataset",
            "table_reference": "olist_customers_dataset",
            "columns": [
                {"name": "customer_city", "display_name": "城市", "type": "VARCHAR"},
            ],
        },
        {
            "name": "olist_order_items_dataset",
            "table_reference": "olist_order_items_dataset",
            "columns": [
                {"name": "price", "type": "DOUBLE"},
                {"name": "product_id", "type": "VARCHAR"},
                {"name": "order_id", "type": "VARCHAR"},
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
            "name": "olist_products_dataset",
            "table_reference": "olist_products_dataset",
            "columns": [
                {"name": "product_id", "type": "VARCHAR"},
                {"name": "product_category_name", "type": "VARCHAR"},
            ],
        },
    ]
    resolved = {
        "dimensions_resolved": [
            {"column": "product_category_name_english", "model": "product_category_name_translation"},
            {"column": "customer_city", "model": "olist_customers_dataset"},
        ]
    }
    sql = (
        "SELECT p.product_category_name_english, SUM(oi.price) AS total_sales "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_orders_dataset o ON oi.order_id = o.order_id "
        "JOIN olist_customers_dataset c ON o.customer_id = c.customer_id "
        "JOIN olist_products_dataset p0 ON oi.product_id = p0.product_id "
        "JOIN product_category_name_translation p ON p0.product_category_name = p.product_category_name "
        "GROUP BY p.product_category_name_english"
    )

    rewritten, issues = _enforce_group_by_constraints(
        sql,
        ["产品", "城市"],
        hit_models=hit_models,
        resolved=resolved,
    )

    assert issues == []
    lower_sql = rewritten.lower()
    assert "c.customer_city" in lower_sql
    assert "group by" in lower_sql


def test_rehint_columns_keeps_cte_alias_when_fixing_typo():
    sql = (
        "WITH t2 AS (SELECT product_category_name_english FROM product_category_name_translation) "
        "SELECT t2.product_category_name_englsh FROM t2"
    )
    rehinted = _rehint_columns(sql, _models())
    assert "t2.product_category_name_english" in rehinted
    assert "product_category_name_translation.product_category_name_english" not in rehinted


def test_fix_type_mismatch_multiply_rewrites_string_times_aggregate():
    sql = (
        "WITH T1 AS (SELECT price, product_id FROM olist_order_items_dataset), "
        "T2 AS (SELECT product_category_name_english, product_category_name FROM product_category_name_translation) "
        "SELECT T2.product_category_name_english * SUM(T1.price) AS total_sales "
        "FROM T1 JOIN T2 ON T1.product_id = T2.product_category_name "
        "GROUP BY T2.product_category_name_english"
    )
    fixed = _fix_type_mismatch_multiply(sql, _models())
    assert "T2.product_category_name_english * SUM(T1.price)" not in fixed
    assert "T2.product_category_name_english, SUM(T1.price)" in fixed


def test_estimate_sql_generation_complexity_scoring():
    low = _estimate_sql_generation_complexity(
        {"tier": "simple", "sub_questions": [], "dimensions": []},
        {"models": [{"name": "orders"}]},
    )
    high = _estimate_sql_generation_complexity(
        {
            "tier": "compound",
            "sub_questions": ["q1", "q2"],
            "dimensions": ["product", "city"],
        },
        {
            "models": [{"name": "m1"}, {"name": "m2"}, {"name": "m3"}, {"name": "m4"}],
            "broad_match": True,
        },
    )
    assert low <= 1
    assert high >= 6


def test_validate_sql_group_by_resolves_product_city_dimensions_with_alias_columns():
    hit_models = [
        {
            "name": "product_category_name_translation",
            "columns": [
                {"name": "product_category_name_english", "display_name": "Product", "type": "VARCHAR"},
            ],
        },
        {
            "name": "olist_sellers_dataset",
            "columns": [
                {"name": "seller_city", "display_name": "City", "type": "VARCHAR"},
            ],
        },
    ]
    sql = (
        "SELECT t.product_category_name_english, s.seller_city, SUM(o.price) "
        "FROM olist_order_items_dataset o "
        "JOIN product_category_name_translation t ON 1=1 "
        "JOIN olist_sellers_dataset s ON 1=1 "
        "GROUP BY t.product_category_name_english, s.seller_city"
    )
    issues = _validate_sql_group_by(sql, ["Product", "City"], hit_models=hit_models, resolved={})
    assert issues == []


def test_validate_sql_group_by_accepts_chinese_display_dimension_aliases_and_ordinals():
    hit_models = [
        {
            "name": "companies",
            "table_reference": "companies",
            "columns": [
                {"name": "id", "display_name": "公司ID", "type": "INTEGER"},
                {"name": "name", "display_name": "公司", "type": "VARCHAR"},
            ],
        },
        {
            "name": "orders",
            "table_reference": "orders",
            "columns": [
                {"name": "company_id", "type": "INTEGER"},
                {"name": "amount", "display_name": "销售额", "type": "DOUBLE"},
            ],
        },
    ]
    sql = (
        "SELECT c.name AS company_name, SUM(o.amount) AS total_sales "
        "FROM orders o "
        "JOIN companies c ON o.company_id = c.id "
        "GROUP BY 1"
    )

    issues = _validate_sql_group_by(sql, ["公司"], hit_models=hit_models, resolved={})

    assert issues == []


def test_validate_sql_group_by_accepts_cjk_dimensions_with_fallback_aliases_when_display_name_missing():
    hit_models = [
        {
            "name": "product_category_name_translation",
            "table_reference": "product_category_name_translation",
            "columns": [
                {"name": "product_category_name_english", "type": "VARCHAR"},
            ],
        },
        {
            "name": "olist_sellers_dataset",
            "table_reference": "olist_sellers_dataset",
            "columns": [
                {"name": "seller_city", "type": "VARCHAR"},
            ],
        },
        {
            "name": "olist_order_items_dataset",
            "table_reference": "olist_order_items_dataset",
            "columns": [
                {"name": "price", "type": "DOUBLE"},
            ],
        },
    ]
    sql = (
        "SELECT t.product_category_name_english, s.seller_city, SUM(o.price) AS total_sales "
        "FROM olist_order_items_dataset o "
        "JOIN product_category_name_translation t ON o.product_id = t.product_category_name "
        "JOIN olist_sellers_dataset s ON o.seller_id = s.seller_id "
        "GROUP BY t.product_category_name_english, s.seller_city"
    )

    issues = _validate_sql_group_by(sql, ["产品", "城市"], hit_models=hit_models, resolved={})

    assert issues == []


def test_validate_sql_group_by_accepts_department_and_title_cjk_aliases_without_display_names():
    hit_models = [
        {
            "name": "departments",
            "table_reference": "departments",
            "columns": [
                {"name": "dept_no", "type": "VARCHAR"},
                {"name": "dept_name", "type": "VARCHAR"},
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
        {
            "name": "salaries",
            "table_reference": "salaries",
            "columns": [
                {"name": "emp_no", "type": "INTEGER"},
                {"name": "salary", "type": "DOUBLE"},
            ],
        },
    ]
    sql = (
        "SELECT d.dept_name, t.title, AVG(s.salary) AS avg_salary "
        "FROM salaries s "
        "JOIN titles t ON s.emp_no = t.emp_no "
        "JOIN departments d ON d.dept_no = 'd001' "
        "GROUP BY d.dept_name, t.title"
    )

    issues = _validate_sql_group_by(sql, ["部门", "岗位"], hit_models=hit_models, resolved={})

    assert issues == []


def test_validate_sql_group_by_prefers_resolved_dimensions_for_unmapped_raw_labels():
    hit_models = [
        {
            "name": "product_category_name_translation",
            "table_reference": "product_category_name_translation",
            "columns": [
                {"name": "product_category_name_english", "type": "VARCHAR"},
            ],
        },
        {
            "name": "olist_sellers_dataset",
            "table_reference": "olist_sellers_dataset",
            "columns": [
                {"name": "seller_city", "type": "VARCHAR"},
            ],
        },
        {
            "name": "olist_order_items_dataset",
            "table_reference": "olist_order_items_dataset",
            "columns": [
                {"name": "price", "type": "DOUBLE"},
            ],
        },
    ]
    resolved = {
        "dimensions_resolved": [
            {"column": "product_category_name_english", "model": "product_category_name_translation"},
            {"column": "seller_city", "model": "olist_sellers_dataset"},
        ]
    }
    sql = (
        "SELECT t.product_category_name_english, s.seller_city, SUM(o.price) AS total_sales "
        "FROM olist_order_items_dataset o "
        "JOIN product_category_name_translation t ON o.product_id = t.product_category_name "
        "JOIN olist_sellers_dataset s ON o.seller_id = s.seller_id "
        "GROUP BY t.product_category_name_english, s.seller_city"
    )

    issues = _validate_sql_group_by(
        sql,
        ["产品维度标签", "城市维度标签"],
        hit_models=hit_models,
        resolved=resolved,
    )

    assert issues == []


def test_validate_sql_group_by_downgrades_unresolved_cjk_dimensions_to_warning():
    hit_models = [
        {
            "name": "orders",
            "table_reference": "orders",
            "columns": [
                {"name": "amount", "type": "DOUBLE"},
            ],
        }
    ]
    sql = "SELECT SUM(o.amount) AS total_sales FROM orders o"

    issues = _validate_sql_group_by(sql, ["城市"], hit_models=hit_models, resolved={})

    assert issues == []


def test_validate_sql_aggregation_flags_non_grouped_order_by_column():
    sql = (
        "SELECT oi.product_id, SUM(oi.price) AS total_revenue "
        "FROM olist_order_items_dataset oi "
        "GROUP BY oi.product_id "
        "ORDER BY total_revenue DESC, oi.price DESC"
    )

    issues = _validate_sql_aggregation(sql)

    assert any("ORDER BY" in issue and "oi.price" in issue for issue in issues)


def test_validate_sql_aggregation_flags_non_grouped_having_column():
    sql = (
        "SELECT oi.product_id, SUM(oi.price) AS total_revenue "
        "FROM olist_order_items_dataset oi "
        "GROUP BY oi.product_id "
        "HAVING oi.price > 0"
    )

    issues = _validate_sql_aggregation(sql)

    assert any("HAVING" in issue and "oi.price" in issue for issue in issues)


def test_apply_group_by_aggregation_rewrite_rules_wraps_non_grouped_order_and_having_columns():
    sql = (
        "SELECT oi.product_id, SUM(oi.price) AS total_revenue "
        "FROM olist_order_items_dataset oi "
        "GROUP BY oi.product_id "
        "HAVING oi.price > 0 "
        "ORDER BY oi.price DESC"
    )

    rewritten_sql, rewrite_notes = _apply_group_by_aggregation_rewrite_rules(sql)

    assert rewritten_sql != sql
    assert rewrite_notes
    lowered = rewritten_sql.lower()
    assert "having max(oi.price) > 0" in lowered
    assert "order by max(oi.price) desc" in lowered
    assert _validate_sql_aggregation(rewritten_sql) == []


def test_normalize_sql_text_converts_fullwidth_punctuation_outside_literals():
    sql = "SELECT 'Ａ，Ｂ' AS label， city FROM orders WHERE note='x；y'；"

    normalized = _normalize_sql_text(sql)

    assert "AS label, city" in normalized
    assert normalized.endswith(";")
    assert "'Ａ，Ｂ'" in normalized
    assert "'x；y'" in normalized


def test_validate_duckdb_sql_syntax_detects_union_after_pre_union_order_by():
    issues = _validate_duckdb_sql_syntax("SELECT 1 ORDER BY 1 UNION ALL SELECT 2")

    assert issues
    assert "union" in issues[0].lower()


def test_validate_duckdb_sql_syntax_accepts_valid_union_ordering():
    issues = _validate_duckdb_sql_syntax("SELECT 1 UNION ALL SELECT 2 ORDER BY 1")

    assert issues == []


def test_validate_duckdb_sql_syntax_detects_duplicate_aliases():
    issues = _validate_duckdb_sql_syntax(
        "SELECT T1.x FROM (SELECT 1 AS x) AS T1 JOIN (SELECT 2 AS x) AS T1 ON 1 = 1"
    )

    assert issues
    assert "duplicate alias" in issues[0].lower()


def test_fuzzy_column_match_does_not_map_unrelated_tokens():
    candidates = {"customer_city", "seller_city", "product_category_name", "order_id"}
    assert _fuzzy_column_match("quantity", candidates) is None


def test_rehint_columns_does_not_force_unrelated_column_mapping():
    models = [
        {
            "name": "olist_customers_dataset",
            "table_reference": "olist_customers_dataset",
            "columns": [
                {"name": "customer_id", "type": "VARCHAR"},
                {"name": "customer_city", "type": "VARCHAR"},
            ],
        }
    ]
    sql = "SELECT t1.quantity FROM olist_customers_dataset t1"
    rehinted = _rehint_columns(sql, models)
    assert "t1.quantity" in rehinted
    assert "customer_city" not in rehinted


def test_classify_unknown_column_issue_buckets():
    assert _classify_unknown_column_issue("T1 (duplicate table alias in the same SELECT scope)") == "duplicate_alias"
    assert _classify_unknown_column_issue("oi.product_id (table/alias not visible in current SELECT scope; available: sales_cte)") == "alias_scope_leak"
    assert _classify_unknown_column_issue("T1.c (not projected by CTE t1)") == "cte_projection_missing"
    assert _classify_unknown_column_issue("t2.city (belongs on: customers)") == "wrong_alias_owner"
    assert _classify_unknown_column_issue("t2.order_id (belongs on: orders, payments)") == "ambiguous_owner"
    assert _classify_unknown_column_issue("t1.qty (not found; did you mean quantity on order_items?)") == "fuzzy_miss"
    assert _classify_unknown_column_issue("t9.foo (not found in any model)") == "hallucinated_column"


def test_summarize_unknown_column_issues_counts_buckets():
    issues = [
        "t2.city (belongs on: customers)",
        "t2.order_id (belongs on: orders, payments)",
        "t1.qty (not found; did you mean quantity on order_items?)",
        "t9.foo (not found in any model)",
    ]
    summary = _summarize_unknown_column_issues(issues)
    assert summary["wrong_alias_owner"] == 1
    assert summary["ambiguous_owner"] == 1
    assert summary["fuzzy_miss"] == 1
    assert summary["hallucinated_column"] == 1


def test_build_ambiguous_owner_hint_prefers_owner_in_query_tables():
    failed_sql = "SELECT t3.order_id FROM olist_orders_dataset t3"
    error = "T3.order_id (belongs on: olist_order_items_dataset, olist_orders_dataset)"
    hint = _build_ambiguous_owner_hint(failed_sql, error, hit_models=[])
    assert "Ambiguous owner resolution hints" in hint
    assert "prefer owner 'olist_orders_dataset'" in hint


def test_schema_linking_plan_selects_preferred_owner_for_ambiguous_column():
    analysis = {"tier": "simple", "dimensions": ["City"], "metrics": ["Orders"]}
    hit_models = {
        "models": [
            {
                "name": "olist_orders_dataset",
                "table_reference": "olist_orders_dataset",
                "columns": [{"name": "order_id"}, {"name": "customer_id"}],
            },
            {
                "name": "olist_order_items_dataset",
                "table_reference": "olist_order_items_dataset",
                "columns": [{"name": "order_id"}, {"name": "price"}],
            },
        ],
        "relations": [],
    }
    resolved = {
        "dimensions_resolved": [{"column": "customer_id", "model": "olist_orders_dataset"}],
        "metrics_resolved": [{"column": "order_id", "model": "olist_orders_dataset"}],
        "entities_resolved": [],
    }
    plan = _build_schema_linking_plan("q", analysis, hit_models, resolved)
    assert plan["selected_owner_map"]["order_id"] == "olist_orders_dataset"


def test_sql_planning_artifact_contains_join_and_group_by_columns():
    analysis = {
        "tier": "multi_dimension",
        "dimensions": ["City"],
        "metrics": ["Revenue"],
        "entities": ["Orders"],
        "filters": [{"field": "order_status", "op": "=", "value": "delivered"}],
    }
    semantic_hits = {
        "models": [
            {"name": "orders", "table_reference": "orders", "columns": [{"name": "customer_id"}, {"name": "order_id"}]},
            {"name": "customers", "table_reference": "customers", "columns": [{"name": "customer_id"}, {"name": "city"}]},
        ],
        "relations": [
            {"source_model": "orders", "source_column": "customer_id", "target_model": "customers", "target_column": "customer_id"}
        ],
    }
    resolved = {
        "dimensions_resolved": [{"column": "city", "model": "customers"}],
        "metrics_resolved": [{"column": "order_id", "model": "orders"}],
        "entities_resolved": [],
    }
    schema_plan = _build_schema_linking_plan("q", analysis, semantic_hits, resolved)
    sql_plan = _build_sql_planning_artifact("q", analysis, semantic_hits, resolved, schema_plan)
    assert "city" in sql_plan["group_by_columns"]
    assert any("orders.customer_id = customers.customer_id" in item for item in sql_plan["join_path_hints"])


def test_owner_selector_rules_rehints_using_preferred_owner():
    models = [
        {
            "name": "olist_orders_dataset",
            "table_reference": "olist_orders_dataset",
            "columns": [{"name": "order_id"}, {"name": "customer_id"}],
        },
        {
            "name": "olist_order_items_dataset",
            "table_reference": "olist_order_items_dataset",
            "columns": [{"name": "order_id"}],
        },
    ]
    sql = (
        "SELECT oi.customer_id "
        "FROM olist_order_items_dataset oi "
        "JOIN olist_orders_dataset o ON oi.order_id = o.order_id"
    )
    bad_columns = ["oi.customer_id (belongs on: olist_orders_dataset)"]
    schema_link_plan = {"selected_owner_map": {"customer_id": "olist_orders_dataset"}}
    rewritten = _apply_owner_selector_rules(sql, models, bad_columns=bad_columns, schema_link_plan=schema_link_plan)
    assert "o.customer_id" in rewritten


def test_group_by_completion_rules_adds_missing_non_aggregated_column():
    sql = "SELECT c.city, o.customer_id, SUM(o.amount) AS total_amount FROM orders o JOIN customers c ON o.customer_id = c.customer_id GROUP BY c.city"
    rewritten = _apply_group_by_completion_rules(sql)
    assert "GROUP BY c.city, o.customer_id" in rewritten or "GROUP BY o.customer_id, c.city" in rewritten


def test_apply_resolved_dimension_group_by_rules_adds_missing_resolved_dimensions():
    hit_models = [
        {
            "name": "departments",
            "table_reference": "departments",
            "columns": [
                {"name": "dept_no", "type": "VARCHAR"},
                {"name": "dept_name", "display_name": "部门", "type": "VARCHAR"},
            ],
        },
        {
            "name": "titles",
            "table_reference": "titles",
            "columns": [
                {"name": "emp_no", "type": "INTEGER"},
                {"name": "title", "display_name": "工作岗位", "type": "VARCHAR"},
            ],
        },
        {
            "name": "salaries",
            "table_reference": "salaries",
            "columns": [
                {"name": "emp_no", "type": "INTEGER"},
                {"name": "salary", "type": "INTEGER"},
            ],
        },
    ]
    sql = (
        "SELECT d.dept_name, AVG(s.salary) AS avg_salary "
        "FROM salaries s "
        "JOIN titles t ON s.emp_no = t.emp_no "
        "JOIN departments d ON d.dept_no = 'd001' "
        "GROUP BY d.dept_name"
    )
    resolved = {
        "dimensions_resolved": [
            {"column": "dept_name", "model": "departments"},
            {"column": "title", "model": "titles"},
        ]
    }

    rewritten = _apply_resolved_dimension_group_by_rules(sql, resolved, hit_models=hit_models)

    assert "t.title" in rewritten
    assert "GROUP BY d.dept_name, t.title" in rewritten or "GROUP BY t.title, d.dept_name" in rewritten
    issues = _validate_sql_group_by(
        rewritten,
        ["部门", "工作岗位"],
        hit_models=hit_models,
        resolved=resolved,
    )
    assert issues == []
