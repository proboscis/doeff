"""local.hy の公開面の型(型検査のための宣言 — 実行時は local.hy を読む・#2374)。

local.hy は Hy の module なので、pyright は中を読めず、`doeff_cluster.sim.local` の名が全部 Unknown になる。手元の runner
sim-cluster の担い手(SimWorker)や process ごとの外の世界(ProcessOutside)を名指す使い手(業務の側の模擬の検・doeff-cluster の検)の
strict に、書き手に直せない赤(Type of "SimWorker" is unknown ほか)が出ていた。ここで型を宣言する(service_model.pyi・
runtime_env_model.pyi と同じ形)。

宣言する範囲:
- 公開の値(defrecord SimWorker・SimProcess・SimReport・SimPreparation・SimWatchFailure・SimCoordinatorRun・SimLink・
  SimOutside・ProcessOutside)は凍った・キーワード引数だけの dataclass。欄の型は local.hy の注記と、構成子・世界が実際に入れる要素の型。
  SimWorker の provides / exclusive は実装が読むのが包含だけなので集合一般(AbstractSet — 使い手は set も frozenset も渡す)、
  DeclareRollout の spec は資源の本文へそのまま運ぶので読みだけの Mapping と書く。
- 検の effect(DeclareRollout … ClientLink)は凍った dataclass の EffectBase[答えの型]。答えの型は defeffect の :answer に、tuple の要素の型を
  足した物(ProcessesOf = SimProcess の tuple ほか)。契約の effect(Crash・Redeclare・ReadinessOf・KillWorker・StopWorker・
  StopCoordinator・CrashCoordinator)と答えの型 ServiceReadiness は shared/intent/cluster_control(道具の .pyi)から読む — local.hy が
  そこから import して答えるので、ここでは同じ名で読み直すだけ(#3029)。
- 入口 sim-cluster・wall-sim-cluster は筋書きの答えの型をそのまま返す(defk は呼ぶと Program を返す)。
- 仕組みの名のうち、他の module(doeff-cluster の検・業務の側の模擬)が import する物(SimChild・SimParts・SimExit・HostTruth・
  PartsOf・HostTruthOf・EndProcess と、coordinator-answers・host-answers・run-context-of・send-request・sim-process・
  process-outside・ended-process・heartbeat・settle-beats・note-watch)も宣言する。外から使われない仕組み(SimPlan・PlanOf ほかの世界の effect と
  筋の組み立て)は宣言しない。

型の宣言がまだ無い doeff-cluster の Hy の module(coordinator/entry/handler_sets の RequestQueue・MemoryWalStore・shared/intent/run_context の
RunContext・shared/intent/protocol の PlainText・ClusterTiming ほか)の型は、import すると Unknown に引きずられるので、この module が
読む欄だけを Protocol(_RequestQueueView ほか)で書く — 実物はその欄の形でこれを満たす。それらの module に宣言を置いたら実物の型へ
置き換える。読まずに運ぶだけの値(heartbeat の答え・HostTruth の process の観測など)は object と書く。
job_model の JobSpec と worker_model の WorkerPolicy は宣言(job_model.pyi・worker_model.pyi — #2435)が付いたので実物の型で書く。
"""

from collections.abc import Callable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Protocol, TypeVar

from doeff_cluster.shared.intent.cluster_control import Crash as Crash
from doeff_cluster.shared.intent.cluster_control import CrashCoordinator as CrashCoordinator
from doeff_cluster.shared.intent.cluster_control import KillWorker as KillWorker
from doeff_cluster.shared.intent.cluster_control import ReadinessOf as ReadinessOf
from doeff_cluster.shared.intent.cluster_control import Redeclare as Redeclare
from doeff_cluster.shared.intent.cluster_control import ServiceReadiness as ServiceReadiness
from doeff_cluster.shared.intent.cluster_control import StopCoordinator as StopCoordinator
from doeff_cluster.shared.intent.cluster_control import StopWorker as StopWorker
from doeff_cluster.coordinator.intent.cluster_model import ClusterState
from doeff_cluster.shared.intent.job_model import JobSpec
from doeff_cluster.shared.intent.runtime_env_model import EnvFailure, RuntimeEnv
from doeff_cluster.shared.intent.service_model import System
from doeff_cluster.worker.intent.worker_model import WorkerPolicy
from doeff_core_effects.process_effects import EnvEntry
from doeff_core_effects.scheduler import Promise, Task
from doeff_events import ArmedTimer, MemoryBroker
from doeff_hy.json_value import JsonValue
from doeff_vm import WithHandler

from doeff import EffectBase, Program

_Answer = TypeVar("_Answer")

# --- 型の宣言の無い module の値の、この module が読む欄 ----------------------------------------------------

class _RequestQueueView(Protocol):
    """coordinator の受け口(protocol.request_queue.RequestQueue)— up = 受け付けているか・crash_waiting = 落ちの注入が待っているか。"""

    @property
    def up(self) -> bool: ...
    @property
    def crash_waiting(self) -> bool: ...

class _WalStoreView(Protocol):
    """coordinator の置き場(handler_sets.MemoryWalStore)— load = 置き場の鍵 → 値。"""

    def load(self) -> dict[str, object]: ...

class _RunContextView(Protocol):
    """宿の契約の run-context(shared/intent/run_context の RunContext)の、sim が読む欄。"""

    @property
    def job(self) -> str: ...
    @property
    def worker(self) -> str: ...
    @property
    def revision(self) -> str: ...
    @property
    def instance(self) -> str: ...

class _ClusterTimingView(Protocol):
    """coordinator と worker の時間の設定(protocol.ClusterTiming)の、sim が読む欄。"""

    @property
    def fence_ms(self) -> int: ...
    @property
    def client_reply_ms(self) -> int: ...
    @property
    def watch_max_ms(self) -> int: ...

class _PlainTextView(Protocol):
    """JSON でない返事の本文(protocol.PlainText — GET /metrics の text)。"""

    @property
    def text(self) -> str: ...
    @property
    def content_type(self) -> str: ...

class _WatchReadingView(Protocol):
    """GET /watch の返事 1 つの読み(beat_policy.WatchReading)— kind = beat_policy.WatchKind の値・revision = 次の版。"""

    @property
    def kind(self) -> object: ...
    @property
    def revision(self) -> int | None: ...

class _Handler(Protocol):
    """defhandler を引数に当てた物 — 本文の Program を受けて handler を被せる(答えの型は本文と同じ)。"""

    def __call__(self, body: Program[_Answer, object], /) -> WithHandler[_Answer]: ...

# --- 定数 ---------------------------------------------------------------------------------------------

NO_STATE_FILE: str
SIM_URL: str
SIM_START_MS: int
SIM_TIMING_RATIO: int
SIM_TICK_SECONDS: float
DECLARE_ACTOR: str
CLIENT_NAME: str
TASK_POLL_SECONDS: float
TASK_LEASE_SECONDS: float
DETACHED_POLL_SECONDS: float
QUEUE_WAIT_SECONDS: float
WORKER_WAIT_SECONDS: float
DRAIN_TTL_SECONDS: float
REPORT_KINDS: tuple[str, ...]
KILLED_CODE: int
PAUSE_STOP: str
PAUSE_CRASH: str
CUT_REASON: str
FAULT_REASON: str

# --- 公開の値 -----------------------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class SimWorker:
    """sim の worker 1 台(本番の worker の --provides・--exclusive・--capacity・--task-reserve・node に当たる)。"""

    name: str
    provides: AbstractSet[str]
    task_reserve: int
    doeff_commit: str = ""
    exclusive: AbstractSet[str] = ...
    capacity: int = 10
    node: str = ""
    versions: dict[str, str] | None = None
    prepare_seconds: float = 0.0
    env_prepare_seconds: float | None = None
    env_failure: EnvFailure | None = None
    starts_down: bool = False
    ignores_fence: bool = False
    beat_every_ms: int | None = None
    retire_stops: bool = False
    ignores_keep_marks: bool = False
    silent_stop: bool = False
    overstates_capacity: int | None = None
    claims_provides: AbstractSet[str] | None = None
    claims_exclusive: AbstractSet[str] | None = None
    fresh_boot_every_beat: bool = False
    hides_retired: bool = False
    claims_task_reserve: int | None = None
    silent_notices: bool = False

@dataclass(frozen=True, kw_only=True)
class SimProcess:
    """sim の宿が起こした process 1 つ(ProcessesOf の答えの要素)。value = service が値で終わった時のその値(型は Program ごと)。"""

    job: str
    worker: str
    instance: str
    attempt: int
    pid: int
    spec_hash: str
    started_ms: int
    ended_ms: int | None = None
    exit_code: int | None = None
    detail: str = ""
    value: object = None

@dataclass(frozen=True, kw_only=True)
class SimReport:
    """coordinator に届いた service の報告 1 つ(ReportsOf の答えの要素)。"""

    job: str
    kind: str
    instance: str
    at: int
    ready: bool | None
    reason: str
    role: str
    metrics: dict[str, JsonValue] | None


@dataclass(frozen=True, kw_only=True)
class SimPreparation:
    """worker の宿が起こした準備 1 つ(PreparationsOf の答えの要素)。"""

    worker: str
    key: str
    env: bool
    warm: bool
    started_ms: int
    ready_ms: int
    failure: EnvFailure | None

@dataclass(frozen=True, kw_only=True)
class SimWatchFailure:
    """worker の名指しの待ちの task が思わぬ例外で止まった記録 1 つ(WatchFailuresOf の答えの要素)。"""

    worker: str
    boot: str
    at: int
    reason: str

@dataclass(frozen=True, kw_only=True)
class SimCoordinatorRun:
    """coordinator の Pod の一生 1 つ(CoordinatorRuns の答えの要素)。"""

    started_ms: int
    ended_ms: int | None = None
    outcome: str = ""

@dataclass(frozen=True, kw_only=True)
class SimLink:
    """coordinator へ話す送り手の口 1 つ(coordinator-answers の引数)。"""

    queue: _RequestQueueView
    actor: str
    revision: str
    peer: str
    versions: dict[str, str]
    timing: _ClusterTimingView
    runtime_env: RuntimeEnv | None = None

@dataclass(frozen=True, kw_only=True)
class ProcessOutside:
    """process ごとの外の世界(SimOutside.per_process の答え): handlers = 柵の外側に並べる handler(外側が先)・effects = その
    process の柵だけが外へ通す effect の型。"""

    handlers: tuple[Callable[..., object], ...]
    effects: tuple[type[object], ...] = ()

@dataclass(frozen=True, kw_only=True)
class SimOutside:
    """sim の外の世界: handlers = 全部の job と筋書きの外側に置く handler の組(外側が先)・effects = それが答える effect の型・
    per_process = (job の名 worker の名) → ProcessOutside(None = 無し)。"""

    handlers: list[Callable[..., object]]
    effects: tuple[type[object], ...]
    per_process: Callable[[str, str], ProcessOutside] | None = None

# --- 行き止まりの見張り(#3078)-----------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class BusinessWait:
    """出来事を待っている業務の task 1 つ: process の pid・job の名・待つ出来事の型の名(, で繋いだ文)。"""

    pid: int
    job: str
    events: str

@dataclass(frozen=True, kw_only=True)
class LiveProcess:
    """生きている業務の process 1 つ: pid・job の名・task の数(主の task 1 + 中で Spawn した task の数)。"""

    pid: int
    job: str
    tasks: int

@dataclass(frozen=True, kw_only=True)
class WaitSnapshot:
    """行き止まりの判じ(deadlock_of)の材料。armed_timers = 業務の timer(#3093)・world_due = sim の世界の次の予定の刻(#3094)。"""

    live: tuple[LiveProcess, ...]
    waits: tuple[BusinessWait, ...]
    scenario_waiting: bool
    rows_settled: bool
    armed_timers: tuple[ArmedTimer, ...] = ()
    world_due: int | None = None

@dataclass(frozen=True, kw_only=True)
class SimDeadlock:
    """行き止まり: waits = 出来事を待って止まっている業務の task の全部・at_ms = 見張りが見つけた仮想の時計の刻。"""

    waits: tuple[BusinessWait, ...]
    at_ms: int | None = None

class SimDeadlockError(RuntimeError):
    """sim-cluster の行き止まり(args = 知らせの文と SimDeadlock)。"""

class SimLivenessError(RuntimeError):
    """比で延ばした世界(:timing を渡さない筋書き)で worker の死の判断が出た(args = 知らせの文と WorkerGone)。"""

def deadlock_of(snapshot: WaitSnapshot) -> Program[SimDeadlock | None, object]: ...
def earliest_due(world_due: int | None, armed: tuple[ArmedTimer, ...]) -> Program[int | None, object]: ...

# --- 検の effect(sim の世界が答える)---------------------------------------------------------------------

@dataclass(frozen=True)
class DeclareRollout(EffectBase[dict[str, JsonValue]]):
    name: str
    spec: Mapping[str, object]

@dataclass(frozen=True)
class KubeCalls(EffectBase[tuple[dict[str, object], ...]]): ...

@dataclass(frozen=True)
class SettleDeployment(EffectBase[None]):
    namespace: str
    name: str
    ready: int | None = None

@dataclass(frozen=True)
class ReportsOf(EffectBase[tuple[SimReport, ...]]):
    name: str

@dataclass(frozen=True)
class ProcessesOf(EffectBase[tuple[SimProcess, ...]]):
    name: str

# 柵の仕組みの effect(process の中で Spawn した task の把手を世界に覚えさせる)。検は、殺された process に後から task が
# 覚えられる登録の隙間を作るために出す(#3057)。
@dataclass(frozen=True)
class KeepChild(EffectBase[None]):
    pid: int
    task: Task[object]

@dataclass(frozen=True)
class AwaitProcessStarted(EffectBase[SimProcess]):
    name: str

@dataclass(frozen=True)
class SharedRows(EffectBase[dict[str, JsonValue]]):
    prefix: str

@dataclass(frozen=True)
class ReadCoordinator(EffectBase[dict[str, JsonValue] | _PlainTextView]):
    path: str

@dataclass(frozen=True)
class FailRoute(EffectBase[None]):
    method: str
    path: str
    status: int
    seconds: float

@dataclass(frozen=True)
class CoordinatorRuns(EffectBase[tuple[SimCoordinatorRun, ...]]): ...

@dataclass(frozen=True)
class ReplaceCoordinatorEnviron(EffectBase[None]):
    environ: tuple[EnvEntry, ...]

@dataclass(frozen=True)
class CoordinatorEnvironOf(EffectBase[tuple[EnvEntry, ...]]): ...

@dataclass(frozen=True)
class CoordinatorStep:
    """置き場へ書いた coordinator の歩 1 つの記録(刻と、その歩の Persist の writes の列 — #2670 の根 B・#3865)。"""

    at: int
    writes: tuple[object, ...]

@dataclass(frozen=True)
class CoordinatorSteps(EffectBase[tuple[CoordinatorStep, ...]]): ...

@dataclass(frozen=True)
class StartWorker(EffectBase[bool]):
    name: str

@dataclass(frozen=True)
class ReplaceWorker(EffectBase[bool]):
    name: str
    worker: SimWorker

@dataclass(frozen=True)
class WorkerOf(EffectBase[SimWorker]):
    name: str

@dataclass(frozen=True)
class CutWorker(EffectBase[None]):
    name: str
    seconds: float

@dataclass(frozen=True)
class StallWorker(EffectBase[None]):
    name: str
    seconds: float

@dataclass(frozen=True)
class NextWorldDue(EffectBase[int | None]):
    """sim の世界の次の予定の刻(行き止まりの見張りが問う・#3094)— now_ms より後の最も早い予定の刻か、頼まれた止まりが残れば now_ms・無ければ None。"""
    now_ms: int

@dataclass(frozen=True)
class DrainWorker(EffectBase[dict[str, JsonValue]]):
    name: str
    ttl_seconds: float = ...

@dataclass(frozen=True)
class PreparationsOf(EffectBase[tuple[SimPreparation, ...]]):
    name: str

@dataclass(frozen=True)
class WatchFailuresOf(EffectBase[tuple[SimWatchFailure, ...]]):
    name: str

@dataclass(frozen=True)
class ClientLink(EffectBase[SimLink]): ...

# --- 仕組みの名のうち、他の module が使う物 --------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class SimParts:
    """coordinator の Pod の部品(1 回の走りに 1 組)。"""

    queue: _RequestQueueView
    store: _WalStoreView
    stop: object
    kube: object
    broker: MemoryBroker

@dataclass(frozen=True, kw_only=True)
class SimExit:
    """sim の子 process の終わり方: code = exit-code・result = task の詰めた結果(service は None)。"""

    code: int
    result: str | None
    detail: str = ""
    value: object = None

@dataclass(frozen=True, kw_only=True)
class HostTruth:
    """worker の宿 1 つの真実(世界の session に在る)。processes・probes・statuses・last_desired・last_warm・task_echo は
    worker_model の観測の値(型の宣言が無い — 読まずに運ぶ)。rest_bell は静かな拍を眠る宿の呼び鈴(request_queue.RestBell —
    型の宣言が無い・読まずに運ぶ — #2850)。"""

    boot: str
    boot_at: int
    processes: tuple[object, ...]
    codes: dict[str, SimPreparation]
    probes: dict[str, object]
    statuses: list[object]
    last_ok_ms: int
    fence_ms: int
    last_desired: tuple[object, ...]
    last_warm: tuple[object, ...]
    programs: dict[str, str]
    results: dict[str, str]
    task_echo: dict[str, object]
    beats: int = 0
    down: bool = False
    stopping: bool = False
    fresh: bool = False
    sent_statuses: list[object] | None = None
    beat_interval_ms: int = ...
    watch_after: int | None = None
    watch_confirmed: bool = False
    watch_unsupported: bool = False
    woken: bool = False
    beat_bells: tuple[Promise[object], ...] = ()
    watch_failure: str | None = None
    tick_bell: Promise[object] | None = None
    stalled_until_ms: int = 0
    keep_fence_ms: int = ...
    sent_stopping: bool = False
    rest_bell: object | None = None
    rest_reach: int = ...
    warm_children: tuple[object, ...] = ()

@dataclass(frozen=True, kw_only=True)
class SimChild:
    """sim の子 process 1 つに宿が答える物(host-answers の引数)。"""

    ctx: _RunContextView
    program_path: str
    environ: dict[str, str]
    link: SimLink
    pid: int
    passable: tuple[type[object], ...]
    outside: tuple[Callable[..., object], ...] = ()

@dataclass(frozen=True)
class PartsOf(EffectBase[SimParts]): ...

@dataclass(frozen=True)
class HostTruthOf(EffectBase[HostTruth]):
    name: str

@dataclass(frozen=True)
class EndProcess(EffectBase[None]):
    worker: str
    pid: int
    ended: SimExit

def coordinator_answers(link: SimLink) -> _Handler: ...
def host_answers(child: SimChild) -> _Handler: ...
def run_context_of(
    worker: str, spec: JobSpec, attempt: int, instance: str
) -> Program[_RunContextView, object]: ...
def send_request(
    link: SimLink, method: str, path: str, query: dict[str, str], body: dict[str, JsonValue] | None
) -> Program[tuple[int | None, object], object]: ...
def process_outside(
    per_process: Callable[[str, str], ProcessOutside] | None, job: str, worker: str
) -> Program[ProcessOutside, object]: ...
def sim_process(
    worker: str, spec: JobSpec, child: SimChild, blob: str | None
) -> Program[None, object]: ...
def ended_process(log: tuple[SimProcess, ...], job: str) -> Program[SimProcess | None, object]: ...
def heartbeat(worker: SimWorker, boot: str, stopping: bool, plan: object, parts: SimParts) -> Program[object, object]: ...
def settle_beats(worker: SimWorker, boot: str, sent: tuple[object, ...]) -> Program[None, object]: ...
def note_watch(
    name: str, boot: str, after: int, reading: _WatchReadingView
) -> Program[bool, object]: ...

# --- 入口 ---------------------------------------------------------------------------------------------

def sim_cluster(
    system: System,
    scenario: Program[_Answer, object],
    *,
    workers: tuple[SimWorker, ...] | None = None,
    environ: dict[str, dict[str, str]] | None = None,
    revision: str = "sim",
    start_ms: int = ...,
    timing: _ClusterTimingView | None = None,
    policy: WorkerPolicy | None = None,
    outside: SimOutside | None = None,
    store: Callable[[], _WalStoreView] | None = None,
    deployments: dict[str, dict[str, int]] | None = None,
    runtime_env: RuntimeEnv | None = None,
    skip_idle: bool = True,
    tick_seconds: float = 0.5,
) -> Program[_Answer, object]: ...
def wall_sim_cluster(
    system: System,
    scenario: Program[_Answer, object],
    *,
    workers: tuple[SimWorker, ...] | None = None,
    environ: dict[str, dict[str, str]] | None = None,
    revision: str = "sim",
    timing: _ClusterTimingView | None = None,
    policy: WorkerPolicy | None = None,
    outside: SimOutside | None = None,
    store: Callable[[], _WalStoreView] | None = None,
    deployments: dict[str, dict[str, int]] | None = None,
    runtime_env: RuntimeEnv | None = None,
    tick_seconds: float = 0.5,
) -> Program[_Answer, object]: ...
