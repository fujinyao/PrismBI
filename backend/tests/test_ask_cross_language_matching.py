from __future__ import annotations

from services.ask_service import _semantic_hits, _tokenize


def _build_model(
    name: str,
    display_name: str,
    description: str,
    columns: list[dict[str, str]],
) -> dict:
    return {
        "id": 1,
        "name": name,
        "display_name": display_name,
        "description": description,
        "table_reference": name,
        "columns": columns,
    }


def test_tokenize_no_longer_expands_synonyms():
    """After removing hardcoded synonym dictionaries, tokenize is raw."""
    tokens = _tokenize("按城市看 GMV 趋势")
    assert "gmv" in tokens
    assert "city" not in tokens
    assert "trend" not in tokens
    assert "按城市看" in tokens


def test_semantic_hits_token_overlap_same_language():
    """Same-language matches still work via token overlap on names/display_names."""
    models = [
        _build_model(
            name="orders",
            display_name="orders",
            description="order records",
            columns=[
                {"name": "amount", "type": "DOUBLE", "display_name": "amount", "description": "order revenue"},
                {"name": "customer_city", "type": "VARCHAR", "display_name": "city", "description": "customer city"},
            ],
        )
    ]

    hits = _semantic_hits("top revenue by city", models, relations=[])

    assert hits["has_hits"] is True
    assert [model["name"] for model in hits["models"]] == ["orders"]


def test_semantic_hits_matches_model_name_directly():
    """Direct model name matches should work."""
    models = [
        _build_model(
            name="sales_fact",
            display_name="Sales Fact",
            description="Sales and city performance",
            columns=[
                {"name": "revenue", "type": "DOUBLE", "display_name": "Revenue", "description": "Order revenue"},
                {"name": "customer_city", "type": "VARCHAR", "display_name": "Customer City", "description": "City"},
            ],
        )
    ]

    hits = _semantic_hits("sales fact revenue", models, relations=[])

    assert hits["has_hits"] is True
    assert [model["name"] for model in hits["models"]] == ["sales_fact"]


def test_semantic_hits_no_match_returns_empty():
    """When no token overlap exists, hits should be empty."""
    models = [
        _build_model(
            name="regional_metrics",
            display_name="Regional Metrics",
            description="Revenue by state",
            columns=[
                {"name": "state", "type": "VARCHAR", "display_name": "State", "description": "Province/region"},
                {"name": "amount", "type": "DOUBLE", "display_name": "Amount", "description": "Total dollar amount"},
            ],
        )
    ]

    hits = _semantic_hits("按省份看GMV", models, relations=[])

    assert hits["has_hits"] is False
