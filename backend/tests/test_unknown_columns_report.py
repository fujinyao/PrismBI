from __future__ import annotations

from scripts.unknown_columns_report import analyze_log


def test_analyze_log_buckets_unknown_columns_lines():
    log_text = """
SQL references unknown columns ['T3.customer_city (belongs on: olist_customers_dataset)'] — attempting rehint then repair
SQL references unknown columns ['t3.order_id (belongs on: olist_order_items_dataset, olist_orders_dataset)', 't1.quantity (not found; did you mean customer_city on olist_customers_dataset?)'] — attempting rehint then repair
SQL references unknown columns ['x.foo (not found in any model)', 't1.bar (not projected by CTE t1)'] — attempting rehint then repair
"""
    report = analyze_log(log_text)

    assert report["unknown_lines"] == 3
    assert report["bucket_counts"]["wrong_alias_owner"] == 1
    assert report["bucket_counts"]["ambiguous_owner"] == 1
    assert report["bucket_counts"]["fuzzy_miss"] == 1
    assert report["bucket_counts"]["hallucinated_column"] == 1
    assert report["bucket_counts"]["cte_projection_missing"] == 1


def test_analyze_log_extracts_top_columns():
    log_text = """
SQL references unknown columns ['t1.order_id (belongs on: olist_order_items_dataset, olist_orders_dataset)'] — attempting rehint then repair
SQL references unknown columns ['t1.order_id (belongs on: olist_order_items_dataset, olist_orders_dataset)', 't2.customer_city (belongs on: olist_customers_dataset)'] — attempting rehint then repair
"""
    report = analyze_log(log_text)
    top_columns = dict(report["top_columns"])

    assert top_columns.get("t1.order_id") == 2
    assert top_columns.get("t2.customer_city") == 1


def test_analyze_log_tracks_alias_scope_leak_bucket_for_product_city_regression():
    log_text = """
SQL references unknown columns ['olist_products_dataset.product_category_name (table/alias not visible in current SELECT scope; available: c, o, oi, p)', 'olist_customers_dataset.customer_city (table/alias not visible in current SELECT scope; available: c, o, oi, p)', 'olist_order_items_dataset.price (table/alias not visible in current SELECT scope; available: c, o, oi, p)', 'olist_order_items_dataset.order_item_id (table/alias not visible in current SELECT scope; available: c, o, oi, p)', 'olist_orders_dataset.order_id (table/alias not visible in current SELECT scope; available: c, o, oi, p)'] — attempting rehint then repair
SQL references unknown columns ['oi.product_category_name (belongs on: olist_products_dataset, product_category_name_translation)'] — attempting rehint then repair
"""
    report = analyze_log(log_text)

    assert report["unknown_lines"] == 2
    assert report["bucket_counts"]["alias_scope_leak"] == 5
    assert report["bucket_counts"]["ambiguous_owner"] == 1
