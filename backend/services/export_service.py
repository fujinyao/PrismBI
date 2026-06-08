from __future__ import annotations

import csv
import io
import json
import yaml
from typing import Any, Optional

import duckdb

from db import connection_lock, get_connection


def _rows_to_dicts(result: duckdb.DuckDBPyConnection) -> list[dict]:
    columns = [desc[0] for desc in result.description or []]
    return [dict(zip(columns, row)) for row in result.fetchall()]


class ExportService:
    def __init__(self, db: duckdb.DuckDBPyConnection | None = None):
        self._db = db

    def export_project(self, project_id: int, format: str = "yaml") -> bytes:
        with connection_lock():
            con = get_connection()
            project_row = con.execute(
                "SELECT id, name, display_name, description, type, created_at, updated_at FROM metadata.projects WHERE id = ?",
                [project_id],
            ).fetchone()
            if not project_row:
                raise ValueError(f"Project {project_id} not found")
            project = {
                "id": project_row[0],
                "name": project_row[1],
                "display_name": project_row[2],
                "description": project_row[3],
                "type": project_row[4],
                "created_at": str(project_row[5]) if project_row[5] else None,
                "updated_at": str(project_row[6]) if project_row[6] else None,
            }
            models = _rows_to_dicts(con.execute(
                "SELECT id, project_id, name, display_name, description, table_reference, type, sql, source_binding_id, column_defs, created_at FROM metadata.models WHERE project_id = ? ORDER BY id",
                [project_id],
            ))
            for model in models:
                mid = model.pop("id")
                model.pop("project_id")
                column_defs_raw = model.pop("column_defs", None)
                if column_defs_raw:
                    try:
                        fields = column_defs_raw if isinstance(column_defs_raw, list) else json.loads(column_defs_raw) if isinstance(column_defs_raw, str) else []
                    except (json.JSONDecodeError, TypeError):
                        fields = []
                else:
                    fields = []
                model["fields"] = fields
            relations = _rows_to_dicts(con.execute(
                "SELECT name, type, source_model_id, target_model_id, source_column, target_column FROM metadata.relations WHERE project_id = ? ORDER BY id",
                [project_id],
            ))
            bindings = _rows_to_dicts(con.execute(
                "SELECT id, alias, datasource_id FROM metadata.project_datasources WHERE project_id = ? ORDER BY id",
                [project_id],
            ))
            for binding in bindings:
                ds_id = binding.get("datasource_id")
                if ds_id:
                    ds_row = con.execute(
                        "SELECT name, type, properties FROM metadata.datasources WHERE id = ?",
                        [int(ds_id)],
                    ).fetchone()
                    if ds_row:
                        binding["datasource_name"] = ds_row[0]
                        binding["datasource_type"] = ds_row[1]
                        try:
                            if isinstance(ds_row[2], str):
                                binding["datasource_properties"] = json.loads(ds_row[2])
                            elif isinstance(ds_row[2], dict):
                                binding["datasource_properties"] = ds_row[2]
                            else:
                                binding["datasource_properties"] = None
                        except (json.JSONDecodeError, TypeError):
                            binding["datasource_properties"] = None
            instructions = _rows_to_dicts(con.execute(
                "SELECT id, project_id, text, category, scope, priority, is_active, created_at FROM metadata.instructions WHERE project_id = ? ORDER BY id",
                [project_id],
            ))
            sql_pairs = _rows_to_dicts(con.execute(
                "SELECT id, project_id, question, sql, description, category, scope, is_active, created_at FROM metadata.sql_pairs WHERE project_id = ? ORDER BY id",
                [project_id],
            ))
            project["models"] = models
            project["relations"] = relations
            project["datasource_bindings"] = bindings
            project["instructions"] = instructions
            project["sql_pairs"] = sql_pairs

        if format.lower() == "yaml":
            return yaml.dump(project, default_flow_style=False, allow_unicode=True, sort_keys=False).encode("utf-8")
        return json.dumps(project, ensure_ascii=False, indent=2, default=str).encode("utf-8")

    def import_project(self, file_content: bytes, format: str = "yaml") -> int:
        if format.lower() == "yaml":
            data = yaml.safe_load(file_content)
        else:
            data = json.loads(file_content)
        if not isinstance(data, dict):
            raise ValueError("Invalid project data: expected a mapping")

        with connection_lock():
            con = get_connection()
            project_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.projects").fetchone()[0]
            con.execute(
                "INSERT INTO metadata.projects (id, name, display_name, description, type) VALUES (?, ?, ?, ?, ?)",
                [project_id, data.get("name", f"imported-{project_id}"), data.get("display_name"), data.get("description"), data.get("type", "imported")],
            )
            model_id_map: dict[int, int] = {}
            for model_data in data.get("models", []):
                old_id = model_data.get("id")
                new_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.models").fetchone()[0]
                model_id_map[old_id] = new_id
                fields = model_data.get("fields", [])
                column_defs = json.dumps(fields) if fields else None
                con.execute(
                    "INSERT INTO metadata.models (id, project_id, name, display_name, description, table_reference, type, sql, column_defs) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [new_id, project_id, model_data.get("name"), model_data.get("display_name"), model_data.get("description"), model_data.get("table_reference"), model_data.get("type", "table"), model_data.get("sql"), column_defs],
                )
            for rel in data.get("relations", []):
                rid = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.relations").fetchone()[0]
                source_id = model_id_map.get(rel.get("source_model_id"), rel.get("source_model_id"))
                target_id = model_id_map.get(rel.get("target_model_id"), rel.get("target_model_id"))
                con.execute(
                    "INSERT INTO metadata.relations (id, project_id, name, type, source_model_id, target_model_id, source_column, target_column) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [rid, project_id, rel.get("name"), rel.get("type", "MANY_TO_ONE"), source_id, target_id, rel.get("source_column"), rel.get("target_column")],
                )
            for instr in data.get("instructions", []):
                iid = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.instructions").fetchone()[0]
                con.execute(
                    "INSERT INTO metadata.instructions (id, project_id, text, category, scope, priority, is_active) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [iid, project_id, instr.get("text"), instr.get("category"), instr.get("scope"), instr.get("priority", 0), instr.get("is_active", True)],
                )
            for sp in data.get("sql_pairs", []):
                sid = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.sql_pairs").fetchone()[0]
                con.execute(
                    "INSERT INTO metadata.sql_pairs (id, project_id, question, sql, description, category, scope, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [sid, project_id, sp.get("question"), sp.get("sql"), sp.get("description"), sp.get("category"), sp.get("scope"), sp.get("is_active", True)],
                )
            con.execute("UPDATE metadata.projects SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", [project_id])
        return project_id

    def export_audit_logs(self, format: str = "csv") -> bytes:
        with connection_lock():
            con = get_connection()
            rows = _rows_to_dicts(con.execute(
                "SELECT id, user_id, action, resource_type, resource_id, detail, ip_address, user_agent, created_at FROM metadata.audit_logs ORDER BY created_at DESC LIMIT 50000"
            ))
        if format.lower() == "csv":
            output = io.StringIO()
            if rows:
                writer = csv.DictWriter(output, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            return output.getvalue().encode("utf-8")
        return json.dumps(rows, ensure_ascii=False, indent=2, default=str).encode("utf-8")