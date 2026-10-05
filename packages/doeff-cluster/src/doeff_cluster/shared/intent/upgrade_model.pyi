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

@dataclass(frozen=True, kw_only=True)
class RosterEntry:
    worker: str
    live: bool
    doeff_commit: str | None
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
class UpgradeLimits:
    drain_seconds: float
    return_seconds: float
    queue_seconds: float

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

class UpgradeRefused(RuntimeError):
    target: str
    reason: str

    def __init__(self, target: str, reason: str) -> None:
        ...

@_doeff_dataclass(frozen=True)
class ConfirmCleanBoot(_doeff_effect_base[CleanBootPassed | CleanBootRefused]):
    launch: WorkerLaunch | CoordinatorLaunch
