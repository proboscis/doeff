# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _pg_sql.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from doeff_core_effects.sql_effects import SqlParam as SqlParam
from doeff_records._admission import key_text as key_text
from doeff_records._values import RecordsSchema as RecordsSchema
from doeff_records._values import RetiredKey as RetiredKey
from doeff_records._values import KeepFor as KeepFor
from doeff_records._values import ByKeySuffix as ByKeySuffix
from doeff_records._values import ListCursor as ListCursor
from doeff_records._values import FIELD_NAME_PATTERN as FIELD_NAME_PATTERN
from doeff_records._values import SEPARATOR_PATTERN as SEPARATOR_PATTERN
from doeff_records._values import TABLE_NAME_PATTERN as TABLE_NAME_PATTERN
PREFIX_PATTERN: Incomplete
RETIRED_INDEX_PATTERN: Incomplete
TABLE_SHAPED_INDEX_PATTERN: Incomplete
DEFAULT_PREFIX: str
WRITER_LOCK_SUFFIX: str
MIGRATE_LOCK_SUFFIX: str
ROWS_UNIT: str
STREAM_UNIT_PREFIX: str
TABLE_UNIT_PREFIX: str
WRITE_KEY_UNIT_PREFIX: str
UNIT_LOCK_SEPARATOR: str
INDEX_LOCK_SUFFIX: str
STORE_RELATIONS: tuple[str, ...]
INDEXED_RELATIONS: tuple[str, ...]

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
    remembered: bool = False

@dataclass(frozen=True, kw_only=True)
class IndexedField:
    table: str
    field: str

@dataclass(frozen=True, kw_only=True)
class DeclaredIndex:
    name: str
    relation: str
    text: str
    serves: tuple[IndexedField, ...]
    scoped: tuple[IndexedField, ...]
    aliases: tuple[str, ...]
    sampled: bool
    peers: tuple[str, ...] = ...

@dataclass(frozen=True, kw_only=True)
class AddedColumn:
    relation: str
    column: str

@dataclass(frozen=True, kw_only=True)
class SchemaStep:
    statement: Statement
    index: DeclaredIndex | None
    column: AddedColumn | None = None
NO_CLAUSE: Clause

def params_of(pairs: tuple) -> _Program[tuple, object]:
    ...

def checked_prefix(prefix: str) -> _Program[str, object]:
    ...

def writer_lock_key(prefix: str) -> _Program[str, object]:
    ...

def stream_unit(stream: str) -> _Program[str, object]:
    ...

def table_unit(table: str) -> _Program[str, object]:
    ...

def write_key_unit(idempotency_key: str) -> _Program[str, object]:
    ...

def unit_lock_key(prefix: str, unit: str) -> _Program[str, object]:
    ...

def numbering_lock_statement(prefix: str) -> _Program[Statement, object]:
    ...

def write_key_lock_statement(prefix: str, idempotency_key: str) -> _Program[Statement, object]:
    ...

def migrate_lock_key(prefix: str) -> _Program[str, object]:
    ...

def writer_lock_statement(prefix: str) -> _Program[Statement, object]:
    ...

def in_list(stem: str, values: tuple) -> _Program[InList, object]:
    ...

def digest_of(text: str) -> _Program[str, object]:
    ...

def index_name(prefix: str, field_name: str) -> _Program[str, object]:
    ...

def table_index_name(prefix: str, table: str, field_name: str) -> _Program[str, object]:
    ...

def hyx_retired_indexXquestion_markX(prefix: str, name: str) -> _Program[bool, object]:
    ...

def hyx_table_shaped_indexXquestion_markX(prefix: str, name: str) -> _Program[bool, object]:
    ...

def table_field_index_name(prefix: str, table: str, field_name: str) -> _Program[str, object]:
    ...

def former_field_index_name(prefix: str, table: str, field_name: str) -> _Program[str, object]:
    ...

def field_expression(payload: str, field_name: str) -> _Program[str, object]:
    ...

def table_field_expression(alias: str, table: str, field_name: str) -> _Program[str, object]:
    ...

def ordered_expression(table: str, field_name: str) -> _Program[str, object]:
    ...

def ordered_index_name(prefix: str, table: str, field_name: str) -> _Program[str, object]:
    ...

def ordered_index(prefix: str, table: str, field_name: str) -> _Program[DeclaredIndex, object]:
    ...

def ordered_within_index_name(prefix: str, table: str, equal_field: str, field_name: str) -> _Program[str, object]:
    ...

def ordered_within_index(prefix: str, table: str, equal_field: str, field_name: str) -> _Program[DeclaredIndex, object]:
    ...

def field_index(prefix: str, table: str, field_name: str, serves: bool) -> _Program[DeclaredIndex, object]:
    ...

def fixed_index(name: str, relation: str, text: str) -> _Program[DeclaredIndex, object]:
    ...

def group_index(prefix: str, separator: str) -> _Program[DeclaredIndex, object]:
    ...

def head_index(prefix: str, separator: str) -> _Program[DeclaredIndex, object]:
    ...

def table_step(text: str) -> _Program[SchemaStep, object]:
    ...

def index_steps(index: DeclaredIndex) -> _Program[tuple[SchemaStep, ...], object]:
    ...

def schema_steps(prefix: str, schema: RecordsSchema) -> _Program[tuple[SchemaStep, ...], object]:
    ...

def remembered_at_step(prefix: str) -> _Program[SchemaStep, object]:
    ...

def columns_statement(prefix: str, relation: str) -> _Program[Statement, object]:
    ...

def schema_statements(prefix: str, schema: RecordsSchema) -> _Program[tuple[Statement, ...], object]:
    ...

def declared_indexes(prefix: str, schema: RecordsSchema) -> _Program[tuple[DeclaredIndex, ...], object]:
    ...

def added_indexes(prefix: str, schema: RecordsSchema) -> _Program[tuple[DeclaredIndex, ...], object]:
    ...

def relation_names(prefix: str, relations: tuple) -> _Program[InList, object]:
    ...

def relations_statement(prefix: str) -> _Program[Statement, object]:
    ...

def indexes_statement(prefix: str) -> _Program[Statement, object]:
    ...

def concurrent_index_statement(index: DeclaredIndex) -> _Program[Statement, object]:
    ...

def analyze_statement(prefix: str, relation: str) -> _Program[Statement, object]:
    ...

def drop_index_statement(name: str) -> _Program[Statement, object]:
    ...

def index_lock_key(prefix: str) -> _Program[str, object]:
    ...

def index_lock_statement(prefix: str) -> _Program[Statement, object]:
    ...

def index_unlock_statement(prefix: str) -> _Program[Statement, object]:
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

def terminal_words(terminal: tuple) -> _Program[tuple, object]:
    ...

def field_in_condition(expression: str, stem: str, encoded: tuple) -> _Program[Clause, object]:
    ...

def key_spellings(key_fields: tuple, name: str, encoded: tuple) -> _Program[tuple | None, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class RowFilter:
    clauses: tuple[str, ...]
    params: tuple[SqlParam, ...]
    ledger_free: bool

def field_term(table: str, scoped: frozenset, name: str) -> _Program[str, object]:
    ...

def where_condition(table: str, key_fields: tuple, scoped: frozenset, i: int, name: str, encoded: str) -> _Program[Clause, object]:
    ...

def any_of_condition(table: str, key_fields: tuple, scoped: frozenset, i: int, name: str, encoded: tuple) -> _Program[Clause, object]:
    ...

def hyx_scoped_conditionXquestion_markX(key_fields: tuple, scoped: frozenset, name: str, encoded: tuple) -> _Program[bool, object]:
    ...

def row_filter(table: str, key_fields: tuple, scoped: frozenset, where_json: dict, where_any_json: dict) -> _Program[RowFilter, object]:
    ...

def unexpired_rows_filter(alias: str, expiries: tuple) -> _Program[Clause, object]:
    ...

def read_row_statement(prefix: str, table: str, key: str, expiry: RowExpiry | None) -> _Program[Statement, object]:
    ...

def lock_row_statement(prefix: str, table: str, key: str) -> _Program[Statement, object]:
    ...

def list_rows_statement(prefix: str, table: str, key_fields: tuple, scoped: frozenset, after_key: str | None, where_json: dict, where_any_json: dict, limit: int, expiry: RowExpiry | None) -> _Program[Statement, object]:
    ...

def top_rows_statement(prefix: str, table: str, key_fields: tuple, scoped: frozenset, descending_by: str, ordered: bool, cursor: ListCursor | None, where_json: dict, where_any_json: dict, limit: int, expiry: RowExpiry | None) -> _Program[Statement, object]:
    ...

def ordered_rows_statement(prefix: str, table: str, key_fields: tuple, scoped: frozenset, field_name: str, ordered: bool, ascending: bool, cursor: ListCursor | None, where_json: dict, where_any_json: dict, limit: int, expiry: RowExpiry | None) -> _Program[Statement, object]:
    ...

def count_rows_statement(prefix: str, table: str, key_fields: tuple, scoped: frozenset, at_least: tuple | None, ordered: bool, where_json: dict, where_any_json: dict, expiry: RowExpiry | None) -> _Program[Statement, object]:
    ...

def terminal_rows_statement(prefix: str, table: str, state_field: str, terminal: tuple, scoped: bool, before_at: int) -> _Program[Statement, object]:
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

def prune_bound_statement(prefix: str, before_at: int, upto_seq: int, limit: int) -> _Program[Statement, object]:
    ...

def prune_changes_statement(prefix: str, upto_seq: int, limit: int) -> _Program[Statement, object]:
    ...

def statement_limit_statement(milliseconds: int) -> _Program[Statement, object]:
    ...

def forget_changes_statement(prefix: str) -> _Program[Statement, object]:
    ...

def find_event_statement(prefix: str, stream: str, idempotency_key: str) -> _Program[Statement, object]:
    ...

def insert_event_statement(prefix: str, stream: str, at: int, payload: str, origin_host: str, epoch: int) -> _Program[Statement, object]:
    ...

def insert_positioned_event_statement(prefix: str, stream: str, at: int, head: str, tail: str, origin_host: str, epoch: int) -> _Program[Statement, object]:
    ...

def insert_unused_event_statement(prefix: str, stream: str, at: int, payload: str, origin_host: str, idempotency_key: str) -> _Program[Statement, object]:
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

def retire_keys_statement(prefix: str, stream: str, keys: tuple, remembered_at: int) -> _Program[Statement, object]:
    ...

def drop_keys_statement(prefix: str, stream: str, keys: tuple) -> _Program[Statement, object]:
    ...

def forget_keys_statement(prefix: str, stream: str, forget_before: int, archived: bool, limit: int) -> _Program[Statement, object]:
    ...

def find_write_key_statement(prefix: str, idempotency_key: str) -> _Program[Statement, object]:
    ...

def remember_write_statement(prefix: str, idempotency_key: str, body_digest: str, answer: str, remembered_at: int) -> _Program[Statement, object]:
    ...

def remember_rows_events_statement(prefix: str, idempotency_key: str, body_digest: str, parts: tuple[str, ...], lookups: tuple[tuple, ...], remembered_at: int) -> _Program[Statement, object]:
    ...

def forget_write_keys_statement(prefix: str, forget_before: int, limit: int) -> _Program[Statement, object]:
    ...

def expired_event_condition(prefix: str, separator: str | None, bound: str, remembered: bool=False) -> _Program[str, object]:
    ...

def expiring_where(prefix: str, separator: str | None, remembered: bool=False) -> _Program[str, object]:
    ...

def expiring_params(stream: str, before_at: int) -> _Program[tuple, object]:
    ...

def expiring_candidates_statement(prefix: str, stream: str, before_at: int, separator: str | None, limit: int) -> _Program[Statement, object]:
    ...

def expire_chosen_events_statement(prefix: str, stream: str, before_at: int, separator: str | None, sequences: tuple, remembered: bool=False) -> _Program[Statement, object]:
    ...

def key_suffix_of(key: str, separator: str) -> _Program[str, object]:
    ...

def key_suffix_expression(payload: str, separator: str) -> _Program[str, object]:
    ...

def key_head_expression(payload: str, separator: str) -> _Program[str, object]:
    ...

def touched_events_statement(prefix: str, stream: str, before_at: int, separator: str | None, idempotency_key: str, remembered: bool=False) -> _Program[Statement, object]:
    ...

def delete_events_statement(prefix: str, stream: str, sequences: tuple) -> _Program[Statement, object]:
    ...

def expired_page_statement(prefix: str, stream: str, before_at: int, separator: str | None, after_at: int | None, after_seq: int | None, limit: int) -> _Program[Statement, object]:
    ...
