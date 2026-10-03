"""The two timed waits on a wall clock — WaitWithin and WaitTicks (agora-redesign #3066).

Both race the future against one deadline timer; they differ only in how long the deadline is and in the
answer. The async and sync handlers share this, so their one clause answers both waits the same way.
"""

from doeff_time.effects import TicksOutcome, WaitTicksEffect, WaitWithinEffect

TimedWait = WaitWithinEffect[object] | WaitTicksEffect[object]


def timed_wait_seconds(effect: TimedWait) -> float:
    """How long the deadline of ``effect`` is on the wall clock: WaitWithin's ``seconds``, or the time to
    WaitTicks' last tick."""
    match effect:
        case WaitWithinEffect():
            return max(0.0, effect.seconds)
        case WaitTicksEffect():
            return effect.every * effect.count


def timed_wait_answer(effect: TimedWait, first: object, elapsed: float) -> object:
    """The answer of ``effect`` once the race gave ``first`` after ``elapsed`` seconds: WaitWithin answers
    ``first``; WaitTicks answers it with the ticks that passed — a wall clock does not observe them one by
    one, so all of them when the ticks ran out first (``first`` is None — the future's producer never
    completes it with None), else the whole spacings in ``elapsed``, at most ``count``."""
    match effect:
        case WaitWithinEffect():
            return first
        case WaitTicksEffect():
            passed = (
                effect.count
                if first is None
                else min(effect.count, max(0, int(elapsed // effect.every)))
            )
            return TicksOutcome(value=first, passed=passed)
