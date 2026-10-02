# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = launch.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from collections.abc import Mapping as Mapping
from dataclasses import dataclass as dataclass
from pathlib import Path as Path
from doeff_core_effects.process_effects import EnvEntry as EnvEntry
from doeff_core_effects.process_effects import EnvMode as EnvMode
from doeff_cluster.shared.core.run_context_rules import process_context_environ as process_context_environ
from doeff_cluster.shared.intent.job_model import JobSpec as JobSpec
from doeff_cluster.worker.intent.worker_model import CodeLayout as CodeLayout
CHILD_ENV_ALLOWED: frozenset[str]
CHILD_ENV_PREFIXES: tuple[str, ...]

def child_environment(base: dict, extra: dict, declared: dict, worker: dict) -> _Program[dict, object]:
    ...

def env_project_dir(root: str, declared: dict) -> _Program[str, object]:
    ...

def program_file(program_dir: Path, sha: str) -> Path:
    ...

def program_file_text(blob: str, versions: Mapping[str, object]) -> str:
    ...

def shim_argv(python: str, grace_ms: int) -> _Program[tuple[str, ...], object]:
    ...

@dataclass(frozen=True, kw_only=True)
class JobLaunch:
    argv: tuple
    cwd: str
    env: tuple[EnvEntry, ...]
    env_mode: EnvMode
    work_dir: str | None
    last_used: str | None

def job_launch(spec: JobSpec, code_path: str, instance: str, attempt: int, *, python: str, hy_command: str, uv: str, extra_env: dict, layout: CodeLayout, allowed_env: dict, worker_pid: int, program_path: str | None, program_env: str, work_dir: str, shim_grace_ms: int) -> _Program[JobLaunch, object]:
    ...
