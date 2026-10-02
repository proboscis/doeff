# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = wire.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from _typeshed import Incomplete
from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from doeff_hy.frozen import FrozenMap as FrozenMap
from doeff_hy.frozen import thaw_json as thaw_json
from doeff_hy.json_value import JsonValue as JsonValue
from doeff_records.values import ExpectAbsent as ExpectAbsent
from doeff_records.values import ExpectVersion as ExpectVersion
from doeff_records.values import ExpectAny as ExpectAny
from doeff_records.values import WatchCursor as WatchCursor
from doeff_records.values import ListCursor as ListCursor
from doeff_records.values import Row as Row
from doeff_records.values import Missing as Missing
from doeff_records.values import Page as Page
from doeff_records.values import Written as Written
from doeff_records.values import WrittenRows as WrittenRows
from doeff_records.values import RowChanged as RowChanged
from doeff_records.values import RowRemoved as RowRemoved
from doeff_records.values import Changes as Changes
from doeff_records.values import Appended as Appended
from doeff_records.values import Event as Event
from doeff_records.values import Events as Events
from doeff_records.values import Conflict as Conflict
from doeff_records.values import Refused as Refused
from doeff_records.values import NotIndexed as NotIndexed
from doeff_records.values import Reset as Reset
from doeff_records.values import RowsConflict as RowsConflict
from doeff_records.values import RowsRefused as RowsRefused
from doeff_records.values import StreamEnd as StreamEnd
from doeff_records.values import StreamEmpty as StreamEmpty
from doeff_records.values import UndeclaredTable as UndeclaredTable
from doeff_records.effects import ReadRow as ReadRow
from doeff_records.effects import ListRows as ListRows
from doeff_records.effects import PutRow as PutRow
from doeff_records.effects import PutRows as PutRows
from doeff_records.effects import RowWrite as RowWrite
from doeff_records.effects import WatchChanges as WatchChanges
from doeff_records.effects import AppendEvent as AppendEvent
from doeff_records.effects import ReadEvents as ReadEvents
from doeff_records.effects import ReadStreamEnd as ReadStreamEnd
PATH_PREFIX: str
WRITER_HEADER: str
OP_READ_ROW: str
OP_LIST_ROWS: str
OP_PUT_ROW: str
OP_WATCH_CHANGES: str
OP_APPEND_EVENT: str
OP_READ_EVENTS: str
OP_PUT_ROWS: str
OP_READ_STREAM_END: str
OPERATIONS: tuple[str, ...]
WRITE_OPERATIONS: frozenset[str]

class RequestKind(StrEnum):
    WRITE = 'write'
    READ = 'read'
    OTHER = 'other'
ERROR_MALFORMED: str
ERROR_UNAUTHORIZED: str
ERROR_NOT_FOUND: str
ERROR_STORE_UNAVAILABLE: str
ERROR_INTERNAL: str
STATUS_OF_ERROR: dict[str, int]
ANSWER_METRIC: str
ANSWER_STATUSES: Incomplete
ANSWER_METRICS: tuple[str, ...]
ANSWER_METRIC_HELPS: FrozenMap
CLIENT_ANSWER_METRIC: str
CLIENT_UNREACHABLE: str
CLIENT_OTHER_STATUS: str
CLIENT_KINDS: tuple[RequestKind, ...]
CLIENT_OUTCOMES: tuple[str, ...]
CLIENT_ANSWER_METRICS: tuple[str, ...]
ANSWER_KINDS: dict[str, tuple[str, ...]]
PublicEffect: TypeAlias = ReadRow | ListRows | PutRow | WatchChanges | AppendEvent | ReadEvents | PutRows | ReadStreamEnd
WireAnswer: TypeAlias = Row | Missing | Page | Written | Conflict | Refused | NotIndexed | Reset | Changes | Appended | Events | WrittenRows | RowsConflict | RowsRefused | StreamEnd | StreamEmpty

class WireMalformed(ValueError):
    ...

@dataclass(frozen=True)
class WireRequest:
    operation: str
    body: dict

@dataclass(frozen=True)
class WireRefusal:
    error: str
    reason: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class DecodedRequest:
    effect: PublicEffect

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class NamedStores:
    tables: tuple
    streams: tuple

def request_kind(path: str) -> _Program[RequestKind, object]:
    ...

def answer_metric(path: str, status: int) -> _Program[str, object]:
    ...

def client_answer_metric(operation: str, outcome: str) -> _Program[str, object]:
    ...

def client_status_outcome(status: int) -> _Program[str, object]:
    ...

def object_of(value: JsonValue, what: str, required: tuple, optional: tuple) -> _Program[dict, object]:
    ...

def string_of(value: JsonValue, what: str) -> _Program[str, object]:
    ...

def integer_of(value: JsonValue, what: str) -> _Program[int, object]:
    ...

def seconds_of(value: JsonValue, what: str) -> _Program[float, object]:
    ...

def strings_of(value: JsonValue, what: str) -> _Program[tuple, object]:
    ...

def json_object_in(value: JsonValue, what: str) -> _Program[dict, object]:
    ...

def malformed(what: str, error: Exception) -> _Program[WireMalformed, object]:
    ...

def watch_cursor_json(cursor: WatchCursor) -> _Program[dict, object]:
    ...

def watch_cursor_from(value: JsonValue, what: str) -> _Program[WatchCursor, object]:
    ...

def list_cursor_json(cursor: ListCursor | None) -> _Program[dict | None, object]:
    ...

def list_cursor_from(value: JsonValue, what: str) -> _Program[ListCursor | None, object]:
    ...

def expect_json(expect: ExpectAbsent | ExpectVersion | ExpectAny) -> _Program[dict, object]:
    ...

def expect_from(value: JsonValue) -> _Program[ExpectAbsent | ExpectVersion | ExpectAny, object]:
    ...

def row_write_json(write: RowWrite) -> _Program[dict, object]:
    ...

def row_write_from(value: JsonValue) -> _Program[RowWrite, object]:
    ...

def row_writes_from(body: JsonValue) -> _Program[tuple, object]:
    ...

def encode_request(ask: PublicEffect) -> _Program[WireRequest, object]:
    ...

def decode_request(request: WireRequest) -> _Program[DecodedRequest, object]:
    ...

def named_stores(ask: PublicEffect) -> _Program[NamedStores, object]:
    ...

def undeclared_reason(ask: PublicEffect, tables: tuple, streams: tuple) -> _Program[str | None, object]:
    ...

def undeclared_refusal(ask: PublicEffect, reason: str) -> _Program[UndeclaredTable, object]:
    ...

def row_json(row: Row) -> _Program[dict, object]:
    ...

def change_json(change: RowChanged | RowRemoved) -> _Program[dict, object]:
    ...

def event_json(event: Event) -> _Program[dict, object]:
    ...

def encode_answer(answer: WireAnswer) -> _Program[dict, object]:
    ...

def row_from(value: JsonValue) -> _Program[Row, object]:
    ...

def change_from(value: JsonValue) -> _Program[RowChanged | RowRemoved, object]:
    ...

def event_from(value: JsonValue) -> _Program[Event, object]:
    ...

def list_in(value: JsonValue, what: str) -> _Program[list, object]:
    ...

def written_rows_from(value: JsonValue) -> _Program[WrittenRows, object]:
    ...

def rows_conflict_from(value: JsonValue) -> _Program[RowsConflict, object]:
    ...

def answer_from(value: JsonValue) -> _Program[WireAnswer, object]:
    ...

def decode_answer(operation: str, value: JsonValue) -> _Program[WireAnswer, object]:
    ...

def refusal_json(refusal: WireRefusal) -> _Program[dict, object]:
    ...

def refusal_from(value: JsonValue) -> _Program[WireRefusal, object]:
    ...
