"""memory.hy の公開面の型(型検査のための宣言 — 実行時は memory.hy を読む)。

memory.hy は Hy の module なので、pyright は中を読めず、置き場 MemoryStore と handler memory-records-handler が Unknown になる
(handler を組む関数の答えが `Unknown | ((...) -> object)` になる等)。ここで型を宣言する。

- MemoryStore の欄は __init__ が置く置き場の data(検と模擬の世界が直に読む — rows・changes・events・head など)。
- defn(普通の関数)は実装の注記どおり。defk は呼ぶと Program を返す(答えの型 = 実装の :post の型)。
- memory-records-handler は defhandler — (store writer) を受け、本文の Program を受けて handler を被せた本文を返す関数を返す
  (答えの型は本文と同じ — doeff_hy/_static_types.pyi の HandlerBody / HandledScope と同じ形)。
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol, TypeVar

from doeff_core_effects import ExternalPromise
from doeff import WithHandlerType

from doeff import Program
from doeff_hy.frozen import FrozenMap
from doeff_records._values import RememberedWrite, RetiredKey
from doeff_records._effects import (
    AppendEvent,
    AppendEvents,
    EventAppend,
    ListRows,
    PutRow,
    PutRows,
    ReadBatch,
    ReadEventByKey,
    ReadEvents,
    ReadRow,
    ReadStreamEnd,
    CountRows,
    WatchChanges,
    WatchEvents,
)
from doeff_records._faults import SetStoreOutage, StoreFault
from doeff_records._faults import StoreOperation as StoreOperation
from doeff_records._store_choice import StoreChoice
from doeff_records.event_source import BodyWrapper, ChangedRow, SignalSourceFactory, SignalTables
from doeff_records._values import (
    Appended,
    AppendedEvents,
    BatchRead,
    Changes,
    Conflict,
    Count,
    TableDecl,
    Event,
    EventAbsent,
    EventRetired,
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

    def __call__(self, body: Program[_Answer, object], /) -> WithHandlerType[_Answer]: ...

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

@dataclass(frozen=True, kw_only=True)
class WriteNote:
    writer: str
    fields: tuple[str, ...]

def new_store_lock() -> threading.RLock: ...

class MemoryStore:
    schema: RecordsSchema
    make_lock: Callable[[], threading.RLock]
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
    retired_keys: dict[tuple[str, str], RetiredKey]
    remembered_at: dict[tuple[str, str], int]
    key_forgets: list[tuple[int, int, tuple[str, str]]]
    outage: SetStoreOutage | None
    faults: tuple[StoreFault, ...]
    purge_due_ms: int | None
    expiry: list[tuple[int, int, RowDue | EventDue | GroupDue]]
    expiry_order: int
    groups: dict[tuple[str, str], StoredGroup]
    write_keys: dict[str, RememberedWrite]
    write_remembered_at: dict[str, int]
    write_forgets: list[tuple[int, int, str]]
    written_by: dict[int, WriteNote]
    appended_at_head: dict[int, int]
    def __init__(self, schema: RecordsSchema, make_lock: Callable[[], threading.RLock] = ...) -> None: ...
    def __deepcopy__(self, memo: dict[int, object]) -> MemoryStore: ...

#: 公開 effect 10 個と WatchEvents の答えの型のどれか(answered・at-now の :post)。
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
    | AppendedEvents
    | BatchRead
    | Events
    | EventsMoved
    | EventsQuiet
    | StreamEnd
    | StreamEmpty
    | Event
    | EventAbsent
    | EventRetired
)

CLOCK_TICK: timedelta
READ: StoreOperation
WRITE: StoreOperation

def purge_expired(store: MemoryStore, now_ms: int) -> int: ...
# 読みの関数は刻 now_ms(epoch ミリ秒)を受け、保持の期限を過ぎた行と出来事を回収の前でも出さない(#3561)。
def memory_read_row(store: MemoryStore, ask: ReadRow, now_ms: int) -> Row | Missing: ...
def memory_matching_texts(store: MemoryStore, decl: TableDecl, table_name: str, where: FrozenMap, where_any: FrozenMap, now_ms: int) -> list: ...
def memory_count_rows(store: MemoryStore, ask: CountRows, now_ms: int) -> Count | NotIndexed: ...
def memory_list_rows(store: MemoryStore, ask: ListRows, now_ms: int) -> object: ...
def memory_read_batch(store: MemoryStore, ask: ReadBatch, now_ms: int) -> BatchRead: ...
def memory_current_row(store: MemoryStore, table: str, key: tuple[str, ...]) -> Row | None: ...
def memory_put_row(store: MemoryStore, writer: str, ask: PutRow, now_ms: int) -> object: ...
def memory_put_rows(
    store: MemoryStore, writer: str, ask: PutRows, now_ms: int
) -> WrittenRows | RowsConflict | RowsRefused: ...
def memory_watch_scan(store: MemoryStore, ask: WatchChanges, now_ms: int) -> object: ...
def memory_append(store: MemoryStore, writer: str, ask: AppendEvent, now_ms: int) -> object: ...
def memory_append_one(
    store: MemoryStore, writer: str, one: EventAppend, fault: StoreFault | None, now_ms: int
) -> object: ...
def memory_append_events(
    store: MemoryStore, writer: str, ask: AppendEvents, faults: tuple[StoreFault | None, ...], now_ms: int
) -> AppendedEvents: ...
def memory_read_events(store: MemoryStore, ask: ReadEvents, now_ms: int) -> Events: ...
def memory_read_stream_end(store: MemoryStore, ask: ReadStreamEnd, now_ms: int) -> StreamEnd | StreamEmpty: ...
def memory_read_event_by_key(store: MemoryStore, ask: ReadEventByKey, now_ms: int) -> Event | EventAbsent | EventRetired: ...
def memory_advance_epoch(store: MemoryStore) -> int: ...
def memory_add_fault(store: MemoryStore, fault: StoreFault) -> None: ...
def memory_clear_faults(store: MemoryStore, names: frozenset[str] | None) -> None: ...
def memory_watch(
    store: MemoryStore, ask: WatchChanges | WatchEvents
) -> Program[Changes | Reset | EventsMoved | EventsQuiet | Unreachable, object]: ...

# 置き場の外の待ち手が、置き場の内側(呼び鈴の名の形・錠)に触らずに書きを待つ口(#3028)。
def hang_bell(
    store: MemoryStore, tables: tuple[str, ...], streams: tuple[str, ...]
) -> Program[ExternalPromise[None], object]: ...
def drop_bell(store: MemoryStore, bell: ExternalPromise[None]) -> Program[None, object]: ...
def answered(
    store: MemoryStore,
    operation: StoreOperation,
    names: tuple[str, ...],
    ask: object,
    program: Program[_StoreAnswer, object],
) -> Program[_StoreAnswer, object]: ...
# 追記の束の故障の当て方と答え(card ki-48c34994ed15 — 故障は束の追記ごとに、同じ追記の AppendEvent として当たる)。
def append_faults(store: MemoryStore, appends: tuple[EventAppend, ...]) -> Program[tuple[StoreFault | None, ...], object]: ...
def answered_appends(store: MemoryStore, writer: str, ask: AppendEvents) -> Program[AppendedEvents | Unreachable, object]: ...
def answered_reads(store: MemoryStore, ask: ReadBatch) -> Program[BatchRead | Unreachable, object]: ...
# 書きの節の形(保持の回収の後に operation)と読みの節の形(回収しない — 読みの関数が期限を見る・#3561)。
def at_now(store: MemoryStore, operation: Callable[[int], _StoreAnswer]) -> Program[_StoreAnswer, object]: ...
def read_at_now(store: MemoryStore, operation: Callable[[int], _StoreAnswer]) -> Program[_StoreAnswer, object]: ...
# 置き場の止まりの見張りの答え(AwaitRecordsBack — #3469)と、模擬の源の止まりの越え方が撃ち直す読み。
def memory_await_back(store: MemoryStore, names: tuple[str, ...]) -> Program[None, object]: ...
def memory_reach(store: MemoryStore, names: tuple[str, ...]) -> Program[Unreachable | bool, object]: ...
def memory_records_handler(store: MemoryStore, writer: str) -> _RecordsHandler: ...
def memory_prepared(
    store: MemoryStore, schema: RecordsSchema, prefix: str, host: str
) -> Program[Callable[[str], _RecordsHandler], object]: ...
def memory_store_choice(store: MemoryStore) -> Program[StoreChoice, object]: ...

# --- 模擬の源(memory の置き場の書きで合図を発する — #3127)------------------------------------------------------------
@dataclass(frozen=True, kw_only=True)
class MemoryMark:
    epoch: int
    sequence: int
    event: int

@dataclass(frozen=True, kw_only=True)
class MemorySignals:
    signals: tuple[object, ...]
    mark: MemoryMark

MEMORY_SOURCE_EFFECTS: tuple[type, ...]

def memory_mark(store: MemoryStore) -> MemoryMark: ...
def binding_keys(binding: SignalTables, changes: tuple[object, ...], events: tuple[object, ...]) -> tuple[ChangedRow, ...]: ...
def memory_signals_since(store: MemoryStore, bindings: tuple[SignalTables, ...], mark: MemoryMark) -> MemorySignals: ...
def memory_publish(
    store: MemoryStore,
    bindings: tuple[SignalTables, ...],
    subscriber: str,
    tables: tuple[str, ...],
    streams: tuple[str, ...],
    mark: MemoryMark,
) -> Program[None, object]: ...
def run_memory_source[T](
    store: MemoryStore, bindings: tuple[SignalTables, ...], subscriber: str, body: Program[T, object]
) -> Program[T, object]: ...
def memory_signal_handler(store: MemoryStore, bindings: tuple[SignalTables, ...], subscriber: str) -> BodyWrapper: ...
def memory_signal_source(store: MemoryStore) -> Program[SignalSourceFactory, object]: ...
