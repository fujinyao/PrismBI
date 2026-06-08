from __future__ import annotations


def test_knowledge_context_retrieves_instructions_and_sql_pairs(test_db, seed_project: dict):
    from services.ask_service import _knowledge_context

    test_db.execute(
        """
        INSERT INTO metadata.instructions (id, project_id, instruction, category, scope, priority, questions)
        VALUES (1, 1, 'Use gross revenue for revenue questions.', 'metric', 'project', 10, '["revenue"]'::JSON)
        """
    )
    test_db.execute(
        """
        INSERT INTO metadata.sql_pairs (id, project_id, question, sql, description, category, scope)
        VALUES (1, 1, 'total revenue by month', 'SELECT month, SUM(revenue) FROM sales GROUP BY month', 'Verified revenue example', 'saved_answer', 'project')
        """
    )

    context, hits = _knowledge_context(1, "Show total revenue")

    assert "Use gross revenue" in context
    assert "total revenue by month" in context
    assert hits["instructions"] == [{"id": 1, "category": "metric", "scope": "project"}]
    assert hits["sql_pairs"] == [{"id": 1, "category": "saved_answer", "scope": "project"}]


def test_knowledge_context_handles_structured_related_questions_items(test_db, seed_project: dict):
    from services.ask_service import _knowledge_context

    test_db.execute(
        """
        INSERT INTO metadata.instructions (id, project_id, instruction, category, scope, priority, questions)
        VALUES (2, 1, 'Prefer paid orders for revenue metrics.', 'metric', 'project', 5, '[{"question": "revenue"}, {"text": "orders"}]'::JSON)
        """
    )

    context, hits = _knowledge_context(1, "Show revenue by orders")

    assert "Prefer paid orders" in context
    assert hits["instructions"] == [{"id": 2, "category": "metric", "scope": "project"}]


def test_create_sql_pair_normalizes_split_read_only_sql(test_app, auth_headers: dict, seed_project: dict):
    response = test_app.post(
        "/api/knowledge/sql-pairs",
        json={
            "project_id": 1,
            "question": "normalized sql pair",
            "sql": "WITH cte AS (SELECT 1 AS id); SELECT id FROM cte",
            "scope": "project",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["sql"] == "WITH cte AS (SELECT 1 AS id) SELECT id FROM cte"
