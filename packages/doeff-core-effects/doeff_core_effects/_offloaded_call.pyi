# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = _offloaded_call.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from collections.abc import Callable as Callable
from concurrent.futures import Executor as Executor
from concurrent.futures import Future as Future
from queue import Empty as Empty
from queue import SimpleQueue as SimpleQueue
from doeff import PyVM as PyVM
from doeff_core_effects._scheduler import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects._scheduler import Wait as Wait
from doeff_core_effects._scheduler import TaskCancelledError as TaskCancelledError
NOT_DELIVERED: Incomplete
_IDLE_SECONDS: float

class Delivered:
    __slots__ = ...

    def __init__(self: Delivered, value: Incomplete) -> None:
        ...

    def take(self: Delivered) -> Incomplete:
        ...

def run_detached(program: Incomplete) -> Incomplete:
    ...

def keep_nothing(_value: Incomplete) -> Incomplete:
    ...

def keep_going() -> Incomplete:
    ...

class ThreadPerCall(Executor):
    on_start: None | Callable[[], None]

    def __init__(self: ThreadPerCall, on_start: None | Callable[[], None]=None) -> None:
        ...

    def submit(self: ThreadPerCall, call: Incomplete, *args: Incomplete) -> Future:
        ...

class ReusedThreads(Executor):
    idle_seconds: float
    idle_lock: Incomplete
    jobs: Incomplete
    idle: Incomplete
    started: Incomplete

    def __init__(self: ReusedThreads, idle_seconds: float=...) -> None:
        ...

    def submit(self: ReusedThreads, call: Incomplete, *args: Incomplete) -> Future:
        ...

    def serve(self: ReusedThreads) -> Incomplete:
        ...

class Handoff:
    pool: Executor
    abandon: Callable[[object], None]
    interrupt: Callable[[], None]
    lock: Incomplete
    abandoned: Incomplete
    delivered: Incomplete

    def __init__(self: Handoff, pool: Executor, abandon: Callable[[object], None], interrupt: Callable[[], None]) -> None:
        ...

    def deliver(self: Handoff, promise: Incomplete, future: Future) -> Incomplete:
        ...

    def give_up(self: Handoff, job: Future) -> Incomplete:
        ...

def offloaded(pool: Executor, call: Callable[[], object], abandon: Callable[[object], None], interrupt: Callable[[], None]=...) -> _Program[Incomplete, object]:
    ...
