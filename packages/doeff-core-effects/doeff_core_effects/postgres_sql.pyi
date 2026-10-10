# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = postgres_sql.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from queue import Queue as Queue
from queue import Empty as Empty
from concurrent.futures import Executor as Executor
from collections.abc import Callable as Callable
from contextlib import AbstractContextManager as AbstractContextManager
from typing import Protocol as Protocol
from typing import runtime_checkable as runtime_checkable
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from doeff import Program as Program
from doeff_hy.wire import Malformed as Malformed
from doeff_hy.wire import dump_json as dump_json
from doeff_hy.wire import parse_json as parse_json
from doeff_core_effects.offloaded_call import ThreadPerCall as ThreadPerCall
from doeff_core_effects.offloaded_call import offloaded as offloaded
from doeff_core_effects.offloaded_call import run_detached as run_detached
from doeff_core_effects.offloaded_call import keep_nothing as keep_nothing
from doeff_core_effects.scheduler import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects.scheduler import ExternalPromise as ExternalPromise
from doeff_core_effects.scheduler import TaskCancelledError as TaskCancelledError
from doeff_core_effects.sql_effects import SqlQuery as SqlQuery
from doeff_core_effects.sql_effects import SqlInsertRows as SqlInsertRows
from doeff_core_effects.sql_effects import SqlBatch as SqlBatch
from doeff_core_effects.sql_effects import SqlTransaction as SqlTransaction
from doeff_core_effects.sql_effects import SqlEnsureTables as SqlEnsureTables
from doeff_core_effects.sql_effects import SqlNotify as SqlNotify
from doeff_core_effects.sql_effects import SqlHangNotice as SqlHangNotice
from doeff_core_effects.sql_effects import SqlDropNotice as SqlDropNotice
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
from doeff_core_effects.sql_transaction import TransactionFlush as TransactionFlush
from doeff_core_effects.sql_transaction import run_in_transaction as run_in_transaction
from doeff_core_effects.sql_transaction import run_in_batched_transaction as run_in_batched_transaction
from doeff_core_effects.sql_transaction import stray_batch as stray_batch
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
DRIVER_CLASS_SQLSTATES: dict[str, str]
UNREACHABLE_CLASSES: tuple[str, ...]
UNREACHABLE_SQLSTATE_CLASS: str
UNREACHABLE_SQLSTATES: tuple[str, ...]
DEFAULT_POOL_SIZE: int
DRIVER_THREADS: ThreadPerCall
LISTEN_RETRY_SECONDS: int
NOTICE_PAYLOAD_LIMIT: int
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

@dataclass(kw_only=True)
class RaisedNotices:
    topics: dict = ...
    sent: bool = False

class PostgresConnections:
    size: Incomplete
    timeouts: Incomplete
    origin: Incomplete
    databases: Incomplete
    idle: Incomplete
    permits: Incomplete
    listeners: Incomplete
    listeners_lock: Incomplete

    def __init__(self, databases: tuple, *, size: Incomplete=..., timeouts: Incomplete=...) -> None:
        ...

    def listener(self, name: str, channel: str) -> PostgresListener:
        ...

    def ring_local(self, name: str, raised: RaisedNotices) -> None:
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

class PostgresListener:
    connections: PostgresConnections
    name: str
    channel: str
    lock: Incomplete
    bells: Incomplete
    listening: Incomplete
    listened: Incomplete
    failure: Incomplete
    settled: Incomplete

    def __init__(self, connections: PostgresConnections, name: str, channel: str) -> None:
        ...

    def ring(self, topics: frozenset | None) -> None:
        ...

    def hang(self, bell: ExternalPromise, topics: frozenset | None) -> str | None:
        ...

    def drop(self, bell: ExternalPromise) -> None:
        ...

    def listen(self) -> None:
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
LOCK_STATEMENT: str
NOTICE_STATEMENT: str

@dataclass(frozen=True, kw_only=True)
class NoticeWire:
    origin: str | None = None
    topics: tuple[str, ...] | None = None

def notice_payload(origin: str | None, topics: tuple | None) -> _Program[str, object]:
    ...

def heard_notice(payload: str) -> _Program[NoticeWire, object]:
    ...

def notice_params(origin: str | None, notify: SqlNotify) -> _Program[tuple, object]:
    ...

def postgres_notice(connection: PipelineConnection, database: str, origin: str | None, notify: SqlNotify) -> _Program[SqlRows | SqlFailed | SqlUnreachable, object]:
    ...

def noted_notice(raised: RaisedNotices, notify: SqlNotify) -> _Program[None, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class PostgresStep:
    text: str
    params: tuple[SqlParam, ...] | None
    rows: tuple[tuple[int | float | str | bytes | bool | None, ...], ...] | None
    answered: bool

def request_step(request: SqlQuery | SqlInsertRows) -> _Program[PostgresStep | None, object]:
    ...

def flush_steps(lock_key: str | None, origin: str | None, flush: TransactionFlush) -> _Program[tuple, object]:
    ...

@runtime_checkable
class StatementCursor(Protocol):
    description: tuple | list | None
    rowcount: int
    fetchall: Callable[[], list]
    close: Callable[[], None]
    executemany: Callable[[str, list], None]

@runtime_checkable
class PipelineConnection(Protocol):
    pipeline: Callable[[], AbstractContextManager]
    execute: Callable[[str, dict | None], StatementCursor]
    cursor: Callable[[], StatementCursor]

def pipelined(connection: PipelineConnection, step: PostgresStep | None) -> _Program[StatementCursor | None, object]:
    ...

def step_answer(step: PostgresStep | None, cursor: StatementCursor | None) -> _Program[SqlRows, object]:
    ...

def first_failure(error: Exception) -> _Program[Exception, object]:
    ...

def postgres_flush(connection: PipelineConnection, lock_key: str | None, origin: str | None, flush: TransactionFlush) -> _Program[tuple | SqlFailed | SqlUnreachable, object]:
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

@dataclass(kw_only=True)
class TransactionLease:
    connection: PipelineConnection | SqlUnreachable | None = None
    submitted: bool = False
    returned: bool = False
    abandoned: bool = False

@dataclass(frozen=True, kw_only=True)
class TransactionDriver:
    connections: PostgresConnections
    pool: Executor
    database: str
    lease: TransactionLease
    guard: AbstractContextManager

def drive_leased(driver: TransactionDriver, work: Callable[[PipelineConnection], Program], closing: bool) -> Incomplete:
    ...

def abandon_lease(driver: TransactionDriver) -> Incomplete:
    ...

def driven(driver: TransactionDriver, work: Callable, closing: bool) -> _Program[Incomplete, object]:
    ...

class TransactionAbandoned(Exception):
    ...

def leased_here(driver: TransactionDriver) -> _Program[PipelineConnection | SqlUnreachable, object]:
    ...

def flushed_here(driver: TransactionDriver, lock_key: str | None, origin: str, raised: RaisedNotices, flush: TransactionFlush) -> _Program[tuple | SqlFailed | SqlUnreachable, object]:
    ...

def rolled_back_here(driver: TransactionDriver) -> _Program[SqlFailed | SqlUnreachable | None, object]:
    ...

def run_batched_here(driver: TransactionDriver, lock_key: str | None, origin: str, raised: RaisedNotices, program: Program) -> Incomplete:
    ...

def abandoned_now(driver: TransactionDriver) -> Incomplete:
    ...

def offloaded_batched_transaction(connections: PostgresConnections, pool: Executor, database: str, program: Program, lock_key: str | None) -> _Program[Incomplete, object]:
    ...

def offloaded_transaction(connections: PostgresConnections, pool: Executor, database: str, program: Program, lock_key: str | None, batched: bool) -> _Program[Incomplete, object]:
    ...

def raised_commit(driver: TransactionDriver, raised: RaisedNotices) -> _Program[SqlFailed | SqlUnreachable | None, object]:
    ...

def raised_notice(driver: TransactionDriver, origin: str, raised: RaisedNotices, request: SqlNotify) -> _Program[SqlRows | SqlFailed | SqlUnreachable | None, object]:
    ...

def offloaded_statement(connections: PostgresConnections, pool: Executor, database: str, work: Incomplete) -> _Program[Incomplete, object]:
    ...

def notified(connections: PostgresConnections, pool: Executor, database: str, notify: SqlNotify) -> _Program[None | SqlFailed | SqlUnreachable, object]:
    ...

def hung_notice(connections: PostgresConnections, pool: Executor, database: str, channel: str, topics: tuple | None) -> _Program[ExternalPromise | SqlUnreachable, object]:
    ...

def postgres_sql_handler(connections: PostgresConnections) -> _Handler:
    ...
