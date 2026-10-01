"""job_context.hy の公開面の型(型検査のための宣言 — 実行時は job_context.hy を読む)。

job_context.hy は Hy の module なので、pyright は中を読めず、`doeff_cluster.job_context` の名が全部 Unknown になる。宿の
run-context(RunContext)を模擬の土台で組む使い手に、書き手に直せない赤(Type of "RunContext" is unknown・Argument type is
unknown ほか)が使い手の file ごとに出た。ここで型を宣言する(runtime_env_model.pyi・service_model.pyi と同じ形)。

- RunContext は凍った dataclass(位置でも組める — context-of-environ が先頭の 4 欄を位置で渡す)。欄の型は job_context.hy の注記。
- defk(worker-context-environ・process-context-environ・context-of-environ・runtime-env-of-context)は呼ぶと Program を返す。
  defn(context-from-env)は普通の関数。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from doeff import Program
from doeff_cluster.shared.intent.job_model import JobSpec
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv

@dataclass(frozen=True)
class RunContext:
    """実行先の文脈(worker が環境変数で渡す)。env の組み立てだけが読む。"""

    coordinator_url: str
    worker: str
    revision: str
    job: str
    instance: str = ""
    attempt: str = ""
    spec_hash: str = ""
    placement: str = ""
    runtime_env: str = ""
    env_key: str = ""

    def identity(self) -> dict[str, str | int | None]: ...

def worker_context_environ(coordinator: str, worker: str) -> Program[dict[str, str], Any]: ...
def process_context_environ(spec: JobSpec, instance: str, attempt: int) -> Program[dict[str, str], Any]: ...
def context_of_environ(environ: Mapping[str, str]) -> Program[RunContext, Any]: ...
def context_from_env() -> RunContext: ...
def runtime_env_of_context(ctx: RunContext) -> Program[RuntimeEnv | None, Any]: ...
