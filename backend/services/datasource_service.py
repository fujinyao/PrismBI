from __future__ import annotations

from typing import Any, List, Optional

from db import connection_lock, get_connection, rows_to_dicts
from services.crypto_service import encrypt_json, is_encrypted_value


class DatasourceService:
    def list_system_datasources(self) -> List[dict]:
        with connection_lock():
            con = get_connection()
            result = con.execute(
                "SELECT id, name, type, description, created_at, updated_at FROM metadata.datasources ORDER BY created_at DESC"
            )
            rows = result.fetchall()
        return rows_to_dicts(rows, result.description)

    def get_system_datasource(self, datasource_id: int) -> Optional[dict]:
        with connection_lock():
            con = get_connection()
            result = con.execute(
                "SELECT id, name, type, description, created_at, updated_at FROM metadata.datasources WHERE id = ?",
                [datasource_id],
            )
            row = result.fetchone()
        return dict(zip([d[0] for d in result.description], row)) if row else None

    def create_system_datasource(self, name: str, type_: str, properties_encrypted: str, description: Optional[str] = None) -> int:
        properties_value = properties_encrypted if is_encrypted_value(properties_encrypted) else encrypt_json(properties_encrypted)
        with connection_lock():
            con = get_connection()
            ds_id = con.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.datasources"
            ).fetchone()[0]
            con.execute(
                """INSERT INTO metadata.datasources (id, name, type, properties_encrypted, description)
                   VALUES (?, ?, ?, ?, ?)""",
                [ds_id, name, type_, properties_value, description],
            )
        return ds_id

    def update_system_datasource(self, datasource_id: int, data: dict) -> bool:
        sets = []
        params = []
        for key in ("name", "properties_encrypted", "description"):
            if key in data:
                sets.append(f"{key} = ?")
                value = data[key]
                if key == "properties_encrypted" and not is_encrypted_value(value):
                    value = encrypt_json(value)
                params.append(value)
        if not sets:
            return False
        sets.append("updated_at = CURRENT_TIMESTAMP")
        params.append(datasource_id)
        with connection_lock():
            con = get_connection()
            con.execute(
                f"UPDATE metadata.datasources SET {', '.join(sets)} WHERE id = ?", params
            )
        return True

    def delete_system_datasource(self, datasource_id: int) -> bool:
        with connection_lock():
            con = get_connection()
            con.execute("DELETE FROM metadata.datasources WHERE id = ?", [datasource_id])
        return True

    def test_connection(self, type_: str, properties: dict) -> dict:
        raise NotImplementedError

    def list_project_datasources(self, project_id: int) -> List[dict]:
        with connection_lock():
            con = get_connection()
            result = con.execute(
                """SELECT pd.id, pd.project_id, pd.datasource_id, pd.alias,
                          pd.config_overrides, d.name as datasource_name,
                          d.type as datasource_type, pd.created_at
                   FROM metadata.project_datasources pd
                   LEFT JOIN metadata.datasources d ON pd.datasource_id = d.id
                   WHERE pd.project_id = ?""",
                [project_id],
            )
            rows = result.fetchall()
        return rows_to_dicts(rows, result.description)

    def bind_datasource(self, project_id: int, datasource_id: int, alias: Optional[str] = None, config_overrides: Optional[Any] = None) -> int:
        with connection_lock():
            con = get_connection()
            binding_id = con.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.project_datasources"
            ).fetchone()[0]
            con.execute(
"""INSERT INTO metadata.project_datasources (id, project_id, datasource_id, alias, config_overrides)
               VALUES (?, ?, ?, ?, ?)""",
                [binding_id, project_id, datasource_id, alias, config_overrides],
            )
        return binding_id

    def unbind_datasource(self, binding_id: int) -> bool:
        with connection_lock():
            con = get_connection()
            con.execute(
                "DELETE FROM metadata.project_datasources WHERE id = ?", [binding_id]
            )
        return True

    def list_tables(self, binding_id: int) -> List[str]:
        raise NotImplementedError

    def sync_tables(self, binding_id: int) -> dict:
        raise NotImplementedError