"""worker_model.hy の公開面の型(型検査のための宣言 — 実行時は worker_model.hy を読む・#2435)。

worker_model.hy は Hy の module なので、pyright は中を読めず、`doeff_cluster.worker.intent.worker_model` の名が全部 Unknown になる。
手元の runner sim-cluster の :policy に渡す WorkerPolicy や、観測(WorldView・CodeView ほか)を組む使い手の strict に、書き手に
直せない赤(Type of "WorkerPolicy" is unknown・Argument type is unknown ほか)が出ていた。ここで型を宣言する(job_model.pyi・
runtime_env_model.pyi と同じ形)。

- 値(CodeLayout・CodeView・ProcessView・ProbeView・EnvDisk・WarmEnv・WorldView・StopProgress・JobRecord・WorkerPolicy・JobStatus・
  DesiredJobs・DesiredUnreadable・WorkerState)は凍った dataclass(キーワード引数に限らない — 実装は位置でも作る)。ProbeStatus・
  NotYetRead・DeclarationRead(最後に読めた宣言の和 — #3731)は
  defrecord なのでキーワード引数だけ(止めの訳 SpecChanged・Undeclared・HandoffAbandoned・Retired・CutOff・WorkerStopping と JobStop も
  defrecord — #3713)。欄の型は worker_model.hy の注記と、組み手(worker の handler・判断)が入れる要素の型
  (WorldView.codes = CodeView の組・WorkerState.records = job の名 → JobRecord ほか)。
- CodeState・ProbeState・StopStage・Outcome は Enum、StartHold は defenum(StrEnum)。StopReason は止めの訳の和の型。
- effect は凍った dataclass の EffectBase[答えの型]。答えは worker の handler が返す物(ReadDesired = DesiredJobs か DesiredUnreadable・
  ObserveWorld = WorldView・残りの action と PublishStatus = None)。止めの問いは核の StopRequested(#3871)。
- Action は action の effect の和の型。
"""

from dataclasses import dataclass, field
from enum import Enum, StrEnum

from doeff_cluster.shared.intent.due_model import DueAt, DueNever, DueNow
from doeff_cluster.shared.intent.job_model import JobPhase, JobSpec
from doeff_cluster.shared.intent.runtime_env_model import EnvFailure
from doeff_core_effects.process_effects import AwaitProcessExit
from doeff_core_effects.scheduler import Future
from doeff_core_effects.warm_effects import AwaitWarmChildExit

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
    notice: Retired | HandoffAbandoned | None = None

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
    """実行環境の root の置き場の disk の観測。sweep-wanted = 掃除の係が拍を求めている。pinned = root のキー(env-<キー>)の集合。"""

    free: int
    sweep_wanted: bool
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
    warm_children: tuple[WarmChildView, ...] = ()

class StopStage(Enum):
    TERM = "term"
    KILL = "kill"

@dataclass(frozen=True)
class StopProgress:
    requested_ms: int
    stage: StopStage
    signalled_ms: int

# --- 止めの訳(#3713)— 閉じた和 StopReason -------------------------------------------

@dataclass(frozen=True, kw_only=True)
class SpecChanged:
    """宣言の spec が、動いている process を起こした spec と違う。"""

@dataclass(frozen=True, kw_only=True)
class Undeclared:
    """job が宣言から外れた。"""

@dataclass(frozen=True, kw_only=True)
class HandoffAbandoned:
    """入れ替えの諦め。"""

@dataclass(frozen=True, kw_only=True)
class Retired:
    """入れ替えで退いた旧の process。"""

@dataclass(frozen=True, kw_only=True)
class CutOff:
    """coordinator との途絶で宣言を絞った(silent_ms = 最後に届いた返事からの ms)。"""

    silent_ms: int

@dataclass(frozen=True, kw_only=True)
class WorkerStopping:
    """worker 自身の停止。"""

StopReason = SpecChanged | Undeclared | HandoffAbandoned | Retired | CutOff | WorkerStopping

@dataclass(frozen=True, kw_only=True)
class JobStop:
    """job の止めの進み(JobRecord.stopping)。"""

    requested_ms: int
    stage: StopStage
    signalled_ms: int
    reason: StopReason

class StartHold(StrEnum):
    PREPARING = "preparing"
    PREPARE_FAILED = "prepare-failed"
    DISK_FULL = "disk-full"
    PROBING = "probing"
    PROBE_FAILED = "probe-failed"
    BACKOFF = "backoff"
    WARM_CHILD = "warm-child"
    HANDOFF_ABANDONED = "handoff-abandoned"

# --- 待ちの子(#3646)-------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class WarmChildMark:
    """待ちの子の準備完了の印。threads = thread の数・vm-live = 生きた VM の数の 3 つ組。"""

    threads: int
    vm_live: tuple[int, ...]

@dataclass(frozen=True, kw_only=True)
class WarmMarkUnreadable:
    """準備完了の印の file は在るが、印の形に読めない(detail = 読めない訳)。"""

    detail: str

@dataclass(frozen=True, kw_only=True)
class WarmLaunch:
    """要る root の待ちの子の起こし方(root・uv の --project・起動で読む module)。"""

    root: str
    project: str
    preload: tuple[str, ...]

@dataclass(frozen=True, kw_only=True)
class WarmChildView:
    """root ごとの待ちの子 1 つの観測。"""

    key: str
    pid: int
    started_ms: int
    mark: WarmChildMark | WarmMarkUnreadable | None = None
    exit_code: int | None = None
    ended_ms: int | None = None
    detail: str = ""
    stop: StopProgress | None = None

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
    stopping: JobStop | None = None
    failures: int = 0
    last_start_ms: int | None = None
    unexpected_exits: int = 0
    held: StartHold | None = None

@dataclass(frozen=True)
class WorkerPolicy:
    stop_grace_ms: int = 10000
    kill_grace_ms: int = 5000
    shim_sweep_margin_ms: int = 1500
    restart_backoff_ms: int = 2000
    restart_backoff_max_ms: int = 60000
    stable_run_ms: int = 60000
    code_retry_ms: int = 30000
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

@dataclass(frozen=True, kw_only=True)
class HeartbeatSent:
    """宣言の読みが coordinator へ送った heartbeat(送った刻・名乗った worker の名・その時の生存の窓)。"""

    at: int
    worker: str
    lease_ms: int

@dataclass(frozen=True)
class DesiredJobs:
    jobs: tuple[JobSpec, ...]
    warm: tuple[WarmEnv, ...] = ()
    cut_off: CutOff | None = None
    changed: Future[bool] | None = field(default=None, compare=False)
    sent: HeartbeatSent | None = field(default=None, compare=False)

@dataclass(frozen=True)
class DesiredUnreadable:
    """宣言が読めない。"""

    reason: str
    sent: HeartbeatSent | None = field(default=None, compare=False)

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
class PublishStatus(EffectBase[None]):
    statuses: tuple[JobStatus, ...]
    note: str = ""

@dataclass(frozen=True)
class BootMarks:
    pod_ms: int | None = None
    script_ms: int | None = None
    exec_ms: int | None = None
    process_ms: int | None = None
    imported_ms: int | None = None

@dataclass(frozen=True)
class ProcessStartedMs(EffectBase[int | None]):
    pid: str

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
    warm_key: str | None = None

@dataclass(frozen=True)
class SignalJob(EffectBase[None]):
    name: str
    pid: int
    stage: StopStage
    reason: StopReason

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
class NoticeJob(EffectBase[None]):
    name: str
    pid: int
    notice: Retired | HandoffAbandoned

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

@dataclass(frozen=True)
class StartWarmChild(EffectBase[None]):
    key: str
    launch: WarmLaunch

@dataclass(frozen=True)
class StopWarmChild(EffectBase[None]):
    key: str
    stage: StopStage
    reason: str

@dataclass(frozen=True)
class ForgetWarmChild(EffectBase[None]):
    key: str

Action = (
    PrepareCode
    | PrepareEnv
    | SweepEnvs
    | StartJob
    | SignalJob
    | ReapJob
    | RetireJob
    | NoticeJob
    | ReleaseLeases
    | ProbeEntry
    | ForgetProbes
    | StartWarmChild
    | StopWarmChild
    | ForgetWarmChild
)

@dataclass(frozen=True, kw_only=True)
class NotYetRead:
    """worker が起きてから宣言をまだ一度も読めていない(#3731)。"""

@dataclass(frozen=True, kw_only=True)
class DeclarationRead:
    """最後に読めた宣言(jobs = JobSpec の列・warm = 温める env の列)。"""

    jobs: tuple[JobSpec, ...]
    warm: tuple[WarmEnv, ...]

@dataclass(frozen=True, kw_only=True)
class TickMarks:
    """拍 1 つで読んだ時刻(epoch ms — 拍の頭・EnvReport の後・ReadDesired の後・最初の ObserveWorld の後・拍の終わり)。"""

    began: int
    env_reported: int
    desired_read: int
    observed: int
    ended: int

@dataclass(frozen=True)
class WorkerState:
    declaration: NotYetRead | DeclarationRead = ...
    records: dict[str, JobRecord] = ...
    last_marks: TickMarks | None = field(default=None, compare=False)
    last_sent_ms: int | None = field(default=None, compare=False)

# --- 拍と拍の間の待ち(#2781)-----------------------------------------------------

@dataclass(frozen=True)
class AwaitNextTick(EffectBase[None]):
    policy: WorkerPolicy
    changed: Future[bool] | None
    wakes: WakeSet
    stopping: bool = False

@dataclass(frozen=True)
class WakeSet:
    due: DueAt | DueNow | DueNever
    bells: tuple[Future[object], ...]
    exits: tuple[AwaitProcessExit | AwaitWarmChildExit, ...]

@dataclass(frozen=True)
class WorkerWakes(EffectBase[WakeSet]): ...

class WorkerUnsettled(Exception): ...
