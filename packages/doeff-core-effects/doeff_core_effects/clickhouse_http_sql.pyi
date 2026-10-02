# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = clickhouse_http_sql.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
import functools as functools
import json as json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from doeff_core_effects.sql_effects import SqlQuery as SqlQuery
from doeff_core_effects.sql_effects import SqlInsertRows as SqlInsertRows
from doeff_core_effects.sql_effects import SqlTransaction as SqlTransaction
from doeff_core_effects.sql_effects import SqlEnsureTables as SqlEnsureTables
from doeff_core_effects.sql_effects import SqlRows as SqlRows
from doeff_core_effects.sql_effects import SqlFailed as SqlFailed
from doeff_core_effects.sql_effects import SqlUnreachable as SqlUnreachable
from doeff_core_effects.sql_effects import SqlSchemaApplied as SqlSchemaApplied
from doeff_core_effects.sql_effects import SqlColumnType as SqlColumnType
from doeff_core_effects.sql_effects import SqlText as SqlText
from doeff_core_effects.sql_effects import SqlPlaceholder as SqlPlaceholder
from doeff_core_effects.sql_effects import split_statement as split_statement
from doeff_core_effects.sql_effects import checked_params as checked_params
from doeff_core_effects.sql_effects import param_value as param_value
from doeff_core_effects.sql_effects import checked_identifier as checked_identifier
from doeff_core_effects.sql_effects import checked_identifiers as checked_identifiers
from doeff_core_effects.sql_effects import checked_rows as checked_rows
from doeff_core_effects.sql_effects import normalized_rows as normalized_rows
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
CLICKHOUSE_TYPES: Incomplete
CLICKHOUSE_COLUMN_TYPES: dict[SqlColumnType, str]
ANSWER_FORMAT: str
QUERY_SETTINGS: tuple[tuple[str, ...], ...]
NOT_SUPPORTED_SQLSTATE: str
ESCAPES: tuple[tuple[bytes, ...], ...]

@dataclass(frozen=True, kw_only=True)
class ClickHouseDatabase:
    name: str
    url: str
    database: str
    user: str
    password: str
    password = ...
    timeout: float
    timeout = 30.0

@dataclass(frozen=True, kw_only=True)
class ClickHouseParam:
    name: str
    text: bytes

@dataclass(frozen=True, kw_only=True)
class ClickHouseStatement:
    text: str
    params: tuple[ClickHouseParam, ...]

@dataclass(frozen=True, kw_only=True)
class ClickHouseHeader:
    name: str
    value: str

@dataclass(frozen=True, kw_only=True)
class ClickHouseRequest:
    url: str
    body: bytes
    headers: tuple[ClickHouseHeader, ...]

@dataclass(frozen=True, kw_only=True)
class ClickHouseResponse:
    status: int
    body: bytes
    exception_code: str | None
    summary: str | None

def clickhouse_type(value: int | float | str | bytes | bool | None) -> _Program[str, object]:
    ...

def clickhouse_param_text(value: int | float | str | bytes | bool | None) -> _Program[bytes, object]:
    ...

def clickhouse_statement(statement: str, params: tuple) -> _Program[ClickHouseStatement, object]:
    ...

def clickhouse_url(database: ClickHouseDatabase, pairs: list) -> _Program[str, object]:
    ...

def clickhouse_headers(database: ClickHouseDatabase) -> _Program[tuple, object]:
    ...

def clickhouse_query_request(database: ClickHouseDatabase, statement: str, params: tuple) -> _Program[ClickHouseRequest, object]:
    ...

def clickhouse_insert_request(database: ClickHouseDatabase, table: str, columns: tuple, rows: tuple) -> _Program[ClickHouseRequest, object]:
    ...

def clickhouse_rows(body: bytes) -> _Program[tuple, object]:
    ...

def clickhouse_written_rows(summary: str | None) -> _Program[int | None, object]:
    ...

def clickhouse_failure(response: ClickHouseResponse) -> _Program[SqlFailed | SqlUnreachable, object]:
    ...

def clickhouse_schema_statements(tables: tuple) -> _Program[tuple | SqlFailed, object]:
    ...

def clickhouse_send(request: ClickHouseRequest, timeout: float) -> _Program[ClickHouseResponse | SqlUnreachable, object]:
    ...

def clickhouse_answer(request: ClickHouseRequest, timeout: float, insert: bool) -> _Program[SqlRows | SqlFailed | SqlUnreachable, object]:
    ...

def clickhouse_ensure_tables(database: ClickHouseDatabase, tables: tuple) -> _Program[SqlSchemaApplied | SqlFailed | SqlUnreachable, object]:
    ...

def clickhouse_http_sql_handler(databases: tuple) -> _Handler:
    ...
