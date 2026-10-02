# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = postgres_sql.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from queue import Queue as Queue
from queue import Empty as Empty
from concurrent.futures import Executor as Executor
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from doeff import Program as Program
from doeff_core_effects.offloaded_call import ThreadPerCall as ThreadPerCall
from doeff_core_effects.offloaded_call import offloaded as offloaded
from doeff_core_effects.offloaded_call import run_detached as run_detached
from doeff_core_effects.offloaded_call import keep_nothing as keep_nothing
from doeff_core_effects.sql_effects import SqlQuery as SqlQuery
from doeff_core_effects.sql_effects import SqlInsertRows as SqlInsertRows
from doeff_core_effects.sql_effects import SqlTransaction as SqlTransaction
from doeff_core_effects.sql_effects import SqlEnsureTables as SqlEnsureTables
from doeff_core_effects.sql_effects import SqlRows as SqlRows
from doeff_core_effects.sql_effects import SqlFailed as SqlFailed
from doeff_core_effects.sql_effects import SqlUnreachable as SqlUnreachable
from doeff_core_effects.sql_effects import SqlSchemaApplied as SqlSchemaApplied
from doeff_core_effects.sql_effects import SqlParam as SqlParam
from doeff_core_effects.sql_effects import SqlColumnType as SqlColumnType
from doeff_core_effects.sql_effects import SqlText as SqlText
from doeff_core_effects.sql_effects import SqlPlaceholder as SqlPlaceholder
from doeff_core_effects.sql_effects import split_statement as split_statement
from doeff_core_effects.sql_effects import checked_params as checked_params
from doeff_core_effects.sql_effects import checked_identifier as checked_identifier
from doeff_core_effects.sql_effects import checked_identifiers as checked_identifiers
from doeff_core_effects.sql_effects import checked_rows as checked_rows
from doeff_core_effects.sql_effects import normalized_rows as normalized_rows
from doeff_core_effects.sql_transaction import run_in_transaction as run_in_transaction
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
DRIVER_CLASS_SQLSTATES: dict[str, str]
UNREACHABLE_CLASSES: tuple[str, ...]
UNREACHABLE_SQLSTATE_CLASS: str
UNREACHABLE_SQLSTATES: tuple[str, ...]
DEFAULT_POOL_SIZE: int
DRIVER_THREADS: ThreadPerCall
POSTGRES_TYPES: dict[SqlColumnType, str]

@dataclass(frozen=True, kw_only=True)
class PostgresDatabase:
    name: str
    dsn: str = ...

@dataclass(frozen=True, kw_only=True)
class PostgresTimeouts:
    connect_seconds: int
    keepalive_idle_seconds: int
    keepalive_interval_seconds: int
    keepalive_count: int
    unacknowledged_milliseconds: int
    statement_milliseconds: int | None
    idle_transaction_milliseconds: int | None
DEFAULT_TIMEOUTS: PostgresTimeouts

@dataclass(frozen=True, kw_only=True)
class PostgresStatement:
    text: str
    params: tuple

class PostgresConnections:
    size: Incomplete
    timeouts: Incomplete
    databases: Incomplete
    idle: Incomplete
    permits: Incomplete

    def __init__(self, databases: tuple, *, size: Incomplete=..., timeouts: Incomplete=...) -> None:
        ...

    def names(self) -> Incomplete:
        ...

    def connection_options(self, name: str) -> Incomplete:
        ...

    def acquire(self, name: str) -> Incomplete:
        ...

    def release(self, name: str, connection: Incomplete) -> Incomplete:
        ...

    def close(self) -> Incomplete:
        ...

def postgres_statement(statement: str, params: tuple) -> _Program[PostgresStatement, object]:
    ...

def postgres_insert_statement(table: str, columns: tuple) -> _Program[str, object]:
    ...

def postgres_failure(sqlstate: str | None, class_names: tuple, message: str) -> _Program[SqlFailed | SqlUnreachable, object]:
    ...

def postgres_schema_statements(tables: tuple) -> _Program[tuple, object]:
    ...

def postgres_error(error: Exception) -> _Program[SqlFailed | SqlUnreachable, object]:
    ...

def postgres_run(connection: Incomplete, text: str, params: tuple | None) -> _Program[SqlRows | SqlFailed | SqlUnreachable, object]:
    ...

def postgres_query(connection: Incomplete, request: SqlQuery) -> _Program[SqlRows | SqlFailed | SqlUnreachable, object]:
    ...

def postgres_insert(connection: Incomplete, request: SqlInsertRows) -> _Program[SqlRows | SqlFailed | SqlUnreachable, object]:
    ...

def postgres_ensure_tables(connection: Incomplete, tables: tuple) -> _Program[SqlSchemaApplied | SqlFailed | SqlUnreachable, object]:
    ...

def postgres_begin(connection: Incomplete, lock_key: str | None) -> _Program[SqlFailed | SqlUnreachable | None, object]:
    ...

def postgres_control(connection: Incomplete, statement: str) -> _Program[SqlFailed | SqlUnreachable | None, object]:
    ...

def postgres_lease(connections: PostgresConnections, database: str) -> _Program[Incomplete, object]:
    ...

def lease_now(connections: PostgresConnections, database: str) -> Incomplete:
    ...

def return_abandoned(connections: PostgresConnections, database: str, leased: Incomplete) -> Incomplete:
    ...

def run_then_return(connections: PostgresConnections, database: str, leased: Incomplete, claim: Incomplete, work: Incomplete) -> Incomplete:
    ...

def offloaded_transaction(connections: PostgresConnections, pool: Executor, database: str, program: Program, lock_key: str | None) -> _Program[Incomplete, object]:
    ...

def offloaded_statement(connections: PostgresConnections, pool: Executor, database: str, work: Incomplete) -> _Program[Incomplete, object]:
    ...

def postgres_sql_handler(connections: PostgresConnections) -> _Handler:
    ...
