# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _process.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from collections.abc import Mapping as Mapping
from datetime import datetime as datetime
from doeff_time import GetMonotonic as GetMonotonic
from doeff_time import GetTime as GetTime
from doeff_time import RaceWithin as RaceWithin
from doeff_core_effects import AwaitProcessExit as AwaitProcessExit
from doeff_core_effects import Cancel as Cancel
from doeff_core_effects import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects import EnvEntry as EnvEntry
from doeff_core_effects import EnvMode as EnvMode
from doeff_core_effects import PollProcess as PollProcess
from doeff_core_effects import ProcessExited as ProcessExited
from doeff_core_effects import ProcessNotStarted as ProcessNotStarted
from doeff_core_effects import ReadEnvironment as ReadEnvironment
from doeff_core_effects import Spawn as Spawn
from doeff_core_effects import StartProcess as StartProcess
from doeff_core_effects import StopProcess as StopProcess
from doeff_run._values import IMAGE_NAME as IMAGE_NAME
from doeff_run._values import RunSpec as RunSpec
from doeff_run._values import RunHandle as RunHandle
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
from doeff_run._effects import VolumeEnsure as VolumeEnsure
from doeff_run._effects import VolumeStatus as VolumeStatus
from doeff_run._effects import VolumeRefused as VolumeRefused
from doeff_run._faults import RunVanish as RunVanish
MIN_SLEEP: float
DEADLINE_SLACK: float
STOP_MARGIN: float
DEFAULT_PATH: str
UNKNOWN_HANDLE: str
REMOVED: str
VANISHED: str

class ProcessRun:
    handle: RunHandle
    pid: int | None
    state: RunState
    events: tuple
    requested_at: datetime | None
    stopping: bool
    bells: tuple

    def __init__(self, handle: RunHandle, pid: int | None, state: RunState, events: tuple) -> None:
        ...

class ProcessRunHost:
    images: Mapping[str, str]
    runs: dict[str, ProcessRun]

    def __init__(self, *, images: Mapping[str, str]) -> None:
        ...

    @property
    def live_pids(self) -> tuple[int, ...]:
        ...

def unsupported_of(spec: RunSpec) -> str | None:
    ...

def image_dir_of(host: ProcessRunHost, spec: RunSpec) -> str | None:
    ...

def child_path(image_dir: str, inherited: str | None) -> str:
    ...

def exit_code_of(returncode: int) -> int:
    ...

def stopped_state(run: ProcessRun, returncode: int) -> RunStopped:
    ...

def not_before(events: tuple, at: datetime) -> datetime:
    ...

def ring(run: ProcessRun) -> None:
    ...

def lookup(host: ProcessRunHost, handle: RunHandle) -> ProcessRun | None:
    ...

def inherited_path() -> _Program[str | None, object]:
    ...

def child_ended(pid: int) -> _Program[None, object]:
    ...

def conclude(run: ProcessRun, state: RunStopped | RunGone) -> _Program[None, object]:
    ...

def observe(run: ProcessRun) -> _Program[None, object]:
    ...

def state_of(host: ProcessRunHost, handle: RunHandle) -> _Program[tuple, object]:
    ...

def wait_change(run: ProcessRun, seconds: float) -> _Program[None, object]:
    ...

def mark_unmade(host: ProcessRunHost, handle: RunHandle, detail: str) -> _Program[RunAccepted, object]:
    ...

def process_start(host: ProcessRunHost, request: RunStart) -> _Program[RunAccepted | RunNameInUse | RunRefused, object]:
    ...

def process_volume_refusal(name: str) -> _Program[VolumeRefused, object]:
    ...

def process_stop(host: ProcessRunHost, request: RunStop) -> _Program[RunStopped | RunGone | StopFailed, object]:
    ...

def process_remove(host: ProcessRunHost, request: RunRemove) -> _Program[RunGone | RunStillAlive, object]:
    ...

def process_status(host: ProcessRunHost, request: RunStatus) -> _Program[RunState, object]:
    ...

def process_read_events(host: ProcessRunHost, request: RunReadEvents) -> _Program[RunEventPage, object]:
    ...

def process_vanish(host: ProcessRunHost, request: RunVanish) -> _Program[bool, object]:
    ...

def process_run_handler(host: ProcessRunHost) -> _Handler:
    ...
