# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = worker_due.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_cluster.shared.intent.due_model import DueAt as DueAt
from doeff_cluster.shared.intent.due_model import DueNow as DueNow
from doeff_cluster.shared.intent.due_model import DueNever as DueNever
from doeff_cluster.shared.core.due_policy import due_of_instants as due_of_instants
from doeff_cluster.shared.core.due_policy import earliest_due as earliest_due
from doeff_cluster.worker.intent.worker_model import CodeState as CodeState
from doeff_cluster.worker.intent.worker_model import ProbeState as ProbeState
from doeff_cluster.worker.intent.worker_model import StopStage as StopStage
from doeff_cluster.worker.intent.worker_model import Outcome as Outcome
from doeff_cluster.worker.intent.worker_model import WorldView as WorldView
from doeff_cluster.worker.intent.worker_model import WorkerPolicy as WorkerPolicy
from doeff_cluster.worker.intent.worker_model import WakeSet as WakeSet
from doeff_cluster.worker.core.policy import backoff_ms as backoff_ms
from doeff_cluster.worker.core.env_upkeep import RootsTally as RootsTally
from doeff_cluster.worker.core.env_upkeep import PrepareLimits as PrepareLimits
from doeff_cluster.worker.core.env_upkeep import SWEEP_EVERY_MS as SWEEP_EVERY_MS
from doeff_cluster.worker.core.warm_rules import warm_child_refused as warm_child_refused

def due_after(now: int, instants: tuple) -> _Program[DueAt | DueNever, object]:
    ...

def plan_due(now: int, world: WorldView, records: dict, policy: WorkerPolicy) -> _Program[DueAt | DueNever, object]:
    ...

def beat_due(now: int, last_ok_ms: int, interval_ms: int) -> _Program[DueAt | DueNever, object]:
    ...

def fence_due(now: int, last_ok_ms: int, fence_ms: int, keep_fence_ms: int) -> _Program[DueAt | DueNever, object]:
    ...

def prepare_stop_due(now: int, progressed_ms: int, limits: PrepareLimits) -> _Program[DueAt | DueNever, object]:
    ...

def sweep_interval_due(now: int, tally: RootsTally | None, cap: int, swept_ms: int) -> _Program[DueAt | DueNever, object]:
    ...

def wakes_with(outer: WakeSet, due: DueAt | DueNow | DueNever, bells: tuple, exits: tuple) -> _Program[WakeSet, object]:
    ...
