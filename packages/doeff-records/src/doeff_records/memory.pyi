"""memory.hy の公開面の型(型検査のための宣言 — 実行時は memory.hy を読む・agora-redesign #2311・#2245)。

memory.hy は Hy の module なので、pyright は中を読めず、置き場 MemoryStore と handler memory-records-handler が Unknown になる
(handler を組む関数の答えが `Unknown | ((...) -> object)` になる等)。ここで型を宣言する。

- MemoryStore の欄は __init__ が置く置き場の data(検と模擬の世界が直に読む — rows・changes・events・head など)。
- defn(普通の関数)は実装の注記どおり。defk は呼ぶと Program を返す(答えの型 = 実装の :post の型)。
- memory-records-handler は defhandler — (store writer) を受け、本文の Program を受けて handler を被せた本文を返す関数を返す
  (答えの型は本文と同じ — doeff_hy/static_types.pyi の HandlerBody / HandledScope と同じ形)。
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol, TypeVar

from doeff_vm import WithHandler

from doeff import Program
from doeff_records.effects import (
    AppendEvent,
    ListRows,
    PutRow,
    PutRows,
    ReadEvents,
    ReadRow,
    ReadStreamEnd,
    WatchChanges,
    WatchEvents,
)
from doeff_records.faults import SetStoreOutage, StoreFault
from doeff_records.faults import StoreOperation as StoreOperation
from doeff_records.store_choice import StoreChoice
from doeff_records.values import (
    Appended,
    Changes,
    Conflict,
    Event,
    Events,
    EventsMoved,
    EventsQuiet,
    Missing,
    NotIndexed,
    Page,
    RecordsSchema,
    Refused,
    Reset,
    Row,
    RowChanged,
    RowRemoved,
    RowsConflict,
    RowsRefused,
    StreamEmpty,
    StreamEnd,
    Unreachable,
    Written,
    WrittenRows,
)

_Answer = TypeVar("_Answer")

class _RecordsHandler(Protocol):
    """memory-records-handler を (store writer) に当てた物 — 本文の Program を受けて handler を被せる(答えの型は本文と同じ)。"""

    def __call__(self, body: Program[_Answer, object], /) -> WithHandler[_Answer]: ...

class StoredRow:
    row: Row
    updated_ms: int
    def __init__(self, row: Row, updated_ms: int) -> None: ...

class StoredGroup:
    last_at: int
    events: list[Event]
    def __init__(self, last_at: int) -> None: ...

@dataclass(frozen=True, kw_only=True)
class RowDue:
    table: str
    text: str
    stored: StoredRow

@dataclass(frozen=True, kw_only=True)
class EventDue:
    event: Event

@dataclass(frozen=True, kw_only=True)
class GroupDue:
    stream: str
    group: str
    last_at: int

class MemoryStore:
    schema: RecordsSchema
    lock: threading.RLock
    bells: dict[object, frozenset[tuple[str, str]]]
    epoch: int
    floor: int
    head: int
    rows: dict[str, dict[str, StoredRow]]
    changes: list[RowChanged | RowRemoved]
    changed_at: dict[int, int]
    event_head: int
    events: list[Event]
    by_idempotency: dict[tuple[str, str], Event]
    outage: SetStoreOutage | None
    faults: tuple[StoreFault, ...]
    purge_due_ms: int | None
    expiry: list[tuple[int, int, RowDue | EventDue | GroupDue]]
    expiry_order: int
    groups: dict[tuple[str, str], StoredGroup]
    def __init__(self, schema: RecordsSchema) -> None: ...

#: 公開 effect 8 つと WatchEvents の答えの型のどれか(answered・at-now の :post)。
_StoreAnswer = (
    Row
    | Missing
    | Page
    | Reset
    | NotIndexed
    | Written
    | Conflict
    | Refused
    | Unreachable
    | WrittenRows
    | RowsConflict
    | RowsRefused
    | Changes
    | Appended
    | Events
    | EventsMoved
    | EventsQuiet
    | StreamEnd
    | StreamEmpty
)

CLOCK_TICK: timedelta
READ: StoreOperation
WRITE: StoreOperation

def purge_expired(store: MemoryStore, now_ms: int) -> int: ...
def memory_read_row(store: MemoryStore, ask: ReadRow) -> Row | Missing: ...
def memory_list_rows(store: MemoryStore, ask: ListRows) -> object: ...
def memory_current_row(store: MemoryStore, table: str, key: tuple[str, ...]) -> Row | None: ...
def memory_put_row(store: MemoryStore, writer: str, ask: PutRow, now_ms: int) -> object: ...
def memory_put_rows(
    store: MemoryStore, writer: str, ask: PutRows, now_ms: int
) -> WrittenRows | RowsConflict | RowsRefused: ...
def memory_watch_scan(store: MemoryStore, ask: WatchChanges) -> object: ...
def memory_append(store: MemoryStore, writer: str, ask: AppendEvent, now_ms: int) -> object: ...
def memory_read_events(store: MemoryStore, ask: ReadEvents) -> Events: ...
def memory_read_stream_end(store: MemoryStore, ask: ReadStreamEnd) -> StreamEnd | StreamEmpty: ...
def memory_advance_epoch(store: MemoryStore) -> int: ...
def memory_add_fault(store: MemoryStore, fault: StoreFault) -> None: ...
def memory_clear_faults(store: MemoryStore, names: frozenset[str] | None) -> None: ...
def memory_watch(
    store: MemoryStore, ask: WatchChanges | WatchEvents
) -> Program[Changes | Reset | EventsMoved | EventsQuiet | Unreachable, object]: ...
def answered(
    store: MemoryStore,
    operation: StoreOperation,
    names: tuple[str, ...],
    ask: object,
    program: Program[_StoreAnswer, object],
) -> Program[_StoreAnswer, object]: ...
def at_now(store: MemoryStore, operation: Callable[[int], _StoreAnswer]) -> Program[_StoreAnswer, object]: ...
def memory_records_handler(store: MemoryStore, writer: str) -> _RecordsHandler: ...
def memory_prepared(
    store: MemoryStore, schema: RecordsSchema, prefix: str, host: str
) -> Program[Callable[[str], _RecordsHandler], object]: ...
def memory_store_choice(store: MemoryStore) -> Program[StoreChoice, object]: ...
