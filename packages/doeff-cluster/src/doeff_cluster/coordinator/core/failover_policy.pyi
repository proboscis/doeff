# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = failover_policy.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_cluster.coordinator.intent.cluster_model import WorkerLoad as WorkerLoad
from doeff_cluster.coordinator.intent.failover_model import FailoverWorker as FailoverWorker
from doeff_cluster.coordinator.intent.failover_model import ResidentJob as ResidentJob
from doeff_cluster.coordinator.intent.failover_model import ExemptJob as ExemptJob
from doeff_cluster.coordinator.intent.failover_model import KeptApart as KeptApart
from doeff_cluster.coordinator.intent.failover_model import JobTakers as JobTakers
from doeff_cluster.coordinator.intent.failover_model import DownSpike as DownSpike
from doeff_cluster.coordinator.core.cluster_policy import placeable as placeable
from doeff_cluster.coordinator.core.cluster_policy import job_room_of as job_room_of
from doeff_cluster.shared.core.capabilities import HOST_PREFIX as HOST_PREFIX
HANDOFF_SURGE: int

def hyx_per_machineXquestion_markX(needs: tuple[str, ...], template: tuple[str, ...]) -> _Program[bool, object]:
    ...

def takers_of(job: ResidentJob, workers: tuple, template: tuple) -> _Program[JobTakers, object]:
    ...

def place_refusal(placed: JobTakers) -> _Program[str | None, object]:
    ...

def load_alone(worker: FailoverWorker, jobs: int) -> _Program[dict[str, WorkerLoad], object]:
    ...

def room_refusal(worker: FailoverWorker, here: tuple) -> _Program[str | None, object]:
    ...

def spike_of(worker: FailoverWorker, down: FailoverWorker, here: tuple) -> _Program[DownSpike, object]:
    ...

def memory_refusal(worker: FailoverWorker, here: tuple, workers: tuple) -> _Program[str | None, object]:
    ...

def apart_refusals(pair: KeptApart, placed: tuple, workers: tuple) -> _Program[tuple[str, ...], object]:
    ...

def failover_refusals(workers: tuple[FailoverWorker, ...], jobs: tuple[ResidentJob, ...], template: tuple[str, ...], exempt: tuple[ExemptJob, ...], apart: tuple[KeptApart, ...]) -> _Program[tuple[str, ...], object]:
    ...
