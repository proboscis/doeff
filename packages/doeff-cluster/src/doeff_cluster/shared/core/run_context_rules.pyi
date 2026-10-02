# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = run_context_rules.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from collections.abc import Mapping as Mapping
from doeff_cluster.shared.intent.run_context import RunContext as RunContext
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv as RuntimeEnv
from doeff_cluster.shared.core.runtime_env_rules import runtime_env_of_json as runtime_env_of_json
from doeff_cluster.shared.intent.job_model import JobSpec as JobSpec
from doeff_cluster.shared.core.job_rules import spec_hash as spec_hash

def worker_context_environ(coordinator: str, worker: str) -> _Program[dict[str, str], object]:
    ...

def process_context_environ(spec: JobSpec, instance: str, attempt: int) -> _Program[dict[str, str], object]:
    ...

def context_of_environ(environ: Mapping[str, str]) -> _Program[RunContext, object]:
    ...

def runtime_env_of_context(ctx: RunContext) -> _Program[RuntimeEnv | None, object]:
    ...
