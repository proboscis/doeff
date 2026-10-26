# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = apart_call.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from concurrent.futures import Future as Future
from doeff import Program as Program
from doeff import Transfer as Transfer
from doeff import TransferThrow as TransferThrow
from doeff import with_handlers as with_handlers
from doeff.program import handler as raw_handler
from doeff_vm import PyVM as PyVM
from collections.abc import Callable as Callable
from doeff_core_effects.scheduler import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects.scheduler import ExternalPromise as ExternalPromise
from doeff_core_effects.scheduler import Wait as Wait
from doeff_core_effects.scheduler import TaskCancelledError as TaskCancelledError
from doeff_core_effects.handlers import try_handler as try_handler

class ApartClosed(Exception):
    ...

@dataclass(frozen=True, kw_only=True)
class ApartAsked:
    effect: object
    reply: Future

@dataclass(frozen=True, kw_only=True)
class ApartDone:
    value: object

@dataclass(frozen=True, kw_only=True)
class ApartFailed:
    error: BaseException

class ApartLane:
    condition: Incomplete
    armed: Incomplete
    asked: Incomplete
    closed: Incomplete

    def __init__(self: ApartLane) -> None:
        ...

    def arm(self: ApartLane, promise: ExternalPromise) -> None:
        ...

    def deliver(self: ApartLane, message: ApartAsked | ApartDone | ApartFailed) -> bool:
        ...

    def close(self: ApartLane) -> None:
        ...

def forwarding_handler(lane: ApartLane) -> Callable:
    ...

def apart_thread(lane: ApartLane, program: Program) -> None:
    ...

def answered_here(asked: ApartAsked) -> _Program[None, object]:
    ...

def next_message(lane: ApartLane) -> _Program[ApartAsked | ApartDone | ApartFailed, object]:
    ...

def run_apart[T](program: Program[T, object]) -> _Program[T, object]:
    ...
