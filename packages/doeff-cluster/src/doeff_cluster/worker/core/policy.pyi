# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = policy.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from doeff import run as run
from doeff_cluster.worker.intent.worker_model import Action as Action
from doeff_cluster.worker.intent.worker_model import CodeState as CodeState
from doeff_cluster.worker.intent.worker_model import CodeView as CodeView
from doeff_cluster.worker.intent.worker_model import ProcessView as ProcessView
from doeff_cluster.worker.intent.worker_model import WorldView as WorldView
from doeff_cluster.worker.intent.worker_model import StopStage as StopStage
from doeff_cluster.worker.intent.worker_model import JobStop as JobStop
from doeff_cluster.worker.intent.worker_model import ProbeState as ProbeState
from doeff_cluster.worker.intent.worker_model import ProbeView as ProbeView
from doeff_cluster.worker.intent.worker_model import ProbeStatus as ProbeStatus
from doeff_cluster.worker.intent.worker_model import Outcome as Outcome
from doeff_cluster.worker.intent.worker_model import JobRecord as JobRecord
from doeff_cluster.worker.intent.worker_model import WorkerPolicy as WorkerPolicy
from doeff_cluster.worker.intent.worker_model import JobStatus as JobStatus
from doeff_cluster.worker.intent.worker_model import PrepareCode as PrepareCode
from doeff_cluster.worker.intent.worker_model import PrepareEnv as PrepareEnv
from doeff_cluster.worker.intent.worker_model import SweepEnvs as SweepEnvs
from doeff_cluster.worker.intent.worker_model import StartJob as StartJob
from doeff_cluster.worker.intent.worker_model import SignalJob as SignalJob
from doeff_cluster.worker.intent.worker_model import ReapJob as ReapJob
from doeff_cluster.worker.intent.worker_model import RetireJob as RetireJob
from doeff_cluster.worker.intent.worker_model import ReleaseLeases as ReleaseLeases
from doeff_cluster.worker.intent.worker_model import ProbeEntry as ProbeEntry
from doeff_cluster.worker.intent.worker_model import ForgetProbes as ForgetProbes
from doeff_cluster.worker.intent.worker_model import WarmChildView as WarmChildView
from doeff_cluster.worker.intent.worker_model import WarmLaunch as WarmLaunch
from doeff_cluster.worker.intent.worker_model import StartWarmChild as StartWarmChild
from doeff_cluster.worker.intent.worker_model import StopWarmChild as StopWarmChild
from doeff_cluster.worker.intent.worker_model import ForgetWarmChild as ForgetWarmChild
from doeff_cluster.worker.intent.worker_model import StopReason as StopReason
from doeff_cluster.worker.intent.worker_model import SpecChanged as SpecChanged
from doeff_cluster.worker.intent.worker_model import Undeclared as Undeclared
from doeff_cluster.worker.intent.worker_model import HandoffAbandoned as HandoffAbandoned
from doeff_cluster.worker.intent.worker_model import Retired as Retired
from doeff_cluster.worker.intent.worker_model import StartHold as StartHold
from doeff_cluster.worker.intent.worker_model import NotYetRead as NotYetRead
from doeff_cluster.worker.intent.worker_model import DeclarationRead as DeclarationRead
from doeff_cluster.shared.intent.job_model import JobSpec as JobSpec
from doeff_cluster.shared.intent.job_model import JobPhase as JobPhase
from doeff_cluster.shared.core.job_rules import spec_hash as spec_hash
from doeff_cluster.worker.core.worker_rules import code_key as code_key
from doeff_cluster.worker.core.worker_rules import probed_job as probed_job
from doeff_cluster.worker.core.worker_rules import retired_name as retired_name
from doeff_cluster.worker.core.worker_rules import ready_path as ready_path
from doeff_cluster.worker.core.worker_rules import RETIRED_MARK as RETIRED_MARK
from doeff_cluster.worker.core.worker_rules import ENV_KEY_PREFIX as ENV_KEY_PREFIX
from doeff_cluster.shared.intent.runtime_env_model import EnvFailure as EnvFailure
from doeff_cluster.shared.intent.runtime_env_model import EnvFailureKind as EnvFailureKind
from doeff_cluster.worker.intent.worker_model import NoticeJob as NoticeJob
from doeff_cluster.worker.core.warm_rules import forks_from_warm_child as forks_from_warm_child
from doeff_cluster.worker.core.warm_rules import warm_key_of as warm_key_of
from doeff_cluster.worker.core.warm_rules import warm_mark_clean as warm_mark_clean
from doeff_cluster.worker.core.warm_rules import warm_child_of as warm_child_of
from doeff_cluster.worker.core.warm_rules import warm_child_ready as warm_child_ready
from doeff_cluster.worker.core.warm_rules import mark_refusal as mark_refusal
from doeff_cluster.worker.core.warm_rules import warm_launch as warm_launch
from doeff_cluster.worker.core.warm_rules import warm_child_refused as warm_child_refused
from doeff_cluster.worker.core.warm_rules import warm_refusal_failure as warm_refusal_failure

def hyx_kept_when_cut_offXquestion_markX(job: JobSpec, silent_ms: int, keep_fence_ms: int) -> _Program[bool, object]:
    ...

def kept_when_cut_off(jobs: tuple, silent_ms: int, keep_fence_ms: int) -> tuple:
    ...

def process_of(world: WorldView, name: str) -> ProcessView | None:
    ...

def code_of(world: WorldView, key: str) -> CodeView | None:
    ...

def probe_of(world: WorldView, spec: JobSpec) -> ProbeView | None:
    ...

def probe_actions(now: int, spec: JobSpec, tree: str, world: WorldView, policy: WorkerPolicy) -> tuple | None:
    ...

def probe_failure(world: WorldView, spec: JobSpec) -> ProbeView | None:
    ...

def probe_in_flight(world: WorldView, spec: JobSpec) -> ProbeView | None:
    ...

def probe_status(now: int, world: WorldView, spec: JobSpec) -> ProbeStatus | None:
    ...

def probing_detail(probe: ProbeStatus) -> str:
    ...

def desired_of(desired: tuple, name: str) -> JobSpec | None:
    ...

def job_names(desired: tuple, world: WorldView) -> _Program[tuple, object]:
    ...

def retired_of(world: WorldView, name: str) -> tuple:
    ...

def retired_at_of(process: ProcessView) -> int:
    ...

def backoff_ms(record: JobRecord, policy: WorkerPolicy) -> int:
    ...

def in_backoff(now: int, record: JobRecord, policy: WorkerPolicy) -> bool:
    ...

def stop_actions(now: int, process: ProcessView, record: JobRecord, policy: WorkerPolicy, reason: StopReason) -> tuple:
    ...

def prepare_action(spec: JobSpec, compile_jobs: int | None) -> PrepareCode | PrepareEnv:
    ...

def prepare_actions(now: int, spec: JobSpec, world: WorldView, policy: WorkerPolicy, compile_jobs: int | None) -> tuple:
    ...

@dataclass(frozen=True, kw_only=True)
class StartStep:
    actions: tuple
    hold: StartHold | None

@dataclass(frozen=True, kw_only=True)
class JobHold:
    name: str
    hold: StartHold | None
RETRYING_HOLDS: tuple[StartHold, ...]

def prepare_hold(code: CodeView | None, record: JobRecord) -> _Program[StartHold, object]:
    ...

def probe_hold(probe: ProbeView | None) -> _Program[StartHold, object]:
    ...

def ready_tree_step(now: int, spec: JobSpec, tree: str, world: WorldView, record: JobRecord, policy: WorkerPolicy) -> _Program[StartStep, object]:
    ...

def start_step(now: int, spec: JobSpec, world: WorldView, record: JobRecord, policy: WorkerPolicy) -> _Program[StartStep, object]:
    ...

def hyx_recreatingXquestion_markX(want: JobSpec | None, process: ProcessView | None, record: JobRecord) -> _Program[bool, object]:
    ...

def replace_step(now: int, want: JobSpec, process: ProcessView, world: WorldView, record: JobRecord, policy: WorkerPolicy) -> _Program[StartStep, object]:
    ...

def handoff_actions(now: int, want: JobSpec, process: ProcessView, world: WorldView, policy: WorkerPolicy) -> tuple:
    ...

def retired_actions(now: int, process: ProcessView, origin: str, desired: tuple, world: WorldView, record: JobRecord, policy: WorkerPolicy) -> tuple:
    ...

def retired_limit_for(want: JobSpec, policy: WorkerPolicy) -> int:
    ...

def plan_job(now: int, name: str, desired: tuple, world: WorldView, record: JobRecord, policy: WorkerPolicy, absent: StopReason) -> tuple:
    ...

def warm_actions(now: int, warm: tuple, world: WorldView, job_actions: tuple, policy: WorkerPolicy) -> _Program[tuple, object]:
    ...

def warm_stop_step(now: int, view: WarmChildView, policy: WorkerPolicy) -> _Program[tuple, object]:
    ...

def warm_child_step(now: int, key: str, launch: WarmLaunch | None, view: WarmChildView | None, policy: WorkerPolicy) -> _Program[tuple, object]:
    ...

def launch_of(key: str, wanted: frozenset, desired: tuple, world: WorldView, warm: tuple) -> _Program[WarmLaunch | None, object]:
    ...

def warm_child_actions(now: int, desired: tuple, world: WorldView, warm: tuple, policy: WorkerPolicy) -> _Program[tuple, object]:
    ...

def pinned_env_keys(desired: tuple, world: WorldView, warm: tuple) -> _Program[frozenset, object]:
    ...

def declared_jobs(declaration: NotYetRead | DeclarationRead) -> _Program[tuple, object]:
    ...

def declared_warm(declaration: NotYetRead | DeclarationRead) -> _Program[tuple, object]:
    ...

def sweep_actions(declaration: NotYetRead | DeclarationRead, world: WorldView) -> _Program[tuple, object]:
    ...

def forget_probe_actions(desired: tuple, world: WorldView) -> _Program[tuple, object]:
    ...

def wanted_notice(want: JobSpec | None) -> _Program[Retired | HandoffAbandoned, object]:
    ...

def notice_actions(desired: tuple, world: WorldView) -> _Program[tuple, object]:
    ...

def plan(now: int, desired: tuple, world: WorldView, records: dict, policy: WorkerPolicy, warm: tuple=..., absent: StopReason=...) -> _Program[tuple, object]:
    ...

def ready_followups(now: int, desired: tuple, before: WorldView, after: WorldView, records: dict, policy: WorkerPolicy, warm: tuple=..., absent: StopReason=...) -> _Program[tuple, object]:
    ...

def record_after(now: int, record: JobRecord, action: Action, policy: WorkerPolicy=...) -> JobRecord:
    ...

def records_after(now: int, records: dict, actions: tuple, policy: WorkerPolicy=...) -> _Program[dict, object]:
    ...

def start_holds(now: int, desired: tuple, world: WorldView, records: dict, policy: WorkerPolicy) -> _Program[tuple, object]:
    ...

def noted_holds(records: dict, holds: tuple) -> _Program[tuple, object]:
    ...

def held_records(records: dict, holds: tuple) -> _Program[dict, object]:
    ...

def phase_of(now: int, want: JobSpec | None, process: ProcessView | None, world: WorldView, record: JobRecord, policy: WorkerPolicy) -> JobPhase:
    ...

def refused_warm_child(world: WorldView, want: JobSpec | None, process: ProcessView | None, record: JobRecord, policy: WorkerPolicy) -> WarmChildView | None:
    ...

def warm_wait_detail(world: WorldView, want: JobSpec | None, process: ProcessView | None, record: JobRecord) -> str | None:
    ...

def statuses(now: int, desired: tuple, world: WorldView, records: dict, policy: WorkerPolicy) -> _Program[tuple, object]:
    ...
