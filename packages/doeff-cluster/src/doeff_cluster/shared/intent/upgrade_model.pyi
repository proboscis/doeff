# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = upgrade_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from doeff_cluster.shared.intent.launch_model import WorkerLaunch as WorkerLaunch
from doeff_cluster.shared.intent.launch_model import CoordinatorLaunch as CoordinatorLaunch

class UpgradeKind(StrEnum):
    WORKER = 'worker'
    COORDINATOR = 'coordinator'

class PendingPhase(StrEnum):
    QUEUED = 'queued'
    ASSIGNED = 'assigned'

class WorkerDeclaration(StrEnum):
    DECLARED = 'declared'
    UNDECLARED = 'undeclared'

@dataclass(frozen=True, kw_only=True)
class RosterEntry:
    worker: str
    live: bool
    doeff_commit: str | None
    declaration: WorkerDeclaration
    unread_reason: str | None = None

@dataclass(frozen=True, kw_only=True)
class PendingTask:
    task: str
    phase: PendingPhase
    worker: str | None

@dataclass(frozen=True, kw_only=True)
class UpgradeState:
    roster: tuple[RosterEntry, ...]
    tasks: tuple[PendingTask, ...]
    coordinator_commit: str | None
    known_tasks: tuple[str, ...]

@dataclass(frozen=True, kw_only=True)
class UpgradeStateUnreachable:
    reason: str

@dataclass(frozen=True, kw_only=True)
class UpgradeStart:
    at_ms: int
    kind: UpgradeKind
    target: str
    doeff_commit: str
    roster: tuple[RosterEntry, ...]
    tasks: tuple[PendingTask, ...]

@dataclass(frozen=True, kw_only=True)
class BootRootsAtStart:
    start: UpgradeStart
    prepared: tuple[str, ...]

@dataclass(frozen=True, kw_only=True)
class UpgradeLimits:
    drain_seconds: float
    return_seconds: float
    queue_seconds: float
    quiet_seconds: float

@dataclass(frozen=True, kw_only=True)
class VerifiedVersions:
    coordinator: str
    workers: frozenset[str]

class UpgradeStalled(RuntimeError):
    step: str
    limit_seconds: float
    observed: str

    def __init__(self, step: str, limit_seconds: float, observed: str) -> None:
        ...

@_doeff_dataclass(frozen=True)
class ReadUpgradeState(_doeff_effect_base[UpgradeState | UpgradeStateUnreachable]):
    ...

@_doeff_dataclass(frozen=True)
class PublishDeclarations(_doeff_effect_base[None]):
    ...

@_doeff_dataclass(frozen=True)
class ApplyDeclarations(_doeff_effect_base[None]):
    ...

@dataclass(frozen=True, kw_only=True)
class CleanBootPassed:
    target: str

@dataclass(frozen=True, kw_only=True)
class CleanBootRefused:
    target: str
    reason: str

@_doeff_dataclass(frozen=True)
class ConfirmCleanBoot(_doeff_effect_base[CleanBootPassed | CleanBootRefused]):
    launch: WorkerLaunch | CoordinatorLaunch

class BootRootRefusal(StrEnum):
    PREPARE_ROLE_UNKNOWN = 'prepare-role-unknown'
    PLACE_UNAVAILABLE = 'place-unavailable'
    PREPARE_STOPPED = 'prepare-stopped'
    PREPARE_FAILED = 'prepare-failed'
    READY_MARK_INCOMPLETE = 'ready-mark-incomplete'

@dataclass(frozen=True, kw_only=True)
class BootRootAlreadyPrepared:
    target: str
    previous_root_present: bool

@dataclass(frozen=True, kw_only=True)
class BootRootBuilt:
    target: str
    seconds: float
    previous_root_present: bool

@dataclass(frozen=True, kw_only=True)
class BootRootRefused:
    target: str
    reason: BootRootRefusal

@_doeff_dataclass(frozen=True)
class PrepareBootRoot(_doeff_effect_base[BootRootAlreadyPrepared | BootRootBuilt | BootRootRefused]):
    launch: WorkerLaunch | CoordinatorLaunch

@dataclass(frozen=True, kw_only=True)
class QuietWindowOpened:
    target: str

@dataclass(frozen=True, kw_only=True)
class QuietWindowMissed:
    target: str
    reason: str

@_doeff_dataclass(frozen=True)
class AwaitQuietWindow(_doeff_effect_base[QuietWindowOpened | QuietWindowMissed]):
    target: str
    timeout_seconds: float

@dataclass(frozen=True, kw_only=True)
class UnverifiedWorkers:
    target: str
    coordinator_commit: str
    workers: tuple[RosterEntry, ...]

@dataclass(frozen=True, kw_only=True)
class RollbackRootMissing:
    target: str
    root: BootRootAlreadyPrepared | BootRootBuilt

@dataclass(frozen=True, kw_only=True)
class QueuedTasksRemain:
    target: str
    tasks: tuple[str, ...]

class RefusalPoint(StrEnum):
    BEFORE_DESIRE = 'before-desire'
    BEFORE_APPLY = 'before-apply'

class UpgradeRefused(RuntimeError):
    target: str
    refusal: CleanBootRefused | BootRootRefused | UnverifiedWorkers | RollbackRootMissing | QueuedTasksRemain | QuietWindowMissed
    point: RefusalPoint

    def __init__(self, target: str, refusal: CleanBootRefused | BootRootRefused | UnverifiedWorkers | RollbackRootMissing | QueuedTasksRemain | QuietWindowMissed, point: RefusalPoint) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class WaitReached:
    state: UpgradeState

@dataclass(frozen=True, kw_only=True)
class WaitExpired:
    observed: str
    last: UpgradeState | None

@dataclass(frozen=True, kw_only=True)
class CoordinatorUpgraded:
    root: BootRootAlreadyPrepared | BootRootBuilt
    undeclared: tuple[RosterEntry, ...]

@dataclass(frozen=True, kw_only=True)
class ClusterUpgraded:
    workers: tuple[BootRootAlreadyPrepared | BootRootBuilt, ...]
    coordinator: CoordinatorUpgraded
