# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = upgrade_invariants.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from doeff_cluster.coordinator.intent.cluster_model import WorkerInfo as WorkerInfo
from doeff_cluster.coordinator.core.cluster_policy import placeable as placeable

class UpgradeKind(StrEnum):
    WORKER = 'worker'
    COORDINATOR = 'coordinator'

class PendingPhase(StrEnum):
    QUEUED = 'queued'
    ASSIGNED = 'assigned'

@dataclass(frozen=True, kw_only=True)
class RosterEntry:
    info: WorkerInfo
    live: bool
    doeff_commit: str

@dataclass(frozen=True, kw_only=True)
class PendingTask:
    task: str
    phase: PendingPhase
    needs: tuple[str, ...]

@dataclass(frozen=True, kw_only=True)
class UpgradeStart:
    at_ms: int
    kind: UpgradeKind
    target: str
    doeff_commit: str
    roster: tuple[RosterEntry, ...]
    tasks: tuple[PendingTask, ...]

@dataclass(frozen=True, kw_only=True)
class UpgradeBreach:
    rule: str
    at_ms: int
    target: str
    detail: str

def coordinator_after_every_worker(starts: tuple[UpgradeStart, ...]) -> _Program[tuple[UpgradeBreach, ...], object]:
    ...

def worker_swap_leaves_a_taker(starts: tuple[UpgradeStart, ...]) -> _Program[tuple[UpgradeBreach, ...], object]:
    ...

def one_worker_at_a_time(starts: tuple[UpgradeStart, ...]) -> _Program[tuple[UpgradeBreach, ...], object]:
    ...
