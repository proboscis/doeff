# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = invariants.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from doeff_cluster.worker.intent.worker_model import StartJob as StartJob
from doeff_cluster.worker.intent.worker_model import SignalJob as SignalJob
from doeff_cluster.worker.intent.worker_model import ReapJob as ReapJob
from doeff_cluster.worker.intent.worker_model import WorldView as WorldView
from doeff_cluster.worker.core.worker_rules import ENV_KEY_PREFIX as ENV_KEY_PREFIX

def handoff_keeps_a_ready_writer(lifetimes: tuple) -> _Program[tuple, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class DescendantLife:
    pid: int
    label: str
    last_alive_ms: int | None

@dataclass(frozen=True, kw_only=True)
class DescendantOutlivedTheStop:
    stopped_ms: int
    life: DescendantLife

def stopped_job_leaves_no_descendant(stopped_ms: int, lives: tuple[DescendantLife, ...]) -> _Program[tuple[DescendantOutlivedTheStop, ...], object]:
    ...

def warm_fork_uses_its_own_root(actions: tuple) -> _Program[tuple, object]:
    ...

def warm_child_state_leaves_running_tasks(baseline: tuple, variant: tuple) -> _Program[tuple, object]:
    ...

def warm_fork_only_before_any_vm(actions: tuple, world: WorldView) -> _Program[tuple, object]:
    ...
