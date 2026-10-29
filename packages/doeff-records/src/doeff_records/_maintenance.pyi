# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = _maintenance.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from collections.abc import Callable as Callable
from doeff import EffectBase as EffectBase
from doeff_time import Delay as Delay
from doeff_time import GetTime as GetTime
from doeff_records._values import Event as Event
from doeff_records._values import RowChanged as RowChanged
from doeff_records._values import RowRemoved as RowRemoved
from doeff_records._values import Unreachable as Unreachable
from datetime import datetime as datetime
ARCHIVE_PAGE: int

@dataclass(frozen=True)
class SweepExpired(EffectBase):
    ...

@dataclass(frozen=True)
class PruneChanges(EffectBase):
    keep_seconds: float
    upto_seq: int | None = None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class ReadChangeLog(EffectBase):
    after: int
    limit: int

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class ChangeLog:
    changes: tuple[RowChanged | RowRemoved, ...]
    epoch: int

@dataclass(frozen=True)
class ArchivedThrough:
    seq: int

@dataclass(frozen=True)
class Swept:
    rows: int
    forgotten: int = 0
    write_keys: int = 0

@dataclass(frozen=True)
class Pruned:
    floor: int
    removed: int

@dataclass(frozen=True)
class ExpiredCursor:
    stream: str
    at: int | None = None
    sequence: int | None = None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class ReadExpiredEvents(EffectBase):
    at: datetime
    after: ExpiredCursor | None = None
    limit: int = ...

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class ExpiredEvents:
    items: tuple[Event, ...]
    next: ExpiredCursor | None

@dataclass(frozen=True)
class RetireArchivedEvents(EffectBase):
    stream: str
    at: datetime
    sequences: tuple[int, ...]

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class RetiredEvents:
    count: int

@dataclass(frozen=True)
class EventsArchived:
    sequences: tuple[int, ...]

@dataclass(frozen=True)
class EventsArchiveReport:
    sent: int
    confirmed: int
    unreached: int
    retired: int
    halted: Unreachable | None = None

@dataclass(frozen=True)
class MaintenanceReport:
    swept: Swept | Unreachable
    pruned: Pruned | Unreachable
    archived: ArchivedThrough | Unreachable | None = None
    events: EventsArchiveReport | None = None

def prune_after_archive(keep_seconds: int | float, archive: Callable | None) -> _Program[MaintenanceReport, object]:
    ...

def archived_page(archive_events: Callable, at: datetime, page: ExpiredEvents) -> _Program[EventsArchiveReport, object]:
    ...

def archive_expired_events(archive_events: Callable) -> _Program[EventsArchiveReport, object]:
    ...

def maintenance_tick(keep_seconds: int | float, archive: Callable | None, archive_events: Callable | None=None) -> _Program[MaintenanceReport, object]:
    ...

def maintenance_loop(interval_seconds: int | float, keep_seconds: int | float, archive: Callable | None, ticks: int | None, archive_events: Callable | None=None) -> _Program[list, object]:
    ...
