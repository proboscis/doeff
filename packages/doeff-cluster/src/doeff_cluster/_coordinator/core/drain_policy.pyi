# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = drain_policy.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff_hy._table import TableWrite as TableWrite
from dataclasses import replace as replace
from doeff_cluster._shared.intent.protocol import ClusterTiming as ClusterTiming
from doeff_cluster._coordinator.intent.cluster_model import ClusterJob as ClusterJob
from doeff_cluster._coordinator.intent.cluster_model import ClusterState as ClusterState
from doeff_cluster._coordinator.intent.cluster_model import Drain as Drain
from doeff_cluster._coordinator.intent.cluster_model import Placement as Placement
from doeff_cluster._coordinator.intent.cluster_model import DrainPhase as DrainPhase
from doeff_cluster._coordinator.intent.cluster_model import DrainProgress as DrainProgress
from doeff_cluster._coordinator.intent.cluster_model import WorkerDrainView as WorkerDrainView
from doeff_cluster._coordinator.intent.request_bodies import DrainBody as DrainBody
from doeff_cluster._coordinator.core.cluster_policy import alive as alive
from doeff_cluster._coordinator.core.cluster_policy import eligible as eligible
from doeff_cluster._coordinator.core.cluster_policy import effective_needs as effective_needs
from doeff_cluster._coordinator.core.cluster_policy import can_take as can_take
from doeff_cluster._coordinator.core.cluster_policy import draining_workers as draining_workers
from doeff_cluster._coordinator.core.cluster_policy import relocating as relocating
from doeff_cluster._coordinator.core.cluster_policy import load_of as load_of
from doeff_cluster._coordinator.core.cluster_policy import other_generation_boot as other_generation_boot
from doeff_cluster._coordinator.core.cluster_policy import handover_pending as handover_pending
from doeff_cluster._coordinator.core.cluster_policy import LIVE_PHASES as LIVE_PHASES
from doeff_cluster._coordinator.core.cluster_policy import MAX_EVENTS as MAX_EVENTS
from doeff_cluster._coordinator.core.cluster_policy import scan_rows as scan_rows
from doeff_cluster._coordinator.core.cluster_policy import retiring_marked as retiring_marked
from doeff_cluster._coordinator.core.resource_policy import refuse as refuse
from doeff_cluster._coordinator.core.resource_policy import service_readiness as service_readiness
DRAIN_DEFAULT_TTL_SECONDS: int
DRAIN_MAX_TTL_SECONDS: int

def request_drain(state: ClusterState, name: str, body: DrainBody, actor: str, now: int) -> ClusterState:
    ...

def superseded_leaving(state: ClusterState, name: str, boot: str | None) -> ClusterState:
    ...
STOPPING_DRAIN_ACTOR: str

def absorb_stopping(state: ClusterState, name: str, boot: str | None, stopping: bool, now: int) -> ClusterState:
    ...

def cancel_drain(state: ClusterState, name: str) -> ClusterState:
    ...

def move_target(now: int, state: ClusterState, job: ClusterJob, source: str, timing: ClusterTiming, draining: frozenset, load: dict) -> str | None:
    ...

def leaving_carrier(now: int, state: ClusterState, job: ClusterJob, placed: Placement, timing: ClusterTiming, draining: frozenset) -> bool:
    ...

def surge_holds(now: int, state: ClusterState, job: ClusterJob, surge: Placement, timing: ClusterTiming, draining: frozenset) -> bool:
    ...

def advance_drains(now: int, state: ClusterState, timing: ClusterTiming) -> ClusterState:
    ...

def live_rows(state: ClusterState, worker: str) -> list:
    ...

def detached_rows(state: ClusterState, worker: str) -> list:
    ...

def drain_view(state: ClusterState, name: str, now: int, timing: ClusterTiming) -> DrainProgress | None:
    ...

def worker_view(state: ClusterState, name: str, now: int, timing: ClusterTiming, ready_instances: dict | None=None) -> WorkerDrainView:
    ...

def superseded_worker_view(state: ClusterState, name: str, boot: str, now: int, timing: ClusterTiming) -> WorkerDrainView:
    ...

def drains_view(state: ClusterState, now: int, timing: ClusterTiming) -> dict[str, DrainProgress]:
    ...
