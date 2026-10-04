# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = launch_rules.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_core_effects.process_effects import EnvEntry as EnvEntry
from doeff_cluster.shared.intent.launch_model import WorkerLaunch as WorkerLaunch
from doeff_cluster.shared.intent.launch_model import CoordinatorLaunch as CoordinatorLaunch

def worker_launch_env(launch: WorkerLaunch) -> _Program[tuple[EnvEntry, ...], object]:
    ...

def coordinator_launch_env(launch: CoordinatorLaunch) -> _Program[tuple[EnvEntry, ...], object]:
    ...
