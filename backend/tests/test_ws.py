from __future__ import annotations

import json
import uuid
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient


def test_ws_ask_requires_auth_token(test_app: TestClient):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with test_app.websocket_connect("/ws/ask") as websocket:
            websocket.receive_text()


def test_ws_ask_ping_returns_pong(test_app: TestClient, auth_headers: dict):
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    with test_app.websocket_connect(f"/ws/ask?token={token}") as websocket:
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json() == {"type": "pong"}


def test_ws_ask_accepts_authorization_header_token(test_app: TestClient, auth_headers: dict):
    with test_app.websocket_connect("/ws/ask", headers=auth_headers) as websocket:
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json() == {"type": "pong"}


def test_ws_ask_accepts_ticket_header(test_app: TestClient, auth_headers: dict):
    ticket_resp = test_app.post("/api/auth/ws-ticket", headers=auth_headers)
    assert ticket_resp.status_code == 200
    ticket = ticket_resp.json()["data"]["ticket"]
    assert ticket

    with test_app.websocket_connect("/ws/ask", headers={"x-ws-ticket": ticket}) as websocket:
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json() == {"type": "pong"}


def test_ws_ask_accepts_ticket_subprotocol(test_app: TestClient, auth_headers: dict):
    ticket_resp = test_app.post("/api/auth/ws-ticket", headers=auth_headers)
    assert ticket_resp.status_code == 200
    ticket = ticket_resp.json()["data"]["ticket"]
    assert ticket

    protocol = f"prismbi-ticket.{quote(ticket, safe='')}"
    with test_app.websocket_connect("/ws/ask", subprotocols=[protocol]) as websocket:
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json() == {"type": "pong"}


def test_ws_ask_accepts_token_subprotocol(test_app: TestClient, auth_headers: dict):
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    protocol = f"prismbi-token.{quote(token, safe='')}"
    with test_app.websocket_connect("/ws/ask", subprotocols=[protocol]) as websocket:
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json() == {"type": "pong"}


def test_ws_chunk_text_uses_windowed_chunk_sizes():
    from routers import ws as ws_router

    chunks = ws_router._chunk_text("a" * 150, min_chars=48, max_chars=80)

    assert len(chunks) == 2
    assert len(chunks[0]) == 80
    assert len(chunks[1]) == 70


def test_ws_stream_messages_include_sequence_and_timing_metadata(test_app: TestClient, auth_headers: dict, monkeypatch):
    from routers import ws as ws_router

    def fake_run_ask_question(*args, **kwargs):
        return {
            "thread_id": 1888,
            "summary": "stream metadata check",
            "sql": "SELECT 1",
            "response": {
                "id": 1888,
                "question": "Hello",
                "sql": "SELECT 1",
                "askingTask": {"type": "GENERAL", "status": "FINISHED"},
            },
        }

    monkeypatch.setattr(ws_router, "run_ask_question", fake_run_ask_question)

    token = auth_headers["Authorization"].removeprefix("Bearer ")
    with test_app.websocket_connect(f"/ws/ask?token={token}") as websocket:
        websocket.send_json({"type": "ask", "request_id": "meta-r1", "question": "Hello", "temporary": True})

        seq_values: list[int] = []
        while True:
            message = websocket.receive_json()
            assert message.get("type") in {"delta", "result"}
            assert isinstance(message.get("seq"), int)
            assert isinstance(message.get("ts"), int)
            assert isinstance(message.get("elapsed_ms"), int)
            seq_values.append(int(message["seq"]))
            if message.get("type") == "result":
                break

        assert len(seq_values) >= 2
        assert seq_values == sorted(seq_values)


def test_ws_stream_result_payload_compacts_non_temporary(monkeypatch):
    from routers import ask as ask_router

    monkeypatch.setattr(ask_router, "_STREAM_COMPACT_RESULT", True)
    payload = ask_router._stream_result_payload(
        {
            "thread_id": 123,
            "summary": "done",
            "sql": "SELECT 1",
            "response": {
                "id": 99,
                "created_at": "2026-06-10T00:00:00Z",
                "askingTask": {"type": "GENERAL"},
            },
        },
        temporary=False,
    )

    assert payload["compact_result"] is True
    assert payload["response"] is None
    assert payload["response_id"] == 99
    assert payload["summary"] == "done"


def test_ws_stream_result_payload_keeps_full_data_for_temporary(monkeypatch):
    from routers import ask as ask_router

    monkeypatch.setattr(ask_router, "_STREAM_COMPACT_RESULT", True)
    original = {
        "thread_id": 123,
        "summary": "done",
        "sql": "SELECT 1",
        "response": {"id": 99, "askingTask": {"type": "GENERAL"}},
    }

    payload = ask_router._stream_result_payload(original, temporary=True)

    assert payload == original


def test_ws_step_detail_is_truncated_for_long_reasoning(test_app: TestClient, auth_headers: dict, monkeypatch):
    from routers import ws as ws_router
    from services.step_progress import _STEP_DETAIL_MAX_CHARS

    long_reasoning = "R" * 1200

    def fake_run_ask_question(*args, **kwargs):
        progress_cb = kwargs.get("progress_cb")
        if progress_cb:
            progress_cb("understand", "understand")
            progress_cb("organize", long_reasoning)
            progress_cb("answer", "answer")
        return {
            "thread_id": 1991,
            "summary": "ok",
            "sql": "SELECT 1",
            "response": {
                "id": 1991,
                "question": "Hello",
                "sql": "SELECT 1",
                "askingTask": {"type": "GENERAL", "status": "FINISHED"},
            },
        }

    monkeypatch.setattr(ws_router, "run_ask_question", fake_run_ask_question)

    token = auth_headers["Authorization"].removeprefix("Bearer ")
    with test_app.websocket_connect(f"/ws/ask?token={token}") as websocket:
        websocket.send_json({"type": "ask", "request_id": "step-cap-r1", "question": "Hello", "temporary": True})

        organize_detail = None
        while True:
            message = websocket.receive_json()
            if message.get("type") == "delta" and message.get("content_type") == "step":
                step_payload = json.loads(message.get("content") or "{}")
                if step_payload.get("key") == "organize":
                    organize_detail = str(step_payload.get("detail") or "")
            if message.get("type") == "result":
                break

        assert organize_detail is not None
        if _STEP_DETAIL_MAX_CHARS > 0:
            assert len(organize_detail) <= _STEP_DETAIL_MAX_CHARS
            assert organize_detail.endswith("...")


def test_ws_ask_temporary_returns_result(test_app: TestClient, auth_headers: dict, test_db):
    before_threads = test_db.execute("SELECT COUNT(*) FROM metadata.threads").fetchone()[0]
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    with test_app.websocket_connect(f"/ws/ask?token={token}") as websocket:
        websocket.send_json({"type": "ask", "request_id": "r1", "question": "Hello", "temporary": True})
        msg = websocket.receive_json()
        assert msg["type"] == "delta"
        while msg["type"] == "delta":
            msg = websocket.receive_json()
        assert msg["type"] == "result"
        assert msg["data"]["response"]["askingTask"]["type"] == "GENERAL"
    assert test_db.execute("SELECT COUNT(*) FROM metadata.threads").fetchone()[0] == before_threads


def test_ws_ticket_auth_roundtrip_for_ws_ask(test_app: TestClient, auth_headers: dict):
    ticket_resp = test_app.post("/api/auth/ws-ticket", headers=auth_headers)
    assert ticket_resp.status_code == 200
    ticket = ticket_resp.json()["data"]["ticket"]
    assert ticket

    with test_app.websocket_connect(f"/ws/ask?ticket={ticket}") as websocket:
        websocket.send_json({"type": "ask", "request_id": "ticket-r1", "question": "Hello", "temporary": True})
        msg = websocket.receive_json()
        assert msg["type"] == "delta"
        while msg["type"] == "delta":
            msg = websocket.receive_json()
        assert msg["type"] == "result"
        assert msg["request_id"] == "ticket-r1"
        assert msg["data"]["response"]["askingTask"]["type"] == "GENERAL"


def test_ws_ticket_is_single_use(test_app: TestClient, auth_headers: dict):
    from starlette.websockets import WebSocketDisconnect

    ticket_resp = test_app.post("/api/auth/ws-ticket", headers=auth_headers)
    assert ticket_resp.status_code == 200
    ticket = ticket_resp.json()["data"]["ticket"]

    with test_app.websocket_connect(f"/ws/ask?ticket={ticket}") as websocket:
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json() == {"type": "pong"}

    with pytest.raises(WebSocketDisconnect):
        with test_app.websocket_connect(f"/ws/ask?ticket={ticket}") as websocket:
            websocket.receive_text()


def test_ws_ask_handles_requested_product_city_question_stream(test_app: TestClient, auth_headers: dict, monkeypatch):
    from routers import ws as ws_router

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
            "thread_id": 1001,
            "summary": "已按产品和城市返回销售表现。",
            "sql": expected_sql,
            "response": {
                "id": 9001,
                "question": question,
                "sql": expected_sql,
                "askingTask": {"type": "NL2SQL", "status": "FINISHED"},
            },
        }

    monkeypatch.setattr(ws_router, "run_ask_question", fake_run_ask_question)

    token = auth_headers["Authorization"].removeprefix("Bearer ")
    with test_app.websocket_connect(f"/ws/ask?token={token}") as websocket:
        websocket.send_json({"type": "ask", "request_id": "complex-r1", "question": question, "temporary": True})
        msg = websocket.receive_json()
        assert msg["type"] == "delta"
        while msg["type"] == "delta":
            msg = websocket.receive_json()

        assert msg["type"] == "result"
        assert msg["request_id"] == "complex-r1"
        assert "GROUP BY t.product_category_name_english, s.seller_city" in msg["data"]["sql"]
        assert msg["data"]["response"]["askingTask"]["type"] == "NL2SQL"


def test_ws_ask_handles_requested_department_title_salary_question_stream(test_app: TestClient, auth_headers: dict, monkeypatch):
    from routers import ws as ws_router

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
            "thread_id": 1002,
            "summary": "已按部门和岗位返回薪资对比。",
            "sql": expected_sql,
            "response": {
                "id": 9002,
                "question": question,
                "sql": expected_sql,
                "askingTask": {"type": "NL2SQL", "status": "FINISHED"},
            },
        }

    monkeypatch.setattr(ws_router, "run_ask_question", fake_run_ask_question)

    token = auth_headers["Authorization"].removeprefix("Bearer ")
    with test_app.websocket_connect(f"/ws/ask?token={token}") as websocket:
        websocket.send_json({"type": "ask", "request_id": "complex-r2", "question": question, "temporary": True})
        msg = websocket.receive_json()
        assert msg["type"] == "delta"
        while msg["type"] == "delta":
            msg = websocket.receive_json()

        assert msg["type"] == "result"
        assert msg["request_id"] == "complex-r2"
        assert "GROUP BY d.dept_name, t.title" in msg["data"]["sql"]
        assert msg["data"]["response"]["askingTask"]["type"] == "NL2SQL"


def test_ws_ask_idempotency_replays_cached_result(test_app: TestClient, auth_headers: dict, monkeypatch):
    from routers import ws as ws_router

    calls = {"count": 0}

    def fake_run_ask_question(*args, **kwargs):
        calls["count"] += 1
        return {
            "thread_id": 1003,
            "summary": f"ws-cached-{calls['count']}",
            "sql": "SELECT 1",
            "response": {
                "id": 9100 + calls["count"],
                "question": str(args[0] if args else ""),
                "sql": "SELECT 1",
                "askingTask": {"type": "GENERAL", "status": "FINISHED"},
            },
        }

    monkeypatch.setattr(ws_router, "run_ask_question", fake_run_ask_question)

    token = auth_headers["Authorization"].removeprefix("Bearer ")
    client_key = f"idem-{uuid.uuid4().hex}"

    def _read_result(ws_client):
        msg = ws_client.receive_json()
        while msg.get("type") == "delta":
            msg = ws_client.receive_json()
        return msg

    with test_app.websocket_connect(f"/ws/ask?token={token}") as websocket:
        websocket.send_json({
            "type": "ask",
            "request_id": "idem-r1",
            "client_request_id": client_key,
            "question": "Hello",
            "thread_id": 1003,
            "temporary": True,
        })
        first = _read_result(websocket)

        websocket.send_json({
            "type": "ask",
            "request_id": "idem-r2",
            "client_request_id": client_key,
            "question": "Hello",
            "thread_id": 1003,
            "temporary": True,
        })
        second = _read_result(websocket)

    assert first["type"] == "result"
    assert second["type"] == "result"
    assert first["data"]["summary"] == second["data"]["summary"]
    assert calls["count"] == 1
