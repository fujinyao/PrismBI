from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

import duckdb

from db import connection_lock, get_connection

LOGGER = logging.getLogger(__name__)

EMBEDDING_DIM = 384


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _deterministic_hash(text: str, dim: int) -> list[float]:
    chunks = []
    for i in range(0, dim * 4, 32):
        h = hashlib.sha256(f"{text}|chunk{i}".encode("utf-8")).hexdigest()
        for j in range(0, len(h), 8):
            chunks.append(int(h[j:j + 8], 16) / 0xFFFFFFFF)
    vec = chunks[:dim]
    norm = sum(x * x for x in vec) ** 0.5
    if norm == 0:
        norm = 1
    return [x / norm for x in vec]


class MemoryService:
    def __init__(self, db_path: str = ""):
        self.db_path = db_path

    def search(self, query: str, type: Optional[str] = None, limit: int = 10, user_id: Optional[int] = None, project_id: Optional[int] = None) -> List[dict]:
        query_embedding = _deterministic_hash(query, EMBEDDING_DIM)
        try:
            with connection_lock():
                con = get_connection()
                conditions = ["embedding IS NOT NULL"]
                params: list = []
                if type:
                    conditions.append("type = ?")
                    params.append(type)
                if user_id is not None:
                    conditions.append("user_id = ?")
                    params.append(user_id)
                if project_id is not None:
                    conditions.append("project_id = ?")
                    params.append(project_id)
                rows = con.execute(
                    f"SELECT id, type, content, embedding, created_at FROM metadata.memories WHERE {' AND '.join(conditions)} ORDER BY created_at DESC LIMIT ?",
                    params + [limit * 3],
                ).fetchall()
        except Exception:
            return []

        results = []
        seen = set()
        for row_id, row_type, content, embedding_raw, created_at in rows:
            if not embedding_raw:
                continue
            try:
                stored_embedding = json.loads(embedding_raw) if isinstance(embedding_raw, str) else embedding_raw
            except Exception:
                continue
            if not isinstance(stored_embedding, list) or len(stored_embedding) != EMBEDDING_DIM:
                continue
            score = _cosine_similarity(query_embedding, stored_embedding)
            if score < 0.1:
                continue
            key = (row_type or "", str(content)[:200])
            if key in seen:
                continue
            seen.add(key)
            parsed_content = content
            if isinstance(content, str):
                try:
                    parsed_content = json.loads(content)
                except Exception:
                    parsed_content = {"text": content}
            results.append({
                "id": row_id,
                "type": row_type,
                "content": parsed_content,
                "score": round(score, 4),
                "created_at": str(created_at) if created_at else None,
            })
            if len(results) >= limit:
                break
        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def store(self, type: str, content: dict, user_id: Optional[int] = None, project_id: Optional[int] = None) -> str:
        text = json.dumps(content, ensure_ascii=False) if isinstance(content, dict) else str(content)
        embedding = _deterministic_hash(text, EMBEDDING_DIM)
        memory_id = str(uuid.uuid4())
        with connection_lock():
            con = get_connection()
            con.execute(
                "INSERT INTO metadata.memories (id, type, content, embedding, user_id, project_id, created_at) VALUES (?, ?, ?, ?::JSON, ?, ?, ?)",
                [memory_id, type, json.dumps(content) if isinstance(content, dict) else content, json.dumps(embedding), user_id, project_id, datetime.now(timezone.utc).isoformat()],
            )
        return memory_id

    def list(self, type: Optional[str] = None, user_id: Optional[int] = None, project_id: Optional[int] = None) -> List[dict]:
        try:
            with connection_lock():
                con = get_connection()
                conditions = []
                params: list = []
                if type:
                    conditions.append("type = ?")
                    params.append(type)
                if user_id is not None:
                    conditions.append("user_id = ?")
                    params.append(user_id)
                if project_id is not None:
                    conditions.append("project_id = ?")
                    params.append(project_id)
                where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
                rows = con.execute(
                    f"SELECT id, type, content, created_at FROM metadata.memories{where} ORDER BY created_at DESC",
                    params,
                ).fetchall()
            results = []
            for row_id, row_type, content, created_at in rows:
                parsed_content = content
                if isinstance(content, str):
                    try:
                        parsed_content = json.loads(content)
                    except Exception:
                        parsed_content = {"text": content}
                results.append({
                    "id": row_id,
                    "type": row_type,
                    "content": parsed_content,
                    "created_at": str(created_at) if created_at else None,
                })
            return results
        except Exception as exc:
            LOGGER.warning("Memory list failed: %s", exc)
            return []

    def forget(self, id: str, user_id: Optional[int] = None) -> bool:
        try:
            with connection_lock():
                con = get_connection()
                if user_id is not None:
                    row = con.execute("SELECT id FROM metadata.memories WHERE id = ? AND user_id = ?", [id, user_id]).fetchone()
                    if not row:
                        return False
                con.execute("DELETE FROM metadata.memories WHERE id = ?", [id])
            return True
        except Exception as exc:
            LOGGER.warning("Memory forget failed: %s", exc)
            return False