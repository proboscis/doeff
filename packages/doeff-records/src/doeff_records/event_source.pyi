# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = event_source.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
from dataclasses import fields as fields
from dataclasses import is_dataclass as is_dataclass
from dataclasses import replace as replace
from functools import partial as partial
from doeff import EffectBase as EffectBase
from doeff import Program as Program
from doeff import with_handlers as with_handlers
from datetime import datetime as datetime
from doeff_core_effects.scheduler import Cancel as Cancel
from doeff_core_effects.scheduler import CompletePromise as CompletePromise
from doeff_core_effects.scheduler import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects.scheduler import CreatePromise as CreatePromise
from doeff_core_effects.scheduler import Promise as Promise
from doeff_core_effects.scheduler import Spawn as Spawn
from doeff_core_effects.scheduler import Task as Task
from doeff_core_effects.scheduler import TaskCancelledError as TaskCancelledError
from doeff_core_effects.scheduler import Wait as Wait
from doeff_events.effects import Publish as Publish
from doeff_events.effects import PublishEffect as PublishEffect
from doeff_events.effects import SourceFailed as SourceFailed
from doeff_events.effects import SourceResumed as SourceResumed
from doeff_events.effects import SourceStalled as SourceStalled
from doeff_events.effects import WaitForEventEffect as WaitForEventEffect
from doeff_time import GetTime as GetTime
from doeff_time import WaitWithin as WaitWithin
from doeff_time.effects.time import GetTimeEffect as GetTimeEffect
from doeff_time.effects.time import WaitWithinEffect as WaitWithinEffect
from doeff_records.admission import key_text as key_text
from doeff_records.effects import AwaitRecordsBack as AwaitRecordsBack
from doeff_records.effects import ListRows as ListRows
from doeff_records.effects import ReadSignalSource as ReadSignalSource
from doeff_records.effects import ReadSourcePatience as ReadSourcePatience
from doeff_records.effects import ReadStreamEnd as ReadStreamEnd
from doeff_records.effects import WatchChanges as WatchChanges
from doeff_records.effects import WatchEvents as WatchEvents
from doeff_records.values import Changes as Changes
from doeff_records.values import EventsMoved as EventsMoved
from doeff_records.values import EventsQuiet as EventsQuiet
from doeff_records.values import NotIndexed as NotIndexed
from doeff_records.values import Page as Page
from doeff_records.values import Reset as Reset
from doeff_records.values import StreamEmpty as StreamEmpty
from doeff_records.values import StreamEnd as StreamEnd
from doeff_records.values import Unreachable as Unreachable
from doeff_records.values import WatchCursor as WatchCursor
from doeff_records.values import checked_table_name as checked_table_name
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
WATCH_SECONDS: float

@dataclass(frozen=True, kw_only=True)
class SignalSourcePatience:
    seconds: float

    def __post_init__(self) -> None:
        ...

def source_patience_handler(patience: SignalSourcePatience) -> _Handler:
    ...

@dataclass(frozen=True, kw_only=True)
class ChangedRow:
    table: str
    key: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class SignalTables:
    signal: type
    tables: tuple[str, ...] = ...
    streams: tuple[str, ...] = ...

    def __post_init__(self) -> None:
        ...

class SignalSourceUnreachable(RuntimeError):
    ...

@dataclass(frozen=True, kw_only=True)
class StreamStart:
    stream: str
    after: int
    signals: tuple[type, ...]

@dataclass(frozen=True, kw_only=True)
class SourcePlan:
    subscriber: str
    bindings: tuple[SignalTables, ...]
    tables: tuple[str, ...]
    cursor: WatchCursor | None
    streams: tuple[StreamStart, ...]

def checked_bindings(bindings: tuple, subscriber: str) -> _Program[tuple[SignalTables, ...], object]:
    ...

def first_seen(names: tuple) -> _Program[tuple, object]:
    ...

def back_announced(names: tuple[str, ...], promise: Promise) -> _Program[None, object]:
    ...

def came_back_within(names: tuple[str, ...], seconds: int | float) -> _Program[bool, object]:
    ...

def ride_out_stall(subscriber: str, names: tuple[str, ...], first: Unreachable, again: EffectBase | Program) -> _Program[Page | NotIndexed | Changes | Reset | EventsMoved | EventsQuiet | StreamEnd | StreamEmpty | bool, object]:
    ...

def reachable(ask: ListRows | WatchChanges | WatchEvents | ReadStreamEnd, subscriber: str, names: tuple[str, ...]) -> _Program[Page | NotIndexed | Changes | Reset | EventsMoved | EventsQuiet | StreamEnd | StreamEmpty, object]:
    ...

def start_cursor(subscriber: str, tables: tuple[str, ...]) -> _Program[WatchCursor | None, object]:
    ...

def stream_starts(bindings: tuple[SignalTables, ...], subscriber: str) -> _Program[tuple[StreamStart, ...], object]:
    ...

def changed_rows(tables: tuple[str, ...], changes: tuple) -> _Program[tuple[ChangedRow, ...], object]:
    ...

def signals_of(bindings: tuple[SignalTables, ...], changes: tuple) -> _Program[tuple, object]:
    ...

def publish_changes(plan: SourcePlan) -> _Program[None, object]:
    ...

def publish_appends(plan: SourcePlan, start: StreamStart) -> _Program[None, object]:
    ...

def failure_announced(source: str, program: Program) -> _Program[None, object]:
    ...

def spawn_sources(plan: SourcePlan) -> _Program[tuple, object]:
    ...

def stop_source(task: Task) -> _Program[None, object]:
    ...

def wait_beside_sources(source: str, event_types: tuple) -> _Program[Incomplete, object]:
    ...

def waits_beside_sources(source: str) -> _Handler:
    ...

def run_with_sources[T](plan: SourcePlan, body: Program[T, object] | EffectBase[T]) -> _Program[T, object]:
    ...

def plan_of(bindings: tuple[SignalTables, ...], subscriber: str) -> _Program[SourcePlan, object]:
    ...

def run_signal_source[T](bindings: tuple[SignalTables, ...], subscriber: str, body: Program[T, object] | EffectBase[T]) -> _Program[T, object]:
    ...

class BodyWrapper(partial):
    ...
SOURCE_EFFECTS: tuple[type, ...]

def records_signal_handler(bindings: tuple[SignalTables, ...], subscriber: str) -> Callable[[object], Program]:
    ...

@dataclass(frozen=True, kw_only=True)
class SignalSourceFactory:
    make: Callable[[tuple[SignalTables, ...], str], Callable[[object], Program]]

    def __post_init__(self) -> None:
        ...
RECORDS_SIGNAL_SOURCE: SignalSourceFactory

def run_read_signal[T](bindings: tuple[SignalTables, ...], subscriber: str, body: Program[T, object] | EffectBase[T]) -> _Program[T, object]:
    ...
READ_SIGNAL_EFFECTS: tuple[type, ...]

def read_signal_handler(bindings: tuple[SignalTables, ...], subscriber: str) -> Callable[[object], Program]:
    ...
