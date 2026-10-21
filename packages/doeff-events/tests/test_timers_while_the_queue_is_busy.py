"""Deadlines reach a program whose subscriber queue never empties (card ki-7d2a9efa8320).

The timer task fires its deadlines only when the scheduler runs it, and the subscriber queue answers a
``WaitForEvent(s)`` at once while it holds an event — so a program kept busy by queued events (the job
agent-toolset-sync reaping land events back to back for half an hour) gave the task no turn, and a deadline that
had passed waited for a moment the queue was empty. And when the task did run late, it fired only the deadlines up
to the one it waited for: a later deadline that had also passed waited for one more round.

- A program that waits only on a busy queue receives every passed deadline before the queue empties (its very next
  wait answers them with the queued events).
- A task that runs late fires every deadline that has passed by then in the one round, not one per round.
- On the virtual clock the order does not change: a deadline still fires at its own instant (tests/test_timers.py).

The wall clock here is the blocking one with its ``now`` replaced by a clock that moves ``STRIDE`` on every read: the
program's work between two reads takes longer than both deadlines, as a long reap does, without the test sleeping.

The counterexamples — a wait that answers from the queue without the overdue deadlines, a late task that fires only
up to the deadline it waited for — fail the checks below.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import count
from typing import Any

from doeff_core_effects.scheduler import CreateExternalPromise, Wait, scheduled
from doeff_events import (
    ArmTimer,
    EventBus,
    Publish,
    TimerFired,
    WaitForEvents,
    subscribed_event_handler,
    timer_handler,
)
from doeff_time import GetTime, WaitWithinEffect
from doeff_time.handlers.sync_time import sync_time_handler

from doeff import Pass, do, run
from doeff import handler as program_handler
from doeff.program import ProgramHandler

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
# How far the clock moves on every read: past both deadlines at once.
STRIDE = timedelta(minutes=10)
FIRST = timedelta(minutes=2)
SECOND = timedelta(minutes=4)
# How long the program parks on the scheduler (real seconds) while the timer task has its rounds.
PARKED = 0.05


@dataclass(frozen=True)
class Landed:
    """An event the program keeps finding in its queue (a land event to reap)."""

    n: int


def _striding_clock() -> Callable[[], datetime]:
    """A clock that reads ``T0``, then ``STRIDE`` later on every read."""
    reads = count()
    return lambda: T0 + STRIDE * next(reads)


def _on_queue(program: Any, *inner: ProgramHandler) -> Any:
    """``program`` under the timer handler (and the ``inner`` handlers), inside a subscriber queue, on the blocking
    wall clock whose time moves ``STRIDE`` per read."""
    handled = timer_handler()(program)
    for handler in inner:
        handled = handler(handled)
    queue = subscribed_event_handler(EventBus(), "main", (Landed, TimerFired))
    return scheduled(sync_time_handler(now=_striding_clock())(queue(handled)))


@do
def busy_reaping(rounds: int):
    """Arm two deadlines, then reap ``rounds`` land events back to back: each round puts the next event in the queue
    before it waits, so the queue never empties and the program never waits on the scheduler. Answer the TimerFired
    tags each wait received."""
    now = yield GetTime()
    yield ArmTimer("count", now + FIRST)
    yield ArmTimer("prune", now + SECOND)
    seen: tuple[tuple[Any, ...], ...] = ()
    for n in range(rounds):
        yield Publish(Landed(n))
        got = yield WaitForEvents(Landed, TimerFired)
        seen = (*seen, tuple(event.tag for event in got if isinstance(event, TimerFired)))
    return seen


def test_a_busy_queue_does_not_hold_back_the_passed_deadlines() -> None:
    # The broken form answers every wait from the queue and no TimerFired ever arrives: ((), (), ()).
    assert run(_on_queue(busy_reaping(3))) == (("count", "prune"), (), ())


def _counting_timed_waits(seen: dict[str, int]) -> ProgramHandler:
    """A handler that counts the ``WaitWithin`` passing outward (the timer task's waits) in ``seen["timed"]``."""

    @do
    def handler(effect: WaitWithinEffect, k: Any):
        seen["timed"] = seen.get("timed", 0) + 1
        yield Pass(effect, k)

    return program_handler(handler)


def _complete_later(seconds: float, gate: Any) -> None:
    """Complete the external promise ``gate`` after ``seconds``, from a wall-clock thread."""
    timer = threading.Timer(seconds, lambda: gate.complete(None))
    timer.daemon = True
    timer.start()


@do
def armed_then_parked():
    """Arm two deadlines (the timer task first reads the clock after both), then park on the scheduler — not on an
    event wait — while the task has its rounds."""
    now = yield GetTime()
    yield ArmTimer("count", now + FIRST)
    yield ArmTimer("prune", now + SECOND)
    gate = yield CreateExternalPromise(deadline=time.monotonic() + PARKED)
    _complete_later(PARKED, gate)
    yield Wait(gate.future)


def test_a_late_task_fires_every_passed_deadline_in_one_round() -> None:
    # The task first reads the clock after both deadlines. The broken form fires "count" (up to the deadline it waited
    # for), then waits again — 0 seconds — for "prune": two timed waits. Fixed, it fires both after its first wait.
    seen: dict[str, int] = {}
    run(_on_queue(armed_then_parked(), _counting_timed_waits(seen)))
    assert seen == {"timed": 1}
