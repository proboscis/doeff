# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = declared.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from pathlib import Path as Path
from doeff_hy.wire import Malformed as Malformed
from doeff_hy.wire import parse as parse
from doeff_cluster.shared.core.capabilities import environ_pairs as environ_pairs
from doeff_cluster.shared.core.runtime_env_rules import runtime_env_of_json as runtime_env_of_json
from doeff_cluster.shared.core.runtime_env_rules import env_key as env_key
from doeff_cluster.shared.core.native_wheel import current_platform as current_platform
from doeff_cluster.shared.intent.job_model import JobSpec as JobSpec
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv as RuntimeEnv
from doeff_cluster.worker.core.worker_rules import ENV_KEY_PREFIX as ENV_KEY_PREFIX

@dataclass(frozen=True, kw_only=True)
class EnvPlacement:
    revision: str
    runtime_env: str | None
    env_key: str | None

def env_placement(declared: dict[str, object] | None, revision: str | None) -> _Program[EnvPlacement, object]:
    ...

def declared_job_spec(job: dict[str, object], draining: bool) -> _Program[JobSpec, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class DeclaredReply:
    jobs: tuple[dict[str, object], ...]
    draining: bool

class DeclaredReplyMalformed(ValueError):
    ...

def declared_reply_of_json(reply: dict[str, object]) -> _Program[DeclaredReply, object]:
    ...

def declared_job_specs(reply: DeclaredReply) -> _Program[tuple[JobSpec, ...], object]:
    ...
JOB_ENTRY: str

def task_spec(task: dict[str, object], task_dir: Path) -> _Program[JobSpec, object]:
    ...

def task_specs(tasks: list[dict[str, object]], task_dir: Path) -> _Program[tuple[JobSpec, ...], object]:
    ...
