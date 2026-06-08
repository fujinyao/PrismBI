from __future__ import annotations

from typing import Any, List, Optional

from db import connection_lock, get_connection, rows_to_dicts


class RatingService:
    def create_feedback(
        self, user_id: int, project_id: int, recommendation_id: int, action: str, context: Optional[str] = None
    ) -> int:
        with connection_lock():
            con = get_connection()
            feedback_id = con.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.recommendation_feedback"
            ).fetchone()[0]
            con.execute(
                """INSERT INTO metadata.recommendation_feedback (id, user_id, project_id, recommendation_id, action, session_context)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [feedback_id, user_id, project_id, recommendation_id, action, context],
            )
            return feedback_id

    def rate(
        self, user_id: int, project_id: int, recommendation_id: int, score: int, context: Optional[str] = None
    ) -> dict:
        with connection_lock():
            con = get_connection()
            score_id = con.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.recommendation_scores"
            ).fetchone()[0]
            con.execute(
                """INSERT INTO metadata.recommendation_scores
                   (id, user_id, project_id, recommendation_id, score, session_context)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [score_id, user_id, project_id, recommendation_id, score, context],
            )
            return {"success": True}

    def list_ratings(
        self, user_id: int, source_layer: Optional[str] = None, from_: Optional[str] = None, to: Optional[str] = None
    ) -> List[dict]:
        query = "SELECT * FROM metadata.recommendation_scores WHERE user_id = ?"
        params: list[Any] = [user_id]
        if source_layer:
            query += " AND source_layer = ?"
            params.append(source_layer)
        if from_:
            query += " AND created_at >= ?"
            params.append(from_)
        if to:
            query += " AND created_at <= ?"
            params.append(to)
        query += " ORDER BY created_at DESC"
        with connection_lock():
            con = get_connection()
            result = con.execute(query, params)
            rows = result.fetchall()
        return rows_to_dicts(rows, result.description)

    def get_rating_detail(self, recommendation_id: int) -> dict:
        with connection_lock():
            con = get_connection()
            row = con.execute(
                """SELECT AVG(score) as avg_score, COUNT(*) as total_ratings
                   FROM metadata.recommendation_scores
                   WHERE recommendation_id = ?""",
                [recommendation_id],
            ).fetchone()
            dist = con.execute(
                """SELECT score, COUNT(*) as cnt
                   FROM metadata.recommendation_scores
                   WHERE recommendation_id = ?
                   GROUP BY score""",
                [recommendation_id],
            ).fetchall()
        distribution = {str(r[0]): r[1] for r in dist}
        return {
            "avg_score": float(row[0]) if row and row[0] else 0.0,
            "total_ratings": int(row[1]) if row and row[1] else 0,
            "distribution": distribution,
        }
