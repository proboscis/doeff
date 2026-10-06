# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = warm_effects.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from doeff import EffectBase as EffectBase
from doeff_core_effects.process_effects import EnvEntry as EnvEntry
from doeff_core_effects.process_effects import ProcessSignal as ProcessSignal
WARM_ANSWER_SECONDS: float
EXIT_CODE_PATTERN: str

@dataclass(frozen=True, kw_only=True)
class ForkFromWarm(EffectBase):
    socket_path: str
    entry: str
    args: tuple[str, ...] = ...
    cwd: str
    env: tuple[EnvEntry, ...] = ...
    log_path: str
    exit_path: str
    grace_seconds: float = 10.0

@dataclass(frozen=True, kw_only=True)
class PollWarmChild(EffectBase):
    pid: int
    start_ticks: int
    exit_path: str

@dataclass(frozen=True, kw_only=True)
class AwaitWarmChildExit(EffectBase):
    pid: int
    start_ticks: int

@dataclass(frozen=True, kw_only=True)
class SignalWarmChild(EffectBase):
    pid: int
    start_ticks: int
    signal: ProcessSignal

@dataclass(frozen=True, kw_only=True)
class WarmForked:
    pid: int
    start_ticks: int

@dataclass(frozen=True, kw_only=True)
class WarmRefused:
    detail: str

@dataclass(frozen=True, kw_only=True)
class WarmRunning:
    pid: int

@dataclass(frozen=True, kw_only=True)
class WarmExited:
    pid: int
    exit_code: int

@dataclass(frozen=True, kw_only=True)
class WarmLost:
    pid: int
    detail: str

@dataclass(frozen=True, kw_only=True)
class WarmSignaled:
    pid: int

@dataclass(frozen=True, kw_only=True)
class WarmGone:
    pid: int
    detail: str

def warm_socket_missing(socket_path: str) -> _Program[str, object]:
    ...

def warm_socket_refused(socket_path: str) -> _Program[str, object]:
    ...

def warm_answer_late(socket_path: str) -> _Program[str, object]:
    ...

def warm_answer_unreadable(socket_path: str) -> _Program[str, object]:
    ...

def warm_lost(pid: int, exit_path: str) -> _Program[WarmLost, object]:
    ...

def warm_gone(pid: int) -> _Program[WarmGone, object]:
    ...

def warm_exit_answer(pid: int, exit_path: str, text: str) -> _Program[WarmExited | WarmLost, object]:
    ...

def signal_number_of(signal: ProcessSignal) -> _Program[int, object]:
    ...
