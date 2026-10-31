# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _docker.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from datetime import datetime as datetime
from datetime import timedelta as timedelta
from doeff_time import GetMonotonic as GetMonotonic
from doeff_time import GetTime as GetTime
from doeff_core_effects import RunProcess as RunProcess
from doeff_core_effects import ProcessOutcome as ProcessOutcome
from doeff_core_effects import EnvEntry as EnvEntry
from doeff_core_effects import EnvMode as EnvMode
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
RUN_ID_LABEL: str
HOST_LABEL: str
VOLUME_NAME: Incomplete
MIN_MEMORY_BYTES: int
CALL_TIMEOUT: float
STOP_MARGIN: float
WATCH_STOP_GRACE: float
DEADLINE_SLACK: float
UNKNOWN_HANDLE: str
REMOVED: str
DOCKER_TIME: Incomplete

class UnmadeRun:
    handle: RunHandle
    state: RunStopped
    event: RunEvent

    def __init__(self, handle: RunHandle, state: RunStopped, event: RunEvent) -> None:
        ...

class DockerRunHost:
    docker: tuple
    host_id: Incomplete
    requested: dict[str, datetime]
    unmade: dict[str, UnmadeRun]

    def __init__(self, *, docker: tuple=..., host_id: str | None=None) -> None:
        ...

def docker_time(text: str) -> datetime | None:
    ...

def unsupported_of(spec: RunSpec) -> str | None:
    ...

def create_argv(host: DockerRunHost, name: str, run_id: str, spec: RunSpec) -> tuple:
    ...

def last_line(text: str) -> str:
    ...

def hyx_no_suchXquestion_markX(stderr: str) -> bool:
    ...

def read_container(host: DockerRunHost, handle: RunHandle, info: dict) -> tuple:
    ...

def watch_script(host: DockerRunHost, container_id: str, since: float) -> str:
    ...

def docker_call(host: DockerRunHost, args: tuple) -> _Program[ProcessOutcome, object]:
    ...

def inspect_name(host: DockerRunHost, name: str) -> _Program[dict | None, object]:
    ...

def find_run(host: DockerRunHost, handle: RunHandle) -> _Program[dict | UnmadeRun | None, object]:
    ...

def state_of(host: DockerRunHost, handle: RunHandle) -> _Program[tuple, object]:
    ...

def mark_unmade(host: DockerRunHost, handle: RunHandle, detail: str) -> _Program[RunAccepted, object]:
    ...

def holder_of(host: DockerRunHost, name: str) -> _Program[RunHandle | str | None, object]:
    ...

def missing_volume(host: DockerRunHost, spec: RunSpec) -> _Program[str | None, object]:
    ...

def docker_start(host: DockerRunHost, request: RunStart) -> _Program[RunAccepted | RunNameInUse | RunRefused, object]:
    ...

def docker_stop(host: DockerRunHost, request: RunStop) -> _Program[RunStopped | RunGone | StopFailed, object]:
    ...

def docker_remove(host: DockerRunHost, request: RunRemove) -> _Program[RunGone | RunStillAlive, object]:
    ...

def docker_status(host: DockerRunHost, request: RunStatus) -> _Program[RunPending | RunLive | RunStopped | RunGone, object]:
    ...

def docker_read_events(host: DockerRunHost, request: RunReadEvents) -> _Program[RunEventPage, object]:
    ...

def docker_vanish(host: DockerRunHost, request: RunVanish) -> _Program[bool, object]:
    ...

def docker_run_handler(host: DockerRunHost) -> _Handler:
    ...
