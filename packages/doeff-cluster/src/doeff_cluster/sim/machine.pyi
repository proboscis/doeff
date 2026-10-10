# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = machine.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
from pathlib import Path as Path
from urllib.parse import quote as url_quote
from doeff import with_handlers as with_handlers
from doeff import Program as Program
from doeff import EffectBase as EffectBase
from doeff_core_effects.handlers import await_handler as await_handler
from doeff_core_effects.handlers import slog_handler as slog_handler
from doeff_core_effects.scheduler import scheduled as scheduled
from doeff_core_effects.http_effects import HttpRequest as HttpRequest
from doeff_core_effects.http_effects import HttpResponse as HttpResponse
from doeff_core_effects.http_effects import HttpFailed as HttpFailed
from doeff_core_effects.http_handlers import http_production_handler as http_production_handler
from doeff_core_effects.file_effects import MakeDirectory as MakeDirectory
from doeff_core_effects.file_effects import WriteText as WriteText
from doeff_core_effects.file_effects import FileFailed as FileFailed
from doeff_core_effects.os_file import os_file_handler as os_file_handler
from doeff_core_effects.process_effects import StartProcess as StartProcess
from doeff_core_effects.process_effects import StopProcess as StopProcess
from doeff_core_effects.process_effects import PollProcess as PollProcess
from doeff_core_effects.process_effects import SignalProcess as SignalProcess
from doeff_core_effects.process_effects import ProcessSignal as ProcessSignal
from doeff_core_effects.process_effects import ProcessSignalled as ProcessSignalled
from doeff_core_effects.process_effects import ProcessStarted as ProcessStarted
from doeff_core_effects.process_effects import ProcessNotStarted as ProcessNotStarted
from doeff_core_effects.process_effects import ProcessRunning as ProcessRunning
from doeff_core_effects.process_effects import ProcessExited as ProcessExited
from doeff_core_effects.process_effects import ProcessNotChild as ProcessNotChild
from doeff_core_effects.process_effects import EnvMode as EnvMode
from doeff_core_effects.process_effects import EnvEntry as EnvEntry
from doeff_core_effects.process_effects import RunProcess as RunProcess
from doeff_core_effects.process_effects import ProcessOutcome as ProcessOutcome
from doeff_core_effects.os_process import subprocess_handler as subprocess_handler
from doeff_time import Delay as Delay
from doeff_time import async_time_handler as async_time_handler
from doeff_cluster.foundation.process_versions import this_process_versions as this_process_versions
from doeff_cluster.shared.entry.declare import apply_declaration as apply_declaration
from doeff_cluster.shared.entry.service_build import system_declaration as system_declaration
from doeff_cluster.shared.intent.protocol import PlainText as PlainText
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv as RuntimeEnv
from doeff_cluster.shared.intent.cluster_control import ServiceReadiness as ServiceReadiness
from doeff_cluster.shared.intent.cluster_control import ReadinessOf as ReadinessOf
from doeff_cluster.shared.intent.cluster_control import KillWorker as KillWorker
from doeff_cluster.shared.intent.cluster_control import StopWorker as StopWorker
from doeff_cluster.shared.intent.cluster_control import StopCoordinator as StopCoordinator
from doeff_cluster.shared.intent.cluster_control import CrashCoordinator as CrashCoordinator
from doeff_cluster.shared.intent.cluster_control import Redeclare as Redeclare
from doeff_cluster.shared.intent.cluster_control import Crash as Crash
from doeff_cluster.shared.intent.cluster_control import AwaitReadiness as AwaitReadiness
from doeff_cluster.shared.intent.cluster_control import ServiceFailed as ServiceFailed
from doeff_cluster.shared.intent.cluster_control import ReadinessWaitExpired as ReadinessWaitExpired
from doeff_cluster.shared.intent.cluster_control import AwaitJobProcess as AwaitJobProcess
from doeff_cluster.shared.intent.cluster_control import JobProcessSeen as JobProcessSeen
from doeff_cluster.shared.intent.cluster_control import JobProcessWaitExpired as JobProcessWaitExpired
from doeff_cluster.sim.local import SimWorker as SimWorker
from doeff_cluster.sim.local import ReadCoordinator as ReadCoordinator
from doeff_cluster.sim.local import CutWorker as CutWorker
from doeff_cluster.sim.local import StallWorker as StallWorker
from doeff_cluster.sim.local import FailRoute as FailRoute
from doeff_cluster.shared.protocol.coordinator_reads import WAIT_PROBE_SECONDS as WAIT_PROBE_SECONDS
from doeff_cluster.shared.protocol.coordinator_reads import readiness_read as readiness_read
from doeff_cluster.shared.protocol.coordinator_reads import readiness_awaited as readiness_awaited
from doeff_cluster.shared.protocol.coordinator_reads import state_of as state_of
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
BOOT_SCRIPT: str
PROBE_SECONDS: float
MACHINE_ACTOR: str

class MachineCannotAnswer(Exception):
    ...

@dataclass(frozen=True, kw_only=True)
class GitSource:
    remote: str
    path: str

@dataclass(frozen=True, kw_only=True)
class LocalMachine:
    work_dir: str
    port: int
    workers: tuple[SimWorker, ...]
    boot_seconds: float = 120.0
    stop_grace: float = 30.0
    code_repo: str = ''
    revision: str = ''
    runtime_env: RuntimeEnv | None = None
    git_sources: tuple[GitSource, ...] = ...

@dataclass(frozen=True, kw_only=True)
class MachineProcess:
    name: str
    pid: int
    log: str
    home: str
    env: tuple[EnvEntry, ...]

@dataclass(frozen=True, kw_only=True)
class JobProcess:
    worker: MachineProcess
    pid: int

class MachineCell:
    roles: tuple[MachineProcess, ...]

    def __init__(self) -> None:
        ...

def coordinator_url(machine: LocalMachine) -> _Program[str, object]:
    ...

def coordinator_env(machine: LocalMachine) -> _Program[tuple[EnvEntry, ...], object]:
    ...

def repo_key_table(machine: LocalMachine) -> _Program[str, object]:
    ...

def git_source_env(sources: tuple[GitSource, ...]) -> _Program[tuple[EnvEntry, ...], object]:
    ...

def worker_env(machine: LocalMachine, worker: SimWorker, url: str) -> _Program[tuple[EnvEntry, ...], object]:
    ...
WORKER_REPOS_FILE: Path

def written_repo_table(machine: LocalMachine, home: Path) -> _Program[None, object]:
    ...

def worker_boot_env(machine: LocalMachine, worker: SimWorker, url: str, home: Path, repos: str) -> _Program[tuple[EnvEntry, ...], object]:
    ...

def started_role(name: str, home: str, env: tuple[EnvEntry, ...]) -> _Program[MachineProcess, object]:
    ...

def still_running(role: MachineProcess) -> _Program[None, object]:
    ...

def await_up(url: str, role: MachineProcess, hyx_readyXquestion_markX: Callable, seconds: float) -> _Program[None, object]:
    ...

def stopped(roles: tuple[MachineProcess, ...], stop_grace: float) -> _Program[None, object]:
    ...

def killed(role: MachineProcess) -> _Program[int, object]:
    ...

def role_named(cell: MachineCell, name: str) -> _Program[MachineProcess, object]:
    ...

def running_jobs(state: dict, name: str, roles: tuple[MachineProcess, ...]) -> _Program[tuple[JobProcess, ...], object]:
    ...

def job_crashed(job: JobProcess) -> _Program[int, object]:
    ...

def coordinator_remade(url: str, cell: MachineCell, machine: LocalMachine, down_seconds: float) -> _Program[None, object]:
    ...

def job_process_awaited(url: str, cell: MachineCell, job: str, excluding: tuple[int, ...], seconds: float) -> _Program[JobProcessSeen | JobProcessWaitExpired, object]:
    ...

def machine_answers(url: str, cell: MachineCell, machine: LocalMachine) -> _Handler:
    ...

def machine_run(scenario: Program | EffectBase, cell: MachineCell, machine: LocalMachine) -> _Program[Incomplete, object]:
    ...

def local_machine_cluster(scenario: Program | EffectBase, *, machine: LocalMachine) -> _Program[Incomplete, object]:
    ...
