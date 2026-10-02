"""worker/protocol/declared.hy の公開面の型(型検査のための宣言 — 実行時は declared.hy を読む・#2824)。

declared.hy は Hy の module なので、pyright は中を読めず、名が全部 Unknown になる。#2751 で defk にした declared-job-specs を、
使い手の repo の模擬の世界が本番の worker と同じ読みとして `<-` で受けると、書き手に直せない赤(Type of "declared_job_specs" is
unknown・受けた値の Argument type is unknown)が出た。ここで型を宣言する(drain_requests.pyi・launch.pyi と同じ形)。

- EnvPlacement は defrecord なので凍った dataclass(キーワード引数だけ)。
- defk(env-placement・declared-job-spec・declared-job-specs・task-spec・task-specs)は呼ぶと Program を返す。答えは実装の :post
  (EnvPlacement・JobSpec・JobSpec の組)。job と task の行は heartbeat の返事の JSON の object のまま受ける。
- 実装との食い違いは packages/doeff-cluster/tests/test_service_model_stubs.py が検める。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doeff import Program
from doeff_cluster.shared.intent.job_model import JobSpec

@dataclass(frozen=True, kw_only=True)
class EnvPlacement:
    """job を起こす版と実行環境の置き場。runtime_env・env_key は実行環境の job だけが持つ(無ければ None)。"""

    revision: str
    runtime_env: str | None
    env_key: str | None

def env_placement(declared: dict[str, object] | None, revision: str | None) -> Program[EnvPlacement, Any]: ...
def declared_job_spec(job: dict[str, object]) -> Program[JobSpec, Any]: ...
def declared_job_specs(jobs: list[dict[str, object]]) -> Program[tuple[JobSpec, ...], Any]: ...

JOB_ENTRY: str

def task_spec(task: dict[str, object], task_dir: Path) -> Program[JobSpec, Any]: ...
def task_specs(tasks: list[dict[str, object]], task_dir: Path) -> Program[tuple[JobSpec, ...], Any]: ...
