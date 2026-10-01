from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from doeff import EffectBase, Program
from doeff_core_effects.file_effects import PathKind

TIMED_OUT_CODE: int
NOT_STARTED_CODE: int

class EnvMode(StrEnum):
    REPLACE = "replace"
    EXTEND = "extend"

@dataclass(frozen=True, kw_only=True)
class EnvEntry:
    name: str
    value: str

@dataclass(frozen=True, kw_only=True)
class ProcessOutcome:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    started: bool = True
    start_error: str = ""

@dataclass(frozen=True, kw_only=True)
class RunProcess(EffectBase):
    argv: tuple[str, ...]
    stdin: str | None = None
    timeout: float | None = None
    cwd: str | None = None
    env: tuple[EnvEntry, ...] | None = None
    env_mode: EnvMode = EnvMode.REPLACE
    env_drop: tuple[str, ...] = ()
    output_path: str | None = None
    process_group: bool = False
    stop_grace: float = 10.0
    stream_output: bool = False

@dataclass(frozen=True, kw_only=True)
class ExecutableAt(EffectBase):
    path: str

@dataclass(frozen=True)
class ReadEnvironment(EffectBase):
    names: tuple[str, ...]
    prefixes: tuple[str, ...] = ()

@dataclass(frozen=True)
class WorkingDirectory(EffectBase): ...

@dataclass(frozen=True)
class ProcessAlive(EffectBase):
    pid: int

@dataclass(frozen=True)
class ReadInterpreter(EffectBase): ...

@dataclass(frozen=True)
class ResolveModule(EffectBase):
    name: str

@dataclass(frozen=True, kw_only=True)
class InterpreterFacts:
    prefix: str
    pid: int

@dataclass(frozen=True, kw_only=True)
class ModuleFound:
    name: str
    origin: str | None
    search_locations: tuple[str, ...]

@dataclass(frozen=True, kw_only=True)
class ModuleNotFound:
    name: str

@dataclass(frozen=True, kw_only=True)
class StartProcess(EffectBase):
    argv: tuple[str, ...]
    cwd: str | None = None
    env: tuple[EnvEntry, ...] | None = None
    env_mode: EnvMode = EnvMode.REPLACE
    env_drop: tuple[str, ...] = ()
    stdout_path: str | None = None
    stderr_path: str | None = None
    process_group: bool = False
    hold_stdin: bool = False
    reap_group: bool = False

@dataclass(frozen=True)
class PollProcess(EffectBase):
    pid: int

@dataclass(frozen=True, kw_only=True)
class StopProcess(EffectBase):
    pid: int
    stop_grace: float = 10.0

class ProcessSignal(StrEnum):
    TERM = "term"
    KILL = "kill"

@dataclass(frozen=True, kw_only=True)
class SignalProcess(EffectBase):
    pid: int
    signal: ProcessSignal

@dataclass(frozen=True, kw_only=True)
class ProcessSignalled:
    pid: int
    delivered: bool

@dataclass(frozen=True, kw_only=True)
class ProcessStarted:
    pid: int

@dataclass(frozen=True, kw_only=True)
class ProcessNotStarted:
    detail: str

@dataclass(frozen=True, kw_only=True)
class ProcessRunning:
    pid: int

@dataclass(frozen=True, kw_only=True)
class ProcessExited:
    pid: int
    exit_code: int

@dataclass(frozen=True, kw_only=True)
class ProcessNotChild:
    pid: int

def timed_out_outcome(stdout: str, stderr: str) -> Program[ProcessOutcome, Any]: ...
def not_started_outcome(detail: str) -> Program[ProcessOutcome, Any]: ...
def start_refusal(error_number: int, path: str) -> Program[str, Any]: ...
def executable_file_answer(kind: PathKind, runnable: bool) -> Program[bool, Any]: ...
