"""job_model.hy の公開面の型(型検査のための宣言 — 実行時は job_model.hy を読む・#2435)。

job_model.hy は Hy の module なので、pyright は中を読めず、`doeff_cluster.shared.intent.job_model` の名が全部 Unknown になる。
worker の観測と記録の型(worker_model.pyi の ProcessView.spec・JobStatus.phase ほか)と手元の runner(sim/local.pyi)が JobSpec・
JobPhase を欄の型に使うので、ここで宣言する(runtime_env_model.pyi・service_model.pyi と同じ形)。

- JobSpec は凍った dataclass(キーワード引数に限らない — 実装は位置でも作る)。args は子の入口へ渡す引数の文字列・environ は
  名の順の (名 値) の組。
- JobPhase は Enum。値は名の小文字・`-` 区切り。
"""

from dataclasses import dataclass
from enum import Enum

@dataclass(frozen=True)
class JobSpec:
    """job 1 本の宣言。"""

    name: str
    entry: str
    args: tuple[str, ...]
    revision: str
    once: bool = False
    placement: int | None = None
    handoff: bool = False
    ready_instance: str | None = None
    handoff_abandoned: bool = False
    detached: bool = False
    runtime_env: str | None = None
    env_key: str | None = None
    program: str | None = None
    environ: tuple[tuple[str, str], ...] = ()
    keep_when_cut_off: bool = False
    hold_version: bool = False

class JobPhase(Enum):
    PREPARING = "preparing"
    CODE_FAILED = "code-failed"
    STARTING = "starting"
    PROBING = "probing"
    BACKOFF = "backoff"
    RUNNING = "running"
    STOPPING = "stopping"
    STOP_UNCONFIRMED = "stop-unconfirmed"
    PROBE_FAILED = "probe-failed"
    ENV_FAILED = "env-failed"
    HANDOFF_ABANDONED = "handoff-abandoned"
    FINISHED = "finished"
    STOPPED = "stopped"
