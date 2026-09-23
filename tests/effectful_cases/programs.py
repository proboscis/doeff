# pyright: strict
"""@effectful programs used by tests/test_effectful.py (the package __init__ installs the hook)."""

from dataclasses import dataclass
from typing import Any, NamedTuple, Never

from doeff_core_effects.scheduler import (
    Cancel,
    CreatePromise,
    Gather,
    Promise,
    Spawn,
    TaskCancelledError,
    Wait,
)

from doeff import EffectBase, Effects, Pure, effectful


@dataclass(frozen=True)
class ReadClock(EffectBase[int]):
    pass


@dataclass(frozen=True)
class WriteShared(EffectBase[bool]):
    key: str


@dataclass(frozen=True)
class Note(EffectBase[None]):
    """Records a step; the test's handler keeps the notes (cleanup may perform effects)."""

    text: str


class JobResult(NamedTuple):
    now: int
    ok: bool
    spent: int
    tag: str


class ParallelResult(NamedTuple):
    first: int
    both: list[int]


@effectful
def elapsed(perform: Effects[ReadClock], since: int) -> int:
    now = perform(ReadClock())
    return now - since


@effectful
def job(perform: Effects[ReadClock | WriteShared]) -> JobResult:
    now = perform(ReadClock())
    ok = perform(WriteShared("row"))
    spent = perform(elapsed(400))
    tag = perform(Pure("done"))
    return JobResult(now, ok, spent, tag)


@effectful
def no_effects(perform: Effects[ReadClock], x: int) -> int:
    return x * 2


@effectful
def loop(perform: Effects[ReadClock], n: int) -> int:
    total = 0
    for _ in range(n):
        total += perform(ReadClock())
    return total


@effectful
def outer_with_inner(perform: Effects[ReadClock]) -> int:
    @effectful
    def inner(perform: Effects[ReadClock], x: int) -> int:
        return perform(ReadClock()) + x

    return perform(inner(1)) * 2


class Service:
    def __init__(self, base: int) -> None:
        self.base = base

    @effectful
    def shifted(self, perform: Effects[ReadClock], by: int) -> int:
        return perform(ReadClock()) + self.base + by


@effectful
def raises_after_effect(perform: Effects[ReadClock]) -> int:
    now = perform(ReadClock())
    raise ValueError(f"boom {now}")  # LINE: raise-after-effect


@effectful
def refused_write(perform: Effects[WriteShared]) -> bool:
    return perform(WriteShared("deny"))  # LINE: refused-write


@effectful
def caught_refusal(perform: Effects[WriteShared]) -> str:
    try:
        perform(WriteShared("deny"))
    except PermissionError as error:
        return f"caught {error}"
    return "not raised"


@effectful
def parallel(
    perform: Effects[Spawn[Any, Any] | Wait[int] | Gather[int] | ReadClock],
) -> ParallelResult:
    first = perform(Spawn(elapsed(1)))
    second = perform(Spawn(elapsed(2)))
    return ParallelResult(perform(Wait(first)), perform(Gather(first, second)))


@effectful
def _noop(perform: Effects[Never]) -> None:
    return None


@effectful
def parked_worker(perform: Effects[Wait[int] | Note], gate: Promise[int]) -> None:
    try:
        perform(Note("start"))
        perform(Wait(gate.future))
        perform(Note("resumed"))
    except TaskCancelledError:
        perform(Note("except"))
        raise
    finally:
        perform(Note("finally"))


@effectful
def cancel_parked(
    perform: Effects[CreatePromise[int] | Spawn[Any, Any] | Wait[Any] | Cancel | Note],
) -> str:
    gate = perform(CreatePromise[int]())
    task = perform(Spawn(parked_worker(gate)))
    perform(Wait(perform(Spawn(_noop()))))  # let the worker park
    perform(Cancel(task))
    try:
        perform(Wait(task))
    except TaskCancelledError:
        return "cancelled"
    return "not cancelled"
