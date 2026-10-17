# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = pg_sql.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from doeff_core_effects.sql_effects import SqlParam as SqlParam
from doeff_records.values import RecordsSchema as RecordsSchema
from doeff_records.values import RetiredKey as RetiredKey
from doeff_records.values import KeepFor as KeepFor
from doeff_records.values import ByKeySuffix as ByKeySuffix
from doeff_records.values import FIELD_NAME_PATTERN as FIELD_NAME_PATTERN
from doeff_records.values import SEPARATOR_PATTERN as SEPARATOR_PATTERN
PREFIX_PATTERN: Incomplete
DEFAULT_PREFIX: str
WRITER_LOCK_SUFFIX: str
MIGRATE_LOCK_SUFFIX: str

@dataclass(frozen=True, kw_only=True)
class Statement:
    text: str
    params: tuple[SqlParam, ...]

@dataclass(frozen=True, kw_only=True)
class InList:
    text: str
    params: tuple[SqlParam, ...]

@dataclass(frozen=True, kw_only=True)
class Clause:
    text: str
    params: tuple[SqlParam, ...]

@dataclass(frozen=True, kw_only=True)
class RowExpiry:
    table: str
    state_field: str
    terminal: tuple
    before_at: int

@dataclass(frozen=True, kw_only=True)
class EventExpiry:
    before_at: int
    separator: str | None
NO_CLAUSE: Clause

def params_of(pairs: tuple) -> _Program[tuple, object]:
    ...

def checked_prefix(prefix: str) -> _Program[str, object]:
    ...

def writer_lock_key(prefix: str) -> _Program[str, object]:
    ...

def migrate_lock_key(prefix: str) -> _Program[str, object]:
    ...

def writer_lock_statement(prefix: str) -> _Program[Statement, object]:
    ...

def in_list(stem: str, values: tuple) -> _Program[InList, object]:
    ...

def index_name(prefix: str, field_name: str) -> _Program[str, object]:
    ...

def field_expression(payload: str, field_name: str) -> _Program[str, object]:
    ...

def field_index_statement(prefix: str, field_name: str) -> _Program[Statement, object]:
    ...

def group_index_statement(prefix: str, separator: str) -> _Program[Statement, object]:
    ...

def head_index_statement(prefix: str, separator: str) -> _Program[Statement, object]:
    ...

def schema_statements(prefix: str, schema: RecordsSchema) -> _Program[tuple[Statement, ...], object]:
    ...

def added_index_statements(prefix: str, schema: RecordsSchema) -> _Program[tuple[Statement, ...], object]:
    ...

def drop_statements(prefix: str) -> _Program[tuple[Statement, ...], object]:
    ...

def store_head_statement(prefix: str) -> _Program[Statement, object]:
    ...

def expired_row_clause(alias: str, expiries: tuple) -> _Program[Clause, object]:
    ...

def expired_row_part(alias: str, stem: str, expiry: RowExpiry) -> _Program[Clause, object]:
    ...

def terminal_state_condition(payload: str, state_field: str, stem: str, terminal: tuple) -> _Program[Clause, object]:
    ...

def field_in_condition(payload: str, field_name: str, stem: str, encoded: tuple) -> _Program[Clause, object]:
    ...

def any_of_filter(where_any_json: dict) -> _Program[Clause, object]:
    ...

def unexpired_rows_filter(alias: str, expiries: tuple) -> _Program[Clause, object]:
    ...

def read_row_statement(prefix: str, table: str, key: str, expiry: RowExpiry | None) -> _Program[Statement, object]:
    ...

def lock_row_statement(prefix: str, table: str, key: str) -> _Program[Statement, object]:
    ...

def list_rows_statement(prefix: str, table: str, after_key: str | None, where_json: dict, where_any_json: dict, limit: int, expiry: RowExpiry | None) -> _Program[Statement, object]:
    ...

def top_rows_statement(prefix: str, table: str, descending_by: str, where_json: dict, where_any_json: dict, limit: int, expiry: RowExpiry | None) -> _Program[Statement, object]:
    ...

def terminal_rows_statement(prefix: str, table: str, state_field: str, terminal: tuple) -> _Program[Statement, object]:
    ...

def upsert_row_statement(prefix: str, table: str, key: str, payload: str, version: int, at: int, writer: str, origin_host: str, epoch: int) -> _Program[Statement, object]:
    ...

def delete_row_statement(prefix: str, table: str, key: str, version: int) -> _Program[Statement, object]:
    ...

def append_change_statement(prefix: str, table: str, key: str, version: int, payload: str | None, at: int, epoch: int) -> _Program[Statement, object]:
    ...

def hidden_changes_filter(prefix: str, expiries: tuple) -> _Program[Clause, object]:
    ...

def changes_statement(prefix: str, after: int, head: int, tables: tuple, limit: int, expiries: tuple) -> _Program[Statement, object]:
    ...

def advance_epoch_statement(prefix: str) -> _Program[Statement, object]:
    ...

def change_log_statement(prefix: str, after: int, limit: int) -> _Program[Statement, object]:
    ...
UNBOUNDED_SEQ: int

def prune_changes_statement(prefix: str, before_at: int, upto_seq: int) -> _Program[Statement, object]:
    ...

def forget_changes_statement(prefix: str) -> _Program[Statement, object]:
    ...

def find_event_statement(prefix: str, stream: str, idempotency_key: str) -> _Program[Statement, object]:
    ...

def insert_event_statement(prefix: str, stream: str, at: int, payload: str, origin_host: str, epoch: int) -> _Program[Statement, object]:
    ...

def living_events_filter(prefix: str, expiry: EventExpiry | None, bound: str) -> _Program[Clause, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class HeadRead:
    separator: str
    head: str

def read_events_statement(prefix: str, stream: str, after: int, limit: int, expiry: EventExpiry | None, head: HeadRead | None) -> _Program[Statement, object]:
    ...

def stream_end_statement(prefix: str, stream: str, expiry: EventExpiry | None) -> _Program[Statement, object]:
    ...

def event_by_key_statement(prefix: str, stream: str, idempotency_key: str, expiry: EventExpiry | None) -> _Program[Statement, object]:
    ...

def stream_tail_statement(prefix: str, stream: str, expiry: EventExpiry | None, stem: str) -> _Program[Statement, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class TailRead:
    stream: str
    expiry: EventExpiry | None

def watch_head_statement(prefix: str, tails: tuple) -> _Program[Statement, object]:
    ...

def find_retired_key_statement(prefix: str, stream: str, idempotency_key: str) -> _Program[Statement, object]:
    ...

def retire_keys_statement(prefix: str, stream: str, keys: tuple) -> _Program[Statement, object]:
    ...

def expired_event_condition(prefix: str, separator: str | None, bound: str) -> _Program[str, object]:
    ...

def expiring_where(prefix: str, separator: str | None) -> _Program[str, object]:
    ...

def expiring_params(stream: str, before_at: int) -> _Program[tuple, object]:
    ...

def expiring_events_statement(prefix: str, stream: str, before_at: int, separator: str | None) -> _Program[Statement, object]:
    ...

def expire_events_statement(prefix: str, stream: str, before_at: int) -> _Program[Statement, object]:
    ...

def key_suffix_of(key: str, separator: str) -> _Program[str, object]:
    ...

def key_suffix_expression(payload: str, separator: str) -> _Program[str, object]:
    ...

def key_head_expression(payload: str, separator: str) -> _Program[str, object]:
    ...

def expire_event_groups_statement(prefix: str, stream: str, before_at: int, separator: str) -> _Program[Statement, object]:
    ...

def touched_events_statement(prefix: str, stream: str, before_at: int, separator: str | None, idempotency_key: str) -> _Program[Statement, object]:
    ...

def delete_events_statement(prefix: str, stream: str, sequences: tuple) -> _Program[Statement, object]:
    ...
