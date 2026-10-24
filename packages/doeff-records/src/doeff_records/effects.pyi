"""effects.hy の公開面の型(型検査のための宣言 — 実行時は effects.hy を読む)。

effect は `EffectBase[答えの型]` の部分型として宣言する — `(<- answer (ReadRow table key))` の answer は型検査の展開で
`_doeff_perform(ReadRow(...))`(doeff_hy/static_types.pyi)になり、答えの型(values.pyi の ReadRowAnswer など)を受ける。
欄の名・順・既定値は実装と同じ。写像の欄(PutRow.value・ListRows.where)は FrozenMap(実装は作る時に凍らせる)。
"""

from collections.abc import Generator
from dataclasses import dataclass

from typing import TYPE_CHECKING, Any

from doeff import EffectBase
from doeff_hy.frozen import FrozenMap
from doeff_records.values import (
    AppendEventAnswer,
    AppendEventsAnswer,
    Event,
    EventRetired,
    EventsMoved,
    EventsQuiet,
    Expectation,
    ListCursor,
    ListRowsAnswer,
    PutRowAnswer,
    PutRowsAnswer,
    ReadEventByKeyAnswer,
    ReadEventsAnswer,
    ReadRowAnswer,
    ReadStreamEndAnswer,
    StreamEmpty,
    StreamEnd,
    WatchChangesAnswer,
    WatchCursor,
    WatchEventsAnswer,
)

DEFAULT_LIST_LIMIT: int
LIST_LIMIT_MAX: int
DEFAULT_WATCH_LIMIT: int
DEFAULT_READ_EVENTS_LIMIT: int
WHERE_ANY_LIMIT: int

def checked_key(key: object, what: str) -> tuple[str, ...]: ...
def checked_limit(limit: object, what: str) -> int: ...
def checked_watch_window(after: object, timeout: object) -> bool: ...
def check_row_write(write: PutRow | RowWrite, what: str) -> None: ...
def check_event_append(append: AppendEvent | EventAppend, what: str) -> None: ...
def checked_event_key(idempotency_key: object, what: str) -> str: ...
@dataclass(frozen=True)
class ReadRow(EffectBase[ReadRowAnswer]):
    table: str
    key: tuple[str, ...]

@dataclass(frozen=True)
class ListRows(EffectBase[ListRowsAnswer]):
    table: str
    where: FrozenMap = ...
    fields: tuple[str, ...] | None = None
    cursor: ListCursor | None = None
    limit: int = ...
    descending_by: str | None = None
    where_any: FrozenMap = ...

@dataclass(frozen=True)
class PutRow(EffectBase[PutRowAnswer]):
    table: str
    key: tuple[str, ...]
    value: FrozenMap
    expect: Expectation
    idempotency_key: str | None = None

@dataclass(frozen=True)
class RowWrite:
    table: str
    key: tuple[str, ...]
    value: FrozenMap
    expect: Expectation

@dataclass(frozen=True)
class PutRows(EffectBase[PutRowsAnswer]):
    writes: tuple[RowWrite, ...]
    idempotency_key: str | None = None

def write_parts(ask: PutRow | PutRows) -> tuple[PutRow | RowWrite, ...]: ...

@dataclass(frozen=True)
class WatchChanges(EffectBase[WatchChangesAnswer]):
    tables: tuple[str, ...]
    cursor: WatchCursor
    timeout: float = 0.0
    limit: int = ...
    streams: tuple[str, ...] = ()

@dataclass(frozen=True)
class AppendEvent(EffectBase[AppendEventAnswer]):
    stream: str
    idempotency_key: str
    body: object

@dataclass(frozen=True)
class EventAppend:
    stream: str
    idempotency_key: str
    body: object

@dataclass(frozen=True)
class AppendEvents(EffectBase[AppendEventsAnswer]):
    appends: tuple[EventAppend, ...]

@dataclass(frozen=True)
class WatchEvents(EffectBase[WatchEventsAnswer]):
    stream: str
    after: int = 0
    timeout: float = 0.0
    # `<-` が束ねる名の型 = 答えから :failure(Unreachable)を除いた物(effects.hy の defeffect と同じ — doeff_hy/static_types.pyi の BoundRun)。
    def __iter__(self) -> Generator[Any, Any, EventsMoved | EventsQuiet]: ...

@dataclass(frozen=True)
class ReadEvents(EffectBase[ReadEventsAnswer]):
    stream: str
    after: int = 0
    limit: int = ...
    key_head: str | None = None

@dataclass(frozen=True)
class ReadStreamEnd(EffectBase[ReadStreamEndAnswer]):
    stream: str
    # `<-` が束ねる名の型 = 答えから :failure(Unreachable)を除いた物(effects.hy の defeffect と同じ — doeff_hy/static_types.pyi の BoundRun)。
    def __iter__(self) -> Generator[Any, Any, StreamEnd | StreamEmpty]: ...

@dataclass(frozen=True)
class ReadEventByKey(EffectBase[ReadEventByKeyAnswer]):
    stream: str
    idempotency_key: str
    # `<-` が束ねる名の型 = 答えから :absent(EventAbsent)と :failure(Unreachable)を除いた物(effects.hy の defeffect と同じ)。
    def __iter__(self) -> Generator[Any, Any, Event | EventRetired]: ...

# 源の工場の問い(#3127)— 答えは doeff_records.event_source の SignalSourceFactory。event_source は effects を読むので、ここでは型を文字列で名指す。
@dataclass(frozen=True)
class ReadSignalSource(EffectBase["SignalSourceFactory"]): ...

# 置き場の戻りを待つ問いと、合図の源が止まりに耐える時間の問い(#3469)・HTTP の client が要求を待つ時間の問い(#3557)— 答えは
# doeff_records.event_source の SignalSourcePatience と doeff_records.http_client の RequestPatience(型を文字列で名指す)。
@dataclass(frozen=True)
class AwaitRecordsBack(EffectBase[None]):
    names: tuple[str, ...]

@dataclass(frozen=True)
class ReadSourcePatience(EffectBase["SignalSourcePatience"]): ...

@dataclass(frozen=True)
class ReadRequestPatience(EffectBase["RequestPatience"]): ...

if TYPE_CHECKING:
    from doeff_records.event_source import SignalSourceFactory, SignalSourcePatience
    from doeff_records.http_client import RequestPatience
