# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = warm_rules.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from doeff_cluster.shared.intent.job_model import JobSpec as JobSpec
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv as RuntimeEnv
from doeff_cluster.shared.intent.runtime_env_model import EnvFailure as EnvFailure
from doeff_cluster.shared.intent.runtime_env_model import EnvFailureKind as EnvFailureKind
from doeff_cluster.shared.core.runtime_env_rules import runtime_env_of_json as runtime_env_of_json
from doeff_cluster.worker.intent.worker_model import WorldView as WorldView
from doeff_cluster.worker.intent.worker_model import WarmChildMark as WarmChildMark
from doeff_cluster.worker.intent.worker_model import WarmMarkUnreadable as WarmMarkUnreadable
from doeff_cluster.worker.intent.worker_model import WarmChildView as WarmChildView
from doeff_cluster.worker.intent.worker_model import WarmLaunch as WarmLaunch
from doeff_cluster.worker.intent.worker_model import WorkerPolicy as WorkerPolicy
from doeff_cluster.worker.core.worker_rules import code_key as code_key
from doeff_cluster.shared.core.runtime_env import project_dir as project_dir

def forks_from_warm_child(spec: JobSpec) -> bool:
    ...

def warm_key_of(spec: JobSpec) -> str:
    ...

def warm_mark_clean(mark: WarmChildMark | WarmMarkUnreadable) -> bool:
    ...

def warm_child_of(world: WorldView, key: str) -> WarmChildView | None:
    ...

def warm_child_ready(view: WarmChildView | None) -> bool:
    ...

def ended_before_ready(view: WarmChildView) -> bool:
    ...

def refusals_after(previous: WarmChildView | None) -> int:
    ...

def warm_child_refused(view: WarmChildView | None, policy: WorkerPolicy) -> bool:
    ...

def warm_refusal_failure(view: WarmChildView) -> EnvFailure:
    ...

def mark_refusal(mark: WarmChildMark | WarmMarkUnreadable) -> _Program[str, object]:
    ...

def warm_launch(key: str, root: str, specs: tuple, warm: tuple) -> _Program[WarmLaunch, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class WarmPlace:
    dir: str
    socket: str
    ready: str

@dataclass(frozen=True, kw_only=True)
class WarmChildFlags:
    process_group: bool
    hold_stdin: bool
    reap_group: bool
WARM_CHILD_FLAGS: WarmChildFlags

def warm_dir_of(state: str) -> _Program[str, object]:
    ...

def warm_place(warm_dir: str, key: str) -> _Program[WarmPlace, object]:
    ...

def warm_child_argv(uv: str, launch: WarmLaunch, place: WarmPlace) -> _Program[tuple[str, ...], object]:
    ...
