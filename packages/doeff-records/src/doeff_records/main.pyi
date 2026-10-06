# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = main.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from collections.abc import Callable as Callable
from concurrent.futures import ThreadPoolExecutor as ThreadPoolExecutor
from doeff import Program as Program
from doeff import EffectBase as EffectBase
from doeff import with_handlers as with_handlers
from doeff_core_effects.handlers import await_handler as await_handler
from doeff_core_effects.handlers import state as state
from doeff_core_effects.scheduler import scheduled as scheduled
from doeff_core_effects.process_effects import ReadEnvironment as ReadEnvironment
from doeff_core_effects.file_effects import ReadText as ReadText
from doeff_core_effects.file_effects import FileFailed as FileFailed
from doeff_core_effects.stop_signal_handlers import os_signal_stop_handler as os_signal_stop_handler
from doeff_core_effects.aiohttp_http_server import aiohttp_http_server as aiohttp_http_server
from doeff_core_effects.http_server_effects import HttpAddress as HttpAddress
from doeff_core_effects.postgres_sql import PostgresConnections as PostgresConnections
from doeff_core_effects.postgres_sql import PostgresDatabase as PostgresDatabase
from doeff_core_effects.sql_effects import SqlQuery as SqlQuery
from doeff_core_effects.sql_effects import SqlRows as SqlRows
from doeff_core_effects.sql_effects import SqlFailed as SqlFailed
from doeff_core_effects.sql_effects import SqlUnreachable as SqlUnreachable
from doeff_core_effects.pooled_postgres_sql import pooled_postgres_sql_handler as pooled_postgres_sql_handler
from doeff_time import async_time_handler as async_time_handler
from doeff_records.values import RecordsSchema as RecordsSchema
from doeff_records.pg import pg_records_handler as pg_records_handler
from doeff_records.pg import prepare_records_store as prepare_records_store
from doeff_records.pg_sql import DEFAULT_PREFIX as DEFAULT_PREFIX
from doeff_records.http_server import MaintenancePlan as MaintenancePlan
from doeff_records.http_server import RecordsServing as RecordsServing
from doeff_records.http_server import RecordsListening as RecordsListening
from doeff_records.http_server import RecordsPrepared as RecordsPrepared
from doeff_records.http_server import REQUEST_MAX_BYTES as REQUEST_MAX_BYTES
from doeff_records.http_server import serve_records as serve_records
from doeff_records.store_choice import StoreChoice as StoreChoice
from doeff_records.store_choice import StorePressure as StorePressure
from doeff_records.store_choice import PressureUnread as PressureUnread
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
ENV_PG_URL_FILE: str
ENV_PREFIX: str
ENV_HOST: str
ENV_PORT: str
ENV_POOL_SIZE: str
ENV_MAINTENANCE_SECONDS: str
ENV_KEEP_CHANGES_SECONDS: str
ENV_ORIGIN_HOST: str
ENV_HOSTNAME: str
DEFAULT_HOST: str
DEFAULT_PORT: int
DEFAULT_POOL_SIZE: int
DEFAULT_MAINTENANCE_SECONDS: float
DEFAULT_KEEP_CHANGES_SECONDS: float
DATABASE: str
DRAIN_SECONDS: float

@dataclass(frozen=True, kw_only=True)
class RecordsSettings:
    dsn: str
    prefix: str
    origin_host: str
    pool_size: int
    address: HttpAddress
    maintenance: MaintenancePlan

    def __post_init__(self) -> None:
        ...

def env_text(name: str, default: str) -> _Program[str, object]:
    ...

def required_env(name: str) -> _Program[str, object]:
    ...

def env_number(name: str, default: float) -> _Program[float, object]:
    ...

def read_secret(path: str) -> _Program[str, object]:
    ...

def origin_host() -> _Program[str, object]:
    ...

def pg_handlers_of(schema: RecordsSchema, prefix: str, host: str) -> _Program[Callable[[str], object], object]:
    ...

def printed_listening(prefix: str) -> _Handler:
    ...

def records_connected[T](settings: RecordsSettings, body: Program[T, object] | EffectBase[T]) -> _Program[T, object]:
    ...

def records_foundation(settings: RecordsSettings, body: Program | EffectBase) -> _Program[int, object]:
    ...

def records_settings(dsn_of: Callable[[str], Program[str, object]]) -> _Program[RecordsSettings, object]:
    ...

def store_reachable() -> _Program[bool, object]:
    ...
PRESSURE_SQL: str

def store_pressure_of_rows(answer: SqlRows | SqlFailed | SqlUnreachable) -> _Program[StorePressure | PressureUnread, object]:
    ...

def store_pressure_pg() -> _Program[StorePressure | PressureUnread, object]:
    ...
PG_STORE: StoreChoice

def records_serving(schema: RecordsSchema, settings: RecordsSettings, choice: StoreChoice) -> _Program[RecordsServing, object]:
    ...

def records_process(foundation: Callable, serving: RecordsServing) -> _Program[int, object]:
    ...

def serve_records_service(schema: RecordsSchema, dsn_of: Callable[[str], Program[str, object]]) -> _Program[int, object]:
    ...
