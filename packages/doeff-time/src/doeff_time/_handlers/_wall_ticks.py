"""The three timed waits on a wall clock — WaitWithin, WaitTicks (agora-redesign #3066) and RaceWithin.

All race the futures against one deadline timer that completes with DEADLINE_PASSED; they differ only in
which futures, how long the deadline is and in the answer. The async and sync handlers share this, so their
one clause answers the three waits the same way.
"""

from doeff_core_effects import Future, Task

from doeff_time._effects import (
    RaceOutcome,
    RaceWithinEffect,
    TicksOutcome,
    WaitTicksEffect,
    WaitWithinEffect,
)
from doeff_time._internals.deadline import DEADLINE_PASSED

TimedWait = WaitWithinEffect[object] | WaitTicksEffect[object] | RaceWithinEffect[object]


def timed_wait_futures(effect: TimedWait) -> "tuple[Task[object] | Future[object], ...]":
    """What ``effect`` races against its deadline: RaceWithin's ``futures``, or the one ``future``."""
    match effect:
        case RaceWithinEffect():
            return effect.futures
        case WaitWithinEffect() | WaitTicksEffect():
            return (effect.future,)


def timed_wait_seconds(effect: TimedWait) -> float:
    """How long the deadline of ``effect`` is on the wall clock: WaitWithin's and RaceWithin's ``seconds``, or
    the time to WaitTicks' last tick."""
    match effect:
        case WaitWithinEffect() | RaceWithinEffect():
            return max(0.0, effect.seconds)
        case WaitTicksEffect():
            return effect.every * effect.count


def timed_wait_answer(effect: TimedWait, first: object, elapsed: float) -> object:
    """The answer of ``effect`` once the race gave ``first`` (DEADLINE_PASSED when the deadline won) after
    ``elapsed`` seconds: WaitWithin answers the future's value or None; RaceWithin answers it as a RaceOutcome;
    WaitTicks answers it with the ticks that passed — a wall clock does not observe them one by one, so all of
    them when the ticks ran out first, else the whole spacings in ``elapsed``, at most ``count``."""
    timed_out = first is DEADLINE_PASSED
    value = None if timed_out else first
    match effect:
        case WaitWithinEffect():
            return value
        case RaceWithinEffect():
            return RaceOutcome(value=value, timed_out=timed_out)
        case WaitTicksEffect():
            passed = (
                effect.count
                if timed_out
                else min(effect.count, max(0, int(elapsed // effect.every)))
            )
            return TicksOutcome(value=value, passed=passed)
