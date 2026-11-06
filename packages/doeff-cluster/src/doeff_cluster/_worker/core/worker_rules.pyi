# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = worker_rules.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from doeff_cluster._shared.intent.job_model import JobSpec as JobSpec
from doeff_cluster._worker.intent.worker_model import CodeView as CodeView
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

def probe_args(spec: JobSpec) -> _Program[tuple, object]:
    ...
OLD_SERVICE_FLAGS: tuple[str, ...]

def probe_refusal(spec: JobSpec) -> _Program[str | None, object]:
    ...
