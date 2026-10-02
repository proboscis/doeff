# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = os_process.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
import contextlib as contextlib
import fnmatch as fnmatch
import importlib.util
import io as io
import os as os
import signal as signal
import subprocess as subprocess
import sys as sys
import threading as threading
import time as time
from doeff_core_effects.file_effects import FileFailed as FileFailed
from doeff_core_effects.file_effects import PathKind as PathKind
from doeff_core_effects.file_effects import PathStat as PathStat
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
from doeff_core_effects.process_effects import ReadInterpreter as ReadInterpreter
from doeff_core_effects.process_effects import ResolveModule as ResolveModule
from doeff_core_effects.process_effects import InterpreterFacts as InterpreterFacts
from doeff_core_effects.process_effects import ModuleFound as ModuleFound
from doeff_core_effects.process_effects import ModuleNotFound as ModuleNotFound
from doeff_core_effects.process_effects import timed_out_outcome as timed_out_outcome
from doeff_core_effects.process_effects import not_started_outcome as not_started_outcome
from doeff_core_effects.process_effects import executable_file_answer as executable_file_answer
from doeff_core_effects.process_effects import environment_answer as environment_answer
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
PROCESS_THREADS: ThreadPerCall

class StartedChildren:

    def __init__(self) -> None:
        ...

    def add(self, child: Incomplete, process_group: Incomplete, reap_group: Incomplete) -> Incomplete:
        ...

    def find(self, pid: Incomplete) -> Incomplete:
        ...

    def forget(self, pid: Incomplete) -> Incomplete:
        ...
STARTED_CHILDREN: StartedChildren

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

class ChildPipes:

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

def run_watched(argv: tuple, stdin: str | None, timeout: int | float | None, cwd: str | None, child_env: dict | None, output_path: str | None, process_group: bool, stop_grace: float, stream_output: bool) -> _Program[ProcessOutcome, object]:
    ...

def run_subprocess(argv: tuple, stdin: str | None, timeout: int | float | None, cwd: str | None, env: tuple | None, env_mode: EnvMode, output_path: str | None, env_drop: tuple, process_group: bool, stop_grace: int | float, stream_output: bool) -> _Program[ProcessOutcome, object]:
    ...

def start_child_process(argv: tuple, cwd: str | None, env: tuple | None, env_mode: EnvMode, env_drop: tuple, stdout_path: str | None, stderr_path: str | None, process_group: bool, hold_stdin: bool=False, reap_group: bool=False) -> _Program[ProcessStarted | ProcessNotStarted, object]:
    ...

def poll_child_process(pid: int) -> _Program[ProcessRunning | ProcessExited | ProcessNotChild, object]:
    ...

def stop_child_process(pid: int, stop_grace: float) -> _Program[ProcessExited | ProcessNotChild, object]:
    ...

def signal_child_process(pid: int, sent: ProcessSignal) -> _Program[ProcessSignalled | ProcessNotChild, object]:
    ...
subprocess_handler: _Handler
offloaded_subprocess_handler: _Handler
