"""Timer handler: ``ArmTimer`` / ``DisarmTimer`` answered over the clock handler's ``WaitWithin`` (agora-redesign #3076).

An armed timer is one task waiting ``WaitWithin(disarm, seconds)``: the clock handler owns the deadline, so
the same timer handler runs on every clock — ``sim_time_handler`` puts the deadline on its virtual time
queue (when every task waits, the clock moves to the next deadline in one step, however far it is), and the
wall-clock handlers sleep or start a timer. ``DisarmTimer`` completes the ``disarm`` promise: the wait ends
before its deadline, which the clock handler withdraws (the virtual clock never moves to it), and nothing is
published. When the deadline passes first, the task publishes ``TimerFired(tag)``. Re-arming a tag with the
deadline it already has keeps its waiting task: a worker that re-arms its deadlines after every pass pays no
new task for the ones that did not move (agora-redesign #3054 C). Disarming a tag that is not armed completes
nothing and costs the same VM steps as an ``ArmedTimers`` query.

Install it inside the event handler and the clock handler (the timer task's ``Publish`` / ``WaitWithin`` /
``GetTime`` go to the handlers outside this one), inside ``scheduled``.

A ``TimerFired`` is delivered as the event handler delivers any event. With the in-memory ``event_handler()``
that means only to the programs waiting for it at that moment: **a fire while no program waits is lost**, so a
worker that is busy (in another wait, or processing) when its deadline passes never sees that deadline. A
worker must not rely on ``ArmTimer`` until its event handler keeps a queue per subscriber (agora-controllers
docs/design/event-waits/README.md 2 (b) — agora-redesign #3075).
"""

from collections.abc import Hashable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from doeff_core_effects.scheduler import CompletePromise, CreatePromise, Promise, Spawn
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

# The value a disarm completes the wait's future with: WaitWithin answers None only when its deadline passes.
_DISARMED = True


@dataclass(frozen=True)
class _Arming:
    """One arming of a tag: the promise that disarms it and its deadline."""

    disarm: Promise[bool]
    at: datetime


def timer_handler() -> ProgramHandler:
    """Create a timer handler: each ``ArmTimer`` is one waiting task, ``DisarmTimer`` ends it unfired."""

    # The current arming of each armed tag. A re-arm or a disarm replaces / removes it, so a waiting task
    # publishes only while its own arming is still the tag's (it may have been replaced while it was waking).
    armed: dict[Hashable, _Arming] = {}

    @do
    def fire_at(tag: Hashable, arming: _Arming, seconds: float):
        first = yield WaitWithin(arming.disarm.future, seconds)
        if first is None and armed.get(tag) is arming:
            del armed[tag]
            yield Publish(TimerFired(tag))

    @do
    def disarm_tag(tag: Hashable):
        arming = armed.pop(tag, None)
        if arming is not None:
            yield CompletePromise(arming.disarm, _DISARMED)

    @do
    def handler(effect: ArmTimerEffect | DisarmTimerEffect | ArmedTimersEffect, k: Any):
        # Every clause performs its final Transfer/Pass from THIS frame (ADR-DOE-CORE-EFFECTS-002);
        # the sub-programs above complete before it.
        if isinstance(effect, ArmTimerEffect):
            current = armed.get(effect.tag)
            if current is not None and current.at == effect.at:
                # The same deadline is already armed: keep its waiting task (a worker that re-arms its deadlines
                # after every pass must not spawn a task per pass — agora-redesign #3054 C / cisco-c8 14:2x (c)).
                return (yield Transfer(k, None))
            if current is not None:
                _ = yield disarm_tag(effect.tag)
            now = yield GetTime()
            disarm = yield CreatePromise()
            arming = _Arming(disarm=disarm, at=effect.at)
            armed[effect.tag] = arming
            seconds = max(0.0, (effect.at - now).total_seconds())
            # daemon: a timer still armed (or one step from its end after publishing) when the root body
            # returns is abandoned with it — that is its lifecycle, not lost work (#501).
            _ = yield Spawn(fire_at(effect.tag, arming, seconds), daemon=True)
            return (yield Transfer(k, None))
        if isinstance(effect, DisarmTimerEffect):
            _ = yield disarm_tag(effect.tag)
            return (yield Transfer(k, None))
        if isinstance(effect, ArmedTimersEffect):
            timers = tuple(
                sorted(
                    (ArmedTimer(tag=tag, at=arming.at) for tag, arming in armed.items()),
                    key=lambda t: t.at,
                )
            )
            return (yield Transfer(k, timers))
        yield Pass(effect, k)

    return _program_handler(handler)
