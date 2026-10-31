# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _fake.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
from datetime import datetime as datetime
from datetime import timedelta as timedelta
from doeff_time import GetMonotonic as GetMonotonic
from doeff_time import GetTime as GetTime
from doeff_time import WaitWithin as WaitWithin
from doeff_core_effects import CreateExternalPromise as CreateExternalPromise
from doeff_run._values import RunSpec as RunSpec
from doeff_run._values import RunHandle as RunHandle
from doeff_run._values import RunPending as RunPending
from doeff_run._values import RunLive as RunLive
from doeff_run._values import RunStopped as RunStopped
from doeff_run._values import RunGone as RunGone
from doeff_run._values import RunState as RunState
from doeff_run._values import StopCause as StopCause
from doeff_run._effects import RunStart as RunStart
from doeff_run._effects import RunStop as RunStop
from doeff_run._effects import RunRemove as RunRemove
from doeff_run._effects import RunStatus as RunStatus
from doeff_run._effects import RunReadEvents as RunReadEvents
from doeff_run._effects import RunAccepted as RunAccepted
from doeff_run._effects import RunNameInUse as RunNameInUse
from doeff_run._effects import RunRefused as RunRefused
from doeff_run._effects import RefusalWhy as RefusalWhy
from doeff_run._effects import StopFailed as StopFailed
from doeff_run._effects import RunStillAlive as RunStillAlive
from doeff_run._effects import RunEvent as RunEvent
from doeff_run._effects import RunEventPage as RunEventPage
from doeff_run._faults import RunVanish as RunVanish
CLOCK_TICK: float
MIN_SLEEP: float
KILLED_EXIT_CODE: int
UNKNOWN_HANDLE: str

@dataclass(frozen=True, kw_only=True)
class FakeBehavior:
    begin_after: float = 0.1
    runs_for: float | None = None
    exit_code: int = 0
    out_of_memory: bool = False
    stop_takes: float | None = 0.1
    exit_on_stop: int = 143
    refuses_to_begin: str | None = None
    refuses_to_stop: str | None = None

    def __post_init__(self) -> None:
        ...

class FakeRun:
    handle: RunHandle
    spec: RunSpec
    behavior: FakeBehavior
    begin_at: float
    state: RunState
    live_since: float | None
    stop_at: tuple | None
    events: tuple[RunEvent, ...]
    bells: tuple

    def __init__(self, handle: RunHandle, spec: RunSpec, behavior: FakeBehavior, begin_at: float) -> None:
        ...

class FakeRunWorld:
    behavior_of: Callable[[RunSpec], FakeBehavior]
    capacity: int | None
    unsupported: Callable[[RunSpec], str | None] | None
    counter: int
    runs: dict[str, FakeRun]

    def __init__(self, *, behavior_of: Callable[[RunSpec], FakeBehavior], capacity: int | None=None, unsupported: Callable[[RunSpec], str | None] | None=None) -> None:
        ...

def hyx_terminalXquestion_markX(state: Incomplete) -> bool:
    ...

def self_end_of(behavior: FakeBehavior) -> RunStopped:
    ...

def stop_plan(behavior: FakeBehavior, now: float, grace: float) -> tuple:
    ...

def live_change(run: FakeRun) -> tuple | None:
    ...

def next_change(run: FakeRun) -> tuple | None:
    ...

def ring(run: FakeRun) -> None:
    ...

def enter(run: FakeRun, at_time: float, state: Incomplete, now: float, now_at: datetime) -> None:
    ...

def advance(run: FakeRun) -> _Program[None, object]:
    ...

def wait_for_change(run: FakeRun, limit: float) -> _Program[None, object]:
    ...

def await_end(run: FakeRun) -> _Program[None, object]:
    ...

def lookup(world: FakeRunWorld, handle: RunHandle) -> FakeRun | None:
    ...

def fake_start(world: FakeRunWorld, request: RunStart) -> _Program[RunAccepted | RunNameInUse | RunRefused, object]:
    ...

def fake_stop(world: FakeRunWorld, request: RunStop) -> _Program[RunStopped | RunGone | StopFailed, object]:
    ...

def fake_remove(world: FakeRunWorld, request: RunRemove) -> _Program[RunGone | RunStillAlive, object]:
    ...

def fake_status(world: FakeRunWorld, request: RunStatus) -> _Program[RunPending | RunLive | RunStopped | RunGone, object]:
    ...

def fake_read_events(world: FakeRunWorld, request: RunReadEvents) -> _Program[RunEventPage, object]:
    ...

def fake_vanish(world: FakeRunWorld, request: RunVanish) -> _Program[bool, object]:
    ...

def fake_run_handler(world: FakeRunWorld) -> _Handler:
    ...
