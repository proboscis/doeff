# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = flux.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from doeff_core_effects.file_effects import ReadText as ReadText
from doeff_core_effects.file_effects import FileFailed as FileFailed
from doeff_core_effects.process_effects import EnvEntry as EnvEntry
from doeff_core_effects.scheduler import Spawn as Spawn
from doeff_core_effects.scheduler import Gather as Gather
from doeff_core_effects.scheduler import Task as Task
from doeff_cluster.shared.core.clock import now_epoch_ms as now_epoch_ms
from doeff_cluster.shared.core.launch_rules import worker_launch_of_env as worker_launch_of_env
from doeff_cluster.shared.core.launch_rules import coordinator_launch_of_env as coordinator_launch_of_env
from doeff_cluster.shared.intent.launch_model import WorkerLaunch as WorkerLaunch
from doeff_cluster.shared.intent.launch_model import CoordinatorLaunch as CoordinatorLaunch
from doeff_cluster.shared.intent.detached_model import AwaitDetached as AwaitDetached
from doeff_cluster.coordinator.core.upgrade_invariants import UpgradeKind as UpgradeKind
from doeff_cluster.coordinator.core.upgrade_invariants import PendingPhase as PendingPhase
from doeff_cluster.coordinator.core.upgrade_invariants import RosterEntry as RosterEntry
from doeff_cluster.coordinator.core.upgrade_invariants import PendingTask as PendingTask
from doeff_cluster.coordinator.core.upgrade_invariants import UpgradeStart as UpgradeStart
from doeff_cluster.sim.local import SimWorker as SimWorker
from doeff_cluster.sim.local import HostTruth as HostTruth
from doeff_cluster.sim.local import DrainWorker as DrainWorker
from doeff_cluster.sim.local import StopWorker as StopWorker
from doeff_cluster.sim.local import ReplaceWorker as ReplaceWorker
from doeff_cluster.sim.local import StartWorker as StartWorker
from doeff_cluster.sim.local import WorkerOf as WorkerOf
from doeff_cluster.sim.local import HostTruthOf as HostTruthOf
from doeff_cluster.sim.local import StopCoordinator as StopCoordinator
from doeff_cluster.sim.local import ReadCoordinator as ReadCoordinator
WORKER_ROLE: str
COORDINATOR_ROLE: str
PLACED_PHASES: frozenset[str]

@dataclass(frozen=True, kw_only=True)
class DeployedEnv:
    deployment: str
    role: str
    env: tuple[EnvEntry, ...]

@dataclass(frozen=True, kw_only=True)
class FluxPass:
    applied: tuple[DeployedEnv, ...]
    starts: tuple[UpgradeStart, ...]

def deployed_envs(text: str) -> _Program[tuple[DeployedEnv, ...], object]:
    ...

def manifest_state(paths: tuple[str, ...]) -> _Program[tuple[DeployedEnv, ...], object]:
    ...

def roster_snapshot(kind: UpgradeKind, target: str, commit: str) -> _Program[UpgradeStart, object]:
    ...

def prestop_drain(name: str) -> _Program[None, object]:
    ...

def recreate_worker(deployed: DeployedEnv, drain: Callable) -> _Program[UpgradeStart, object]:
    ...

def recreate_coordinator(deployed: DeployedEnv, seconds: float) -> _Program[UpgradeStart, object]:
    ...

def reconcile_manifests(paths: tuple[str, ...], applied: tuple[DeployedEnv, ...], drain: Callable, coordinator_seconds: float) -> _Program[FluxPass, object]:
    ...
