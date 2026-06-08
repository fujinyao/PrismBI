"""
Legacy wren-ui SQLite Migration Tool

Migrates data from a legacy wren-ui SQLite database into PrismBI's DuckDB schema.
Supports: projects, models, model fields, relations, datasources, knowledge entries,
instructions, sql_pairs, threads, thread_responses, dashboards, dashboard_items.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from db import connection_lock, get_connection

logger = logging.getLogger(__name__)

LEGACY_TABLES = {
    "project": ["id", "name", "display_name", "description", "type", "created_at", "updated_at"],
    "model": ["id", "project_id", "name", "display_name", "description", "table_reference", "type", "sql", "source_binding_id", "created_at", "updated_at"],
    "model_field": ["id", "model_id", "name", "display_name", "description", "type", "is_primary_key", "is_nullable", "default_value", "expression"],
    "relation": ["id", "project_id", "name", "type", "source_model_id", "target_model_id", "source_column", "target_column"],
    "datasource": ["id", "project_id", "name", "type", "properties", "created_at", "updated_at"],
    "knowledge": ["id", "project_id", "type", "content", "instruction", "sql_pattern", "is_active", "created_at", "updated_at"],
    "instruction": ["id", "project_id", "instruction", "category", "scope", "priority", "is_default", "created_at", "updated_at"],
    "sql_pair": ["id", "project_id", "question", "sql", "description", "category", "scope", "created_at", "updated_at"],
    "thread": ["id", "project_id", "summary", "user_id", "created_at", "updated_at"],
    "thread_response": ["id", "thread_id", "question", "sql", "created_at"],
    "dashboard": ["id", "project_id", "name", "created_at"],
    "dashboard_item": ["id", "dashboard_id", "type", "display_name", "response_id", "layout_x", "layout_y", "layout_w", "layout_h", "created_at"],
}


def read_legacy_sqlite(sqlite_path: str) -> dict[str, list[dict]]:
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        data: dict[str, list[dict]] = {}
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        table_names = {row[0] for row in cursor.fetchall()}
        for table, columns in LEGACY_TABLES.items():
            if table not in table_names:
                logger.warning("Legacy table '%s' not found in SQLite file, skipping", table)
                continue
            available_cols = []
            cursor.execute(f"PRAGMA table_info({table})")
            col_info = {row[1]: row[2] for row in cursor.fetchall()}
            for col in columns:
                if col in col_info:
                    available_cols.append(col)
            if not available_cols:
                logger.warning("No matching columns for table '%s', skipping", table)
                continue
            cols_sql = ", ".join(available_cols)
            cursor.execute(f"SELECT {cols_sql} FROM {table}")
            rows = cursor.fetchall()
            data[table] = [{col: row[idx] for idx, col in enumerate(available_cols)} for row in rows]
            logger.info("Read %d rows from legacy table '%s'", len(data[table]), table)
        return data
    finally:
        conn.close()


def migrate_sqlite_to_prismbi(sqlite_path: str, default_user_id: int = 1) -> dict[str, Any]:
    data = read_legacy_sqlite(sqlite_path)
    if not data:
        return {"migrated": False, "error": "No data found in legacy SQLite file"}

    result: dict[str, Any] = {"migrated": True, "projects": 0, "models": 0, "fields": 0, "relations": 0, "knowledge": 0, "instructions": 0, "sql_pairs": 0, "threads": 0, "dashboards": 0}
    model_id_map: dict[int, int] = {}

    with connection_lock():
        con = get_connection()

        for project in data.get("project", []):
            old_id = project.get("id", 0)
            new_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.projects").fetchone()[0]
            con.execute(
                "INSERT INTO metadata.projects (id, name, display_name, description, type) VALUES (?, ?, ?, ?, ?)",
                [new_id, project.get("name", f"migrated-{new_id}"), project.get("display_name"), project.get("description"), project.get("type", "imported")],
            )
            result["projects"] += 1

            for ds in data.get("datasource", []):
                if ds.get("project_id") != old_id:
                    continue
                new_ds_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.datasources").fetchone()[0]
                props = ds.get("properties", {})
                if isinstance(props, str):
                    try:
                        props = json.loads(props)
                    except (json.JSONDecodeError, TypeError):
                        props = {}
                con.execute(
                    "INSERT INTO metadata.datasources (id, name, type, properties) VALUES (?, ?, ?, ?::JSON)",
                    [new_ds_id, ds.get("name", f"ds-{new_ds_id}"), ds.get("type", "duckdb"), json.dumps(props)],
                )
                binding_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.project_datasource_bindings").fetchone()[0]
                con.execute(
                    "INSERT INTO metadata.project_datasource_bindings (id, project_id, datasource_id, alias) VALUES (?, ?, ?, ?)",
                    [binding_id, new_id, new_ds_id, ds.get("name", f"ds-{new_ds_id}")],
                )

            for model in data.get("model", []):
                if model.get("project_id") != old_id:
                    continue
                old_model_id = model.get("id", 0)
                new_model_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.models").fetchone()[0]
                model_id_map[old_model_id] = new_model_id
                source_binding_id = model.get("source_binding_id")
                binding_row = con.execute(
                    "SELECT id FROM metadata.project_datasource_bindings WHERE project_id = ? ORDER BY id LIMIT 1",
                    [new_id],
                ).fetchone()
                source_bid = binding_row[0] if binding_row else None
                con.execute(
                    "INSERT INTO metadata.models (id, project_id, name, display_name, description, table_reference, type, sql, source_binding_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [new_model_id, new_id, model.get("name"), model.get("display_name"), model.get("description"), model.get("table_reference"), model.get("type", "table"), model.get("sql"), source_bid],
                )
                result["models"] += 1

                for field in data.get("model_field", []):
                    if field.get("model_id") != old_model_id:
                        continue
                    new_field_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.model_fields").fetchone()[0]
                    con.execute(
                        "INSERT INTO metadata.model_fields (id, model_id, name, display_name, description, type, is_primary_key, is_nullable, default_value, expression) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [new_field_id, new_model_id, field.get("name"), field.get("display_name"), field.get("description"), field.get("type", "VARCHAR"), field.get("is_primary_key", False), field.get("is_nullable", True), field.get("default_value"), field.get("expression")],
                    )
                    result["fields"] += 1

            for rel in data.get("relation", []):
                if rel.get("project_id") != old_id:
                    continue
                new_rel_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.relations").fetchone()[0]
                source_mid = model_id_map.get(rel.get("source_model_id", 0), rel.get("source_model_id"))
                target_mid = model_id_map.get(rel.get("target_model_id", 0), rel.get("target_model_id"))
                con.execute(
                    "INSERT INTO metadata.relations (id, project_id, name, type, source_model_id, target_model_id, source_column, target_column) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [new_rel_id, new_id, rel.get("name"), rel.get("type", "MANY_TO_ONE"), source_mid, target_mid, rel.get("source_column"), rel.get("target_column")],
                )
                result["relations"] += 1

            for knowledge in data.get("knowledge", []):
                if knowledge.get("project_id") != old_id:
                    continue
                new_kid = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.knowledge").fetchone()[0]
                con.execute(
                    "INSERT INTO metadata.knowledge (id, project_id, type, content, instruction, sql_pattern, is_active) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [new_kid, new_id, knowledge.get("type", "instruction"), knowledge.get("content"), knowledge.get("instruction"), knowledge.get("sql_pattern"), knowledge.get("is_active", True)],
                )
                result["knowledge"] += 1

            for instr in data.get("instruction", []):
                if instr.get("project_id") != old_id:
                    continue
                new_iid = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.instructions").fetchone()[0]
                con.execute(
                    "INSERT INTO metadata.instructions (id, project_id, instruction, category, scope, priority, is_default) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [new_iid, new_id, instr.get("instruction", ""), instr.get("category"), instr.get("scope"), instr.get("priority", 0), instr.get("is_default", False)],
                )
                result["instructions"] += 1

            for sp in data.get("sql_pair", []):
                if sp.get("project_id") != old_id:
                    continue
                new_spid = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.sql_pairs").fetchone()[0]
                con.execute(
                    "INSERT INTO metadata.sql_pairs (id, project_id, question, sql, description, category, scope) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [new_spid, new_id, sp.get("question", ""), sp.get("sql", ""), sp.get("description"), sp.get("category"), sp.get("scope")],
                )
                result["sql_pairs"] += 1

            for thread in data.get("thread", []):
                if thread.get("project_id") != old_id:
                    continue
                new_tid = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.threads").fetchone()[0]
                con.execute(
                    "INSERT INTO metadata.threads (id, project_id, summary, user_id) VALUES (?, ?, ?, ?)",
                    [new_tid, new_id, thread.get("summary", ""), default_user_id],
                )
                result["threads"] += 1

                for resp in data.get("thread_response", []):
                    if resp.get("thread_id") != thread.get("id"):
                        continue
                    new_rid = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.thread_responses").fetchone()[0]
                    con.execute(
                        "INSERT INTO metadata.thread_responses (id, thread_id, user_id, question, sql) VALUES (?, ?, ?, ?, ?)",
                        [new_rid, new_tid, default_user_id, resp.get("question", ""), resp.get("sql", "")],
                    )

            for dash in data.get("dashboard", []):
                if dash.get("project_id") != old_id:
                    continue
                new_did = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.dashboards").fetchone()[0]
                con.execute(
                    "INSERT INTO metadata.dashboards (id, project_id, name) VALUES (?, ?, ?)",
                    [new_did, new_id, dash.get("name", f"Dashboard {new_did}")],
                )
                result["dashboards"] += 1

                for item in data.get("dashboard_item", []):
                    if item.get("dashboard_id") != dash.get("id"):
                        continue
                    new_diid = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.dashboard_items").fetchone()[0]
                    con.execute(
                        "INSERT INTO metadata.dashboard_items (id, dashboard_id, type, display_name, layout_x, layout_y, layout_w, layout_h) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [new_diid, new_did, item.get("type", "text"), item.get("display_name"), item.get("layout_x", 0), item.get("layout_y", 0), item.get("layout_w", 3), item.get("layout_h", 2)],
                    )

        con.execute("UPDATE metadata.projects SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", [project_id])

    logger.info("Migration complete: %s", result)
    return result