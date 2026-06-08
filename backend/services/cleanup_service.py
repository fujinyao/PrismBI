from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import duckdb

from db import connection_lock, get_connection, DATA_DIR

LOGGER = logging.getLogger(__name__)

TEMP_SCHEMA_PREFIX = "temp_"


class CleanupService:
    def __init__(self, db: duckdb.DuckDBPyConnection):
        self.db = db

    def cleanup_responses(self, before: datetime, project_id: Optional[int] = None) -> int:
        with connection_lock():
            con = get_connection()
            query = "DELETE FROM metadata.thread_responses WHERE created_at < ?"
            params: list = [before.isoformat()]
            if project_id is not None:
                query += " AND thread_id IN (SELECT id FROM metadata.threads WHERE project_id = ?)"
                params.append(project_id)
            result = con.execute(query, params)
            rowcount = result.fetchone()[0] if result.description else 0
        return rowcount

    def cleanup_api_history(self, before: datetime, status_code: Optional[int] = None) -> int:
        with connection_lock():
            con = get_connection()
            query = "DELETE FROM metadata.api_history WHERE created_at < ?"
            params: list = [before.isoformat()]
            if status_code is not None:
                query += " AND status_code = ?"
                params.append(status_code)
            result = con.execute(query, params)
            rowcount = result.fetchone()[0] if result.description else 0
        return rowcount

    def cleanup_temp_schemas(self) -> int:
        dropped = 0
        try:
            with connection_lock():
                con = get_connection()
                schemas = con.execute(
                    "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE ?",
                    [f"{TEMP_SCHEMA_PREFIX}%"],
                ).fetchall()
                for (schema_name,) in schemas:
                    if len(schema_name) <= len(TEMP_SCHEMA_PREFIX) + 40 and schema_name[len(TEMP_SCHEMA_PREFIX):].replace("-", "").replace("_", "").isalnum():
                        try:
                            con.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
                            dropped += 1
                        except Exception as exc:
                            LOGGER.warning("Failed to drop temp schema %s: %s", schema_name, exc)
        except Exception as exc:
            LOGGER.warning("Temp schema cleanup failed: %s", exc)
        return dropped

    def cleanup_cache(self) -> int:
        cleaned = 0
        try:
            with connection_lock():
                con = get_connection()
                cutoff = (datetime.now(timezone.utc)).isoformat()
                items = con.execute(
                    "SELECT di.id FROM metadata.dashboard_items di WHERE di.cache_data IS NOT NULL AND di.cache_created_at < ?",
                    [cutoff],
                ).fetchall()
                for (item_id,) in items:
                    age_check = con.execute(
                        "SELECT cache_created_at FROM metadata.dashboard_items WHERE id = ?",
                        [item_id],
                    ).fetchone()
                    if age_check and age_check[0]:
                        age_seconds = (datetime.now(timezone.utc) - datetime.fromisoformat(str(age_check[0]))).total_seconds()
                        if age_seconds > 86400:
                            con.execute(
                                "UPDATE metadata.dashboard_items SET cache_data = NULL, cache_created_at = NULL WHERE id = ?",
                                [item_id],
                            )
                            cleaned += 1
        except Exception as exc:
            LOGGER.warning("Dashboard cache cleanup failed: %s", exc)
        return cleaned

    def cleanup_expired_sessions(self) -> int:
        with connection_lock():
            con = get_connection()
            result = con.execute(
                "DELETE FROM metadata.sessions WHERE expires_at < ? AND is_revoked = FALSE",
                [datetime.now(timezone.utc).isoformat()],
            )
            rowcount = result.fetchone()[0] if result.description else 0
        return rowcount

    def cleanup_stale_temp_data(self, max_age_hours: int = 24) -> int:
        total = 0
        try:
            temp_dir = os.path.join(DATA_DIR, "projects", "0")
            if not os.path.isdir(temp_dir):
                return 0
            cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)
            for entry in os.listdir(temp_dir):
                path = os.path.join(temp_dir, entry)
                if os.path.isfile(path):
                    try:
                        if os.path.getmtime(path) < cutoff:
                            os.remove(path)
                            total += 1
                    except OSError:
                        pass
        except Exception as exc:
            LOGGER.warning("Stale temp data cleanup failed: %s", exc)
        return total

    def on_session_end(self, session_id: str) -> None:
        if not session_id.replace("-", "").isalnum():
            raise ValueError(f"Invalid session_id: {session_id}")
        with connection_lock():
            con = get_connection()
            con.execute(f'DROP SCHEMA IF EXISTS "{TEMP_SCHEMA_PREFIX}{session_id}" CASCADE')