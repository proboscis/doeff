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

@dataclass(frozen=True)
class WorkingDirectory(EffectBase): ...

@dataclass(frozen=True)
class ProcessAlive(EffectBase):
    pid: int

def timed_out_outcome(stdout: str, stderr: str) -> Program[ProcessOutcome, Any]: ...
def not_started_outcome(detail: str) -> Program[ProcessOutcome, Any]: ...
def start_refusal(error_number: int, path: str) -> Program[str, Any]: ...
def executable_file_answer(kind: PathKind, runnable: bool) -> Program[bool, Any]: ...
