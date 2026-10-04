# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = coordinator_reads.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from urllib.parse import quote as url_quote
from doeff_core_effects.http_effects import HttpRequest as HttpRequest
from doeff_core_effects.http_effects import HttpResponse as HttpResponse
from doeff_time import Delay as Delay
from doeff_cluster.shared.intent.cluster_control import ServiceReadiness as ServiceReadiness
from doeff_cluster.shared.intent.cluster_control import ReadinessWaitExpired as ReadinessWaitExpired
from doeff_cluster.shared.intent.cluster_control import JobProcessSeen as JobProcessSeen
from doeff_cluster.shared.intent.cluster_control import JobProcessWaitExpired as JobProcessWaitExpired
WAIT_PROBE_SECONDS: float

def readiness_read(url: str, name: str) -> _Program[ServiceReadiness, object]:
    ...

def readiness_awaited(url: str, name: str, state: str, seconds: float) -> _Program[ServiceReadiness | ReadinessWaitExpired, object]:
    ...

def state_of(url: str) -> _Program[dict | None, object]:
    ...

def job_pids_of(state: dict, name: str) -> _Program[tuple[int, ...], object]:
    ...

def job_process_awaited(url: str, job: str, excluding: tuple[int, ...], seconds: float) -> _Program[JobProcessSeen | JobProcessWaitExpired, object]:
    ...
