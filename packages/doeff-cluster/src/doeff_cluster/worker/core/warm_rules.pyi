# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = warm_rules.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_cluster.shared.intent.job_model import JobSpec as JobSpec
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv as RuntimeEnv
from doeff_cluster.shared.core.runtime_env_rules import runtime_env_of_json as runtime_env_of_json
from doeff_cluster.worker.intent.worker_model import WorldView as WorldView
from doeff_cluster.worker.intent.worker_model import WarmChildMark as WarmChildMark
from doeff_cluster.worker.intent.worker_model import WarmChildView as WarmChildView
from doeff_cluster.worker.core.worker_rules import code_key as code_key

def forks_from_warm_child(spec: JobSpec) -> bool:
    ...

def warm_key_of(spec: JobSpec) -> str:
    ...

def warm_mark_clean(mark: WarmChildMark) -> bool:
    ...

def warm_child_of(world: WorldView, key: str) -> WarmChildView | None:
    ...

def warm_child_ready(view: WarmChildView | None) -> bool:
    ...

def mark_refusal(mark: WarmChildMark) -> str:
    ...

def warm_preload(key: str, specs: tuple, warm: tuple) -> _Program[tuple, object]:
    ...
