# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = upgrade_program.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from collections.abc import Callable as Callable
from functools import partial as partial
from doeff_time import Delay as Delay
from doeff_cluster.shared.core.clock import now_epoch_ms as now_epoch_ms
from doeff_cluster.shared.intent.detached_model import AwaitRunnersChange as AwaitRunnersChange
from doeff_cluster.shared.intent.detached_model import RunnersChange as RunnersChange
from doeff_cluster.shared.intent.launch_model import WorkerLaunch as WorkerLaunch
from doeff_cluster.shared.intent.launch_model import CoordinatorLaunch as CoordinatorLaunch
from doeff_cluster.shared.intent.launch_model import DesireWorker as DesireWorker
from doeff_cluster.shared.intent.launch_model import DesireCoordinator as DesireCoordinator
from doeff_cluster.shared.intent.upgrade_model import PendingPhase as PendingPhase
from doeff_cluster.shared.intent.upgrade_model import UpgradeState as UpgradeState
from doeff_cluster.shared.intent.upgrade_model import UpgradeLimits as UpgradeLimits
from doeff_cluster.shared.intent.upgrade_model import UpgradeStalled as UpgradeStalled
from doeff_cluster.shared.intent.upgrade_model import ReadUpgradeState as ReadUpgradeState
from doeff_cluster.shared.intent.upgrade_model import PublishDeclarations as PublishDeclarations
from doeff_cluster.shared.intent.upgrade_model import ApplyDeclarations as ApplyDeclarations
UNREACHABLE_RETRY_SECONDS: float
WATCH_SECONDS: float

def no_task_on(name: str, state: UpgradeState) -> _Program[bool, object]:
    ...

def back_on(name: str, commit: str, state: UpgradeState) -> _Program[bool, object]:
    ...

def all_back_on(commit: str, state: UpgradeState) -> _Program[bool, object]:
    ...

def queue_empty(state: UpgradeState) -> _Program[bool, object]:
    ...

def all_live(state: UpgradeState) -> _Program[bool, object]:
    ...

def await_until(step: str, done: Callable, limit_seconds: float) -> _Program[None, object]:
    ...

def upgrade_cluster(workers: tuple[WorkerLaunch, ...], coordinator: CoordinatorLaunch, limits: UpgradeLimits) -> _Program[None, object]:
    ...
