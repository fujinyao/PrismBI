from __future__ import annotations

from typing import Any, List, Optional

from db import connection_lock, get_connection, rows_to_dicts


class ProjectService:
    def list_projects(self) -> List[dict]:
        with connection_lock():
            con = get_connection()
            result = con.execute(
                "SELECT * FROM metadata.projects ORDER BY created_at DESC"
            )
            rows = result.fetchall()
        return rows_to_dicts(rows, result.description)

    def get_project(self, project_id: int) -> Optional[dict]:
        with connection_lock():
            con = get_connection()
            result = con.execute(
                "SELECT * FROM metadata.projects WHERE id = ?", [project_id]
            )
            row = result.fetchone()
        return rows_to_dicts([row], result.description)[0] if row else None

    def create_project(self, data: dict) -> int:
        with connection_lock():
            con = get_connection()
            project_id = con.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.projects"
            ).fetchone()[0]
            con.execute(
"""INSERT INTO metadata.projects (id, name, display_name, description, type, connection_info, language, sample_dataset)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    project_id,
                    data["name"],
                    data.get("display_name"),
                    data.get("description"),
                    data.get("type"),
                    data.get("connection_info"),
                    data.get("language", "EN"),
                    data.get("sample_dataset"),
                ],
            )
        return project_id

    def update_project(self, project_id: int, data: dict) -> bool:
        sets = []
        params = []
        for key in ("name", "display_name", "description", "connection_info", "language"):
            if key in data:
                col = key if key != "connection_info" else "connection_info"
                sets.append(f"{col} = ?")
                params.append(data[key])
        if not sets:
            return False
        sets.append("updated_at = CURRENT_TIMESTAMP")
        params.append(project_id)
        sql = f"UPDATE metadata.projects SET {', '.join(sets)} WHERE id = ?"
        with connection_lock():
            con = get_connection()
            con.execute(sql, params)
        return True

    def delete_project(self, project_id: int) -> bool:
        with connection_lock():
            con = get_connection()
            con.execute("DELETE FROM metadata.projects WHERE id = ?", [project_id])
        return True

    def set_current(self, project_id: int) -> None:
        with connection_lock():
            con = get_connection()
            con.execute("UPDATE metadata.projects SET is_current = false")
            con.execute(
                "UPDATE metadata.projects SET is_current = true WHERE id = ?",
                [project_id],
            )

    def list_members(self, project_id: int) -> List[dict]:
        with connection_lock():
            con = get_connection()
            result = con.execute(
                """SELECT ur.id, ur.user_id, u.username, u.display_name,
                          ur.role_id, r.name as role_name, ur.expires_at, ur.created_at
                   FROM metadata.user_roles ur
                   LEFT JOIN metadata.users u ON ur.user_id = u.id
                   LEFT JOIN metadata.roles r ON ur.role_id = r.id
                   WHERE ur.project_id = ?""",
                [project_id],
            )
            rows = result.fetchall()
        return rows_to_dicts(rows, result.description)

    def add_member(self, project_id: int, user_id: int, role_id: int, expires_at: Any = None) -> int:
        with connection_lock():
            con = get_connection()
            member_id = con.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.user_roles"
            ).fetchone()[0]
            con.execute(
                """INSERT INTO metadata.user_roles (id, user_id, role_id, project_id, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                [member_id, user_id, role_id, project_id, expires_at],
            )
        return member_id

    def update_member(self, member_id: int, role_id: int) -> bool:
        with connection_lock():
            con = get_connection()
            con.execute(
                "UPDATE metadata.user_roles SET role_id = ? WHERE id = ?",
                [role_id, member_id],
            )
        return True

    def remove_member(self, member_id: int) -> bool:
        with connection_lock():
            con = get_connection()
            con.execute("DELETE FROM metadata.user_roles WHERE id = ?", [member_id])
        return True