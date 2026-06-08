from __future__ import annotations

from scripts.unknown_columns_replay import build_replay_cases


def test_build_replay_cases_extracts_buckets_and_actions():
    log_text = """
SQL references unknown columns ['T3.order_id (belongs on: olist_order_items_dataset, olist_orders_dataset)'] — attempting rehint then repair
SQL references unknown columns ['t1.quantity (not found; did you mean customer_city on olist_customers_dataset?)'] — attempting rehint then repair
SQL references unknown columns ['x.foo (not found in any model)'] — attempting rehint then repair
"""
    cases = build_replay_cases(log_text, top=10)
    assert len(cases) == 3

    by_col = {case["column_ref"]: case for case in cases}
    assert by_col["T3.order_id"]["bucket"] == "ambiguous_owner"
    assert by_col["T3.order_id"]["suggested_action"] == "disambiguate_owner_with_join_path"
    assert by_col["t1.quantity"]["bucket"] == "fuzzy_miss"
    assert by_col["x.foo"]["bucket"] == "hallucinated_column"


def test_build_replay_cases_aggregates_duplicate_issues():
    log_text = """
SQL references unknown columns ['t2.customer_city (belongs on: olist_customers_dataset)'] — attempting rehint then repair
SQL references unknown columns ['t2.customer_city (belongs on: olist_customers_dataset)'] — attempting rehint then repair
"""
    cases = build_replay_cases(log_text, top=10)
    assert len(cases) == 1
    assert cases[0]["column_ref"] == "t2.customer_city"
    assert cases[0]["count"] == 2
    assert cases[0]["bucket"] == "wrong_alias_owner"


def test_build_replay_cases_maps_alias_scope_leak_to_rewrite_action():
    log_text = """
SQL references unknown columns ['olist_customers_dataset.customer_city (table/alias not visible in current SELECT scope; available: c, o, oi, p)'] — attempting rehint then repair
"""
    cases = build_replay_cases(log_text, top=10)

    assert len(cases) == 1
    assert cases[0]["bucket"] == "alias_scope_leak"
    assert cases[0]["suggested_action"] == "rewrite_to_visible_scope_alias"
