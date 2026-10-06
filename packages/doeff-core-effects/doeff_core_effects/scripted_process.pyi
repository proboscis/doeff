# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = scripted_process.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
from doeff import Program as Program
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
from doeff_core_effects.process_effects import ReadInterpreter as ReadInterpreter
from doeff_core_effects.process_effects import ReadMachineName as ReadMachineName
from doeff_core_effects.process_effects import ResolveModule as ResolveModule
from doeff_core_effects.process_effects import InterpreterFacts as InterpreterFacts
from doeff_core_effects.process_effects import ModuleFound as ModuleFound
from doeff_core_effects.process_effects import ModuleNotFound as ModuleNotFound
from doeff_core_effects.process_effects import not_started_outcome as not_started_outcome
from doeff_core_effects.process_effects import start_refusal as start_refusal
from doeff_core_effects.process_effects import executable_file_answer as executable_file_answer
from doeff_core_effects.process_effects import environment_answer as environment_answer
from doeff_core_effects.file_effects import PathKind as PathKind
from doeff_core_effects.file_effects import PathStat as PathStat
from doeff_core_effects.file_effects import StatPath as StatPath
from doeff_core_effects.file_effects import MakeDirectory as MakeDirectory
from doeff_core_effects.file_effects import AppendText as AppendText
from doeff_core_effects.file_effects import FileFailed as FileFailed
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put

@dataclass(frozen=True, kw_only=True)
class ScriptedCommand:
    name: str
    run: Callable[[tuple[ScriptedCommand, ...], RunProcess], Program[ProcessOutcome, object]]

@dataclass(frozen=True, kw_only=True)
class ProcessScript:
    commands: tuple[ScriptedCommand, ...]
    env: tuple[EnvEntry, ...] = ...
    work_root: str = '/work/jobs'
    alive: frozenset[int] = ...
    interpreter: InterpreterFacts = ...
    machine_name: str = 'scripted-machine'
    modules: tuple[ModuleFound, ...] = ...

    def __post_init__(self) -> None:
        ...

def scripted_child_env(inherited: tuple[EnvEntry, ...], env: tuple[EnvEntry, ...] | None, env_mode: EnvMode, env_drop: tuple[str, ...]) -> _Program[tuple[EnvEntry, ...] | None, object]:
    ...

def run_scripted(commands: tuple[ScriptedCommand, ...], request: RunProcess) -> _Program[ProcessOutcome, object]:
    ...
SCRIPTED_FIRST_PID: int
SCRIPTED_STOPPED_CODE: int
SCRIPTED_RUNNING: str

def scripted_open_outputs(paths: tuple[str | None, ...]) -> _Program[ProcessNotStarted | None, object]:
    ...

def scripted_append_outputs(stdout_path: str | None, stderr_path: str | None, outcome: ProcessOutcome) -> _Program[None, object]:
    ...

def scripted_executable_at(commands: tuple[ScriptedCommand, ...], path: str) -> _Program[bool, object]:
    ...

def ended_watchers(watching: dict, pid: int) -> _Program[dict, object]:
    ...

def scripted_process_handler(script: ProcessScript) -> _Handler:
    ...
