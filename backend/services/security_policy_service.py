from __future__ import annotations

import json
import math
from typing import Any, Optional

import sqlglot
from sqlglot import exp

from db import connection_lock, get_connection
from routers.auth import get_effective_permissions
from services.sql_guard import validate_read_only_sql

ALLOWED_OPERATORS = {"=", "!=", ">", ">=", "<", "<=", "IN", "NOT IN", "LIKE", "ILIKE"}
ALLOWED_ACCESS_TYPES = {"HIDE", "MASK"}


def normalize_operator(operator: str) -> str:
    normalized = (operator or "=").strip().upper()
    if normalized not in ALLOWED_OPERATORS:
        raise ValueError(f"Unsupported RLS operator: {operator}")
    return normalized


def normalize_access_type(access_type: str) -> str:
    normalized = (access_type or "HIDE").strip().upper()
    if normalized not in ALLOWED_ACCESS_TYPES:
        raise ValueError(f"Unsupported CLS access type: {access_type}")
    return normalized


def _safe_identifier(value: str) -> str:
    if not value:
        raise ValueError(f"Unsafe identifier: {value}")
    for part in value.split("."):
        if not part or not part.replace("_", "").isalnum():
            raise ValueError(f"Unsafe identifier: {value}")
        if len(part) > 128:
            raise ValueError(f"Identifier too long (max 128 chars): {part[:20]}...")
    return value


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if math.isinf(value) or math.isnan(value):
            raise ValueError(f"Unsupported float value in security policy: {value}")
        return str(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''").replace("\x00", "") + "'"


def _literal_expression(value: Any) -> exp.Expression:
    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, int) and not isinstance(value, bool):
        return exp.Literal.number(value)
    if isinstance(value, float):
        return exp.Literal.number(value)
    return exp.Literal.string(str(value))


def _parse_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _user_attribute_value(user: dict, attribute: Optional[str]) -> Any:
    if not attribute:
        return None
    if attribute in user:
        return user.get(attribute)
    profile = user.get("profile")
    if isinstance(profile, dict):
        return profile.get(attribute)
    return None


def build_rls_condition(policy: dict, user: dict) -> str:
    column = _safe_identifier(policy["column_name"])
    operator = normalize_operator(policy["operator"])
    value_source = (policy.get("value_source") or "literal").lower()
    raw_value = _user_attribute_value(user, policy.get("user_attribute")) if value_source == "user_attribute" else policy.get("value")
    value = _parse_jsonish(raw_value)

    if operator in {"IN", "NOT IN"}:
        values = value if isinstance(value, list) else [value] if value is not None else []
        if not values:
            return "1=0" if operator == "IN" else "1=1"
        return f"{column} {operator} ({', '.join(_sql_literal(v) for v in values)})"
    if value is None:
        if operator == "=":
            return f"{column} IS NULL"
        if operator == "!=":
            return f"{column} IS NOT NULL"
        return "1=0"
    return f"{column} {operator} {_sql_literal(value)}"


def build_rls_expression(policy: dict, user: dict) -> exp.Expression:
    column = str(_safe_identifier(policy["column_name"])).split(".")[-1]
    operator = normalize_operator(policy["operator"])
    value_source = (policy.get("value_source") or "literal").lower()
    raw_value = _user_attribute_value(user, policy.get("user_attribute")) if value_source == "user_attribute" else policy.get("value")
    value = _parse_jsonish(raw_value)
    left = exp.column(column)
    if value is None:
        if operator == "=":
            return exp.Is(this=left, expression=exp.Null())
        if operator == "!=":
            return exp.Not(this=exp.Is(this=left, expression=exp.Null()))
        if operator == "NOT IN":
            return exp.true()
        return exp.false()
    if isinstance(value, list) and len(value) == 0 and operator in {"IN", "NOT IN"}:
        return exp.false() if operator == "IN" else exp.true()
    if operator == "=":
        return exp.EQ(this=left, expression=_literal_expression(value))
    if operator == "!=":
        return exp.NEQ(this=left, expression=_literal_expression(value))
    if operator == ">":
        return exp.GT(this=left, expression=_literal_expression(value))
    if operator == ">=":
        return exp.GTE(this=left, expression=_literal_expression(value))
    if operator == "<":
        return exp.LT(this=left, expression=_literal_expression(value))
    if operator == "<=":
        return exp.LTE(this=left, expression=_literal_expression(value))
    if operator in {"LIKE", "ILIKE"}:
        expression = exp.Like(this=left, expression=_literal_expression(value))
        return expression if operator == "LIKE" else exp.ILike(this=left, expression=_literal_expression(value))
    values = value if isinstance(value, list) else [value]
    in_exp = exp.In(this=left, expressions=[_literal_expression(item) for item in values])
    return exp.Not(this=in_exp) if operator == "NOT IN" else in_exp


def get_current_user_record(user_id: int) -> dict:
    with connection_lock():
        con = get_connection()
        row = con.execute(
            "SELECT id, username, display_name, email, status, default_project_id FROM metadata.users WHERE id = ?",
            [user_id],
        ).fetchone()
        if not row:
            return {"id": user_id}
        return {
            "id": row[0],
            "username": row[1],
            "display_name": row[2],
            "email": row[3],
            "status": row[4],
            "default_project_id": row[5],
        }


def _role_ids_for_user(user_id: int, project_id: int) -> list[int]:
    with connection_lock():
        con = get_connection()
        rows = con.execute(
            "SELECT role_id FROM metadata.user_roles "
            "WHERE user_id = ? "
            "AND (project_id IS NULL OR project_id = ?) "
            "AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)",
            [user_id, project_id],
        ).fetchall()
        return [row[0] for row in rows]


def get_effective_security_policies(user_id: int, project_id: int) -> dict[str, list[dict]]:
    role_ids = _role_ids_for_user(user_id, project_id)
    if not role_ids:
        return {"row_policies": [], "column_policies": []}
    placeholders = ",".join("?" for _ in role_ids)
    with connection_lock():
        con = get_connection()
        rls_rows = con.execute(
        f"""
        SELECT id, project_id, role_id, model_name, column_name, operator, value, value_source, user_attribute,
               filter_expression, description, is_enabled
        FROM metadata.row_level_security_policies
        WHERE project_id = ? AND role_id IN ({placeholders}) AND COALESCE(is_enabled, true) = true
        ORDER BY id
        """,
        [project_id, *role_ids],
    ).fetchall()
        cls_rows = con.execute(
        f"""
        SELECT id, project_id, role_id, model_name, column_name, access_type, mask_with, is_enabled
        FROM metadata.column_level_security_policies
        WHERE project_id = ? AND role_id IN ({placeholders}) AND COALESCE(is_enabled, true) = true
        ORDER BY id
        """,
        [project_id, *role_ids],
    ).fetchall()
    return {
        "row_policies": [
            {
                "id": row[0],
                "project_id": row[1],
                "role_id": row[2],
                "model_name": row[3],
                "column_name": row[4],
                "operator": row[5] or "=",
                "value": row[6],
                "value_source": row[7] or "literal",
                "user_attribute": row[8],
                "filter_expression": row[9],
                "description": row[10],
                "is_enabled": bool(row[11]),
            }
            for row in rls_rows
        ],
        "column_policies": [
            {
                "id": row[0],
                "project_id": row[1],
                "role_id": row[2],
                "model_name": row[3],
                "column_name": row[4],
                "access_type": row[5],
                "mask_with": row[6],
                "is_enabled": bool(row[7]),
            }
            for row in cls_rows
        ],
    }


def _tables_in_sql(sql: str) -> set[str]:
    try:
        parsed = sqlglot.parse_one(sql)
    except Exception:
        return set()
    return {table.name for table in parsed.find_all(exp.Table) if table.name}


def _cte_names(parsed: exp.Expression, *, case_sensitive: bool = False) -> set[str]:
    names: set[str] = set()
    for cte in parsed.find_all(exp.CTE):
        alias = str(cte.alias or "").strip()
        if not alias:
            continue
        names.add(alias if case_sensitive else alias.lower())
    return names


def _table_aliases(parsed: exp.Expression, cte_names: set[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for table in parsed.find_all(exp.Table):
        name = str(table.name or "")
        if not name or name.lower() in cte_names:
            continue
        aliases[str(table.alias_or_name or name).lower()] = name
    return aliases


def _normalize_bool_setting(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 1:
            return True
        if value == 0:
            return False
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        lowered = text.lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        try:
            parsed = json.loads(text)
        except Exception:
            return default
        return _normalize_bool_setting(parsed, default)
    return default


def _model_ref_case_sensitive_enabled(default: bool = True) -> bool:
    try:
        with connection_lock():
            con = get_connection()
            row = con.execute(
                "SELECT value FROM metadata.settings WHERE key = 'router_model_ref_case_sensitive'"
            ).fetchone()
    except Exception:
        return default
    if not row:
        return default
    return _normalize_bool_setting(row[0], default)


def _normalize_identifier_token(value: Any, *, case_sensitive: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', '`'}:
        quote = text[0]
        token = text[1:-1].replace(quote * 2, quote).strip()
    elif len(text) >= 2 and text[0] == "[" and text[-1] == "]":
        token = text[1:-1].replace("]]", "]").strip()
    else:
        token = text
    if not token:
        return ""
    return token if case_sensitive else token.lower()


def _model_reference_parts(reference: str, *, case_sensitive: bool = False) -> tuple[str, ...]:
    ref = str(reference or "").strip()
    if not ref:
        return ()
    try:
        parsed = sqlglot.parse_one(f"SELECT 1 FROM {ref}", read="duckdb")
        table = next(parsed.find_all(exp.Table), None)
        if table is not None and str(table.name or "").strip():
            parts: list[str] = []
            catalog = _normalize_identifier_token(table.catalog, case_sensitive=case_sensitive)
            db = _normalize_identifier_token(table.db, case_sensitive=case_sensitive)
            name = _normalize_identifier_token(table.name, case_sensitive=case_sensitive)
            if catalog:
                parts.append(catalog)
            if db:
                parts.append(db)
            if name:
                parts.append(name)
            if parts:
                return tuple(parts)
    except Exception:
        pass
    parts = [_normalize_identifier_token(part, case_sensitive=case_sensitive) for part in ref.split(".")]
    return tuple(part for part in parts if part)


def _register_unique_lookup(
    mapping: dict[str, str | None],
    key: str,
    model_name: str,
    *,
    case_sensitive: bool = False,
) -> None:
    normalized_key = str(key or "").strip()
    if not case_sensitive:
        normalized_key = normalized_key.lower()
    candidate_model = str(model_name or "").strip()
    if not normalized_key or not candidate_model:
        return
    existing = mapping.get(normalized_key)
    if existing is None and normalized_key in mapping:
        return
    if existing:
        existing_token = str(existing).strip()
        if (existing_token if case_sensitive else existing_token.lower()) != (
            candidate_model if case_sensitive else candidate_model.lower()
        ):
            mapping[normalized_key] = None
            return
    mapping[normalized_key] = candidate_model


def _canonicalize_sql_model_refs(sql: str, project_id: int, *, case_sensitive: bool) -> str:
    def _normalize_lookup_key(value: str) -> str:
        token = str(value or "").strip()
        return token if case_sensitive else token.lower()

    try:
        with connection_lock():
            con = get_connection()
            rows = con.execute(
                "SELECT name, table_reference FROM metadata.models WHERE project_id = ?",
                [project_id],
            ).fetchall()
    except Exception:
        return sql
    if not rows:
        return sql

    semantic_names: set[str] = set()
    qualified_lookup: dict[str, str | None] = {}
    unqualified_lookup: dict[str, str | None] = {}

    for row in rows:
        model_name_raw = str(row[0] or "").strip()
        model_name = _normalize_identifier_token(model_name_raw, case_sensitive=case_sensitive)
        table_reference = str(row[1] or row[0] or "")
        if not model_name or not model_name_raw:
            continue
        semantic_names.add(model_name)
        parts = _model_reference_parts(table_reference, case_sensitive=case_sensitive)
        if not parts:
            continue
        _register_unique_lookup(
            qualified_lookup,
            ".".join(parts),
            model_name_raw,
            case_sensitive=case_sensitive,
        )
        if len(parts) >= 2:
            _register_unique_lookup(
                qualified_lookup,
                ".".join(parts[-2:]),
                model_name_raw,
                case_sensitive=case_sensitive,
            )
        _register_unique_lookup(
            unqualified_lookup,
            parts[-1],
            model_name_raw,
            case_sensitive=case_sensitive,
        )

    qualified = {k: v for k, v in qualified_lookup.items() if v}
    unqualified = {k: v for k, v in unqualified_lookup.items() if v}
    if not qualified and not unqualified:
        return sql

    try:
        parsed = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return sql

    cte_names = {
        _normalize_identifier_token(cte.alias, case_sensitive=case_sensitive)
        for cte in parsed.find_all(exp.CTE)
        if str(cte.alias or "").strip()
    }
    changed = False
    for table in parsed.find_all(exp.Table):
        table_name = _normalize_identifier_token(table.name, case_sensitive=case_sensitive)
        if not table_name or table_name in cte_names:
            continue

        db = _normalize_identifier_token(table.db, case_sensitive=case_sensitive)
        catalog = _normalize_identifier_token(table.catalog, case_sensitive=case_sensitive)
        if not catalog and not db and table_name in semantic_names:
            continue
        has_qualifier = bool(catalog or db)

        candidates: list[str] = []
        if catalog and db:
            candidates.append(f"{catalog}.{db}.{table_name}")
        if catalog and not db:
            candidates.append(f"{catalog}.{table_name}")
        if db:
            candidates.append(f"{db}.{table_name}")
        candidates.append(table_name)

        mapped_model = ""
        for key in candidates:
            mapped_model = str(qualified.get(_normalize_lookup_key(key)) or "")
            if mapped_model:
                break
        if not mapped_model and not has_qualifier:
            mapped_model = str(unqualified.get(_normalize_lookup_key(table_name)) or "")
        if not mapped_model:
            continue

        table.set("this", exp.to_identifier(mapped_model))
        table.set("db", None)
        table.set("catalog", None)
        changed = True

    if not changed:
        return sql
    try:
        return parsed.sql(dialect="duckdb")
    except Exception:
        return sql


def _is_protected_column_reference(column: exp.Column, policy: dict, aliases: dict[str, str]) -> bool:
    protected = str(policy["column_name"] or "").split(".")[-1].lower()
    if str(column.name or "").lower() != protected:
        return False
    table = str(column.table or "").lower()
    if table:
        return aliases.get(table, "").lower() == str(policy["model_name"]).lower()
    # Unqualified protected column references are ambiguous in joins and unsafe to allow.
    return any(model.lower() == str(policy["model_name"]).lower() for model in aliases.values())


def _enforce_cls_reference_rules(parsed: exp.Expression, cls_policies: list[dict], aliases: dict[str, str]) -> None:
    hidden = [p for p in cls_policies if normalize_access_type(p["access_type"]) == "HIDE"]
    if not hidden:
        return
    for column in parsed.find_all(exp.Column):
        for policy in hidden:
            if _is_protected_column_reference(column, policy, aliases):
                raise ValueError(
                    f"Column '{policy['column_name']}' is hidden by column-level security and cannot be referenced."
                )


def _secured_table_subquery(table: exp.Table, conditions: list[exp.Expression]) -> exp.Subquery:
    table_without_alias = table.copy()
    table_without_alias.set("alias", None)
    secured_select = exp.select("*").from_(table_without_alias)
    combined = None
    for condition in conditions:
        condition_exp = condition.copy()
        combined = condition_exp if combined is None else exp.and_(combined, condition_exp)
    if combined is not None:
        secured_select.set("where", exp.Where(this=combined))
    alias = str(table.alias_or_name or table.name)
    return exp.Subquery(this=secured_select, alias=exp.TableAlias(this=exp.to_identifier(alias)))


def _apply_rls_to_table_refs(
    parsed: exp.Expression,
    conditions_by_model: dict[str, list[exp.Expression]],
    cte_names: set[str],
    *,
    case_sensitive: bool = False,
) -> str:
    if not conditions_by_model:
        return parsed.sql(dialect="duckdb")

    def _token(value: Any) -> str:
        token = str(value or "").strip()
        return token if case_sensitive else token.lower()

    def rewrite(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Table):
            return node
        model_name = _token(node.name)
        if not model_name or model_name in cte_names:
            return node
        conditions = conditions_by_model.get(model_name)
        if not conditions:
            return node
        return _secured_table_subquery(node, conditions)

    return parsed.transform(rewrite).sql(dialect="duckdb")


def plan_secured_sql(sql: str, project_id: int, user_id: int) -> dict:
    if project_id <= 0:
        raise ValueError("A real project is required")
    model_ref_case_sensitive = _model_ref_case_sensitive_enabled(default=True)

    def _model_token(value: Any) -> str:
        token = str(value or "").strip()
        return token if model_ref_case_sensitive else token.lower()

    normalized_sql = validate_read_only_sql(sql)
    normalized_sql = _canonicalize_sql_model_refs(
        normalized_sql,
        project_id,
        case_sensitive=model_ref_case_sensitive,
    )
    user = get_current_user_record(user_id)
    policies = get_effective_security_policies(user_id, project_id)
    try:
        parsed = sqlglot.parse_one(normalized_sql, read="duckdb")
    except Exception as exc:
        raise ValueError("SQL could not be parsed safely.") from exc
    cte_name_tokens = _cte_names(parsed, case_sensitive=model_ref_case_sensitive)
    aliases = _table_aliases(parsed, {name.lower() for name in cte_name_tokens})
    table_names = {
        table.name
        for table in parsed.find_all(exp.Table)
        if table.name and _model_token(table.name) not in cte_name_tokens
    }
    table_name_tokens = {_model_token(name) for name in table_names if str(name or "").strip()}
    permissions = get_effective_permissions(user_id, project_id)
    can_manage_security = any(
        p["resource"] == "security_policies" and p["action"] in {"manage", "read"}
        for p in permissions
    )

    applied_rls = []
    conditions_by_model: dict[str, list[exp.Expression]] = {}
    for policy in policies["row_policies"]:
        if _model_token(policy["model_name"]) not in table_name_tokens:
            continue
        condition = build_rls_condition(policy, user)
        conditions_by_model.setdefault(_model_token(policy["model_name"]), []).append(build_rls_expression(policy, user))
        applied_rls.append({"id": policy["id"], "model_name": policy["model_name"], "condition": condition})

    planned_sql = normalized_sql
    if conditions_by_model:
        planned_sql = _apply_rls_to_table_refs(
            parsed,
            conditions_by_model,
            cte_name_tokens,
            case_sensitive=model_ref_case_sensitive,
        )

    applied_cls = [
        {
            "id": policy["id"],
            "model_name": policy["model_name"],
            "column_name": policy["column_name"],
            "access_type": normalize_access_type(policy["access_type"]),
            "mask_with": policy.get("mask_with"),
        }
        for policy in policies["column_policies"]
        if _model_token(policy["model_name"]) in table_name_tokens
    ]
    _enforce_cls_reference_rules(parsed, applied_cls, aliases)

    masked_in_sql = [
        p for p in applied_cls
        if normalize_access_type(p["access_type"]) == "MASK" and _model_token(p["model_name"]) in table_name_tokens
    ]
    if masked_in_sql:
        planned_sql = _apply_cls_mask_to_sql(
            parsed if not conditions_by_model else sqlglot.parse_one(planned_sql, read="duckdb"),
            masked_in_sql,
            aliases,
            {name.lower() for name in cte_name_tokens},
        )

    model_columns_map: dict[str, list[str]] = {}
    try:
        with connection_lock():
            con = get_connection()
            for tname in table_names:
                try:
                    if model_ref_case_sensitive:
                        rows = con.execute(
                            "SELECT name FROM metadata.model_fields WHERE model_id = (SELECT id FROM metadata.models WHERE name = ?) ORDER BY name",
                            [tname],
                        ).fetchall()
                    else:
                        rows = con.execute(
                            "SELECT name FROM metadata.model_fields WHERE model_id = (SELECT id FROM metadata.models WHERE lower(name) = lower(?) LIMIT 1) ORDER BY name",
                            [tname],
                        ).fetchall()
                    model_columns_map[tname.lower()] = [str(r[0]) for r in rows]
                except Exception:
                    model_columns_map[tname.lower()] = []
    except Exception:
        pass

    mask_expressions = detect_masked_columns_in_expressions(planned_sql, applied_cls, model_columns_map)
    column_lineage = compute_column_lineage(planned_sql, model_columns_map)

    return {
        "planned_sql": planned_sql,
        "model_refs": sorted(table_names),
        "model_ref_case_sensitive": model_ref_case_sensitive,
        "security": {
            "rls": applied_rls,
            "cls": applied_cls,
            "visible_to_user": can_manage_security,
            "mask_in_expressions": mask_expressions,
            "column_lineage": {k: sorted(v) for k, v in column_lineage.items()},
        },
    }


def _apply_cls_mask_to_sql(parsed: exp.Expression, masked_policies: list[dict], aliases: dict[str, str], cte_names: set[str]) -> str:
    mask_map: dict[str, str] = {}
    model_map: dict[str, str] = {}
    for policy in masked_policies:
        col_name = str(policy["column_name"]).lower()
        mask_value = str(policy.get("mask_with") or "***")
        escaped = mask_value.replace("'", "''")
        mask_map[col_name] = f"'{escaped}'"
        model_map[col_name] = str(policy["model_name"]).lower()

    def _is_masked_column(node: exp.Expression) -> bool:
        if not isinstance(node, exp.Column):
            return False
        col_name = str(node.name or "").lower()
        if col_name not in mask_map:
            return False
        table = str(node.table or "").lower()
        if table:
            resolved = aliases.get(table, "").lower()
            return resolved == model_map.get(col_name, "")
        for _p in masked_policies:
            if str(_p["column_name"]).lower() == col_name:
                matched_model = str(_p["model_name"]).lower()
                if not aliases or all(v.lower() == matched_model for v in aliases.values() if v):
                    return True
        return False

    def rewrite(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and _is_masked_column(node):
            col_name = str(node.name or "").lower()
            return exp.Literal.string(mask_map[col_name].strip("'").replace("''", "'"))
        if isinstance(node, (exp.Alias,)):
            return node
        if isinstance(node, (exp.Select, exp.From, exp.Where, exp.Group, exp.Order, exp.Having, exp.Join, exp.Subquery)):
            return node
        for col_node in node.find_all(exp.Column):
            if _is_masked_column(col_node):
                new_node = node.copy()
                new_node = new_node.transform(lambda n: (
                    exp.Literal.string(mask_map[str(n.name or "").lower()].strip("'").replace("''", "'"))
                    if isinstance(n, exp.Column) and _is_masked_column(n) else n
                ))
                return new_node
        return node

    return parsed.transform(rewrite).sql(dialect="duckdb")


def apply_cls_to_rows(rows: list[dict], cls_policies: list[dict]) -> list[dict]:
    if not cls_policies:
        return rows
    hidden_columns = {p["column_name"].split(".")[-1] if "." in p["column_name"] else p["column_name"] for p in cls_policies if normalize_access_type(p["access_type"]) == "HIDE"}
    masked = {(p["column_name"].split(".")[-1] if "." in p["column_name"] else p["column_name"]): p.get("mask_with") or "***" for p in cls_policies if normalize_access_type(p["access_type"]) == "MASK"}
    result = []
    for row in rows:
        next_row = {k: v for k, v in row.items() if k not in hidden_columns}
        for column, mask in masked.items():
            if column in next_row:
                next_row[column] = mask
        result.append(next_row)
    return result


def compute_column_lineage(sql: str, model_columns: dict[str, list[str]]) -> dict[str, set[str]]:
    try:
        parsed = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return {}
    if not isinstance(parsed, exp.Select):
        return {}
    lineage: dict[str, set[str]] = {}
    for expr in parsed.expressions:
        output_name = str(expr.alias or expr.name) if hasattr(expr, 'alias') else str(getattr(expr, 'name', ''))
        if not output_name:
            expr_sql = expr.sql(dialect="duckdb")
            if len(expr_sql) > 64:
                output_name = f"expr_{hash(expr_sql) % 10000}"
            else:
                output_name = expr_sql
        source_cols: set[str] = set()
        for col in expr.find_all(exp.Column):
            table = str(col.table or "").lower()
            name = str(col.name or "").lower()
            if table:
                model_cols = model_columns.get(table, [])
                if name in [c.lower() for c in model_cols] or not model_cols:
                    source_cols.add(f"{table}.{name}" if table else name)
            else:
                for model_name, cols in model_columns.items():
                    if name in [c.lower() for c in cols]:
                        source_cols.add(f"{model_name}.{name}")
                        break
                else:
                    source_cols.add(name)
        lineage[output_name] = source_cols
    return lineage


def detect_masked_columns_in_expressions(
    sql: str,
    cls_policies: list[dict],
    model_columns: dict[str, list[str]] | None = None,
) -> list[dict]:
    masked_cols = {p["column_name"].lower(): p for p in cls_policies if normalize_access_type(p["access_type"]) == "MASK"}
    if not masked_cols:
        return []
    try:
        parsed = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return []
    affected: list[dict] = []
    for column in parsed.find_all(exp.Column):
        col_name = str(column.name or "").lower()
        if col_name not in masked_cols:
            continue
        parent = column.parent
        if parent and not isinstance(parent, (exp.Select, exp.From, exp.Where)):
            table = str(column.table or "").lower()
            policy = masked_cols[col_name]
            affected.append({
                "column": col_name,
                "table": table,
                "policy_id": policy.get("id"),
                "mask_with": policy.get("mask_with") or "***",
                "in_expression": parent.sql(dialect="duckdb"),
                "expression_type": type(parent).__name__,
            })
    return affected
