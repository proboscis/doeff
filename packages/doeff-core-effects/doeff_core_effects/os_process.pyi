# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = os_process.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from collections.abc import Callable as Callable
import subprocess as subprocess
from doeff import with_handlers as with_handlers
from doeff_core_effects.file_effects import FileFailed as FileFailed
from doeff_core_effects.file_effects import PathKind as PathKind
from doeff_core_effects.file_effects import PathStat as PathStat
from doeff_core_effects.handlers import state as state
from doeff_core_effects.meter_effects import CountMetric as CountMetric
from doeff_core_effects.os_file import stat_path as stat_path
from doeff_core_effects.offloaded_call import ThreadPerCall as ThreadPerCall
from doeff_core_effects.offloaded_call import offloaded as offloaded
from doeff_core_effects.offloaded_call import run_detached as run_detached
from doeff_core_effects.offloaded_call import keep_nothing as keep_nothing
from doeff_core_effects.process_effects import EnvEntry as EnvEntry
from doeff_core_effects.process_effects import EnvMode as EnvMode
from doeff_core_effects.process_effects import ProcessOutcome as ProcessOutcome
from doeff_core_effects.process_effects import RunProcess as RunProcess
from doeff_core_effects.process_effects import ExecutableAt as ExecutableAt
from doeff_core_effects.process_effects import ReadEnvironment as ReadEnvironment
from doeff_core_effects.process_effects import WorkingDirectory as WorkingDirectory
from doeff_core_effects.process_effects import ProcessAlive as ProcessAlive
from doeff_core_effects.process_effects import StartProcess as StartProcess
from doeff_core_effects.process_effects import PollProcess as PollProcess
from doeff_core_effects.process_effects import StopProcess as StopProcess
from doeff_core_effects.process_effects import ProcessStarted as ProcessStarted
from doeff_core_effects.process_effects import ProcessNotStarted as ProcessNotStarted
from doeff_core_effects.process_effects import ProcessRunning as ProcessRunning
from doeff_core_effects.process_effects import ProcessExited as ProcessExited
from doeff_core_effects.process_effects import ProcessNotChild as ProcessNotChild
from doeff_core_effects.process_effects import SignalProcess as SignalProcess
from doeff_core_effects.process_effects import ProcessSignal as ProcessSignal
from doeff_core_effects.process_effects import ProcessSignalled as ProcessSignalled
from doeff_core_effects.process_effects import WriteProcessInput as WriteProcessInput
from doeff_core_effects.process_effects import ProcessInputWritten as ProcessInputWritten
from doeff_core_effects.process_effects import WatchExits as WatchExits
from doeff_core_effects.process_effects import UnwatchExits as UnwatchExits
from doeff_core_effects.process_effects import ExitTarget as ExitTarget
from doeff_core_effects.process_effects import ReadInterpreter as ReadInterpreter
from doeff_core_effects.process_effects import ReadMachineName as ReadMachineName
from doeff_core_effects.process_effects import ResolveModule as ResolveModule
from doeff_core_effects.process_effects import InterpreterFacts as InterpreterFacts
from doeff_core_effects.process_effects import ModuleFound as ModuleFound
from doeff_core_effects.process_effects import ModuleNotFound as ModuleNotFound
from doeff_core_effects.process_effects import timed_out_outcome as timed_out_outcome
from doeff_core_effects.process_effects import not_started_outcome as not_started_outcome
from doeff_core_effects.process_effects import executable_file_answer as executable_file_answer
from doeff_core_effects.process_effects import environment_answer as environment_answer
from doeff_core_effects.os_warm_process import same_child_running as same_child_running
from doeff_core_effects.scheduler import ExternalPromise as ExternalPromise
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
PROCESS_THREADS: ThreadPerCall
CANCEL_TERMINATED_METRIC: str
CANCEL_KILLED_METRIC: str
GROUP_POLL: float

class CancelStop(StrEnum):
    NOT_RUNNING = 'not-running'
    TERMINATED = 'terminated'
    KILLED = 'killed'

@dataclass(frozen=True, kw_only=True)
class WatchedChild:
    child: subprocess.Popen
    process_group: bool

class ChildWatch:
    stop_grace: float
    meter: Callable | None
    lock: Incomplete
    cancelled: Incomplete
    running: Incomplete

    def __init__(self: ChildWatch, stop_grace: float, meter: Callable | None) -> None:
        ...

    def attach(self: ChildWatch, child: subprocess.Popen, process_group: bool) -> bool:
        ...

    def release(self: ChildWatch) -> None:
        ...

    def cancel(self: ChildWatch) -> WatchedChild | None:
        ...

class StartedChildren:
    lock: Incomplete
    children: Incomplete

    def __init__(self) -> None:
        ...

    def add(self, child: Incomplete, process_group: Incomplete, reap_group: Incomplete) -> Incomplete:
        ...

    def find(self, pid: Incomplete) -> Incomplete:
        ...

    def forget(self, pid: Incomplete) -> Incomplete:
        ...
STARTED_CHILDREN: StartedChildren

class ExitWatcher:
    lock: Incomplete
    fds: Incomplete
    bells: Incomplete
    poller: Incomplete

    def __init__(self: ExitWatcher) -> None:
        ...

    def watch(self: ExitWatcher, bell: ExternalPromise, fds: tuple) -> None:
        ...

    def unwatch(self: ExitWatcher, bell: ExternalPromise) -> None:
        ...

    def dropped(self: ExitWatcher, bell: ExternalPromise) -> None:
        ...

    def serve(self: ExitWatcher) -> None:
        ...
EXIT_WATCHER: ExitWatcher

def exit_target_fd(target: ExitTarget) -> _Program[int | None, object]:
    ...

def watch_exits(bell: ExternalPromise, targets: tuple) -> _Program[None, object]:
    ...

def os_executable_at(path: str) -> _Program[bool, object]:
    ...

def os_process_alive(pid: int) -> _Program[bool, object]:
    ...

def os_interpreter_facts() -> _Program[InterpreterFacts, object]:
    ...

def os_module_location(name: str) -> _Program[ModuleFound | ModuleNotFound, object]:
    ...

def decoded(value: str | bytes | None) -> _Program[str, object]:
    ...

def append_output(output_path: str | None, stdout: str, stderr: str) -> _Program[None, object]:
    ...

def child_environment(env: tuple | None, env_mode: EnvMode, env_drop: tuple) -> _Program[dict | None, object]:
    ...

def signal_group(pgid: int, sig: int) -> _Program[None, object]:
    ...

def group_alive(pgid: int) -> _Program[bool, object]:
    ...

def group_gone_by(child: subprocess.Popen, deadline: float) -> _Program[bool, object]:
    ...

def stop_for_cancel(child: subprocess.Popen, process_group: bool, stop_grace: float) -> _Program[CancelStop, object]:
    ...

def reported_stop(child: subprocess.Popen, how: str, metric: str, meter: Callable | None) -> _Program[None, object]:
    ...

def stop_cancelled_child(child: subprocess.Popen, process_group: bool, watch: ChildWatch) -> _Program[None, object]:
    ...

def watched_child(watch: ChildWatch, child: subprocess.Popen, process_group: bool) -> _Program[None, object]:
    ...

def stop_on_cancel(watch: ChildWatch) -> _Program[None, object]:
    ...

class ChildPipes:
    child: Incomplete
    sink: Incomplete
    lock: Incomplete
    out: Incomplete
    err: Incomplete
    readers: Incomplete

    def __init__(self, child: Incomplete, sink: Incomplete) -> None:
        ...

    def take(self, buffer: Incomplete, stream: Incomplete) -> Incomplete:
        ...

    def give(self, text: Incomplete) -> Incomplete:
        ...

    def start(self, stdin: Incomplete) -> Incomplete:
        ...

    def drained(self, until: Incomplete) -> Incomplete:
        ...

    def texts(self) -> Incomplete:
        ...

def stop_child(child: subprocess.Popen, pipes: ChildPipes, process_group: bool, stop_grace: float) -> _Program[None, object]:
    ...

def run_watched(argv: tuple, stdin: str | None, timeout: int | float | None, cwd: str | None, child_env: dict | None, output_path: str | None, process_group: bool, stop_grace: float, stream_output: bool, watch: ChildWatch) -> _Program[ProcessOutcome, object]:
    ...

def run_communicated(argv: tuple, stdin: str | None, timeout: int | float | None, cwd: str | None, child_env: dict | None, watch: ChildWatch) -> _Program[ProcessOutcome, object]:
    ...

def run_subprocess(argv: tuple, stdin: str | None, timeout: int | float | None, cwd: str | None, env: tuple | None, env_mode: EnvMode, output_path: str | None, env_drop: tuple, process_group: bool, stop_grace: int | float, stream_output: bool, watch: ChildWatch | None=None) -> _Program[ProcessOutcome, object]:
    ...

def start_child_process(argv: tuple, cwd: str | None, env: tuple | None, env_mode: EnvMode, env_drop: tuple, stdout_path: str | None, stderr_path: str | None, process_group: bool, hold_stdin: bool=False, reap_group: bool=False) -> _Program[ProcessStarted | ProcessNotStarted, object]:
    ...

def poll_child_process(pid: int) -> _Program[ProcessRunning | ProcessExited | ProcessNotChild, object]:
    ...

def stop_child_process(pid: int, stop_grace: float) -> _Program[ProcessExited | ProcessNotChild, object]:
    ...

def signal_child_process(pid: int, sent: ProcessSignal) -> _Program[ProcessSignalled | ProcessNotChild, object]:
    ...

def write_child_input(pid: int, text: str) -> _Program[ProcessInputWritten | ProcessNotChild, object]:
    ...
subprocess_handler: _Handler

def offloaded_run(argv: tuple, stdin: str | None, timeout: int | float | None, cwd: str | None, env: tuple | None, env_mode: EnvMode, output_path: str | None, env_drop: tuple, process_group: bool, stop_grace: int | float, stream_output: bool, meter: Callable | None) -> _Program[ProcessOutcome, object]:
    ...

def metered_offloaded_subprocess_handler(meter: Callable[..., object] | None) -> _Handler:
    ...
offloaded_subprocess_handler: _Handler
