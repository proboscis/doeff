"""worker_model.hy の公開面の型(型検査のための宣言 — 実行時は worker_model.hy を読む・#2435)。

worker_model.hy は Hy の module なので、pyright は中を読めず、`doeff_cluster.worker.intent.worker_model` の名が全部 Unknown になる。
手元の runner sim-cluster の :policy に渡す WorkerPolicy や、観測(WorldView・CodeView ほか)を組む使い手の strict に、書き手に
直せない赤(Type of "WorkerPolicy" is unknown・Argument type is unknown ほか)が出ていた。ここで型を宣言する(job_model.pyi・
runtime_env_model.pyi と同じ形)。

- 値(CodeLayout・CodeView・ProcessView・ProbeView・EnvDisk・WarmEnv・WorldView・StopProgress・JobRecord・WorkerPolicy・JobStatus・
  DesiredJobs・DesiredUnreadable・WorkerState)は凍った dataclass(キーワード引数に限らない — 実装は位置でも作る)。ProbeStatus は
  defrecord なのでキーワード引数だけ。欄の型は worker_model.hy の注記と、組み手(worker の handler・判断)が入れる要素の型
  (WorldView.codes = CodeView の組・WorkerState.records = job の名 → JobRecord ほか)。
- CodeState・ProbeState・StopStage・Outcome は Enum。
- effect は凍った dataclass の EffectBase[答えの型]。答えは worker の handler が返す物(ReadDesired = DesiredJobs か DesiredUnreadable・
  ObserveWorld = WorldView・WorkerStopRequested = bool・残りの action と PublishStatus = None)。
- Action は action の effect の和の型。
"""

from dataclasses import dataclass, field
from enum import Enum

from doeff_cluster.shared.intent.job_model import JobPhase, JobSpec
from doeff_cluster.shared.intent.runtime_env_model import EnvFailure
from doeff_core_effects.scheduler import Future

from doeff import EffectBase

@dataclass(frozen=True)
class CodeLayout:
    """業務の repo の木の形(子 process の import の根と、土台の import の路)。"""

    import_roots: tuple[str, ...] = (".",)
    base_paths: tuple[str, ...] = ()
    def pythonpath(self, tree: str) -> str: ...
    def roots_arg(self) -> str: ...

class CodeState(Enum):
    PREPARING = "preparing"
    READY = "ready"
    FAILED = "failed"

@dataclass(frozen=True)
class CodeView:
    """revision ごとに展開したコードの観測。"""

    revision: str
    state: CodeState
    path: str | None = None
    detail: str = ""
    failed_ms: int | None = None
    failure: EnvFailure | None = None

@dataclass(frozen=True)
class ProcessView:
    """worker が起動した子 process 1 本の観測。"""

    name: str
    spec: JobSpec
    attempt: int
    pid: int
    started_ms: int
    exit_code: int | None = None
    instance: str = ""
    retired_from: str | None = None

class ProbeState(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"

@dataclass(frozen=True)
class ProbeView:
    """入口の検め(probe)1 回の観測。"""

    spec_hash: str
    state: ProbeState
    detail: str = ""
    failed_ms: int | None = None
    started_ms: int | None = None
    attempts: int = 1
    last_failure: str = ""

@dataclass(frozen=True, kw_only=True)
class ProbeStatus:
    """状態の報告に載せる入口の検めの姿。"""

    state: str
    elapsed_seconds: int
    attempts: int
    last_failure: str

@dataclass(frozen=True)
class EnvDisk:
    """実行環境の root の置き場の disk の観測。pinned = root のキー(env-<キー>)の集合。"""

    free: int
    floor: int
    pinned: frozenset[str]

@dataclass(frozen=True)
class WarmEnv:
    """coordinator の温める表から受けた env 1 つ。"""

    key: str
    runtime_env: str

@dataclass(frozen=True)
class WorldView:
    codes: tuple[CodeView, ...]
    processes: tuple[ProcessView, ...]
    probes: tuple[ProbeView, ...] = ()
    env_disk: EnvDisk | None = None

class StopStage(Enum):
    TERM = "term"
    KILL = "kill"

@dataclass(frozen=True)
class StopProgress:
    requested_ms: int
    stage: StopStage
    signalled_ms: int

class Outcome(Enum):
    EXITED = "exited"
    STOPPED = "stopped"

@dataclass(frozen=True)
class JobRecord:
    """worker の一時的な記憶。"""

    name: str
    attempts: int = 0
    last_exit_ms: int | None = None
    last_outcome: Outcome | None = None
    last_exit_code: int | None = None
    stopping: StopProgress | None = None
    failures: int = 0
    last_start_ms: int | None = None
    unexpected_exits: int = 0

@dataclass(frozen=True)
class WorkerPolicy:
    stop_grace_ms: int = 10000
    kill_grace_ms: int = 5000
    shim_sweep_margin_ms: int = 1500
    restart_backoff_ms: int = 2000
    restart_backoff_max_ms: int = 60000
    stable_run_ms: int = 60000
    code_retry_ms: int = 30000
    tick_seconds: float = 0.5
    wake_gap_seconds: float = 0.1

@dataclass(frozen=True)
class JobStatus:
    name: str
    phase: JobPhase
    desired_revision: str | None
    running_revision: str | None
    pid: int | None
    attempts: int
    detail: str = ""
    failures: int = 0
    last_exit_code: int | None = None
    last_exit_at_ms: int | None = None
    instance: str | None = None
    spec_hash: str | None = None
    placement: int | None = None
    retired_from: str | None = None
    failure: EnvFailure | None = None
    probe: ProbeStatus | None = None

# --- 宣言の読み取り -------------------------------------------------------------

@dataclass(frozen=True)
class DesiredJobs:
    jobs: tuple[JobSpec, ...]
    warm: tuple[WarmEnv, ...] = ()
    changed: Future[bool] | None = field(default=None, compare=False)

@dataclass(frozen=True)
class DesiredUnreadable:
    """宣言が読めない。"""

    reason: str

# --- effect ----------------------------------------------------------------------

@dataclass(frozen=True)
class ReadDesired(EffectBase[DesiredJobs | DesiredUnreadable]):
    env_report: dict[str, object] | None = None
    stopping: bool = False

@dataclass(frozen=True)
class EnvReport(EffectBase[dict[str, object] | None]): ...

@dataclass(frozen=True)
class ObserveWorld(EffectBase[WorldView]): ...

@dataclass(frozen=True)
class WorkerStopRequested(EffectBase[bool]): ...

@dataclass(frozen=True)
class PublishStatus(EffectBase[None]):
    statuses: tuple[JobStatus, ...]
    note: str = ""

# --- action(判断の結果。そのまま effect として実行する) -------------------------

@dataclass(frozen=True)
class PrepareCode(EffectBase[None]):
    revision: str

@dataclass(frozen=True)
class PrepareEnv(EffectBase[None]):
    key: str
    runtime_env: str
    warm: bool = False

@dataclass(frozen=True)
class SweepEnvs(EffectBase[None]):
    pinned: frozenset[str]

@dataclass(frozen=True)
class StartJob(EffectBase[None]):
    spec: JobSpec
    attempt: int
    code_path: str

@dataclass(frozen=True)
class SignalJob(EffectBase[None]):
    name: str
    pid: int
    stage: StopStage

@dataclass(frozen=True)
class ReapJob(EffectBase[None]):
    name: str
    pid: int
    outcome: Outcome
    exit_code: int

@dataclass(frozen=True)
class RetireJob(EffectBase[None]):
    name: str
    pid: int
    new_name: str

@dataclass(frozen=True)
class ProbeEntry(EffectBase[None]):
    spec: JobSpec
    code_path: str

@dataclass(frozen=True)
class ForgetProbes(EffectBase[None]):
    keep: frozenset[str]

@dataclass(frozen=True)
class ReleaseLeases(EffectBase[None]):
    job: str
    instance: str

Action = (
    PrepareCode
    | PrepareEnv
    | SweepEnvs
    | StartJob
    | SignalJob
    | ReapJob
    | RetireJob
    | ReleaseLeases
    | ProbeEntry
    | ForgetProbes
)

@dataclass(frozen=True)
class WorkerState:
    desired: tuple[JobSpec, ...] = ()
    records: dict[str, JobRecord] = ...
    warm: tuple[WarmEnv, ...] = ()

# --- 拍と拍の間の待ち(#2781)-----------------------------------------------------

@dataclass(frozen=True)
class AwaitNextTick(EffectBase[None]):
    policy: WorkerPolicy
    changed: Future[bool] | None
    state: WorkerState
