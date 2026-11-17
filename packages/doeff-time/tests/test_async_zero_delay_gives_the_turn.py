"""A zero ``Delay`` under ``async_time_handler`` gives the turn to the tasks an external completion woke.

agora's turn-dispatcher ends each reaction with ``Delay(0)`` so that its sources (tasks parked in an ``Await`` of a
broker read) put on its queue what came during the reaction. The zero delay was one ``Await`` of ``sleep(0)`` on the
bridge's event loop, another thread: when the loop answered it before the caller reached its ``Wait``, the ``Wait``
answered at once without a switch, and a source woken by a completion that had come long before ran only at the
caller's next real wait (2026-11-17: inputs reached the queue one or two reactions late).

The world: the scheduler and ``async_time_handler``. The bridge that answers an ``Await`` before the caller's
``Wait`` is the worst case of that race, made certain here: a handler under the clock answers the ``Await`` of the
clock's zero sleep (a marked awaitable) by completing its external promise before the ``Wait``; every other ``Await``
goes to ``await_handler``. A task waits for an external promise the test completes from the caller, as a broker read
completes from the bridge's thread.

No ``from __future__ import annotations`` here: the VM reads a handler's effect types from the annotation of its
first parameter.
"""

import asyncio
from collections.abc import Awaitable, Generator
from typing import TYPE_CHECKING, Final, final

import pytest
from doeff_core_effects import (
    Await,
    CreateExternalPromise,
    ExternalPromise,
    Spawn,
    Task,
    Wait,
    await_handler,
    scheduled,
)
from doeff_time import Delay, async_time_handler

from doeff import K, Pass, Resume, do, run
from doeff import handler as program_handler

if TYPE_CHECKING:
    from doeff import EffectGenerator

# The subject is the await bridge's real asyncio loop (the waiter parks behind a real sleep on it).
pytestmark = pytest.mark.realtime

# The waiter's start: long enough for the spawned waiter to park in its wait (a real sleep on the bridge).
START_SECONDS: Final = 0.05


@final
class _AnsweredAtOnce:
    """The clock's sleep for a zero span: an awaitable the bridge below answers before the caller's ``Wait``."""

    def __await__(self) -> Generator[None, None, None]:
        """Never awaited."""
        raise AssertionError("the zero sleep is answered by the handler, not awaited")


def _sleep(seconds: float) -> Awaitable[None]:
    """The clock's sleep: the marked awaitable for a zero span, ``asyncio.sleep`` otherwise."""
    return _AnsweredAtOnce() if seconds <= 0 else asyncio.sleep(seconds)


@do
def _answer_the_zero_sleep_first(effect: Await, k: K) -> "EffectGenerator[object]":
    """The bridge at its fastest for the zero sleep: complete its external promise before the caller's ``Wait``;
    pass every other ``Await`` on."""
    if not isinstance(effect.coroutine, _AnsweredAtOnce):
        yield Pass(effect, k)
        return None
    promise: ExternalPromise[None] = yield CreateExternalPromise()
    promise.complete(None)
    yield Wait(promise.future)
    return (yield Resume(k, None))


answers_the_zero_sleep_first = program_handler(_answer_the_zero_sleep_first)


@final
class _Turns:
    """Who took a turn, in order."""

    __slots__ = ("_mut_taken",)

    def __init__(self) -> None:
        """Nobody yet."""
        self._mut_taken: tuple[str, ...] = ()

    def took(self, who: str) -> None:
        """``who`` took its turn now."""
        self._mut_taken = (*self._mut_taken, who)

    @property
    def taken(self) -> tuple[str, ...]:
        """Who took a turn, in order."""
        return self._mut_taken


@do
def _woken(came: ExternalPromise[None], turns: _Turns) -> "EffectGenerator[None]":
    """The source: wait for the broker's answer, then take its turn."""
    yield Wait(came.future)
    turns.took("woken")


@do
def _caller(turns: _Turns) -> "EffectGenerator[tuple[str, ...]]":
    """The subscriber: start the waiter, let its answer come, then give the turn with a zero delay."""
    came: ExternalPromise[None] = yield CreateExternalPromise()
    waiter: Task[None] = yield Spawn(_woken(came, turns))
    yield Delay(START_SECONDS)
    came.complete(None)
    yield Delay(0.0)
    turns.took("caller")
    yield Wait(waiter)
    return turns.taken


def test_a_zero_delay_lets_a_task_woken_by_a_come_completion_run_first() -> None:
    clock = async_time_handler(sleep=_sleep)
    ran = run(scheduled(await_handler(answers_the_zero_sleep_first(clock(_caller(_Turns()))))))
    assert ran == ("woken", "caller"), ran
