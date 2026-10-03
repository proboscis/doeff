"""Timer handler: ``ArmTimer`` / ``DisarmTimer`` answered by one task over a queue of deadlines (agora-redesign #3076).

The handler keeps the armed deadlines in one queue, ordered by instant and then by arming order, and one task
works the queue: it waits ``WaitWithin(wake, seconds)`` until the earliest deadline, publishes ``TimerFired``
for every deadline that has come, and waits for the next one (cisco-c8 2026-10-03 15:1x — a task per deadline
cost a ``Spawn``, a promise and a wait for every arming; agora-redesign #3054 / #3116). The clock handler owns
the deadline, so the same timer handler runs on every clock — ``sim_time_handler`` puts it on its virtual time
queue (when every task waits, the clock moves to the next deadline in one step, however far it is), and the
wall-clock handlers sleep or start a timer.

``ArmTimer`` adds a deadline to the queue and ``DisarmTimer`` takes one out; neither starts a task. Only when
the earliest deadline changes (an earlier one is armed, or the earliest is moved or disarmed) does the handler
complete the waiting task's ``wake`` promise, so the task waits again for the new earliest deadline — the clock
handler withdraws the old one (the virtual clock never moves to a disarmed deadline). Re-arming a tag with the
deadline it already has, or arming a deadline later than the earliest, costs no promise and no task. With no
deadline armed the task waits for ``wake`` alone. Deadlines of the same instant fire in the order they were
armed.

Install it inside the event handler and the clock handler (the timer task's ``Publish`` / ``WaitWithin`` /
``GetTime`` go to the handlers outside this one), inside ``scheduled``.

A ``TimerFired`` is delivered as the event handler delivers any event. With the in-memory ``event_handler()``
that means only to the programs waiting for it at that moment: **a fire while no program waits is lost**, so a
worker that is busy (in another wait, or processing) when its deadline passes never sees that deadline. A
worker must not rely on ``ArmTimer`` until its event handler keeps a queue per subscriber (agora-controllers
docs/design/event-waits/README.md 2 (b) — agora-redesign #3075).
"""

from collections.abc import Hashable
from dataclasses import dataclass, replace
from datetime import datetime
from itertools import count
from typing import Any

from doeff_core_effects.scheduler import CompletePromise, CreatePromise, Promise, Spawn, Wait
from doeff_time import GetTime, WaitWithin

from doeff import Pass, Transfer, do
from doeff import handler as _program_handler
from doeff.program import ProgramHandler
from doeff_events.effects import (
    ArmedTimer,
    ArmedTimersEffect,
    ArmTimerEffect,
    DisarmTimerEffect,
    Publish,
    TimerFired,
)

# The value the handler completes the task's wake promise with: WaitWithin answers None only when its deadline passes.
_WOKEN = True


@dataclass(frozen=True)
class _Arming:
    """One arming of a tag: its deadline and its place in the arming order (same-instant deadlines fire in it)."""

    at: datetime
    order: int


@dataclass(frozen=True)
class _TaskState:
    """Where the handler's one task is, as its clauses see it: whether it was started, whether it is in its wait now,
    its ``wake`` promise (None once the handler completed it) and the arming it waits until (None = no deadline)."""

    running: bool = False
    waiting: bool = False
    wake: Promise[bool] | None = None
    target: _Arming | None = None


def _earliest(armed: dict[Hashable, _Arming]) -> _Arming | None:
    """The arming the task waits until: the earliest deadline, the earliest arming among the same instant."""
    return min(armed.values(), key=lambda arming: (arming.at, arming.order), default=None)


def _in_order(armed: dict[Hashable, _Arming]) -> tuple[tuple[Hashable, _Arming], ...]:
    """The armed deadlines in firing order — by instant, then by arming order (``ArmedTimers`` answers this order)."""
    return tuple(sorted(armed.items(), key=lambda item: (item[1].at, item[1].order)))


def _due(armed: dict[Hashable, _Arming], reached: datetime) -> tuple[tuple[Hashable, _Arming], ...]:
    """The armed deadlines that have come once the clock reached ``reached``, in firing order."""
    return tuple((tag, arming) for tag, arming in _in_order(armed) if arming.at <= reached)


def timer_handler() -> ProgramHandler:
    """Create a timer handler: ``ArmTimer`` / ``DisarmTimer`` change one queue, one task fires its deadlines."""

    # The queue: the current arming of each armed tag (a handler-local cache — re-arming or disarming a tag replaces
    # or removes its entry). Arming order numbers come from ``orders``; the task's place is the frozen ``task``.
    armed: dict[Hashable, _Arming] = {}
    orders = count()
    task = _TaskState()

    @do
    def run_queue():
        # The one task: wait for the earliest deadline (or, with none armed, for the handler's wake), publish
        # the deadlines that came, and go round. A wake promise the deadline beat stays pending and is reused.
        nonlocal task
        while True:
            wake = task.wake
            if wake is None:
                wake = yield CreatePromise()
                task = replace(task, wake=wake)
            target: _Arming | None = None
            if not armed:
                task = replace(task, waiting=True, target=None)
                woken = yield Wait(wake.future)
            else:
                now = yield GetTime()
                # No yield between choosing the target and the wait: an arming in between would be missed.
                target = _earliest(armed)
                if target is None:
                    continue
                task = replace(task, waiting=True, target=target)
                woken = yield WaitWithin(wake.future, max(0.0, (target.at - now).total_seconds()))
            task = replace(task, waiting=False)
            if woken is not None or target is None:
                continue
            for tag, arming in _due(armed, target.at):
                # A deadline moved or disarmed while an earlier fire was being published is not fired.
                if armed.get(tag) is arming:
                    del armed[tag]
                    yield Publish(TimerFired(tag))

    @do
    def queue_changed():
        # Start the task on the first arming; afterwards wake it only when the deadline it waits for is no longer
        # the earliest (an arming later than the earliest leaves its wait as it is).
        nonlocal task
        if not task.running:
            task = replace(task, running=True)
            # daemon: the task waits for the next deadline when the root body returns, and is abandoned with
            # it — that is its lifecycle, not lost work (#501).
            _ = yield Spawn(run_queue(), daemon=True)
            return None
        wake = task.wake
        if task.waiting and wake is not None and _earliest(armed) is not task.target:
            task = replace(task, wake=None)
            yield CompletePromise(wake, _WOKEN)
        return None

    @do
    def handler(effect: ArmTimerEffect | DisarmTimerEffect | ArmedTimersEffect, k: Any):
        # Every clause performs its final Transfer/Pass from THIS frame (ADR-DOE-CORE-EFFECTS-002);
        # the sub-programs above complete before it.
        if isinstance(effect, ArmTimerEffect):
            current = armed.get(effect.tag)
            if current is not None and current.at == effect.at:
                # The same deadline is already armed: nothing changes (a worker re-arms its deadlines after every
                # pass — agora-redesign #3054 C / cisco-c8 14:2x (c)).
                return (yield Transfer(k, None))
            armed[effect.tag] = _Arming(at=effect.at, order=next(orders))
            _ = yield queue_changed()
            return (yield Transfer(k, None))
        if isinstance(effect, DisarmTimerEffect):
            if armed.pop(effect.tag, None) is not None:
                _ = yield queue_changed()
            return (yield Transfer(k, None))
        if isinstance(effect, ArmedTimersEffect):
            timers = tuple(ArmedTimer(tag=tag, at=arming.at) for tag, arming in _in_order(armed))
            return (yield Transfer(k, timers))
        yield Pass(effect, k)

    return _program_handler(handler)
