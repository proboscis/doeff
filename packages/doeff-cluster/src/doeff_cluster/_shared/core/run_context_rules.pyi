# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = run_context_rules.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from collections.abc import Mapping as Mapping
from doeff_cluster._shared.intent.run_context import RunContext as RunContext
from doeff_cluster._shared.intent.runtime_env_model import RuntimeEnv as RuntimeEnv
from doeff_cluster._shared.core.runtime_env_rules import runtime_env_of_json as runtime_env_of_json
from doeff_cluster._shared.intent.job_model import JobSpec as JobSpec
from doeff_cluster._shared.core.job_rules import spec_hash as spec_hash

def worker_context_environ(coordinator: str, worker: str) -> _Program[dict[str, str], object]:
    ...

def process_context_environ(spec: JobSpec, instance: str, attempt: int) -> _Program[dict[str, str], object]:
    ...

def context_of_environ(environ: Mapping[str, str]) -> _Program[RunContext, object]:
    ...

def runtime_env_of_context(ctx: RunContext) -> _Program[RuntimeEnv | None, object]:
    ...
