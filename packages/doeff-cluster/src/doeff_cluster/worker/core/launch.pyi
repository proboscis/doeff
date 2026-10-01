"""launch.hy の公開面の型(型検査のための宣言 — 実行時は launch.hy を読む)。

launch.hy は Hy の module なので、pyright は中を読めず、`doeff_cluster.worker.core.launch` の名が全部 Unknown になる。詰めた Program の
cache の file の置き場と中身(program-file・program-file-text)を検の道具で使う使い手に、書き手に直せない赤(Type of "program_file" is
unknown ほか)が出た(agora-redesign #2427 — doeff の handlers.hy の write-program-file を退役させる前の付け替え)。ここで型を宣言する
(job_context.pyi と同じ形)。

- defn(child-environment・env-project-dir・program-file・program-file-text)は普通の関数。defk(job-launch)は呼ぶと Program を返す。
- JobLaunch は凍った dataclass(欄の型は launch.hy の注記)。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doeff import Program
from doeff_core_effects.process_effects import EnvEntry, EnvMode
from doeff_cluster.shared.intent.job_model import JobSpec
from doeff_cluster.worker.intent.worker_model import CodeLayout

MODULE_TAGS: dict[str, str]
CHILD_ENV_ALLOWED: frozenset[str]
CHILD_ENV_PREFIXES: tuple[str, ...]

def child_environment(base: dict[str, str], extra: dict[str, str], declared: dict[str, Any], worker: dict[str, str]) -> dict[str, str]: ...
def env_project_dir(root: str, declared: dict[str, Any]) -> str: ...
def program_file(program_dir: Path, sha: str) -> Path: ...
def program_file_text(blob: str, versions: dict[str, Any]) -> str: ...

@dataclass(frozen=True, kw_only=True)
class JobLaunch:
    argv: tuple[str, ...]
    cwd: str
    env: tuple[EnvEntry, ...]
    env_mode: EnvMode
    work_dir: str | None
    last_used: str | None

def job_launch(
    spec: JobSpec,
    code_path: str,
    instance: str,
    attempt: int,
    *,
    python: str,
    hy_command: str,
    uv: str,
    extra_env: dict[str, str],
    layout: CodeLayout,
    allowed_env: dict[str, str],
    worker_pid: int,
    program_path: str | None,
    program_env: str,
    work_dir: str,
) -> Program[JobLaunch, Any]: ...
