# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = upgrade_invariants.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from doeff_cluster.shared.intent.upgrade_model import UpgradeKind as UpgradeKind
from doeff_cluster.shared.intent.upgrade_model import PendingPhase as PendingPhase
from doeff_cluster.shared.intent.upgrade_model import RosterEntry as RosterEntry
from doeff_cluster.shared.intent.upgrade_model import PendingTask as PendingTask
from doeff_cluster.shared.intent.upgrade_model import UpgradeStart as UpgradeStart
from doeff_cluster.shared.intent.upgrade_model import BootRootsAtStart as BootRootsAtStart

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

def swap_after_boot_root_prepared(places: tuple[BootRootsAtStart, ...]) -> _Program[tuple[UpgradeBreach, ...], object]:
    ...
