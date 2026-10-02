# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = worker_rules.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff_cluster.shared.intent.job_model import JobSpec as JobSpec
from doeff_cluster.worker.intent.worker_model import CodeView as CodeView
ENV_KEY_PREFIX: str

def code_key(spec: JobSpec) -> str:
    ...

def ready_path(code: CodeView | None) -> str | None:
    ...
RETIRED_MARK: str

def retired_name(name: str, instance: str) -> str:
    ...

def probed_job(spec: JobSpec) -> bool:
    ...

def probe_args(spec: JobSpec) -> tuple:
    ...
OLD_SERVICE_FLAGS: tuple[str, ...]

def probe_refusal(spec: JobSpec) -> str | None:
    ...
