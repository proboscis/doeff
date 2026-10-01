"""sqlite_sql.hy の公開面の型(I/O なしの SQL の答え手 — 型検査のための宣言・実行時は sqlite_sql.hy を読む・agora-redesign #2320)。

- sqlite-sql-handler(Python の名 sqlite_sql_handler)は答える database の名の tuple を受け、本文の Program に被せる関数を返す
  (defhandler の展開と同じ形: 本文の答えの型をそのまま運ぶ WithHandler)。
- defk は呼ぶと Program を返す(答えの型は Program の 1 つ目の引数)。
- 実装との食い違いは packages/doeff-core-effects/tests/test_sql_stubs.py が検める。
"""

import sqlite3
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from doeff_vm import WithHandler

from doeff import Program
from doeff_core_effects.sql_effects import (
    SqlColumnType,
    SqlFailed,
    SqlInsertRows,
    SqlParam,
    SqlQuery,
    SqlRows,
    SqlSchemaApplied,
    SqlTable,
    SqlUnreachable,
    SqlValue,
)

_A = TypeVar("_A")

SQLITE_CLASS_SQLSTATES: dict[str, str]
BIND_ERRORS: tuple[type[Exception], ...]
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

def sqlite_statement(
    statement: str, params: tuple[SqlParam, ...]
) -> Program[SqliteStatement, Any]: ...
def sqlite_failure(
    class_names: tuple[str, ...], message: str
) -> Program[SqlFailed | SqlUnreachable, Any]: ...
def sqlite_schema_statements(tables: tuple[SqlTable, ...]) -> Program[tuple[str, ...], Any]: ...
def sqlite_run(
    connection: sqlite3.Connection, text: str, values: tuple[SqlValue, ...]
) -> Program[SqlRows | SqlFailed | SqlUnreachable, Any]: ...
def sqlite_query(
    connection: sqlite3.Connection, request: SqlQuery
) -> Program[SqlRows | SqlFailed | SqlUnreachable, Any]: ...
def sqlite_insert(
    connection: sqlite3.Connection, request: SqlInsertRows
) -> Program[SqlRows | SqlFailed | SqlUnreachable, Any]: ...
def sqlite_ensure_tables(
    connection: sqlite3.Connection, tables: tuple[SqlTable, ...]
) -> Program[SqlSchemaApplied | SqlFailed | SqlUnreachable, Any]: ...
def sqlite_control(
    connection: sqlite3.Connection, statement: str
) -> Program[SqlFailed | SqlUnreachable | None, Any]: ...
def with_connection(
    connections: tuple[SqliteConnection, ...], database: str
) -> Program[tuple[SqliteConnection, ...], Any]: ...
def connection_of(
    connections: tuple[SqliteConnection, ...], database: str
) -> Program[sqlite3.Connection, Any]: ...

class _SqliteSqlHandler(Protocol):
    """本文の Program に handler を被せる関数(答えの型は本文のまま)。"""

    def __call__(self, body: Program[_A, object], /) -> WithHandler[_A]: ...

def sqlite_sql_handler(databases: tuple[str, ...]) -> _SqliteSqlHandler: ...
