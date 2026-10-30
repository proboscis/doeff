"""_values.hy の公開面の型(型検査のための宣言 — 実行時は _values.hy を読む)。

_values.hy は Hy の module なので、pyright は中を読めず、`from doeff_records._values import RecordsSchema` の名が全部 Unknown に
なる(使う側の表の宣言・行の読みの答え・変更の欄の読みに、書き手に直せない reportUnknown* が出る)。ここで型を宣言する。

- 値の型は全部 frozen の dataclass(実装と同じ欄の名・順・既定値)。欄の注記は実装の注記に、実装が素の `tuple` / `object` と
  書く所だけ中身の型を足す(実装が __post_init__ で確かめる形 — 例: 鍵は文字列の tuple・保持は KeepForever | KeepFor)。
- 写像の欄(RecordsSchema.tables / streams・Row.value など)は FrozenMap。実装は作る時に受けた写像を凍らせるので実行時は dict も
  通るが、型の約束は FrozenMap(頭の註のとおり、静的な検査は呼び手に FrozenMap / frozen-json-object で包ませる)。
- 答えの union(ReadRowAnswer など)は型の別名。
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from doeff import Program
from doeff_hy._frozen import FrozenMap

TABLE_NAME_PATTERN: re.Pattern[str]
FIELD_NAME_PATTERN: re.Pattern[str]
SEPARATOR_PATTERN: re.Pattern[str]

class UndeclaredTable(ValueError):
    tables: tuple[str, ...]
    streams: tuple[str, ...]
    def __init__(self, message: str, *, tables: tuple[str, ...] = (), streams: tuple[str, ...] = ()) -> None: ...
class UndeclaredField(ValueError): ...

def checked_table_name(name: str, what: str) -> str: ...
def checked_field_name(name: str, what: str) -> str: ...
def freeze_field(instance: object, name: str, what: str) -> None: ...
def checked_names(names: object, what: str) -> tuple[str, ...]: ...

# --- 保持 ---

@dataclass(frozen=True)
class KeepForever:
    ...

@dataclass(frozen=True)
class KeepFor:
    seconds: float

Retention: TypeAlias = KeepForever | KeepFor

@dataclass(frozen=True)
class EachEvent:
    ...

@dataclass(frozen=True)
class ByKeySuffix:
    separator: str

RetentionGroup: TypeAlias = EachEvent | ByKeySuffix

@dataclass(frozen=True)
class RememberKeysForever:
    ...

@dataclass(frozen=True)
class RememberKeysFor:
    seconds: float

@dataclass(frozen=True)
class RememberNoKeys:
    ...

KeyMemory: TypeAlias = RememberKeysForever | RememberKeysFor | RememberNoKeys

def key_memory_ms(memory: KeyMemory) -> int | None: ...

WRITE_KEY_MEMORY_SECONDS: float
WRITE_KEY_MAX_LENGTH: int

def checked_write_key(key: object, what: str) -> str | None: ...

@dataclass(frozen=True)
class ByKeyHead:
    separator: str

# --- 表の宣言 ---

@dataclass(frozen=True)
class FieldDecl:
    name: str
    writers: tuple[str, ...]
    founders: tuple[str, ...] = ()

@dataclass(frozen=True)
class DescendingWithin:
    equal: str
    descending: str

@dataclass(frozen=True)
class TableDecl:
    name: str
    key_fields: tuple[str, ...]
    fields: tuple[FieldDecl, ...]
    indexes: tuple[str, ...] = ()
    descending: tuple[str, ...] = ()
    descending_within: tuple[DescendingWithin, ...] = ()
    state_field: str = "state"
    states: tuple[str, ...] = ()
    terminal: tuple[str, ...] = ()
    initial: str | None = None
    operator_paths: tuple[str, ...] = ()
    retention: Retention = ...
    size_budget: int | None = None
    def field_names(self) -> tuple[str, ...]: ...
    def declares(self, name: str) -> bool: ...
    def writers_of(self, name: str) -> tuple[str, ...]: ...
    def founders_of(self, name: str) -> tuple[str, ...]: ...

@dataclass(frozen=True)
class StreamDecl:
    name: str
    writers: tuple[str, ...]
    retention: Retention = ...
    size_budget: int | None = None
    retention_group: RetentionGroup = ...
    key_lookup: ByKeyHead | None = None
    archive_before_retire: bool = False
    key_memory: KeyMemory = ...

@dataclass(frozen=True)
class RecordsSchema:
    tables: FrozenMap[TableDecl] = ...
    streams: FrozenMap[StreamDecl] = ...
    operators: tuple[str, ...] = ()
    def table(self, name: str) -> TableDecl: ...
    def stream(self, name: str) -> StreamDecl: ...

# --- 書きの期待 ---

@dataclass(frozen=True)
class ExpectAbsent:
    ...

@dataclass(frozen=True)
class ExpectVersion:
    version: int

@dataclass(frozen=True)
class ExpectAny:
    ...

Expectation: TypeAlias = ExpectAbsent | ExpectVersion | ExpectAny

# --- 位置 ---

@dataclass(frozen=True)
class WatchCursor:
    epoch: int
    sequence: int

@dataclass(frozen=True)
class ListCursor:
    epoch: int
    after_key: str
    after_value: int | None = None

# --- 成功の答え ---

@dataclass(frozen=True)
class Row:
    key: tuple[str, ...]
    value: FrozenMap
    version: int

@dataclass(frozen=True)
class Missing:
    ...

@dataclass(frozen=True)
class Page:
    rows: tuple[Row, ...]
    next_cursor: ListCursor | None
    epoch: int
    sequence: int

@dataclass(frozen=True)
class Written:
    version: int
    value: FrozenMap
    position: WatchCursor | None = None
    replayed: bool = False

@dataclass(frozen=True)
class WrittenRows:
    items: tuple[Written, ...]
    replayed: bool = False

@dataclass(frozen=True)
class RowChanged:
    table: str
    key: tuple[str, ...]
    version: int
    value: FrozenMap
    sequence: int
    at: int

# 保存の綴り(_values.hy の頭の註): 置き場が保存した値の JSON の綴りを持つ答えの作り手と読み。
STORED_TEXT: str
def stored_text(holder: Row | RowChanged) -> str | None: ...
def stored_row(key: tuple[str, ...], text: str, version: int) -> Row: ...
def spelled_row(key: tuple[str, ...], value: FrozenMap, text: str, version: int) -> Row: ...
def stored_change(table: str, key: tuple[str, ...], version: int, text: str, sequence: int, at: int) -> RowChanged: ...
def spelled_change(table: str, key: tuple[str, ...], version: int, value: FrozenMap, text: str, sequence: int, at: int) -> RowChanged: ...

@dataclass(frozen=True)
class RowRemoved:
    table: str
    key: tuple[str, ...]
    sequence: int

# WatchChanges の答えの tails の 1 つ — 名指した列 1 つの末尾(#3718)。
@dataclass(frozen=True, kw_only=True)
class StreamTail:
    stream: str
    sequence: int
    at: int

@dataclass(frozen=True, kw_only=True)
class StreamTailEmpty:
    stream: str

@dataclass(frozen=True)
class Changes:
    items: tuple[RowChanged | RowRemoved, ...]
    cursor: WatchCursor
    tails: tuple[StreamTail | StreamTailEmpty, ...]

@dataclass(frozen=True)
class Appended:
    sequence: int
    replayed: bool

@dataclass(frozen=True)
class AppendedEvents:
    items: tuple[Appended | Refused, ...]

@dataclass(frozen=True)
class Event:
    stream: str
    sequence: int
    idempotency_key: str
    body: object
    writer: str
    at: int
    position: WatchCursor | None = None

@dataclass(frozen=True)
class WrittenAndAppended:
    rows: WrittenRows
    appends: tuple[Appended, ...]
    replayed: bool = False

@dataclass(frozen=True, kw_only=True)
class RetiredKey:
    idempotency_key: str
    sequence: int
    body_digest: str

@dataclass(frozen=True, kw_only=True)
class RememberedWrite:
    body_digest: str
    answer: Written | WrittenRows | WrittenAndAppended

@dataclass(frozen=True)
class Events:
    items: tuple[Event, ...]
    last_sequence: int

@dataclass(frozen=True)
class EventsMoved:
    ...

@dataclass(frozen=True)
class EventsQuiet:
    ...

@dataclass(frozen=True)
class Count:
    count: int

@dataclass(frozen=True)
class StreamEnd:
    sequence: int

@dataclass(frozen=True)
class StreamEmpty:
    ...

@dataclass(frozen=True)
class EventAbsent:
    ...

@dataclass(frozen=True)
class EventRetired:
    sequence: int

# --- 合図の源と HTTP の client の問いの答え ---

@dataclass(frozen=True, kw_only=True)
class ChangedRow:
    table: str
    key: str

    def __post_init__(self) -> None:
        ...

class KeyedSignal(Protocol):
    @property
    def keys(self) -> tuple[ChangedRow, ...]: ...

@dataclass(frozen=True, kw_only=True)
class SignalTables:
    signal: type[KeyedSignal]
    tables: tuple[str, ...] = ...
    streams: tuple[str, ...] = ...

@dataclass(frozen=True, kw_only=True)
class SignalSourceFactory:
    make: Callable[[tuple[SignalTables, ...], str], Callable[[object], Program]]

@dataclass(frozen=True, kw_only=True)
class SignalSourcePatience:
    seconds: float

@dataclass(frozen=True, kw_only=True)
class RequestPatience:
    seconds: float

# --- 失敗の答え ---

@dataclass(frozen=True)
class Conflict:
    current: Row | Missing

@dataclass(frozen=True)
class Refused:
    reason: str

@dataclass(frozen=True)
class RowsConflict:
    index: int
    table: str
    key: tuple[str, ...]
    current: Row | Missing

@dataclass(frozen=True)
class RowsRefused:
    index: int
    table: str
    key: tuple[str, ...]
    reason: str

@dataclass(frozen=True)
class AppendRefused:
    index: int
    stream: str
    idempotency_key: str
    reason: str

@dataclass(frozen=True)
class Unreachable:
    detail: str

@dataclass(frozen=True)
class NotIndexed:
    fields: tuple[str, ...]

@dataclass(frozen=True)
class Reset:
    epoch: int
    floor: int

@dataclass(frozen=True)
class BatchRead:
    items: tuple[Row | Missing | Page | Reset | NotIndexed, ...]

@dataclass(frozen=True, kw_only=True)
class WaitsClosed:
    reason: str

ReadRowAnswer: TypeAlias = Row | Missing | Unreachable
ListRowsAnswer: TypeAlias = Page | Reset | Unreachable | NotIndexed
PutRowAnswer: TypeAlias = Written | Conflict | Refused | Unreachable
WatchChangesAnswer: TypeAlias = Changes | Reset | Unreachable
AppendEventAnswer: TypeAlias = Appended | Refused | Unreachable
ReadEventsAnswer: TypeAlias = Events | NotIndexed | Unreachable
WatchEventsAnswer: TypeAlias = EventsMoved | EventsQuiet | Unreachable
CountRowsAnswer: TypeAlias = Count | Unreachable | NotIndexed
ReadStreamEndAnswer: TypeAlias = StreamEnd | StreamEmpty | Unreachable
ReadEventByKeyAnswer: TypeAlias = Event | EventAbsent | EventRetired | Unreachable
PutRowsAnswer: TypeAlias = WrittenRows | RowsConflict | RowsRefused | Unreachable
AppendEventsAnswer: TypeAlias = AppendedEvents | Unreachable
PutRowsAppendEventsAnswer: TypeAlias = WrittenAndAppended | RowsConflict | RowsRefused | AppendRefused | Unreachable
ReadBatchAnswer: TypeAlias = BatchRead | Unreachable

