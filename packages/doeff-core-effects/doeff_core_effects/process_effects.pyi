from dataclasses import dataclass

from doeff import EffectBase

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
    output_path: str | None = None

@dataclass(frozen=True, kw_only=True)
class ExecutableAt(EffectBase):
    path: str

@dataclass(frozen=True)
class ReadEnvironment(EffectBase):
    names: tuple[str, ...]

@dataclass(frozen=True)
class WorkingDirectory(EffectBase): ...
