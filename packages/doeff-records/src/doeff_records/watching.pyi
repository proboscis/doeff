# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = watching.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from collections.abc import Callable as Callable
from doeff_time import GetMonotonic as GetMonotonic
from doeff_time import GetTime as GetTime
from doeff_time import WaitWithin as WaitWithin
from doeff_records.values import Changes as Changes
from doeff_records.values import Events as Events
from doeff_records.values import EventsMoved as EventsMoved
from doeff_records.values import EventsQuiet as EventsQuiet
from doeff_records.values import Reset as Reset
from doeff_records.values import Unreachable as Unreachable
from doeff_records.values import WaitsClosed as WaitsClosed
from doeff_records.admission import epoch_ms as epoch_ms

def hyx_closing_wakeXquestion_markX(woke: WaitsClosed | bool | None) -> _Program[bool, object]:
    ...

def moved_of(answer: Events | Unreachable) -> _Program[EventsMoved | EventsQuiet | Unreachable, object]:
    ...

def hyx_waitingXquestion_markX(answer: Changes | Reset | EventsMoved | EventsQuiet | Unreachable) -> _Program[bool, object]:
    ...

def wait_for_signal(scan: Callable, hang: Callable, drop: Callable, timeout: int | float) -> _Program[Changes | Reset | EventsMoved | EventsQuiet | Unreachable, object]:
    ...
