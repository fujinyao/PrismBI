from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


class TestAsk:
    def test_ask_sse_chunk_text_uses_windowed_chunk_sizes(self):
        from routers import ask as ask_router

        chunks = ask_router._chunk_text("b" * 150, min_chars=48, max_chars=80)

        assert len(chunks) == 2
        assert len(chunks[0]) == 80
        assert len(chunks[1]) == 70

    def test_ask_stream_result_payload_compacts_non_temporary(self, monkeypatch):
        from routers import ask as ask_router

        monkeypatch.setattr(ask_router, "_STREAM_COMPACT_RESULT", True)
        payload = ask_router._stream_result_payload(
            {
                "thread_id": 321,
                "summary": "ready",
                "sql": "SELECT 1",
                "response": {
                    "id": 77,
                    "created_at": "2026-06-10T00:00:00Z",
                    "askingTask": {"type": "GENERAL"},
                },
            },
            temporary=False,
        )

        assert payload["compact_result"] is True
        assert payload["response"] is None
        assert payload["response_id"] == 77
        assert payload["summary"] == "ready"

    def test_ask_stream_result_payload_keeps_full_data_for_temporary(self, monkeypatch):
        from routers import ask as ask_router

        monkeypatch.setattr(ask_router, "_STREAM_COMPACT_RESULT", True)
        payload = ask_router._stream_result_payload(
            {
                "thread_id": 321,
                "summary": "ready",
                "sql": "SELECT 1",
                "response": {
                    "id": 77,
                    "askingTask": {"type": "GENERAL"},
                },
            },
            temporary=True,
        )

        assert payload["response"] is not None
        assert payload["response"]["id"] == 77
        assert payload.get("compact_result") is None

    def test_ask_question_without_active_project_requires_temporary(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/ask",
            json={"question": "What is the total revenue?"},
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "No active project. Use temporary ask for empty-project chat."

    def test_ask_question_temporary_does_not_persist_thread(self, test_app: TestClient, auth_headers: dict):
        import db

        before_threads = db.get_connection().execute("SELECT COUNT(*) FROM metadata.threads").fetchone()[0]
        before_responses = db.get_connection().execute("SELECT COUNT(*) FROM metadata.thread_responses").fetchone()[0]

        response = test_app.post(
            "/api/ask",
            json={"question": "Hello", "temporary": True, "preview_row_limit": 20},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["thread_id"]
        assert data["response"]["askingTask"]["type"] == "GENERAL"
        assert db.get_connection().execute("SELECT COUNT(*) FROM metadata.projects WHERE id = 0").fetchone()[0] == 0
        assert db.get_connection().execute("SELECT COUNT(*) FROM metadata.threads").fetchone()[0] == before_threads
        assert db.get_connection().execute("SELECT COUNT(*) FROM metadata.thread_responses").fetchone()[0] == before_responses

    def test_ask_question_with_missing_thread(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/ask",
            json={"question": "Show me sales", "thread_id": 1},
            headers=auth_headers,
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Thread not found"

    def test_ask_question_missing_question(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post(
            "/api/ask", json={}, headers=auth_headers
        )
        assert response.status_code == 422

    def test_ask_stream_handles_requested_product_city_question(self, test_app: TestClient, auth_headers: dict, monkeypatch):
        from routers import ask as ask_router

        question = "订单中哪些产品销售的比较好、这些产品在不同的城市表现怎样"
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

        def fake_run_ask_question(*args, **kwargs):
            return {
                "thread_id": 3001,
                "summary": "已返回产品在不同城市的销售表现。",
                "sql": expected_sql,
                "response": {
                    "id": 8001,
                    "question": question,
                    "sql": expected_sql,
                    "askingTask": {"type": "NL2SQL", "status": "FINISHED"},
                },
            }

        monkeypatch.setattr(ask_router, "run_ask_question", fake_run_ask_question)

        with test_app.stream(
            "POST",
            "/api/ask/stream",
            json={"question": question, "temporary": True},
            headers=auth_headers,
        ) as response:
            assert response.status_code == 200
            result_payload = None
            for line in response.iter_lines():
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="ignore")
                if not line.startswith("data: "):
                    continue
                import json as _json

                payload = _json.loads(line[len("data: "):])
                if payload.get("type") == "result":
                    result_payload = payload["data"]
                    break

            assert result_payload is not None
            assert "GROUP BY t.product_category_name_english, s.seller_city" in result_payload["sql"]
            assert result_payload["response"]["askingTask"]["type"] == "NL2SQL"

    def test_ask_stream_events_include_sequence_and_timing_metadata(self, test_app: TestClient, auth_headers: dict, monkeypatch):
        from routers import ask as ask_router

        def fake_run_ask_question(*args, **kwargs):
            return {
                "thread_id": 3555,
                "summary": "sse metadata check",
                "sql": "SELECT 1",
                "response": {
                    "id": 3555,
                    "question": "Hello",
                    "sql": "SELECT 1",
                    "askingTask": {"type": "GENERAL", "status": "FINISHED"},
                },
            }

        monkeypatch.setattr(ask_router, "run_ask_question", fake_run_ask_question)

        with test_app.stream(
            "POST",
            "/api/ask/stream",
            json={"question": "Hello", "temporary": True, "client_request_id": "sse-meta-r1"},
            headers=auth_headers,
        ) as response:
            assert response.status_code == 200
            seq_values: list[int] = []
            got_result = False
            for line in response.iter_lines():
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="ignore")
                if not line.startswith("data: "):
                    continue
                import json as _json

                payload = _json.loads(line[len("data: "):])
                if payload.get("type") not in {"delta", "result"}:
                    continue
                assert payload.get("request_id") == "sse-meta-r1"
                assert isinstance(payload.get("seq"), int)
                assert isinstance(payload.get("ts"), int)
                assert isinstance(payload.get("elapsed_ms"), int)
                seq_values.append(int(payload["seq"]))
                if payload.get("type") == "result":
                    got_result = True
                    break

            assert got_result
            assert len(seq_values) >= 2
            assert seq_values == sorted(seq_values)

    def test_ask_stream_step_detail_is_truncated_for_long_reasoning(self, test_app: TestClient, auth_headers: dict, monkeypatch):
        from routers import ask as ask_router
        from services.step_progress import _STEP_DETAIL_MAX_CHARS

        long_reasoning = "S" * 1500

        def fake_run_ask_question(*args, **kwargs):
            progress_cb = kwargs.get("progress_cb")
            if progress_cb:
                progress_cb("understand", "understand")
                progress_cb("organize", long_reasoning)
                progress_cb("answer", "answer")
            return {
                "thread_id": 4555,
                "summary": "done",
                "sql": "SELECT 1",
                "response": {
                    "id": 4555,
                    "question": "Hello",
                    "sql": "SELECT 1",
                    "askingTask": {"type": "GENERAL", "status": "FINISHED"},
                },
            }

        monkeypatch.setattr(ask_router, "run_ask_question", fake_run_ask_question)

        with test_app.stream(
            "POST",
            "/api/ask/stream",
            json={"question": "Hello", "temporary": True, "client_request_id": "sse-step-cap-r1"},
            headers=auth_headers,
        ) as response:
            assert response.status_code == 200
            organize_detail = None
            for line in response.iter_lines():
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="ignore")
                if not line.startswith("data: "):
                    continue
                import json as _json

                payload = _json.loads(line[len("data: "):])
                if payload.get("type") == "delta" and payload.get("content_type") == "step":
                    step_payload = _json.loads(str(payload.get("content") or "{}"))
                    if step_payload.get("key") == "organize":
                        organize_detail = str(step_payload.get("detail") or "")
                if payload.get("type") == "result":
                    break

        assert organize_detail is not None
        if _STEP_DETAIL_MAX_CHARS > 0:
            assert len(organize_detail) <= _STEP_DETAIL_MAX_CHARS
            assert organize_detail.endswith("...")

    def test_ask_stream_handles_requested_department_title_salary_question(self, test_app: TestClient, auth_headers: dict, monkeypatch):
        from routers import ask as ask_router

        question = "在这些雇员中哪些部门或者工作岗位的薪资比较高"
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

        def fake_run_ask_question(*args, **kwargs):
            return {
                "thread_id": 3002,
                "summary": "已返回部门和岗位薪资对比。",
                "sql": expected_sql,
                "response": {
                    "id": 8002,
                    "question": question,
                    "sql": expected_sql,
                    "askingTask": {"type": "NL2SQL", "status": "FINISHED"},
                },
            }

        monkeypatch.setattr(ask_router, "run_ask_question", fake_run_ask_question)

        with test_app.stream(
            "POST",
            "/api/ask/stream",
            json={"question": question, "temporary": True},
            headers=auth_headers,
        ) as response:
            assert response.status_code == 200
            result_payload = None
            for line in response.iter_lines():
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="ignore")
                if not line.startswith("data: "):
                    continue
                import json as _json

                payload = _json.loads(line[len("data: "):])
                if payload.get("type") == "result":
                    result_payload = payload["data"]
                    break

            assert result_payload is not None
            assert "GROUP BY d.dept_name, t.title" in result_payload["sql"]
            assert result_payload["response"]["askingTask"]["type"] == "NL2SQL"

    def test_ask_idempotency_replays_cached_result(self, test_app: TestClient, auth_headers: dict, monkeypatch):
        from routers import ask as ask_router

        calls = {"count": 0}

        def fake_run_ask_question(*args, **kwargs):
            calls["count"] += 1
            return {
                "thread_id": 777,
                "summary": f"cached-summary-{calls['count']}",
                "sql": "SELECT 1",
                "response": {
                    "id": 9900 + calls["count"],
                    "question": str(args[0] if args else ""),
                    "sql": "SELECT 1",
                    "askingTask": {"type": "GENERAL", "status": "FINISHED"},
                },
            }

        monkeypatch.setattr(ask_router, "run_ask_question", fake_run_ask_question)
        key = f"idem-{uuid.uuid4().hex}"
        body = {
            "question": "Hello",
            "temporary": True,
            "thread_id": 777,
            "client_request_id": key,
        }

        first = test_app.post("/api/ask", json=body, headers=auth_headers)
        second = test_app.post("/api/ask", json=body, headers=auth_headers)

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["data"]["summary"] == second.json()["data"]["summary"]
        assert calls["count"] == 1

    def test_ask_stream_idempotency_replays_cached_result(self, test_app: TestClient, auth_headers: dict, monkeypatch):
        from routers import ask as ask_router

        calls = {"count": 0}

        def fake_run_ask_question(*args, **kwargs):
            calls["count"] += 1
            return {
                "thread_id": 778,
                "summary": f"stream-cached-{calls['count']}",
                "sql": "SELECT 1",
                "response": {
                    "id": 9950 + calls["count"],
                    "question": str(args[0] if args else ""),
                    "sql": "SELECT 1",
                    "askingTask": {"type": "GENERAL", "status": "FINISHED"},
                },
            }

        monkeypatch.setattr(ask_router, "run_ask_question", fake_run_ask_question)
        key = f"idem-{uuid.uuid4().hex}"
        body = {
            "question": "Hello",
            "temporary": True,
            "thread_id": 778,
            "client_request_id": key,
        }

        first = test_app.post("/api/ask", json=body, headers=auth_headers)
        assert first.status_code == 200

        with test_app.stream("POST", "/api/ask/stream", json=body, headers=auth_headers) as response:
            assert response.status_code == 200
            result_payload = None
            for line in response.iter_lines():
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="ignore")
                if not line.startswith("data: "):
                    continue
                import json as _json

                payload = _json.loads(line[len("data: "):])
                if payload.get("type") == "result":
                    result_payload = payload.get("data")
                    break

        assert result_payload is not None
        assert result_payload["summary"] == first.json()["data"]["summary"]
        assert calls["count"] == 1

    def test_ask_idempotency_replays_from_database_when_memory_cleared(self):
        from db import connection_lock, get_connection
        from services import ask_idempotency

        key = f"idem-{uuid.uuid4().hex}"
        payload = {
            "thread_id": 779,
            "summary": "db-cached-1",
            "sql": "SELECT 1",
            "response": {
                "id": 9991,
                "question": "Hello",
                "sql": "SELECT 1",
                "askingTask": {"type": "GENERAL", "status": "FINISHED"},
            },
        }
        try:
            owner = ask_idempotency.acquire_ask_idempotency(779, key)
            assert owner.is_owner
            owner.complete_success(payload)
            owner.release()

            ask_idempotency._ENTRIES.clear()

            replay = ask_idempotency.acquire_ask_idempotency(779, key)
            assert not replay.is_owner
            assert replay.wait_result()["summary"] == payload["summary"]
            replay.release()
        finally:
            ask_idempotency._ENTRIES.clear()
            with connection_lock():
                con = get_connection()
                con.execute("DELETE FROM metadata.idempotency_keys WHERE key = ?", [f"ask:779:{key}"])

    def test_ask_idempotency_evicts_stale_in_flight_entry(self):
        from services import ask_idempotency

        thread_id = 780
        key = f"idem-{uuid.uuid4().hex}"
        full_key = f"ask:{thread_id}:{key}"
        try:
            owner = ask_idempotency.acquire_ask_idempotency(thread_id, key)
            assert owner.is_owner
            owner.release()

            stale_entry = ask_idempotency._ENTRIES.get(full_key)
            assert stale_entry is not None
            stale_entry.created_at -= ask_idempotency._IN_FLIGHT_STALE_SECONDS + 1
            old_cancel_event = stale_entry.cancel_event

            reacquired = ask_idempotency.acquire_ask_idempotency(thread_id, key)
            assert reacquired.is_owner
            assert old_cancel_event.is_set()
            reacquired.release()
        finally:
            ask_idempotency._ENTRIES.clear()

    def test_ask_question_cancelled_returns_409(self, test_app: TestClient, auth_headers: dict, monkeypatch):
        from routers import ask as ask_router
        from services.ask_service import AskCancelledError

        def fake_run_ask_question(*args, **kwargs):
            raise AskCancelledError("Ask request cancelled during generate_sql")

        monkeypatch.setattr(ask_router, "run_ask_question", fake_run_ask_question)

        response = test_app.post(
            "/api/ask",
            json={
                "question": "Hello",
                "temporary": True,
                "thread_id": 781,
                "client_request_id": f"idem-{uuid.uuid4().hex}",
            },
            headers=auth_headers,
        )

        assert response.status_code == 409
        assert "Ask request cancelled" in response.json()["detail"]

    def test_ask_stream_emits_cancelled_error_event(self, test_app: TestClient, auth_headers: dict, monkeypatch):
        from routers import ask as ask_router
        from services.ask_service import AskCancelledError

        def fake_run_ask_question(*args, **kwargs):
            raise AskCancelledError("Ask request cancelled during generate_sql")

        monkeypatch.setattr(ask_router, "run_ask_question", fake_run_ask_question)

        with test_app.stream(
            "POST",
            "/api/ask/stream",
            json={
                "question": "Hello",
                "temporary": True,
                "thread_id": 782,
                "client_request_id": f"idem-{uuid.uuid4().hex}",
            },
            headers=auth_headers,
        ) as response:
            assert response.status_code == 200
            error_message = None
            for line in response.iter_lines():
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="ignore")
                if not line.startswith("data: "):
                    continue
                import json as _json

                payload = _json.loads(line[len("data: "):])
                if payload.get("type") == "error":
                    error_message = str(payload.get("message") or "")
                    break

            assert error_message is not None
            assert "Ask request cancelled" in error_message
