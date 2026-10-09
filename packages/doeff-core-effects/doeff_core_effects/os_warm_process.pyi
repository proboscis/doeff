# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = os_warm_process.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
import socket as socket
from dataclasses import dataclass as dataclass
from doeff_hy.wire import Malformed as Malformed
from doeff_hy.wire import dump as dump
from doeff_hy.wire import parse_json as parse_json
from doeff_core_effects.process_effects import ProcessSignal as ProcessSignal
from doeff_core_effects.process_stat import ProcStat as ProcStat
from doeff_core_effects.darwin_proc import darwin_proc_stat as darwin_proc_stat
from doeff_core_effects.darwin_proc import darwin_thread_count as darwin_thread_count
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
from doeff_core_effects.warm_effects import WARM_ANSWER_SECONDS as WARM_ANSWER_SECONDS
from doeff_core_effects.warm_effects import warm_socket_missing as warm_socket_missing
from doeff_core_effects.warm_effects import warm_socket_refused as warm_socket_refused
from doeff_core_effects.warm_effects import warm_answer_late as warm_answer_late
from doeff_core_effects.warm_effects import warm_answer_unreadable as warm_answer_unreadable
from doeff_core_effects.warm_effects import warm_lost as warm_lost
from doeff_core_effects.warm_effects import warm_gone as warm_gone
from doeff_core_effects.warm_effects import warm_exit_answer as warm_exit_answer
from doeff_core_effects.warm_effects import signal_number_of as signal_number_of
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
PROC_ROOT: str
ANSWER_LIMIT: int

class WarmProcUnavailable(RuntimeError):
    ...

@dataclass(frozen=True, kw_only=True)
class WarmEnvWire:
    name: str
    value: str

@dataclass(frozen=True, kw_only=True)
class WarmRequestWire:
    entry: str
    args: tuple[str, ...]
    cwd: str
    env: tuple[WarmEnvWire, ...]
    log_path: str
    exit_path: str
    grace_seconds: float

@dataclass(frozen=True, kw_only=True)
class WarmForkedWire:
    pid: int
    start_ticks: int

@dataclass(frozen=True, kw_only=True)
class WarmRefusedWire:
    detail: str

def wire_line(wire: WarmRequestWire | WarmForkedWire | WarmRefusedWire) -> _Program[bytes, object]:
    ...

def warm_request_line(request: ForkFromWarm) -> _Program[bytes, object]:
    ...

def warm_request_of(line: bytes) -> _Program[WarmRequestWire | Malformed, object]:
    ...

def warm_answer_line(answer: WarmForked | WarmRefused) -> _Program[bytes, object]:
    ...

def warm_answer_of(line: bytes, socket_path: str) -> _Program[WarmForked | WarmRefused, object]:
    ...

def received_line(connection: socket.socket) -> _Program[bytes | None, object]:
    ...

def os_fork_from_warm(request: ForkFromWarm) -> _Program[WarmForked | WarmRefused, object]:
    ...

def proc_stat_of(pid: int) -> _Program[ProcStat | None, object]:
    ...

def own_thread_count() -> _Program[int, object]:
    ...

def linux_proc_stat(pid: int) -> _Program[ProcStat | None, object]:
    ...

def same_child_running(pid: int, start_ticks: int) -> _Program[bool, object]:
    ...

def os_poll_warm_child(pid: int, start_ticks: int, exit_path: str) -> _Program[WarmRunning | WarmExited | WarmLost, object]:
    ...

def os_signal_warm_child(pid: int, start_ticks: int, sent: ProcessSignal) -> _Program[WarmSignaled | WarmGone, object]:
    ...
os_warm_process_handler: _Handler
