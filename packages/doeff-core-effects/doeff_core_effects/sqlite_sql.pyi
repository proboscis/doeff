# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = sqlite_sql.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
import sqlite3 as sqlite3
from dataclasses import dataclass as dataclass
from doeff import Program as Program
from doeff_core_effects.sql_effects import SqlQuery as SqlQuery
from doeff_core_effects.sql_effects import SqlInsertRows as SqlInsertRows
from doeff_core_effects.sql_effects import SqlTransaction as SqlTransaction
from doeff_core_effects.sql_effects import SqlEnsureTables as SqlEnsureTables
from doeff_core_effects.sql_effects import SetSqlOutage as SetSqlOutage
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
from doeff_core_effects.sql_effects import SqlTable as SqlTable
from doeff_core_effects.sql_effects import SqlParam as SqlParam
from doeff_core_effects.sql_effects import SqlValue as SqlValue
from doeff_core_effects.sql_transaction import run_in_transaction as run_in_transaction
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put
SQLITE_CLASS_SQLSTATES: dict[str, str]
BIND_ERRORS: tuple[type, ...]
BIND_CLASS_SQLSTATES: dict[str, str]
BUSY_SQLSTATE: str
OPERATIONAL_SQLSTATE: str
SQLITE_TYPES: dict[SqlColumnType, str]

@dataclass(frozen=True, kw_only=True)
class SqliteStatement:
    text: str
    values: tuple[SqlValue, ...]

@dataclass(frozen=True, kw_only=True)
class SqliteConnection:
    name: str
    connection: sqlite3.Connection

def sqlite_statement(statement: str, params: tuple[SqlParam, ...]) -> _Program[SqliteStatement, object]:
    ...

def sqlite_failure(class_names: tuple[str, ...], message: str) -> _Program[SqlFailed | SqlUnreachable, object]:
    ...

def sqlite_schema_statements(tables: tuple[SqlTable, ...]) -> _Program[tuple[str, ...], object]:
    ...

def sqlite_run(connection: sqlite3.Connection, text: str, values: tuple[SqlValue, ...]) -> _Program[SqlRows | SqlFailed | SqlUnreachable, object]:
    ...

def sqlite_query(connection: sqlite3.Connection, request: SqlQuery) -> _Program[SqlRows | SqlFailed | SqlUnreachable, object]:
    ...

def sqlite_insert(connection: sqlite3.Connection, request: SqlInsertRows) -> _Program[SqlRows | SqlFailed | SqlUnreachable, object]:
    ...

def sqlite_ensure_tables(connection: sqlite3.Connection, tables: tuple[SqlTable, ...]) -> _Program[SqlSchemaApplied | SqlFailed | SqlUnreachable, object]:
    ...

def sqlite_control(connection: sqlite3.Connection, statement: str) -> _Program[SqlFailed | SqlUnreachable | None, object]:
    ...

def sqlite_connection(target: str, uri: bool) -> _Program[sqlite3.Connection, object]:
    ...

def with_connection(connections: tuple[SqliteConnection, ...], database: str) -> _Program[tuple[SqliteConnection, ...], object]:
    ...

def connection_of(connections: tuple[SqliteConnection, ...], database: str) -> _Program[sqlite3.Connection, object]:
    ...

def outage_marked(unreachable: tuple[str, ...], database: str, down: bool) -> _Program[tuple[str, ...], object]:
    ...

def outage_of(unreachable: tuple[str, ...], database: str) -> _Program[SqlUnreachable | None, object]:
    ...

def sqlite_answer_query(connection: sqlite3.Connection, unreachable: tuple[str, ...], request: SqlQuery) -> _Program[SqlRows | SqlFailed | SqlUnreachable, object]:
    ...

def sqlite_answer_insert(connection: sqlite3.Connection, unreachable: tuple[str, ...], request: SqlInsertRows) -> _Program[SqlRows | SqlFailed | SqlUnreachable, object]:
    ...

def sqlite_answer_tables(connection: sqlite3.Connection, unreachable: tuple[str, ...], database: str, tables: tuple[SqlTable, ...]) -> _Program[SqlSchemaApplied | SqlFailed | SqlUnreachable, object]:
    ...

def sqlite_answer_transaction[A](connection: sqlite3.Connection, unreachable: tuple[str, ...], database: str, program: Program[A, object]) -> _Program[A | SqlFailed | SqlUnreachable, object]:
    ...

def sqlite_sql_handler(databases: tuple[str, ...]) -> _Handler:
    ...
