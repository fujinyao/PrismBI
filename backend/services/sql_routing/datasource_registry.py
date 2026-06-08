from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Iterable, Protocol


class DataSourceAdapter(Protocol):
    ds_type: str
    aliases: tuple[str, ...]

    def discover(self, props: dict, *, project_id: int, binding_id: int) -> dict:
        ...

    def execute(self, props: dict, sql: str, *, row_limit: int, project_id: int | None) -> dict:
        ...

    def transpile(self, sql_duckdb: str) -> tuple[str, str | None]:
        ...

    def apply_limit(self, sql: str, limit: int) -> str:
        ...

    def health(self, conn: object) -> bool:
        ...


@dataclass(frozen=True, slots=True)
class DataSourceAdapterDefinition:
    canonical_type: str
    aliases: tuple[str, ...]
    dialect: str
    limit_style: str = "standard"
    supports_pooling: bool = False


class DataSourceAdapterRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, DataSourceAdapterDefinition] = {}
        self._alias_to_canonical: dict[str, str] = {}
        self._lock = RLock()

    def register(self, definition: DataSourceAdapterDefinition) -> None:
        canonical = str(definition.canonical_type or "").strip().lower()
        if not canonical:
            raise ValueError("canonical_type is required")
        aliases = tuple(str(alias or "").strip().lower() for alias in definition.aliases if str(alias or "").strip())
        normalized = DataSourceAdapterDefinition(
            canonical_type=canonical,
            aliases=aliases,
            dialect=str(definition.dialect or "duckdb").strip().lower() or "duckdb",
            limit_style=str(definition.limit_style or "standard").strip().lower() or "standard",
            supports_pooling=bool(definition.supports_pooling),
        )
        with self._lock:
            self._definitions[canonical] = normalized
            self._alias_to_canonical[canonical] = canonical
            for alias in aliases:
                self._alias_to_canonical[alias] = canonical

    def normalize(self, ds_type: str | None) -> str:
        key = str(ds_type or "").strip().lower()
        if not key:
            return ""
        with self._lock:
            return self._alias_to_canonical.get(key, key)

    def resolve(self, ds_type: str | None) -> DataSourceAdapterDefinition | None:
        canonical = self.normalize(ds_type)
        if not canonical:
            return None
        with self._lock:
            return self._definitions.get(canonical)

    def all_canonical_types(self) -> list[str]:
        with self._lock:
            return sorted(self._definitions.keys())

    def all_supported_types(self, include_aliases: bool = False) -> list[str]:
        with self._lock:
            if include_aliases:
                return sorted(self._alias_to_canonical.keys())
            return sorted(self._definitions.keys())


DEFAULT_DATASOURCE_REGISTRY = DataSourceAdapterRegistry()


def _register_defaults(registry: DataSourceAdapterRegistry) -> None:
    definitions: Iterable[DataSourceAdapterDefinition] = (
        DataSourceAdapterDefinition("duckdb", tuple(), dialect="duckdb", limit_style="standard"),
        DataSourceAdapterDefinition("sample", tuple(), dialect="duckdb", limit_style="standard"),
        DataSourceAdapterDefinition(
            "postgresql",
            aliases=("postgres",),
            dialect="postgres",
            limit_style="standard",
            supports_pooling=True,
        ),
        DataSourceAdapterDefinition(
            "redshift",
            aliases=tuple(),
            dialect="redshift",
            limit_style="standard",
            supports_pooling=True,
        ),
        DataSourceAdapterDefinition(
            "mysql",
            aliases=("mariadb",),
            dialect="mysql",
            limit_style="standard",
            supports_pooling=True,
        ),
        DataSourceAdapterDefinition("clickhouse", tuple(), dialect="clickhouse", limit_style="standard"),
        DataSourceAdapterDefinition(
            "mssql",
            aliases=("sqlserver",),
            dialect="tsql",
            limit_style="mssql_top",
            supports_pooling=True,
        ),
        DataSourceAdapterDefinition(
            "trino",
            aliases=tuple(),
            dialect="trino",
            limit_style="standard",
            supports_pooling=True,
        ),
        DataSourceAdapterDefinition("athena", tuple(), dialect="trino", limit_style="standard"),
        DataSourceAdapterDefinition("oracle", tuple(), dialect="oracle", limit_style="oracle_fetch"),
        DataSourceAdapterDefinition("snowflake", tuple(), dialect="snowflake", limit_style="standard"),
        DataSourceAdapterDefinition("bigquery", tuple(), dialect="bigquery", limit_style="standard"),
        DataSourceAdapterDefinition("databricks", tuple(), dialect="databricks", limit_style="standard"),
    )
    for definition in definitions:
        registry.register(definition)


_register_defaults(DEFAULT_DATASOURCE_REGISTRY)


def normalize_datasource_type(ds_type: str | None) -> str:
    return DEFAULT_DATASOURCE_REGISTRY.normalize(ds_type)


def resolve_datasource_definition(ds_type: str | None) -> DataSourceAdapterDefinition | None:
    return DEFAULT_DATASOURCE_REGISTRY.resolve(ds_type)


def dialect_for_datasource(ds_type: str | None) -> str:
    definition = resolve_datasource_definition(ds_type)
    if definition is None:
        return "duckdb"
    return definition.dialect


def apply_limit_for_datasource(sql: str, ds_type: str | None, limit: int) -> str:
    definition = resolve_datasource_definition(ds_type)
    style = definition.limit_style if definition is not None else "standard"
    normalized_limit = int(limit)
    if style == "mssql_top":
        return f"SELECT TOP {normalized_limit} * FROM ({sql}) AS prismbi_limited"
    if style == "oracle_fetch":
        return f"SELECT * FROM ({sql}) prismbi_limited FETCH FIRST {normalized_limit} ROWS ONLY"
    return f"SELECT * FROM ({sql}) AS prismbi_limited LIMIT {normalized_limit}"
