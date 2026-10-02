# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = policy.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import replace as replace
from doeff import run as run
from doeff_cluster.worker.intent.worker_model import Action as Action
from doeff_cluster.worker.intent.worker_model import CodeState as CodeState
from doeff_cluster.worker.intent.worker_model import CodeView as CodeView
from doeff_cluster.worker.intent.worker_model import ProcessView as ProcessView
from doeff_cluster.worker.intent.worker_model import WorldView as WorldView
from doeff_cluster.worker.intent.worker_model import StopStage as StopStage
from doeff_cluster.worker.intent.worker_model import StopProgress as StopProgress
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
from doeff_cluster.shared.intent.job_model import JobSpec as JobSpec
from doeff_cluster.shared.intent.job_model import JobPhase as JobPhase
from doeff_cluster.shared.core.job_rules import spec_hash as spec_hash
from doeff_cluster.worker.core.worker_rules import code_key as code_key
from doeff_cluster.worker.core.worker_rules import probed_job as probed_job
from doeff_cluster.worker.core.worker_rules import retired_name as retired_name
from doeff_cluster.worker.core.worker_rules import ready_path as ready_path
from doeff_cluster.worker.core.worker_rules import RETIRED_MARK as RETIRED_MARK
from doeff_cluster.worker.core.worker_rules import ENV_KEY_PREFIX as ENV_KEY_PREFIX

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

def job_names(desired: tuple, world: WorldView) -> tuple:
    ...

def retired_exists(world: WorldView, name: str) -> bool:
    ...

def backoff_ms(record: JobRecord, policy: WorkerPolicy) -> int:
    ...

def in_backoff(now: int, record: JobRecord, policy: WorkerPolicy) -> bool:
    ...

def stop_actions(now: int, process: ProcessView, record: JobRecord, policy: WorkerPolicy) -> tuple:
    ...

def prepare_action(spec: JobSpec) -> PrepareCode | PrepareEnv:
    ...

def prepare_actions(now: int, spec: JobSpec, world: WorldView, policy: WorkerPolicy) -> tuple:
    ...

def start_actions(now: int, spec: JobSpec, world: WorldView, record: JobRecord, policy: WorkerPolicy) -> tuple:
    ...

def start_on_ready_tree(now: int, spec: JobSpec, tree: str, world: WorldView, record: JobRecord, policy: WorkerPolicy) -> tuple:
    ...

def handoff_actions(now: int, want: JobSpec, process: ProcessView, world: WorldView, policy: WorkerPolicy) -> tuple:
    ...

def retired_actions(now: int, process: ProcessView, origin: str, desired: tuple, world: WorldView, record: JobRecord, policy: WorkerPolicy) -> tuple:
    ...

def plan_job(now: int, name: str, desired: tuple, world: WorldView, record: JobRecord, policy: WorkerPolicy) -> tuple:
    ...

def warm_actions(now: int, warm: tuple, world: WorldView, job_actions: tuple, policy: WorkerPolicy) -> tuple:
    ...

def pinned_env_keys(desired: tuple, world: WorldView, warm: tuple) -> frozenset:
    ...

def sweep_actions(desired: tuple, world: WorldView, warm: tuple) -> tuple:
    ...

def forget_probe_actions(desired: tuple, world: WorldView) -> tuple:
    ...

def plan(now: int, desired: tuple, world: WorldView, records: dict, policy: WorkerPolicy, warm: tuple=...) -> tuple:
    ...

def ready_followups(now: int, desired: tuple, before: WorldView, after: WorldView, records: dict, policy: WorkerPolicy) -> _Program[tuple, object]:
    ...

def record_after(now: int, record: JobRecord, action: Action, policy: WorkerPolicy=...) -> JobRecord:
    ...

def records_after(now: int, records: dict, actions: tuple, policy: WorkerPolicy=...) -> dict:
    ...

def phase_of(now: int, want: JobSpec | None, process: ProcessView | None, world: WorldView, record: JobRecord, policy: WorkerPolicy) -> JobPhase:
    ...

def statuses(now: int, desired: tuple, world: WorldView, records: dict, policy: WorkerPolicy) -> tuple:
    ...
