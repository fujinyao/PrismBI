from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

FORBIDDEN_KEYWORDS = re.compile(
    r"\b(attach|detach|pragma|install|vacuum|checkpoint|force|grant|revoke)\b",
    re.IGNORECASE,
)

READ_ONLY_ROOTS = (exp.Select, exp.Union, exp.Except, exp.Intersect)
FORBIDDEN_EXPRESSIONS = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,
)
FORBIDDEN_DUCKDB_FUNCTIONS = {
    "read_csv",
    "read_csv_auto",
    "read_json",
    "read_json_auto",
    "read_parquet",
    "read_ndjson",
    "read_text",
    "read_blob",
    "read_xlsx",
    "httpfs_scan",
    "postgres_scan",
    "mysql_scan",
    "sqlite_scan",
    "parquet_scan",
    "glob",
    "query",
}


def _has_semicolon_outside_strings(sql: str) -> bool:
    in_string = False
    quote_char = None
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_string:
            if ch == quote_char:
                if i + 1 < len(sql) and sql[i + 1] == quote_char:
                    i += 2
                    continue
                in_string = False
                quote_char = None
        else:
            if ch in ("'", '"'):
                in_string = True
                quote_char = ch
            elif ch == ';':
                return True
        i += 1
    return False


def _split_sql_statements(sql: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    in_string = False
    quote_char = None
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_string:
            current.append(ch)
            if ch == quote_char:
                if i + 1 < len(sql) and sql[i + 1] == quote_char:
                    current.append(sql[i + 1])
                    i += 2
                    continue
                in_string = False
                quote_char = None
            i += 1
            continue
        if ch in ("'", '"'):
            in_string = True
            quote_char = ch
            current.append(ch)
            i += 1
            continue
        if ch == ';':
            piece = "".join(current).strip()
            if piece:
                parts.append(piece)
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_sql_statements(sql: str, dialect: str) -> list[exp.Expression]:
    try:
        return sqlglot.parse(sql, read=dialect)
    except Exception:
        try:
            return sqlglot.parse(sql, read="duckdb")
        except Exception as exc:
            raise ValueError("SQL could not be parsed safely.") from exc


def normalize_read_only_sql(sql: str, dialect: str = "duckdb") -> str:
    normalized = (sql or "").strip()
    if not normalized:
        raise ValueError("SQL is required.")
    has_inner_semicolon = _has_semicolon_outside_strings(normalized.rstrip(";"))
    if not has_inner_semicolon:
        statements = _parse_sql_statements(normalized, dialect)
        if len(statements) != 1 or statements[0] is None:
            raise ValueError("Only a single read-only query can be executed.")
        _validate_expression(statements[0])
        return statements[0].sql(dialect="duckdb")

    fragments = _split_sql_statements(normalized)
    if not fragments:
        raise ValueError("SQL could not be parsed safely.")

    for fragment in fragments:
        try:
            fragment_statements = _parse_sql_statements(fragment, dialect)
        except ValueError:
            continue
        if len(fragment_statements) != 1 or fragment_statements[0] is None:
            continue
        _validate_expression(fragment_statements[0])

    recombined_sql = " ".join(fragment.strip() for fragment in fragments if fragment.strip())
    if not recombined_sql:
        raise ValueError("SQL could not be parsed safely.")

    recombined_statements = _parse_sql_statements(recombined_sql, dialect)
    if len(recombined_statements) != 1 or recombined_statements[0] is None:
        raise ValueError("Only a single read-only query can be executed.")
    _validate_expression(recombined_statements[0])
    return recombined_statements[0].sql(dialect="duckdb")


def validate_read_only_sql(sql: str, dialect: str = "duckdb") -> str:
    return normalize_read_only_sql(sql, dialect=dialect)


def _validate_expression(root: exp.Expression) -> None:
    if not _is_read_only_root(root):
        raise ValueError("Only read-only SELECT/WITH queries can be executed.")
    for node in root.walk():
        if isinstance(node, FORBIDDEN_EXPRESSIONS):
            raise ValueError("Only read-only SELECT/WITH queries can be executed.")
        if isinstance(node, exp.Paren):
            inner = node.this
            if inner is not None:
                _validate_expression(inner)
            continue
        function_name = _function_name(node)
        if function_name and function_name in FORBIDDEN_DUCKDB_FUNCTIONS:
            raise ValueError(f"DuckDB function '{function_name}' is not allowed in user SQL.")


def _is_read_only_root(root: exp.Expression) -> bool:
    if isinstance(root, READ_ONLY_ROOTS):
        return True
    if isinstance(root, exp.With):
        expression = root.this
        return isinstance(expression, READ_ONLY_ROOTS) if expression is not None else False
    if isinstance(root, exp.Paren):
        inner = root.this
        return _is_read_only_root(inner) if inner is not None else False
    return False


def _function_name(node: exp.Expression) -> str | None:
    if isinstance(node, exp.Anonymous):
        return str(node.name or "").lower()
    if isinstance(node, exp.Func):
        return str(node.sql_name() or "").lower()
    return None
