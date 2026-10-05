# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = flux.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
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
from doeff import with_handlers as with_handlers
from doeff_cluster.shared.intent.remote_model import RemoteJobFailed as RemoteJobFailed
from doeff_cluster.worker.core.drain_client import await_drained as await_drained
from doeff_cluster.worker.core.drain_client import DRAIN_DEADLINE_SECONDS as DRAIN_DEADLINE_SECONDS
from doeff_cluster.worker.core.drain_client import DRAIN_INTERVAL_SECONDS as DRAIN_INTERVAL_SECONDS
from doeff_cluster.worker.intent.drain_model import AskDrain as AskDrain
from doeff_cluster.shared.intent.upgrade_model import UpgradeKind as UpgradeKind
from doeff_cluster.shared.intent.upgrade_model import PendingPhase as PendingPhase
from doeff_cluster.shared.intent.upgrade_model import RosterEntry as RosterEntry
from doeff_cluster.shared.intent.upgrade_model import PendingTask as PendingTask
from doeff_cluster.shared.intent.upgrade_model import UpgradeStart as UpgradeStart
from doeff_cluster.shared.intent.upgrade_model import UpgradeState as UpgradeState
from doeff_cluster.shared.intent.upgrade_model import UpgradeStateUnreachable as UpgradeStateUnreachable
from doeff_cluster.shared.intent.upgrade_model import ReadUpgradeState as ReadUpgradeState
from doeff_cluster.shared.intent.upgrade_model import PublishDeclarations as PublishDeclarations
from doeff_cluster.shared.intent.upgrade_model import ApplyDeclarations as ApplyDeclarations
from doeff_cluster.shared.intent.upgrade_model import ConfirmCleanBoot as ConfirmCleanBoot
from doeff_cluster.shared.intent.upgrade_model import CleanBootPassed as CleanBootPassed
from doeff_cluster.shared.intent.upgrade_model import CleanBootRefused as CleanBootRefused
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
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put
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

@_doeff_dataclass(frozen=True)
class ManifestDocuments(_doeff_effect_base[tuple[dict, ...]]):
    text: str

def deployed_envs(documents: tuple[dict, ...]) -> _Program[tuple[DeployedEnv, ...], object]:
    ...

def manifest_state(paths: tuple[str, ...]) -> _Program[tuple[DeployedEnv, ...], object]:
    ...

def upgrade_state() -> _Program[UpgradeState | UpgradeStateUnreachable, object]:
    ...

def roster_snapshot(kind: UpgradeKind, target: str, commit: str) -> _Program[UpgradeStart, object]:
    ...
drain_asks_on_sim: _Handler

def prestop_drain(name: str) -> _Program[None, object]:
    ...

def recreate_worker(deployed: DeployedEnv, drain: Callable) -> _Program[UpgradeStart, object]:
    ...

def recreate_coordinator(deployed: DeployedEnv, seconds: float) -> _Program[UpgradeStart, object]:
    ...

def reconcile_manifests(paths: tuple[str, ...], applied: tuple[DeployedEnv, ...], drain: Callable, coordinator_seconds: float) -> _Program[FluxPass, object]:
    ...

@_doeff_dataclass(frozen=True)
class UpgradeStartsSeen(_doeff_effect_base[tuple[UpgradeStart, ...]]):
    ...

def flux_declarations(paths: tuple, drain: Callable, coordinator_seconds: float, initial: tuple) -> _Handler:
    ...

def launch_target(launch: WorkerLaunch | CoordinatorLaunch) -> _Program[str, object]:
    ...

def refused_clean_boots(targets: frozenset) -> _Handler:
    ...
