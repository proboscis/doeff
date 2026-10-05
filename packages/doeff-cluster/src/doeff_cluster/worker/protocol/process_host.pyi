# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = process_host.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from enum import StrEnum as StrEnum
from pathlib import Path as Path
from doeff_core_effects import slog as slog
from doeff_core_effects.file_effects import MakeDirectory as MakeDirectory
from doeff_core_effects.file_effects import RemoveTree as RemoveTree
from doeff_core_effects.file_effects import WriteText as WriteText
from doeff_core_effects.file_effects import file_done as file_done
from doeff_core_effects.process_effects import EnvEntry as EnvEntry
from doeff_core_effects.process_effects import ReadEnvironment as ReadEnvironment
from doeff_core_effects.process_effects import ReadInterpreter as ReadInterpreter
from doeff_core_effects.process_effects import StartProcess as StartProcess
from doeff_core_effects.process_effects import PollProcess as PollProcess
from doeff_core_effects.process_effects import StopProcess as StopProcess
from doeff_core_effects.process_effects import SignalProcess as SignalProcess
from doeff_core_effects.process_effects import ProcessSignal as ProcessSignal
from doeff_core_effects.process_effects import ProcessStarted as ProcessStarted
from doeff_core_effects.process_effects import ProcessNotStarted as ProcessNotStarted
from doeff_core_effects.process_effects import ProcessRunning as ProcessRunning
from doeff_core_effects.process_effects import ProcessExited as ProcessExited
from doeff_core_effects.process_effects import ProcessNotChild as ProcessNotChild
from doeff_core_effects.process_effects import WriteProcessInput as WriteProcessInput
from doeff_core_effects.process_effects import ProcessInputWritten as ProcessInputWritten
from doeff_cluster.worker.intent.worker_model import NoticeJob as NoticeJob
from doeff_cluster.shared.core.clock import now_epoch_ms as now_epoch_ms
from doeff_cluster.worker.intent.worker_model import CodeLayout as CodeLayout
from doeff_cluster.worker.intent.worker_model import ProcessView as ProcessView
from doeff_cluster.worker.intent.worker_model import StartJob as StartJob
from doeff_cluster.worker.intent.worker_model import SignalJob as SignalJob
from doeff_cluster.worker.intent.worker_model import ReapJob as ReapJob
from doeff_cluster.worker.intent.worker_model import RetireJob as RetireJob
from doeff_cluster.worker.intent.worker_model import StopStage as StopStage
from doeff_cluster.worker.intent.worker_model import StopReason as StopReason
from doeff_cluster.worker.intent.worker_model import SpecChanged as SpecChanged
from doeff_cluster.worker.intent.worker_model import Undeclared as Undeclared
from doeff_cluster.worker.intent.worker_model import HandoffAbandoned as HandoffAbandoned
from doeff_cluster.worker.intent.worker_model import Retired as Retired
from doeff_cluster.worker.intent.worker_model import CutOff as CutOff
from doeff_cluster.worker.intent.worker_model import WorkerStopping as WorkerStopping
from doeff_cluster.worker.protocol.observations import ObserveProcesses as ObserveProcesses
from doeff_cluster.worker.core.launch import JobLaunch as JobLaunch
from doeff_cluster.worker.core.launch import job_launch as job_launch
from doeff_cluster.worker.core.launch import program_file as program_file
from doeff_cluster.worker.core.launch import CHILD_ENV_ALLOWED as CHILD_ENV_ALLOWED
from doeff_cluster.worker.core.launch import CHILD_ENV_PREFIXES as CHILD_ENV_PREFIXES
from doeff_cluster.worker.core.shim_timing import ShimSpans as ShimSpans
from doeff_cluster.worker.core.shim_timing import shim_deadline_ms as shim_deadline_ms
from doeff_core_effects.warm_effects import ForkFromWarm as ForkFromWarm
from doeff_core_effects.warm_effects import PollWarmChild as PollWarmChild
from doeff_core_effects.warm_effects import SignalWarmChild as SignalWarmChild
from doeff_core_effects.warm_effects import WarmRefused as WarmRefused
from doeff_core_effects.warm_effects import WarmRunning as WarmRunning
from doeff_core_effects.warm_effects import WarmExited as WarmExited
from doeff_core_effects.warm_effects import WarmLost as WarmLost
from doeff_cluster.worker.core.warm_rules import WarmPlace as WarmPlace
from doeff_cluster.worker.core.warm_rules import warm_place as warm_place
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put

@dataclass(frozen=True, kw_only=True)
class HostSettings:
    log_dir: str
    jobs_dir: str
    program_dir: str
    python: str
    hy_command: str
    uv: str
    extra_env: tuple[EnvEntry, ...]
    layout: CodeLayout
    program_env: str
    shim: ShimSpans
    warm_dir: str
    notice_env: str

@dataclass(frozen=True, kw_only=True)
class ForkedFrom:
    start_ticks: int
    exit_path: str

@dataclass(frozen=True, kw_only=True)
class StopAsked:
    requested_ms: int
    killed: bool
    reason: StopReason

@dataclass(frozen=True, kw_only=True)
class Started:
    view: ProcessView
    fork: ForkedFrom | None
    stop: StopAsked | None = None

class StopMoment(StrEnum):
    TERM = 'term'
    KILL = 'kill'
    REAPED = 'reaped'
STOP_TIMING_LOG: str

def stop_reason_word(reason: StopReason) -> _Program[str, object]:
    ...
RETIREMENT_NOTICES: tuple[Retired | HandoffAbandoned, ...]

def retirement_line(notice: Retired | HandoffAbandoned) -> _Program[str, object]:
    ...

def retirement_of_word(word: str) -> _Program[Retired | HandoffAbandoned, object]:
    ...

def silent_ms_of(reason: StopReason) -> _Program[int | None, object]:
    ...

def noted_stop(moment: StopMoment, name: str, pid: int, asked: StopAsked, now_ms: int) -> _Program[None, object]:
    ...

def tell_retirement(started: Started, notice: Retired | HandoffAbandoned) -> _Program[Started, object]:
    ...

def job_work_dir(settings: HostSettings, name: str) -> _Program[str, object]:
    ...

def process_instance(attempt: int, worker_pid: int, started_ms: int, name: str) -> _Program[str, object]:
    ...

def start_job(settings: HostSettings, action: StartJob) -> _Program[Started, object]:
    ...

def process_host(settings: HostSettings) -> _Handler:
    ...
