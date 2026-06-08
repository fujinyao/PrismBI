from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import re
from typing import Any

LOGGER = logging.getLogger(__name__)

import duckdb
from fastapi import APIRouter, Depends, HTTPException

from db import connection_lock, get_connection
from models.schemas import (
    DatasourceBindingCreate,
    DatasourceCreate,
    DatasourceUpdate,
)
import threading

from routers.auth import get_current_user, payload_has_permission, require_permission
from services.crypto_service import decrypt_json, encrypt_json, is_encrypted_value
from services.sql_routing.datasource_registry import resolve_datasource_definition

router = APIRouter()
_runtime_tables_ensured = False
_runtime_tables_lock = threading.Lock()
_init_sql_lock = threading.Lock()

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.getenv("PRISMBI_DATA_DIR") or os.path.join(BACKEND_DIR, "data")
PROJECT_DATA_DIR = os.path.join(DATA_DIR, "projects")
SAFE_DBNAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
SENSITIVE_PROPERTY_KEYS = {
    "password",
    "passwd",
    "pwd",
    "api_key",
    "apikey",
    "access_key",
    "secret",
    "token",
    "client_secret",
    "private_key",
}


def _redact_properties(value: Any):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if any(part in str(key).lower() for part in SENSITIVE_PROPERTY_KEYS) else _redact_properties(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_properties(item) for item in value]
    return value


def _safe_warning_from_exception(exc: Exception) -> str:
    text = str(exc or "").strip()
    if not text:
        return "unknown error"
    redacted = re.sub(
        r"(?i)(password|passwd|pwd|token|secret|api[_-]?key)\s*=\s*[^,;\s]+",
        r"\1=[REDACTED]",
        text,
    )
    redacted = re.sub(
        r"(?i)(://[^:/\s]+:)[^@/\s]+@",
        r"\1[REDACTED]@",
        redacted,
    )
    if len(redacted) > 240:
        redacted = redacted[:240].rstrip() + "..."
    return redacted


def _require_datasource_permission(payload: dict, action: str, project_id: int | None = None) -> None:
    if project_id is not None and project_id <= 0:
        raise HTTPException(status_code=400, detail="A real project is required")
    if not payload_has_permission(payload, "datasources", action, project_id):
        raise HTTPException(status_code=403, detail="Permission denied")

def _safe_json_loads(value: Any, fallback: Any):
    decoded = decrypt_json(value, fallback)
    return decoded if decoded is not None else fallback


def _encrypted_json_value(value: Any) -> str:
    return value if is_encrypted_value(value) else encrypt_json(value)


def _normalize_ds_type(ds_type: str) -> str:
    normalized = (ds_type or "").strip().lower()
    definition = resolve_datasource_definition(normalized)
    if definition is not None:
        return definition.canonical_type
    return normalized


def _import_optional(module_name: str):
    try:
        return importlib.import_module(module_name)
    except Exception:
        return None


def _coerce_json_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    if os.path.exists(text):
        try:
            with open(text, encoding="utf-8") as handle:
                parsed = json.load(handle)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
    return None


def _bigquery_credentials_from_props(props: dict):
    info = _coerce_json_dict(props.get("credentials"))
    if not info:
        return None
    service_account = _import_optional("google.oauth2.service_account")
    credentials_cls = getattr(service_account, "Credentials", None) if service_account else None
    if credentials_cls is None:
        return None
    try:
        return credentials_cls.from_service_account_info(info)
    except Exception:
        return None


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "require", "required"}


def _table_reference(schema: str | None, table_name: str) -> str:
    if schema and schema not in ("main", "memory", "public"):
        return f"{schema}.{table_name}"
    return table_name


def _normalize_table_type(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", " ").replace("_", " ")
    compact = "".join(raw.split())
    if not raw:
        return "table"
    if "materialized" in raw and "view" in raw:
        return "materialized_view"
    if compact in {"materializedview", "matview", "mview"}:
        return "materialized_view"
    if "view" in raw or compact in {"view", "logicalview"}:
        return "view"
    if "foreign" in raw and "table" in raw:
        return "foreign_table"
    if "external" in raw and "table" in raw:
        return "external_table"
    if "temp" in raw:
        return "temporary_table"
    if "table" in raw:
        return "table"
    return "other"


def _metadata_from_config(props: dict, ds_type: str) -> dict[str, Any] | None:
    table_details = props.get("table_details") or props.get("tableDetails")
    if isinstance(table_details, list):
        details = []
        for item in table_details:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("table") or "").strip()
            if not name:
                continue
            schema = item.get("schema")
            schema = str(schema).strip() if schema else None
            reference = str(item.get("reference") or _table_reference(schema, name))
            columns = []
            for col in item.get("columns") or []:
                if isinstance(col, dict) and col.get("name"):
                    columns.append(
                        {
                            "name": str(col.get("name")),
                            "type": str(col.get("type") or col.get("data_type") or "UNKNOWN"),
                            "is_primary_key": bool(col.get("is_primary_key") or col.get("primary_key")),
                            "display_name": col.get("display_name") or col.get("displayName"),
                            "description": col.get("description") or col.get("comment") or col.get("remarks"),
                        }
                    )
            details.append({
                "name": name,
                "schema": schema,
                "reference": reference,
                "description": item.get("description") or item.get("comment") or item.get("remarks"),
                "display_name": item.get("display_name") or item.get("displayName"),
                "table_type": _normalize_table_type(
                    item.get("table_type") or item.get("tableType") or item.get("object_type") or item.get("objectType")
                ),
                "columns": columns,
            })
        if details:
            return {
                "tables": [item["reference"] for item in details],
                "table_details": details,
                "warning": f"Using configured metadata for datasource type '{ds_type}'.",
            }

    configured_tables = props.get("tables")
    if isinstance(configured_tables, list):
        details = []
        for item in configured_tables:
            if isinstance(item, str):
                name = item
                schema = None
                if "." in item:
                    schema, name = item.rsplit(".", 1)
                details.append(
                    {
                        "name": name,
                        "schema": schema,
                        "reference": item,
                        "columns": [],
                        "description": None,
                        "table_type": "table",
                    }
                )
            elif isinstance(item, dict):
                name = str(item.get("name") or item.get("table") or "").strip()
                if not name:
                    continue
                schema = item.get("schema")
                schema = str(schema).strip() if schema else None
                reference = str(item.get("reference") or _table_reference(schema, name))
                details.append({
                    "name": name,
                    "schema": schema,
                    "reference": reference,
                    "description": item.get("description") or item.get("comment") or item.get("remarks"),
                    "display_name": item.get("display_name") or item.get("displayName"),
                    "table_type": _normalize_table_type(
                        item.get("table_type") or item.get("tableType") or item.get("object_type") or item.get("objectType")
                    ),
                    "columns": [],
                })
        return {
            "tables": [item["reference"] for item in details],
            "table_details": details,
            "warning": f"Using configured table list for datasource type '{ds_type}'. Column metadata is unavailable.",
        }

    return None


def _ensure_runtime_tables() -> None:
    global _runtime_tables_ensured
    with _runtime_tables_lock:
        if _runtime_tables_ensured:
            return
        with connection_lock():
            get_connection().execute("""
                CREATE TABLE IF NOT EXISTS metadata.datasource_runtime_state (
                    binding_id INTEGER PRIMARY KEY,
                    init_sql_hash VARCHAR,
                    initialized_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        _runtime_tables_ensured = True


def _init_sql_hash(init_sql: str) -> str:
    return hashlib.sha256(init_sql.encode("utf-8")).hexdigest()


def _should_apply_init_sql(binding_id: int, init_hash: str) -> bool:
    _ensure_runtime_tables()
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT init_sql_hash FROM metadata.datasource_runtime_state WHERE binding_id = ?",
            [binding_id],
        ).fetchone()
    if not row:
        return True
    return row[0] != init_hash


def _mark_init_sql_applied(binding_id: int, init_hash: str) -> None:
    _ensure_runtime_tables()
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT binding_id FROM metadata.datasource_runtime_state WHERE binding_id = ?",
            [binding_id],
        ).fetchone()
        if existing:
            con.execute(
                "UPDATE metadata.datasource_runtime_state SET init_sql_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE binding_id = ?",
                [init_hash, binding_id],
            )
        else:
            con.execute(
                "INSERT INTO metadata.datasource_runtime_state (binding_id, init_sql_hash) VALUES (?, ?)",
                [binding_id, init_hash],
        )


def _safe_duckdb_name(raw_name: str, binding_id: int) -> str:
    name = os.path.basename(str(raw_name or "").strip())
    if name.endswith(".duckdb"):
        name = name[: -len(".duckdb")]
    name = SAFE_DBNAME_RE.sub("_", name).strip("._-")
    if not name:
        name = f"datasource_{binding_id}"
    return f"{name}.duckdb"


def _resolve_duckdb_path(props: dict, project_id: int, binding_id: int) -> str:
    if project_id == 0:
        raise ValueError("A real project is required for DuckDB file materialization")
    dbname = str(props.get("dbname") or "").strip()
    project_dir = os.path.join(DATA_DIR, "datasource-tests") if project_id < 0 else os.path.join(PROJECT_DATA_DIR, str(project_id))
    os.makedirs(project_dir, exist_ok=True)
    return os.path.join(project_dir, _safe_duckdb_name(dbname, binding_id))


def _sample_metadata_lookup(props: dict) -> dict[str, dict[str, Any]]:
    table_details = props.get("sampleTableDetails") or props.get("sample_table_details") or []
    if not isinstance(table_details, list):
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    for detail in table_details:
        if not isinstance(detail, dict):
            continue
        reference = str(detail.get("reference") or detail.get("tableName") or detail.get("name") or "").strip()
        if not reference:
            continue
        column_lookup: dict[str, dict[str, Any]] = {}
        for column in detail.get("columns") or []:
            if isinstance(column, dict) and column.get("name"):
                column_lookup[str(column["name"]).lower()] = column
        lookup[reference.lower()] = {**detail, "column_lookup": column_lookup}
    return lookup


def _merge_sample_metadata(discovered: dict[str, Any], props: dict) -> dict[str, Any]:
    lookup = _sample_metadata_lookup(props)
    if not lookup:
        return discovered
    for detail in discovered.get("table_details") or []:
        if not isinstance(detail, dict):
            continue
        reference = str(detail.get("reference") or detail.get("name") or "").lower()
        meta = lookup.get(reference)
        if not meta:
            continue
        detail["description"] = detail.get("description") or meta.get("description")
        detail["display_name"] = detail.get("display_name") or meta.get("displayName") or meta.get("display_name")
        column_lookup = meta.get("column_lookup") or {}
        for column in detail.get("columns") or []:
            if not isinstance(column, dict) or not column.get("name"):
                continue
            column_meta = column_lookup.get(str(column["name"]).lower())
            if not column_meta:
                continue
            column["description"] = column.get("description") or column_meta.get("description")
            column["display_name"] = column.get("display_name") or column_meta.get("displayName") or column_meta.get("display_name")
            if column_meta.get("is_primary_key") or column_meta.get("primaryKey"):
                column["is_primary_key"] = True
    return discovered


INIT_SQL_BLOCKED_KEYWORDS = frozenset({
    "DROP", "ALTER", "TRUNCATE", "INSERT", "UPDATE", "DELETE",
    "COPY", "ATTACH", "DETACH", "PRAGMA", "EXPORT", "IMPORT",
})
INIT_SQL_BLOCKED_PATTERNS_ALWAYS = frozenset({
    "metadata.", "information_schema.", "pg_catalog.", "sqlite_master",
    "read_json", "read_json_auto", "read_text",
    "read_blob", "read_file", "glob", "listdir", "chdir",
    "httpfs_scan", "parquet_scan", "sqlite_scan",
    "postgres_scan", "mysql_scan", "delta_scan", "iceberg_scan",
})

INIT_SQL_DATA_FUNCTION_PATTERNS = frozenset({
    "read_csv", "read_csv_auto", "read_parquet", "read_delta",
})

_CREATE_TABLE_RE = re.compile(
    r"^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+AS\s+SELECT\s",
    re.IGNORECASE,
)
_CREATE_VIEW_RE = re.compile(
    r"^CREATE\s+VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+AS\s+SELECT\s",
    re.IGNORECASE,
)


def _validate_init_sql(sql: str) -> None:
    sql_stripped = sql.strip()
    if not sql_stripped:
        return
    sql_lower = sql_stripped.lower()
    if any(pattern in sql_lower for pattern in INIT_SQL_BLOCKED_PATTERNS_ALWAYS):
        raise HTTPException(status_code=400, detail="initSql contains forbidden reference")
    statements = [s.strip() for s in sql_stripped.rstrip(";").split(";") if s.strip()]
    for stmt in statements:
        stmt_lower = stmt.lower()
        first_word = stmt.split()[0].upper() if stmt.split() else ""
        if first_word == "CREATE":
            if not _CREATE_TABLE_RE.match(stmt) and not _CREATE_VIEW_RE.match(stmt):
                raise HTTPException(
                    status_code=400,
                    detail="initSql CREATE statements must be CREATE TABLE ... AS SELECT or CREATE VIEW ... AS SELECT",
                )
        elif first_word in INIT_SQL_BLOCKED_KEYWORDS:
            raise HTTPException(status_code=400, detail=f"initSql must not contain {first_word} statements")
        elif not first_word.startswith(("SELECT", "WITH", "COMMENT")):
            raise HTTPException(status_code=400, detail=f"initSql only allows SELECT, WITH, COMMENT, or CREATE TABLE/VIEW AS SELECT statements; found: {first_word}")
        if first_word != "CREATE" and any(pattern in stmt_lower for pattern in INIT_SQL_DATA_FUNCTION_PATTERNS):
            raise HTTPException(status_code=400, detail="initSql contains forbidden data function reference outside CREATE TABLE AS SELECT")


def _apply_init_sql_if_needed(conn: duckdb.DuckDBPyConnection, binding_id: int, init_sql: str) -> None:
    sql = (init_sql or "").strip()
    if not sql:
        return
    _validate_init_sql(sql)
    if binding_id <= 0:
        with _init_sql_lock:
            for stmt in [s.strip() for s in sql.rstrip(";").split(";") if s.strip()]:
                try:
                    conn.execute(stmt)
                except Exception as exc:
                    message = str(exc).lower()
                    if "already exists" not in message and "write-write conflict" not in message:
                        raise RuntimeError(f"Init SQL execution failed: {exc}") from exc
        return
    sql_hash = _init_sql_hash(sql)
    if not _should_apply_init_sql(binding_id, sql_hash):
        return
    with _init_sql_lock:
        if not _should_apply_init_sql(binding_id, sql_hash):
            return
        for stmt in [s.strip() for s in sql.rstrip(";").split(";") if s.strip()]:
            try:
                conn.execute(stmt)
            except Exception as exc:
                message = str(exc).lower()
                if "already exists" not in message and "write-write conflict" not in message:
                    raise RuntimeError(f"Init SQL execution failed: {exc}") from exc
        _mark_init_sql_applied(binding_id, sql_hash)


def _load_duckdb_table_details(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    tables: list[str] = []

    rows = conn.execute(
        """
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
          AND table_type IN ('BASE TABLE', 'VIEW', 'LOCAL TEMPORARY')
        ORDER BY table_schema, table_name
        """
    ).fetchall()

    for schema, table_name, table_type in rows:
        qualified = f"{schema}.{table_name}" if schema and schema.lower() not in ("main", "") else str(table_name)
        pragma_rows = conn.execute(
            "SELECT * FROM pragma_table_info(?)",
            [qualified],
        ).fetchall()
        column_comments: dict[str, str] = {}
        table_comment = None
        try:
            comment_rows = conn.execute(
                """
                SELECT column_name, comment
                FROM duckdb_columns()
                WHERE schema_name = ? AND table_name = ?
                """,
                [schema, table_name],
            ).fetchall()
            column_comments = {str(name): comment for name, comment in comment_rows if comment}
        except Exception:
            column_comments = {}
        try:
            table_comment_row = conn.execute(
                """
                SELECT comment
                FROM duckdb_tables()
                WHERE schema_name = ? AND table_name = ?
                LIMIT 1
                """,
                [schema, table_name],
            ).fetchone()
            table_comment = table_comment_row[0] if table_comment_row and table_comment_row[0] else None
        except Exception:
            table_comment = None

        columns = [
            {
                "name": col[1],
                "type": col[2],
                "is_primary_key": bool(col[5]),
                "description": column_comments.get(str(col[1])),
            }
            for col in pragma_rows
        ]

        schema_name = str(schema or "")
        if schema_name and schema_name not in ("main", "memory"):
            reference = f"{schema_name}.{table_name}"
        else:
            reference = str(table_name)

        tables.append(reference)
        details.append(
            {
                "name": str(table_name),
                "schema": schema_name or None,
                "reference": reference,
                "description": table_comment,
                "table_type": _normalize_table_type(table_type),
                "columns": columns,
            }
        )

    return {"tables": tables, "table_details": details}


def _rows_to_table_details(rows: list[tuple], default_schema: str | None = None) -> dict[str, Any]:
    grouped: dict[tuple[str | None, str], dict[str, Any]] = {}
    for row in rows:
        schema = row[0] if row[0] is not None else default_schema
        table_name = str(row[1])
        column_name = str(row[2])
        data_type = str(row[3] or "UNKNOWN")
        is_primary_key = bool(row[4]) if len(row) > 4 else False
        table_description = row[5] if len(row) > 5 else None
        column_description = row[6] if len(row) > 6 else None
        table_type = _normalize_table_type(row[7]) if len(row) > 7 else "table"
        key = (str(schema) if schema else None, table_name)
        entry = grouped.setdefault(key, {"description": table_description, "table_type": table_type, "columns": []})
        if not entry.get("description") and table_description:
            entry["description"] = table_description
        if entry.get("table_type") in (None, "table", "other") and table_type not in (None, "table"):
            entry["table_type"] = table_type
        entry["columns"].append(
            {
                "name": column_name,
                "type": data_type,
                "is_primary_key": is_primary_key,
                "description": column_description,
            }
        )

    details = []
    for (schema, table_name), data in sorted(grouped.items(), key=lambda item: (item[0][0] or "", item[0][1])):
        reference = _table_reference(schema, table_name)
        details.append(
            {
                "name": table_name,
                "schema": schema,
                "reference": reference,
                "description": data.get("description"),
                "table_type": data.get("table_type") or "table",
                "columns": data.get("columns", []),
            }
        )
    return {"tables": [item["reference"] for item in details], "table_details": details}


def _discover_postgresql(props: dict) -> dict[str, Any]:
    psycopg = _import_optional("psycopg")
    psycopg2 = None if psycopg else _import_optional("psycopg2")
    driver = psycopg or psycopg2
    if not driver:
        return _unsupported_driver("postgresql", "psycopg or psycopg2")

    conn = driver.connect(
        host=props.get("host"),
        port=int(props.get("port") or 5432),
        user=props.get("user") or props.get("username"),
        password=props.get("password"),
        dbname=props.get("database") or props.get("dbname"),
        sslmode="require" if _normalize_bool(props.get("ssl")) else "prefer",
        connect_timeout=10,
    )
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                WITH rels AS (
                    SELECT n.nspname AS table_schema,
                           c.relname AS table_name,
                           c.oid AS table_oid,
                           CASE c.relkind
                               WHEN 'v' THEN 'VIEW'
                               WHEN 'm' THEN 'MATERIALIZED VIEW'
                               WHEN 'f' THEN 'FOREIGN TABLE'
                               ELSE 'BASE TABLE'
                           END AS table_type
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
                      AND n.nspname NOT IN ('information_schema', 'pg_catalog')
                )
                SELECT rels.table_schema,
                       rels.table_name,
                       a.attname AS column_name,
                       format_type(a.atttypid, a.atttypmod) AS data_type,
                       CASE WHEN pk.conoid IS NULL THEN false ELSE true END AS is_primary_key,
                       obj_description(rels.table_oid, 'pg_class') AS table_description,
                       col_description(rels.table_oid, a.attnum) AS column_description,
                       rels.table_type
                FROM rels
                JOIN pg_attribute a
                  ON a.attrelid = rels.table_oid
                 AND a.attnum > 0
                 AND NOT a.attisdropped
                LEFT JOIN LATERAL (
                    SELECT c.oid AS conoid
                    FROM pg_constraint c
                    WHERE c.conrelid = rels.table_oid
                      AND c.contype = 'p'
                      AND a.attnum = ANY(c.conkey)
                    LIMIT 1
                ) pk ON true
                ORDER BY rels.table_schema, rels.table_name, a.attnum
                """
            )
        except Exception:
            cur.execute(
                """
                SELECT c.table_schema,
                       c.table_name,
                       c.column_name,
                       c.data_type,
                       CASE WHEN kcu.column_name IS NULL THEN false ELSE true END AS is_primary_key,
                       obj_description(format('%I.%I', c.table_schema, c.table_name)::regclass, 'pg_class') AS table_description,
                       col_description(format('%I.%I', c.table_schema, c.table_name)::regclass, c.ordinal_position) AS column_description,
                       COALESCE(t.table_type, 'BASE TABLE') AS table_type
                FROM information_schema.columns c
                LEFT JOIN information_schema.table_constraints tc
                  ON tc.table_schema = c.table_schema
                 AND tc.table_name = c.table_name
                 AND tc.constraint_type = 'PRIMARY KEY'
                LEFT JOIN information_schema.key_column_usage kcu
                  ON kcu.constraint_name = tc.constraint_name
                 AND kcu.table_schema = tc.table_schema
                 AND kcu.table_name = tc.table_name
                 AND kcu.column_name = c.column_name
                LEFT JOIN information_schema.tables t
                  ON t.table_schema = c.table_schema
                 AND t.table_name = c.table_name
                WHERE c.table_schema NOT IN ('information_schema', 'pg_catalog')
                ORDER BY c.table_schema, c.table_name, c.ordinal_position
                """
            )
        return _rows_to_table_details(cur.fetchall())
    finally:
        conn.close()


def _discover_mysql(props: dict) -> dict[str, Any]:
    connector = _import_optional("mysql.connector")
    pymysql = None if connector else _import_optional("pymysql")
    database = props.get("database") or props.get("dbname")
    if connector:
        conn = connector.connect(
            host=props.get("host"),
            port=int(props.get("port") or 3306),
            user=props.get("user") or props.get("username"),
            password=props.get("password"),
            database=database,
            connection_timeout=10,
            ssl_disabled=not _normalize_bool(props.get("ssl")),
        )
    elif pymysql:
        conn = pymysql.connect(
            host=props.get("host"),
            port=int(props.get("port") or 3306),
            user=props.get("user") or props.get("username"),
            password=props.get("password"),
            database=database,
            connect_timeout=10,
            ssl={} if _normalize_bool(props.get("ssl")) else None,
        )
    else:
        return _unsupported_driver("mysql", "mysql-connector-python or pymysql")
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT c.table_schema, c.table_name, c.column_name, c.data_type,
                   CASE WHEN c.column_key = 'PRI' THEN 1 ELSE 0 END AS is_primary_key,
                   t.table_comment AS table_description,
                   c.column_comment AS column_description,
                   COALESCE(t.table_type, 'BASE TABLE') AS table_type
            FROM information_schema.columns c
            LEFT JOIN information_schema.tables t
              ON t.table_schema = c.table_schema
              AND t.table_name = c.table_name
            WHERE c.table_schema = %s
            ORDER BY c.table_schema, c.table_name, c.ordinal_position
            """,
            [database],
        )
        return _rows_to_table_details(cur.fetchall())
    finally:
        conn.close()


def _discover_clickhouse(props: dict) -> dict[str, Any]:
    clickhouse_connect = _import_optional("clickhouse_connect")
    if not clickhouse_connect:
        return _unsupported_driver("clickhouse", "clickhouse-connect")
    client = clickhouse_connect.get_client(
        host=props.get("host"),
        port=int(props.get("port") or (8443 if _normalize_bool(props.get("ssl")) else 8123)),
        username=props.get("user") or props.get("username") or "default",
        password=props.get("password") or "",
        database=props.get("database") or "default",
        secure=_normalize_bool(props.get("ssl")),
        connect_timeout=10,
    )
    try:
        result = client.query(
            """
            SELECT c.database,
                   c.table,
                   c.name,
                   c.type,
                   c.is_in_primary_key,
                   NULL,
                   c.comment,
                   CASE
                       WHEN lower(COALESCE(t.engine, '')) = 'view' THEN 'VIEW'
                       WHEN lower(COALESCE(t.engine, '')) IN ('materializedview', 'liveview', 'windowview') THEN 'MATERIALIZED VIEW'
                       ELSE 'BASE TABLE'
                   END AS table_type
            FROM system.columns c
            LEFT JOIN system.tables t
              ON t.database = c.database
             AND t.name = c.table
            WHERE c.database = {database:String}
            ORDER BY c.database, c.table, c.position
            """,
            parameters={"database": props.get("database") or "default"},
        )
        return _rows_to_table_details(result.result_rows)
    finally:
        client.close()


def _discover_mssql(props: dict) -> dict[str, Any]:
    pyodbc = _import_optional("pyodbc")
    pymssql = _import_optional("pymssql")
    conn = None
    pyodbc_error = None
    if pyodbc:
        driver = props.get("driver") or "ODBC Driver 18 for SQL Server"
        trust = "yes" if _normalize_bool(props.get("ssl")) else "no"
        try:
            conn = pyodbc.connect(
                driver=driver,
                server=props.get("host"),
                port=int(props.get("port") or 1433),
                database=props.get("database"),
                uid=props.get("user") or props.get("username"),
                pwd=props.get("password") or "",
                trustservercertificate=trust,
                timeout=10,
            )
        except Exception as exc:
            pyodbc_error = exc
            conn = None
    if conn is None and pymssql:
        conn = pymssql.connect(
            server=props.get("host"),
            port=int(props.get("port") or 1433),
            user=props.get("user") or props.get("username"),
            password=props.get("password"),
            database=props.get("database"),
            timeout=10,
            login_timeout=10,
        )
    if conn is None:
        if pyodbc_error is not None:
            return {
                "tables": [],
                "table_details": [],
                "warning": f"Metadata discovery via pyodbc failed ({pyodbc_error}); configure ODBC driver or use pymssql.",
            }
        return _unsupported_driver("mssql", "pyodbc or pymssql")
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT c.table_schema, c.table_name, c.column_name, c.data_type,
                   CASE WHEN kcu.column_name IS NULL THEN 0 ELSE 1 END AS is_primary_key,
                   CAST(ep_table.value AS NVARCHAR(MAX)) AS table_description,
                   CAST(ep_column.value AS NVARCHAR(MAX)) AS column_description,
                   COALESCE(it.table_type, 'BASE TABLE') AS table_type
            FROM information_schema.columns c
            LEFT JOIN information_schema.tables it
              ON it.table_schema = c.table_schema
             AND it.table_name = c.table_name
            LEFT JOIN information_schema.table_constraints tc
              ON tc.table_schema = c.table_schema
             AND tc.table_name = c.table_name
             AND tc.constraint_type = 'PRIMARY KEY'
            LEFT JOIN information_schema.key_column_usage kcu
              ON kcu.constraint_name = tc.constraint_name
             AND kcu.table_schema = tc.table_schema
             AND kcu.table_name = tc.table_name
             AND kcu.column_name = c.column_name
            LEFT JOIN sys.schemas ss ON ss.name = c.table_schema
            LEFT JOIN sys.tables st ON st.name = c.table_name AND st.schema_id = ss.schema_id
            LEFT JOIN sys.columns sc ON sc.object_id = st.object_id AND sc.name = c.column_name
            LEFT JOIN sys.extended_properties ep_table
              ON ep_table.major_id = st.object_id
             AND ep_table.minor_id = 0
             AND ep_table.name = 'MS_Description'
            LEFT JOIN sys.extended_properties ep_column
              ON ep_column.major_id = st.object_id
             AND ep_column.minor_id = sc.column_id
             AND ep_column.name = 'MS_Description'
            WHERE c.table_schema NOT IN ('INFORMATION_SCHEMA', 'sys')
            ORDER BY c.table_schema, c.table_name, c.ordinal_position
            """
        )
        return _rows_to_table_details(cur.fetchall())
    finally:
        conn.close()


def _discover_trino(props: dict) -> dict[str, Any]:
    trino = _import_optional("trino")
    if not trino:
        return _unsupported_driver("trino", "trino")
    auth = None
    if props.get("password"):
        auth = trino.auth.BasicAuthentication(props.get("username") or props.get("user"), props.get("password"))
    conn = trino.dbapi.connect(
        host=props.get("host"),
        port=int(props.get("port") or 8080),
        user=props.get("username") or props.get("user"),
        http_scheme="https" if _normalize_bool(props.get("ssl")) else "http",
        auth=auth,
    )
    try:
        cur = conn.cursor()
        schema_specs = [s.strip() for s in str(props.get("schemas") or "").split(",") if s.strip()]
        rows = []
        for spec in schema_specs:
            if "." not in spec:
                continue
            catalog, schema = spec.split(".", 1)
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", catalog):
                continue
            cur.execute(
                f"""
                SELECT c.table_schema,
                       c.table_name,
                       c.column_name,
                       c.data_type,
                       false,
                       NULL,
                       NULL,
                       COALESCE(t.table_type, 'BASE TABLE')
                FROM "{catalog}".information_schema.columns c
                LEFT JOIN "{catalog}".information_schema.tables t
                  ON t.table_schema = c.table_schema
                 AND t.table_name = c.table_name
                WHERE c.table_schema = ?
                ORDER BY c.table_schema, c.table_name, c.ordinal_position
                """,
                [schema],
            )
            rows.extend(cur.fetchall())
        return _rows_to_table_details(rows)
    finally:
        conn.close()


def _discover_athena(props: dict) -> dict[str, Any]:
    pyathena = _import_optional("pyathena")
    if not pyathena:
        if props.get("host"):
            return _discover_trino(props)
        return _unsupported_driver("athena", "pyathena or trino")
    connect_kwargs: dict[str, Any] = {}
    schema_name = props.get("schema") or props.get("database") or props.get("dbname")
    if schema_name:
        connect_kwargs["schema_name"] = schema_name
    if props.get("catalog"):
        connect_kwargs["catalog_name"] = props.get("catalog")
    if props.get("s3_staging_dir"):
        connect_kwargs["s3_staging_dir"] = props.get("s3_staging_dir")
    if props.get("aws_region"):
        connect_kwargs["region_name"] = props.get("aws_region")
    if props.get("work_group"):
        connect_kwargs["work_group"] = props.get("work_group")
    if props.get("aws_access_key"):
        connect_kwargs["aws_access_key_id"] = props.get("aws_access_key")
    if props.get("aws_secret_key"):
        connect_kwargs["aws_secret_access_key"] = props.get("aws_secret_key")
    if props.get("aws_session_token"):
        connect_kwargs["aws_session_token"] = props.get("aws_session_token")
    conn = pyathena.connect(**connect_kwargs)
    try:
        cur = conn.cursor()
        where_sql = ""
        if schema_name:
            escaped_schema = str(schema_name).replace("'", "''")
            where_sql = f" WHERE c.table_schema = '{escaped_schema}'"
        cur.execute(
            f"""
            SELECT c.table_schema,
                   c.table_name,
                   c.column_name,
                   c.data_type,
                   false,
                   NULL,
                   NULL,
                   COALESCE(t.table_type, 'BASE TABLE')
            FROM information_schema.columns c
            LEFT JOIN information_schema.tables t
              ON t.table_schema = c.table_schema
             AND t.table_name = c.table_name
            {where_sql}
            ORDER BY c.table_schema, c.table_name, c.ordinal_position
            """
        )
        return _rows_to_table_details(cur.fetchall())
    finally:
        conn.close()


def _discover_oracle(props: dict) -> dict[str, Any]:
    oracledb = _import_optional("oracledb")
    cx_oracle = None if oracledb else _import_optional("cx_Oracle")
    driver = oracledb or cx_oracle
    if not driver:
        return _unsupported_driver("oracle", "oracledb or cx_Oracle")
    dsn = str(props.get("dsn") or "").strip()
    if not dsn:
        host = props.get("host")
        port = int(props.get("port") or 1521)
        service_name = props.get("service_name") or props.get("serviceName") or props.get("database") or props.get("dbname")
        if hasattr(driver, "makedsn") and host and service_name:
            dsn = driver.makedsn(host, port, service_name=service_name)
        else:
            dsn = f"{host}:{port}/{service_name}"
    owner = str(props.get("schema") or props.get("user") or props.get("username") or "").strip().upper() or None
    conn = driver.connect(
        user=props.get("user") or props.get("username"),
        password=props.get("password"),
        dsn=dsn,
    )
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT c.owner,
                   c.table_name,
                   c.column_name,
                   c.data_type,
                   CASE WHEN pk.column_name IS NULL THEN 0 ELSE 1 END AS is_primary_key,
                   tc.comments AS table_description,
                   cc.comments AS column_description,
                   CASE
                       WHEN o.object_type = 'VIEW' THEN 'VIEW'
                       WHEN o.object_type = 'MATERIALIZED VIEW' THEN 'MATERIALIZED VIEW'
                       ELSE 'BASE TABLE'
                   END AS table_type
            FROM all_tab_columns c
            LEFT JOIN all_tab_comments tc
              ON tc.owner = c.owner
             AND tc.table_name = c.table_name
            LEFT JOIN all_col_comments cc
              ON cc.owner = c.owner
             AND cc.table_name = c.table_name
             AND cc.column_name = c.column_name
            LEFT JOIN all_objects o
              ON o.owner = c.owner
             AND o.object_name = c.table_name
             AND o.object_type IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW')
            LEFT JOIN (
                SELECT acc.owner, acc.table_name, acc.column_name
                FROM all_constraints ac
                JOIN all_cons_columns acc
                  ON acc.owner = ac.owner
                 AND acc.constraint_name = ac.constraint_name
                WHERE ac.constraint_type = 'P'
            ) pk
              ON pk.owner = c.owner
             AND pk.table_name = c.table_name
             AND pk.column_name = c.column_name
            WHERE (:owner IS NULL OR c.owner = :owner)
            ORDER BY c.owner, c.table_name, c.column_id
            """,
            {"owner": owner},
        )
        return _rows_to_table_details(cur.fetchall())
    finally:
        conn.close()


def _discover_snowflake(props: dict) -> dict[str, Any]:
    snowflake_connector = _import_optional("snowflake.connector")
    if not snowflake_connector:
        return _unsupported_driver("snowflake", "snowflake-connector-python")
    conn = snowflake_connector.connect(
        account=props.get("account"),
        user=props.get("user") or props.get("username"),
        password=props.get("password"),
        database=props.get("database") or props.get("dbname"),
        schema=props.get("schema"),
        warehouse=props.get("warehouse"),
        role=props.get("role"),
        login_timeout=10,
    )
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT c.table_schema,
                   c.table_name,
                   c.column_name,
                   c.data_type,
                   CASE WHEN kcu.column_name IS NULL THEN 0 ELSE 1 END AS is_primary_key,
                   t.comment AS table_description,
                   c.comment AS column_description,
                   COALESCE(t.table_type, 'BASE TABLE') AS table_type
            FROM information_schema.columns c
            LEFT JOIN information_schema.tables t
              ON t.table_schema = c.table_schema
             AND t.table_name = c.table_name
            LEFT JOIN information_schema.table_constraints tc
              ON tc.table_schema = c.table_schema
             AND tc.table_name = c.table_name
             AND tc.constraint_type = 'PRIMARY KEY'
            LEFT JOIN information_schema.key_column_usage kcu
              ON kcu.constraint_name = tc.constraint_name
             AND kcu.table_schema = tc.table_schema
             AND kcu.table_name = tc.table_name
             AND kcu.column_name = c.column_name
            WHERE c.table_schema <> 'INFORMATION_SCHEMA'
            ORDER BY c.table_schema, c.table_name, c.ordinal_position
            """
        )
        return _rows_to_table_details(cur.fetchall())
    finally:
        conn.close()


def _discover_databricks(props: dict) -> dict[str, Any]:
    databricks_sql = _import_optional("databricks.sql")
    if not databricks_sql:
        return _unsupported_driver("databricks", "databricks-sql-connector")
    server_hostname = props.get("server_hostname") or props.get("serverHostname") or props.get("host")
    http_path = props.get("http_path") or props.get("httpPath")
    access_token = props.get("access_token") or props.get("accessToken") or props.get("token")
    if not server_hostname or not http_path or not access_token:
        return {
            "tables": [],
            "table_details": [],
            "warning": "Metadata discovery for datasource type 'databricks' requires properties: server_hostname, http_path, access_token.",
        }
    conn = databricks_sql.connect(
        server_hostname=server_hostname,
        http_path=http_path,
        access_token=access_token,
    )
    try:
        cur = conn.cursor()
        filters = []
        catalog = str(props.get("catalog") or "").strip()
        schema = str(props.get("schema") or "").strip()
        if catalog:
            filters.append(f"c.table_catalog = '{catalog.replace("'", "''")}'")
        if schema:
            filters.append(f"c.table_schema = '{schema.replace("'", "''")}'")
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        cur.execute(
            f"""
            SELECT c.table_schema,
                   c.table_name,
                   c.column_name,
                   c.data_type,
                   false,
                   NULL,
                   NULL,
                   COALESCE(t.table_type, 'BASE TABLE')
            FROM information_schema.columns c
            LEFT JOIN information_schema.tables t
              ON t.table_catalog = c.table_catalog
             AND t.table_schema = c.table_schema
             AND t.table_name = c.table_name
            {where_sql}
            ORDER BY c.table_schema, c.table_name, c.ordinal_position
            """
        )
        return _rows_to_table_details(cur.fetchall())
    finally:
        conn.close()


def _discover_bigquery(props: dict) -> dict[str, Any]:
    bigquery = _import_optional("google.cloud.bigquery")
    if not bigquery:
        return _unsupported_driver("bigquery", "google-cloud-bigquery")
    client_kwargs: dict[str, Any] = {}
    project_id = props.get("project_id") or props.get("projectId")
    if project_id:
        client_kwargs["project"] = project_id
    credentials = _bigquery_credentials_from_props(props)
    if credentials is not None:
        client_kwargs["credentials"] = credentials
    client = bigquery.Client(**client_kwargs)
    try:
        dataset_raw = str(props.get("dataset_id") or props.get("datasetId") or "")
        dataset_ids = [item.strip() for item in dataset_raw.split(",") if item.strip()]
        if not dataset_ids:
            dataset_ids = [d.dataset_id for d in client.list_datasets()]
        details = []
        for dataset_id in dataset_ids:
            dataset_ref = dataset_id
            if project_id and "." not in dataset_ref:
                dataset_ref = f"{project_id}.{dataset_ref}"
            for table_item in client.list_tables(dataset_ref):
                table = client.get_table(table_item.reference)
                table_schema = str(getattr(table.reference, "dataset_id", "") or "")
                table_name = str(getattr(table.reference, "table_id", "") or "")
                if not table_name:
                    continue
                columns = []
                for field in list(getattr(table, "schema", None) or []):
                    col_name = str(getattr(field, "name", "") or "")
                    if not col_name:
                        continue
                    columns.append(
                        {
                            "name": col_name,
                            "type": str(getattr(field, "field_type", "UNKNOWN") or "UNKNOWN"),
                            "is_primary_key": False,
                            "description": getattr(field, "description", None),
                        }
                    )
                details.append(
                    {
                        "name": table_name,
                        "schema": table_schema or None,
                        "reference": _table_reference(table_schema or None, table_name),
                        "description": getattr(table, "description", None),
                        "table_type": _normalize_table_type(getattr(table, "table_type", None)),
                        "columns": columns,
                    }
                )
        details.sort(key=lambda item: ((item.get("schema") or ""), item.get("name") or ""))
        return {"tables": [item["reference"] for item in details], "table_details": details}
    finally:
        close_fn = getattr(client, "close", None)
        if callable(close_fn):
            close_fn()


def _unsupported_driver(ds_type: str, package: str) -> dict[str, Any]:
    return {
        "tables": [],
        "table_details": [],
        "warning": f"Metadata discovery for datasource type '{ds_type}' requires optional Python package: {package}.",
    }


def _discover_external_tables(ds_type: str, props: dict) -> dict[str, Any]:
    configured = _metadata_from_config(props, ds_type)
    normalized = _normalize_ds_type(ds_type)
    definition = resolve_datasource_definition(normalized)
    canonical = definition.canonical_type if definition is not None else normalized

    discoverers = {
        "postgresql": _discover_postgresql,
        "redshift": _discover_postgresql,
        "mysql": _discover_mysql,
        "clickhouse": _discover_clickhouse,
        "mssql": _discover_mssql,
        "trino": _discover_trino,
        "athena": _discover_athena,
        "oracle": _discover_oracle,
        "snowflake": _discover_snowflake,
        "bigquery": _discover_bigquery,
        "databricks": _discover_databricks,
    }
    discover = discoverers.get(canonical)
    if not discover:
        return configured or {
            "tables": [],
            "table_details": [],
            "warning": f"Metadata discovery for datasource type '{ds_type}' is not implemented yet. Provide 'tables' or 'table_details' in connection properties as a fallback.",
        }

    try:
        discovered = discover(props)
    except Exception as exc:
        LOGGER.warning("Metadata discovery failed for datasource type '%s': %s", ds_type, exc)
        reason = _safe_warning_from_exception(exc)
        if configured:
            configured["warning"] = (
                f"Live metadata discovery failed for datasource type '{ds_type}': {reason}. "
                "Using configured metadata fallback."
            )
            return configured
        return {
            "tables": [],
            "table_details": [],
            "warning": f"Live metadata discovery failed for datasource type '{ds_type}': {reason}.",
        }

    if not discovered.get("tables") and configured:
        configured["warning"] = f"Live metadata discovery returned no tables for datasource type '{ds_type}'. Using configured metadata fallback."
        return configured
    return discovered


def _list_tables_for_binding(
    ds_type: str,
    props: dict,
    project_id: int,
    binding_id: int,
) -> dict[str, Any]:
    normalized = _normalize_ds_type(ds_type)

    if normalized in ("duckdb", "sample"):
        init_sql = str(props.get("initSql") or props.get("init_sql") or "")
        path = _resolve_duckdb_path(props, project_id, binding_id)
        conn = duckdb.connect(path)
        try:
            _apply_init_sql_if_needed(conn, binding_id, init_sql)
            return _merge_sample_metadata(_load_duckdb_table_details(conn), props)
        finally:
            conn.close()

    return _discover_external_tables(ds_type, props)


# ── System-level ─────────────────────────────────────────────────────


@router.get("/system/datasources", response_model=dict)
def list_system_datasources(
    payload: dict = Depends(require_permission("datasources", "read")),
):
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            "SELECT * FROM metadata.datasources ORDER BY created_at DESC"
        ).fetchall()
    return {
        "data": [
            {
                "id": r[0],
                "name": r[1],
                "type": r[2],
                "properties": _redact_properties(_safe_json_loads(r[3], {})),
                "description": r[4],
                "created_at": str(r[5]) if r[5] else None,
                "updated_at": str(r[6]) if r[6] else None,
            }
            for r in rows
        ]
    }


@router.post("/system/datasources", response_model=dict)
def create_system_datasource(
    body: DatasourceCreate,
    payload: dict = Depends(require_permission("datasources", "create")),
):
    normalized_type = _normalize_ds_type(body.type)
    if normalized_type in ("duckdb", "sample"):
        init_sql = str((body.properties or {}).get("initSql") or (body.properties or {}).get("init_sql") or "")
        if init_sql.strip():
            _validate_init_sql(init_sql)
    with connection_lock():
        con = get_connection()
        max_id = con.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.datasources"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO metadata.datasources (id, name, type, properties_encrypted, description) VALUES (?, ?, ?, ?, ?)",
            [max_id, body.name, _normalize_ds_type(body.type), _encrypted_json_value(body.properties), body.description],
        )
        row = con.execute(
            "SELECT * FROM metadata.datasources WHERE id = ?", [max_id]
        ).fetchone()
    return {
        "data": {
            "id": row[0],
            "name": row[1],
            "type": row[2],
            "properties": _redact_properties(_safe_json_loads(row[3], {})),
            "description": row[4],
            "created_at": str(row[5]) if row[5] else None,
            "updated_at": str(row[6]) if row[6] else None,
        }
    }


@router.put("/system/datasources/{datasource_id}", response_model=dict)
def update_system_datasource(
    datasource_id: int,
    body: DatasourceUpdate,
    payload: dict = Depends(require_permission("datasources", "update")),
):
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT id, type FROM metadata.datasources WHERE id = ?", [datasource_id]
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Datasource not found")
        existing_type = existing[1]
        if body.properties is not None and _normalize_ds_type(existing_type) in ("duckdb", "sample"):
            init_sql = str((body.properties or {}).get("initSql") or (body.properties or {}).get("init_sql") or "")
            if init_sql.strip():
                _validate_init_sql(init_sql)
        sets = []
        params = []
        for col in ("name", "description",):
            val = getattr(body, col, None)
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)
        if body.properties is not None:
            sets.append("properties_encrypted = ?")
            params.append(_encrypted_json_value(body.properties))
        if sets:
            sets.append("updated_at = CURRENT_TIMESTAMP")
            params.append(datasource_id)
            con.execute(
                f"UPDATE metadata.datasources SET {', '.join(sets)} WHERE id = ?", params
            )
        row = con.execute(
            "SELECT * FROM metadata.datasources WHERE id = ?", [datasource_id]
        ).fetchone()
    return {
        "data": {
            "id": row[0],
            "name": row[1],
            "type": row[2],
            "properties": _redact_properties(_safe_json_loads(row[3], {})),
            "description": row[4],
            "created_at": str(row[5]) if row[5] else None,
            "updated_at": str(row[6]) if row[6] else None,
        }
    }


@router.delete("/system/datasources/{datasource_id}", response_model=dict)
def delete_system_datasource(
    datasource_id: int,
    payload: dict = Depends(require_permission("datasources", "delete")),
):
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT id FROM metadata.datasources WHERE id = ?", [datasource_id]
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Datasource not found")
        bound = con.execute(
            "SELECT COUNT(*) FROM metadata.project_datasources WHERE datasource_id = ?",
            [datasource_id],
        ).fetchone()[0]
        if bound:
            raise HTTPException(status_code=409, detail="Datasource is bound to one or more projects")
        con.execute("DELETE FROM metadata.datasources WHERE id = ?", [datasource_id])
    return {"data": {"success": True}}


@router.post("/system/datasources/{datasource_id}/test", response_model=dict)
def test_datasource(
    datasource_id: int,
    payload: dict = Depends(require_permission("datasources", "read")),
):
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT type, properties_encrypted FROM metadata.datasources WHERE id = ?",
            [datasource_id],
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Datasource not found")
    ds_type, props_encrypted = row
    props = _safe_json_loads(props_encrypted, {})
    if _normalize_ds_type(ds_type) in {"duckdb", "sample"}:
        return {
            "data": {
                "success": True,
                "latency_ms": 0,
                "error": None,
                "tables_discovered": len(_metadata_from_config(props, ds_type).get("tables", []) if _metadata_from_config(props, ds_type) else []),
                "warning": "DuckDB/sample table discovery requires binding the datasource to a real project.",
            }
        }
    result = _list_tables_for_binding(ds_type, props, project_id=-1, binding_id=-datasource_id)
    return {
        "data": {
            "success": True,
            "latency_ms": 0,
            "error": None,
            "tables_discovered": len(result.get("tables", [])),
            "warning": result.get("warning"),
        }
    }


# ── Project-level bindings ───────────────────────────────────────────


@router.get("/projects/{project_id:int}/datasources", response_model=dict)
def list_project_datasources(
    project_id: int,
    payload: dict = Depends(get_current_user),
):
    _require_datasource_permission(payload, "read", project_id)
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            """SELECT pd.id, pd.project_id, pd.datasource_id, pd.alias, pd.config_overrides,
                      d.name, d.type, d.description, d.properties_encrypted, pd.created_at
               FROM metadata.project_datasources pd
               JOIN metadata.datasources d ON d.id = pd.datasource_id
               WHERE pd.project_id = ?
               ORDER BY pd.created_at DESC""",
            [project_id],
        ).fetchall()
    return {
        "data": [
            {
                "id": r[0],
                "binding_id": r[0],
                "bindingId": r[0],
                "project_id": r[1],
                "datasource_id": r[2],
                "alias": r[3],
                "config_overrides": _redact_properties(decrypt_json(r[4], {}) if is_encrypted_value(r[4] or "") else _safe_json_loads(r[4] or "{}", {})),
                "datasource_name": r[5],
                "datasource_type": r[6],
                "description": r[7],
                "datasource": {
                    "id": r[2],
                    "name": r[5],
                    "type": r[6],
                },
                "properties": _redact_properties(_safe_json_loads(r[8], {})),
                "created_at": str(r[9]) if r[9] else None,
            }
            for r in rows
        ]
    }


@router.post("/projects/{project_id:int}/datasources", response_model=dict)
def bind_datasource(
    project_id: int,
    body: DatasourceBindingCreate,
    payload: dict = Depends(get_current_user),
):
    _require_datasource_permission(payload, "manage", project_id)
    with connection_lock():
        con = get_connection()
        project_exists = con.execute(
            "SELECT id FROM metadata.projects WHERE id = ?",
            [project_id],
        ).fetchone()
        if not project_exists:
            raise HTTPException(status_code=404, detail="Project not found")

        datasource_exists = con.execute(
            "SELECT id FROM metadata.datasources WHERE id = ?",
            [body.datasource_id],
        ).fetchone()
        if not datasource_exists:
            raise HTTPException(status_code=404, detail="Datasource not found")

        existing = con.execute(
            "SELECT id FROM metadata.project_datasources WHERE project_id = ? AND datasource_id = ?",
            [project_id, body.datasource_id],
        ).fetchone()
        if existing:
            raise HTTPException(
                status_code=409, detail="Datasource already bound to this project"
            )
        max_id = con.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.project_datasources"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO metadata.project_datasources (id, project_id, datasource_id, alias, config_overrides) VALUES (?, ?, ?, ?, ?)",
            [
                max_id,
                project_id,
                body.datasource_id,
                body.alias,
                _encrypted_json_value(body.config_overrides) if body.config_overrides else None,
            ],
        )
    return {
        "data": {
            "id": max_id,
            "bindingId": max_id,
            "binding_id": max_id,
            "project_id": project_id,
            "datasource_id": body.datasource_id,
            "alias": body.alias,
        }
    }


@router.delete("/projects/{project_id:int}/datasources/{binding_id:int}", response_model=dict)
def unbind_datasource(
    project_id: int,
    binding_id: int,
    payload: dict = Depends(get_current_user),
):
    _require_datasource_permission(payload, "manage", project_id)
    with connection_lock():
        con = get_connection()
        existing = con.execute(
            "SELECT id FROM metadata.project_datasources WHERE id = ? AND project_id = ?",
            [binding_id, project_id],
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Binding not found")
        refs = con.execute(
            "SELECT COUNT(*) FROM metadata.models WHERE project_id = ? AND source_binding_id = ?",
            [project_id, binding_id],
        ).fetchone()[0]
        if refs:
            raise HTTPException(status_code=409, detail="Datasource binding is used by one or more models")
        con.execute(
            "DELETE FROM metadata.model_datasource_mappings WHERE project_datasource_id = ?",
            [binding_id],
        )
        con.execute(
            "DELETE FROM metadata.project_datasources WHERE id = ?", [binding_id]
        )
    return {"data": {"success": True}}


@router.post("/projects/{project_id:int}/datasources/register", response_model=dict)
def register_and_bind_datasource(
    project_id: int,
    body: DatasourceCreate,
    payload: dict = Depends(get_current_user),
):
    _require_datasource_permission(payload, "create", project_id)
    normalized_type = _normalize_ds_type(body.type)
    if normalized_type in ("duckdb", "sample"):
        init_sql = str((body.properties or {}).get("initSql") or (body.properties or {}).get("init_sql") or "")
        if init_sql.strip():
            _validate_init_sql(init_sql)
    with connection_lock():
        con = get_connection()

        project_exists = con.execute(
            "SELECT id FROM metadata.projects WHERE id = ?",
            [project_id],
        ).fetchone()
        if not project_exists:
            raise HTTPException(status_code=404, detail="Project not found")

        # Reuse same-name binding as an upsert so retries or edits do not keep stale config.
        existing = con.execute(
            """SELECT pd.id, pd.datasource_id
               FROM metadata.project_datasources pd
               JOIN metadata.datasources d ON d.id = pd.datasource_id
               WHERE pd.project_id = ? AND d.name = ?""",
            [project_id, body.name],
        ).fetchone()

        if existing:
            binding_id, ds_id = existing
            encrypted_props = _encrypted_json_value(body.properties)
            if body.description is None:
                con.execute(
                    """
                    UPDATE metadata.datasources
                    SET type = ?, properties_encrypted = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    [normalized_type, encrypted_props, ds_id],
                )
            else:
                con.execute(
                    """
                    UPDATE metadata.datasources
                    SET type = ?, properties_encrypted = ?, description = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    [normalized_type, encrypted_props, body.description, ds_id],
                )
            con.execute(
                "UPDATE metadata.project_datasources SET alias = ? WHERE id = ?",
                [body.name, binding_id],
            )
            return {"data": {"id": ds_id, "bindingId": binding_id}}

        ds_id = con.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.datasources"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO metadata.datasources (id, name, type, properties_encrypted, description) VALUES (?, ?, ?, ?, ?)",
            [ds_id, body.name, _normalize_ds_type(body.type), _encrypted_json_value(body.properties), body.description],
        )

        binding_id = con.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.project_datasources"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO metadata.project_datasources (id, project_id, datasource_id, alias) VALUES (?, ?, ?, ?)",
            [binding_id, project_id, ds_id, body.name],
        )

    return {"data": {"id": ds_id, "bindingId": binding_id}}


@router.get(
    "/projects/{project_id:int}/datasources/{binding_id:int}/tables", response_model=dict
)
def list_tables(
    project_id: int,
    binding_id: int,
    payload: dict = Depends(get_current_user),
):
    _require_datasource_permission(payload, "read", project_id)
    with connection_lock():
        con = get_connection()
        row = con.execute(
            """SELECT pd.datasource_id, d.type, d.properties_encrypted
               FROM metadata.project_datasources pd
               JOIN metadata.datasources d ON d.id = pd.datasource_id
               WHERE pd.id = ? AND pd.project_id = ?""",
            [binding_id, project_id],
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Binding not found")
    _, ds_type, props_encrypted = row
    props = _safe_json_loads(props_encrypted, {})
    try:
        result = _list_tables_for_binding(ds_type, props, project_id, binding_id)
    except RuntimeError as exc:
        if "Init SQL" in str(exc):
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        raise
    return {"data": result}


@router.post(
    "/projects/{project_id:int}/datasources/{binding_id:int}/sync", response_model=dict
)
def sync_tables(
    project_id: int,
    binding_id: int,
    payload: dict = Depends(get_current_user),
):
    _require_datasource_permission(payload, "update", project_id)
    with connection_lock():
        con = get_connection()
        row = con.execute(
            """SELECT pd.datasource_id, d.type, d.properties_encrypted
               FROM metadata.project_datasources pd
               JOIN metadata.datasources d ON d.id = pd.datasource_id
               WHERE pd.id = ? AND pd.project_id = ?""",
            [binding_id, project_id],
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Binding not found")
    _, ds_type, props_encrypted = row
    props = _safe_json_loads(props_encrypted, {})
    discovered = _list_tables_for_binding(ds_type, props, project_id, binding_id)
    return {
        "data": {
            "tables_discovered": len(discovered.get("tables", [])),
            "tables_removed": 0,
            "warning": discovered.get("warning"),
        }
    }
