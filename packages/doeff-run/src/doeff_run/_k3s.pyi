# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _k3s.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from datetime import datetime as datetime
from doeff_time import GetMonotonic as GetMonotonic
from doeff_time import GetTime as GetTime
from doeff_core_effects import RunProcess as RunProcess
from doeff_core_effects import ProcessOutcome as ProcessOutcome
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
ENDED_ANNOTATION: str
FINALIZER: str
CONTAINER_NAME: str
NAMESPACE_NAME: Incomplete
PVC_NAME: Incomplete
ENV_MAX_BYTES: int
IMAGE_ERRORS: set[str]
PVC_MISSING: Incomplete
CALL_TIMEOUT: float
STOP_MARGIN: float
WATCH_STOP_GRACE: float
DEADLINE_SLACK: float
DETAIL_MAX: int
UNKNOWN_HANDLE: str
REMOVED: str
RESOURCE_VERSION: Incomplete

class K3sRunHost:
    kubectl: tuple
    namespace: str
    host_id: Incomplete

    def __init__(self, *, namespace: str, kubectl: tuple=..., host_id: str | None=None) -> None:
        ...

def k8s_time(text: object) -> datetime | None:
    ...

def ended_annotation(*, cause: str, at: datetime, detail: str='') -> str:
    ...

def read_ended(pod: dict) -> dict | None:
    ...

def unsupported_of(spec: RunSpec) -> str | None:
    ...

def secret_name_of(run_id: str) -> str:
    ...

def pod_manifest(host: K3sRunHost, name: str, run_id: str, spec: RunSpec) -> dict:
    ...

def secret_manifest(host: K3sRunHost, run_id: str, spec: RunSpec, pod_name: str, pod_uid: str) -> dict:
    ...

def missing_claim_of(pod: dict) -> str | None:
    ...

def read_pod(handle: RunHandle, pod: dict) -> tuple:
    ...

def watch_script(host: K3sRunHost, name: str, resource_version: str, seconds: float) -> str:
    ...

def last_line(text: str) -> str:
    ...

def hyx_not_foundXquestion_markX(stderr: str) -> bool:
    ...

def hyx_already_existsXquestion_markX(stderr: str) -> bool:
    ...

def hyx_annotation_keptXquestion_markX(stderr: str) -> bool:
    ...

def kubectl_call(host: K3sRunHost, args: tuple, stdin: str | None) -> _Program[ProcessOutcome, object]:
    ...

def failure_of(what: str, outcome: ProcessOutcome) -> str:
    ...

def get_pod(host: K3sRunHost, name: str) -> _Program[dict | None, object]:
    ...

def hyx_oursXquestion_markX(pod: dict, handle: RunHandle) -> bool:
    ...

def find_run(host: K3sRunHost, handle: RunHandle) -> _Program[dict | None, object]:
    ...

def write_ended(host: K3sRunHost, name: str, cause: str, detail: str) -> _Program[bool, object]:
    ...

def delete_pod(host: K3sRunHost, name: str, grace: int, wait: bool) -> _Program[None, object]:
    ...

def release_pod(host: K3sRunHost, name: str) -> _Program[None, object]:
    ...

def settle_never_began(host: K3sRunHost, name: str, detail: str) -> _Program[None, object]:
    ...

def state_of(host: K3sRunHost, handle: RunHandle) -> _Program[tuple, object]:
    ...

def version_of(pod: dict) -> str:
    ...

def wait_change(host: K3sRunHost, name: str, resource_version: str, seconds: float) -> _Program[None, object]:
    ...

def await_ended(host: K3sRunHost, handle: RunHandle, deadline: float) -> _Program[RunState, object]:
    ...

def holder_of(host: K3sRunHost, name: str) -> _Program[RunHandle | str | None, object]:
    ...

def k3s_start(host: K3sRunHost, request: RunStart) -> _Program[RunAccepted | RunNameInUse | RunRefused, object]:
    ...

def k3s_stop(host: K3sRunHost, request: RunStop) -> _Program[RunStopped | RunGone | StopFailed, object]:
    ...

def k3s_remove(host: K3sRunHost, request: RunRemove) -> _Program[RunGone | RunStillAlive, object]:
    ...

def k3s_status(host: K3sRunHost, request: RunStatus) -> _Program[RunPending | RunLive | RunStopped | RunGone, object]:
    ...

def k3s_read_events(host: K3sRunHost, request: RunReadEvents) -> _Program[RunEventPage, object]:
    ...

def k3s_vanish(host: K3sRunHost, request: RunVanish) -> _Program[bool, object]:
    ...

def k3s_run_handler(host: K3sRunHost) -> _Handler:
    ...
