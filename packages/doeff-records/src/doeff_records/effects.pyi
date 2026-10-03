"""effects.hy の公開面の型(型検査のための宣言 — 実行時は effects.hy を読む)。

effect は `EffectBase[答えの型]` の部分型として宣言する — `(<- answer (ReadRow table key))` の answer は型検査の展開で
`_doeff_perform(ReadRow(...))`(doeff_hy/static_types.pyi)になり、答えの型(values.pyi の ReadRowAnswer など)を受ける。
欄の名・順・既定値は実装と同じ。写像の欄(PutRow.value・ListRows.where)は FrozenMap(実装は作る時に凍らせる)。
"""

from dataclasses import dataclass

from typing import TYPE_CHECKING

from doeff import EffectBase
from doeff_hy.frozen import FrozenMap
from doeff_records.values import (
    AppendEventAnswer,
    Expectation,
    ListCursor,
    ListRowsAnswer,
    PutRowAnswer,
    PutRowsAnswer,
    ReadEventsAnswer,
    ReadRowAnswer,
    ReadStreamEndAnswer,
    WatchChangesAnswer,
    WatchCursor,
    WatchEventsAnswer,
)

DEFAULT_LIST_LIMIT: int
DEFAULT_WATCH_LIMIT: int
DEFAULT_READ_EVENTS_LIMIT: int

def checked_key(key: object, what: str) -> tuple[str, ...]: ...
def checked_limit(limit: object, what: str) -> int: ...
def check_row_write(write: PutRow | RowWrite, what: str) -> None: ...
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

@dataclass(frozen=True)
class PutRow(EffectBase[PutRowAnswer]):
    table: str
    key: tuple[str, ...]
    value: FrozenMap
    expect: Expectation

@dataclass(frozen=True)
class RowWrite:
    table: str
    key: tuple[str, ...]
    value: FrozenMap
    expect: Expectation

@dataclass(frozen=True)
class PutRows(EffectBase[PutRowsAnswer]):
    writes: tuple[RowWrite, ...]

@dataclass(frozen=True)
class WatchChanges(EffectBase[WatchChangesAnswer]):
    tables: tuple[str, ...]
    cursor: WatchCursor
    timeout: float = 0.0
    limit: int = ...

@dataclass(frozen=True)
class AppendEvent(EffectBase[AppendEventAnswer]):
    stream: str
    idempotency_key: str
    body: object

@dataclass(frozen=True)
class WatchEvents(EffectBase[WatchEventsAnswer]):
    stream: str
    after: int = 0
    timeout: float = 0.0

@dataclass(frozen=True)
class ReadEvents(EffectBase[ReadEventsAnswer]):
    stream: str
    after: int = 0
    limit: int = ...

@dataclass(frozen=True)
class ReadStreamEnd(EffectBase[ReadStreamEndAnswer]):
    stream: str

# 源の工場の問い(#3127)— 答えは doeff_records.event_source の SignalSourceFactory。event_source は effects を読むので、ここでは型を文字列で名指す。
@dataclass(frozen=True)
class ReadSignalSource(EffectBase["SignalSourceFactory"]): ...

if TYPE_CHECKING:
    from doeff_records.event_source import SignalSourceFactory
