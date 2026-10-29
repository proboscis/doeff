# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = clickhouse_http_sql.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
import http.client
import threading as threading
import types as types
from concurrent.futures import Future as Future
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from doeff import Program as Program
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
from doeff_core_effects._offloaded_call import ThreadPerCall as ThreadPerCall
from doeff_core_effects._offloaded_call import offloaded as offloaded
from doeff_core_effects._offloaded_call import run_detached as run_detached
from doeff_core_effects._offloaded_call import keep_nothing as keep_nothing
CLICKHOUSE_TYPES: Incomplete
CLICKHOUSE_COLUMN_TYPES: dict[SqlColumnType, str]
ANSWER_FORMAT: str
QUERY_SETTINGS: tuple[tuple[str, ...], ...]
TIMEOUT_OVERFLOW_MODE: str
WAIT_MARGIN_SECONDS: float
KILL_GRACE_SECONDS: float
EXIT_SETTLE_SECONDS: float
EXIT_KILL_GRACE_SECONDS: float
USER_AGENT: str
QUERY_ID_SHAPE: Incomplete
NOT_SUPPORTED_SQLSTATE: str
CLICKHOUSE_THREADS: ThreadPerCall
ESCAPES: tuple[tuple[bytes, ...], ...]

@dataclass(frozen=True, kw_only=True)
class ClickHouseDatabase:
    name: str
    url: str
    database: str
    user: str
    password: str = ...
    deadline: float = 30.0

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class ClickHouseWaits:
    deadline: float
    wait: float
    grace: float

    def __post_init__(self) -> None:
        ...

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
    query_id: str
    waits: ClickHouseWaits

@dataclass(frozen=True, kw_only=True)
class ClickHouseKill:
    url: str
    body: bytes
    headers: tuple[ClickHouseHeader, ...]

@dataclass(frozen=True, kw_only=True)
class ClickHouseResponse:
    status: int
    body: bytes
    exception_code: str | None
    summary: str | None

class RoundTrips:
    lock: Incomplete
    running: Incomplete
    closing: Incomplete

    def __init__(self: RoundTrips) -> None:
        ...

    def enter(self: RoundTrips, request: ClickHouseRequest) -> threading.Event | None:
        ...

    def leave(self: RoundTrips, request: ClickHouseRequest, done: threading.Event) -> Incomplete:
        ...

    def close(self: RoundTrips) -> tuple:
        ...
ROUND_TRIPS: RoundTrips

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

def clickhouse_waits(database: ClickHouseDatabase) -> _Program[ClickHouseWaits, object]:
    ...

def clickhouse_seconds_text(seconds: float) -> _Program[str, object]:
    ...

def clickhouse_deadline_pairs(waits: ClickHouseWaits, query_id: str) -> _Program[list, object]:
    ...

def new_query_id() -> _Program[str, object]:
    ...

def clickhouse_query_request(database: ClickHouseDatabase, statement: str, params: tuple) -> _Program[ClickHouseRequest, object]:
    ...

def clickhouse_insert_request(database: ClickHouseDatabase, table: str, columns: tuple, rows: tuple) -> _Program[ClickHouseRequest, object]:
    ...

def clickhouse_kill_request(request: ClickHouseRequest) -> _Program[ClickHouseKill, object]:
    ...

def clickhouse_rows(body: bytes) -> _Program[tuple, object]:
    ...

def clickhouse_written_rows(summary: str | None) -> _Program[int | None, object]:
    ...

def clickhouse_failure(response: ClickHouseResponse) -> _Program[SqlFailed | SqlUnreachable, object]:
    ...

def clickhouse_schema_statements(tables: tuple) -> _Program[tuple | SqlFailed, object]:
    ...

def clickhouse_connection(url: str, seconds: float) -> _Program[http.client.HTTPConnection, object]:
    ...

def clickhouse_path(url: str) -> _Program[str, object]:
    ...

def clickhouse_post(connection: http.client.HTTPConnection, url: str, body: bytes, headers: tuple) -> _Program[None, object]:
    ...

def answer_started(connection: http.client.HTTPConnection, seconds: float) -> _Program[bool, object]:
    ...

def clickhouse_read(connection: http.client.HTTPConnection) -> _Program[ClickHouseResponse | SqlUnreachable, object]:
    ...

def clickhouse_kill(kill: ClickHouseKill, seconds: float) -> _Program[ClickHouseResponse | SqlUnreachable, object]:
    ...

def clickhouse_abandon(connection: http.client.HTTPConnection, request: ClickHouseRequest, why: str) -> _Program[ClickHouseResponse | SqlUnreachable, object]:
    ...

def clickhouse_send(request: ClickHouseRequest) -> _Program[ClickHouseResponse | SqlUnreachable, object]:
    ...

def clickhouse_exit_kill(request: ClickHouseRequest, grace: float) -> _Program[ClickHouseResponse | SqlUnreachable, object]:
    ...

def submit_exit_kill(request: ClickHouseRequest, grace: float) -> Future:
    ...

def settle_round_trips(settle_seconds: float, grace: float) -> tuple:
    ...

def settle_at_exit() -> Incomplete:
    ...

class SettleThenTerminate:
    received: Incomplete

    def __init__(self: SettleThenTerminate) -> None:
        ...

    def receive(self: SettleThenTerminate, number: int, frame: types.FrameType | None) -> Incomplete:
        ...

def install_exit_settle() -> Incomplete:
    ...

def clickhouse_answer(request: ClickHouseRequest, insert: bool) -> _Program[SqlRows | SqlFailed | SqlUnreachable, object]:
    ...

def clickhouse_ensure_tables(database: ClickHouseDatabase, tables: tuple) -> _Program[SqlSchemaApplied | SqlFailed | SqlUnreachable, object]:
    ...

def off_scheduler(program: Program) -> _Program[SqlRows | SqlFailed | SqlUnreachable | SqlSchemaApplied, object]:
    ...

def clickhouse_http_sql_handler(databases: tuple) -> _Handler:
    ...
