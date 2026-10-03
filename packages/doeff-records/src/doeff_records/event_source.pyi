# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = event_source.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
from dataclasses import fields as fields
from dataclasses import is_dataclass as is_dataclass
from doeff_events.effects import WaitForEventEffect as WaitForEventEffect
from doeff_events.handlers.memory import Empty as Empty
from doeff_events.handlers.memory import SubscriberQueue as SubscriberQueue
from doeff_time import Delay as Delay
from doeff_records.admission import key_text as key_text
from doeff_records.effects import ListRows as ListRows
from doeff_records.effects import WatchChanges as WatchChanges
from doeff_records.values import Changes as Changes
from doeff_records.values import NotIndexed as NotIndexed
from doeff_records.values import Page as Page
from doeff_records.values import Reset as Reset
from doeff_records.values import Unreachable as Unreachable
from doeff_records.values import WatchCursor as WatchCursor
from doeff_records.values import checked_table_name as checked_table_name
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
WATCH_SECONDS: float
RECONNECT_TRIES: int
RECONNECT_SECONDS: float

@dataclass(frozen=True, kw_only=True)
class ChangedRow:
    table: str
    key: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class SignalTables:
    signal: type
    tables: tuple

    def __post_init__(self) -> None:
        ...

class SignalSourceUnreachable(RuntimeError):
    ...

@dataclass()
class Subscription:
    subscriber: str
    bindings: tuple
    tables: tuple
    queue: SubscriberQueue
    cursor: WatchCursor | None

def checked_bindings(bindings: tuple, subscriber: str) -> _Program[tuple, object]:
    ...

def bound_tables(bindings: tuple) -> _Program[tuple, object]:
    ...

def reachable(ask: ListRows | WatchChanges, subscriber: str, tables: tuple) -> _Program[Page | NotIndexed | Changes | Reset, object]:
    ...

def start_cursor(subscriber: str, tables: tuple) -> _Program[WatchCursor | None, object]:
    ...

def changed_rows(tables: tuple, changes: tuple) -> _Program[tuple, object]:
    ...

def signals_of(bindings: tuple, changes: tuple) -> _Program[tuple, object]:
    ...

def watch_once(subscription: Subscription) -> _Program[None, object]:
    ...

def subscribed_signals(subscription: Subscription) -> _Handler:
    ...

def records_signal_handler(bindings: tuple, subscriber: str) -> _Program[Callable, object]:
    ...
