# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = upgrade_invariants.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum

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
    doeff_commit: str

@dataclass(frozen=True, kw_only=True)
class PendingTask:
    task: str
    phase: PendingPhase
    worker: str | None

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

def worker_swap_waits_for_its_tasks(starts: tuple[UpgradeStart, ...]) -> _Program[tuple[UpgradeBreach, ...], object]:
    ...

def one_worker_at_a_time(starts: tuple[UpgradeStart, ...]) -> _Program[tuple[UpgradeBreach, ...], object]:
    ...

def coordinator_swap_on_an_empty_queue(starts: tuple[UpgradeStart, ...]) -> _Program[tuple[UpgradeBreach, ...], object]:
    ...
