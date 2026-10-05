# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = scripted_warm_process.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from enum import StrEnum as StrEnum
from doeff_core_effects.process_effects import ProcessSignal as ProcessSignal
from doeff_core_effects.warm_effects import ForkFromWarm as ForkFromWarm
from doeff_core_effects.warm_effects import PollWarmChild as PollWarmChild
from doeff_core_effects.warm_effects import SignalWarmChild as SignalWarmChild
from doeff_core_effects.warm_effects import WarmForked as WarmForked
from doeff_core_effects.warm_effects import WarmRefused as WarmRefused
from doeff_core_effects.warm_effects import WarmRunning as WarmRunning
from doeff_core_effects.warm_effects import WarmExited as WarmExited
from doeff_core_effects.warm_effects import WarmLost as WarmLost
from doeff_core_effects.warm_effects import WarmSignaled as WarmSignaled
from doeff_core_effects.warm_effects import WarmGone as WarmGone
from doeff_core_effects.warm_effects import warm_socket_missing as warm_socket_missing
from doeff_core_effects.warm_effects import warm_answer_late as warm_answer_late
from doeff_core_effects.warm_effects import warm_lost as warm_lost
from doeff_core_effects.warm_effects import warm_gone as warm_gone
from doeff_core_effects.warm_effects import signal_number_of as signal_number_of
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put
SCRIPTED_WARM_FIRST_PID: int
SCRIPTED_WARM_FIRST_TICKS: int

class WarmSocketAnswer(StrEnum):
    ACCEPTS = 'accepts'
    REFUSES = 'refuses'
    SILENT = 'silent'

@dataclass(frozen=True, kw_only=True)
class WarmSocket:
    path: str
    answer: WarmSocketAnswer = ...
    refusal: str = ''

@dataclass(frozen=True, kw_only=True)
class WarmRun:
    entry: str
    polls: int = 0
    exit_code: int = 0
    writes_exit: bool = True

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class WarmScript:
    sockets: tuple[WarmSocket, ...] = ...
    runs: tuple[WarmRun, ...] = ...

@dataclass(frozen=True, kw_only=True)
class WarmChildState:
    start_ticks: int
    polls_left: int
    ended: bool
    exit_code: int
    writes_exit: bool

def scripted_fork(script: WarmScript, request: ForkFromWarm, pid: int, ticks: int) -> _Program[WarmChildState | WarmRefused, object]:
    ...

def ended_answer(pid: int, exit_path: str, state: WarmChildState) -> _Program[WarmExited | WarmLost, object]:
    ...

def scripted_warm_process_handler(script: WarmScript) -> _Handler:
    ...
