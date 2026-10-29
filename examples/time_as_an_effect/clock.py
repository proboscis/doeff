# clock.py
from datetime import datetime, timedelta, timezone

from doeff import Gather, Resume, Spawn, do, handler, run
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import scheduled
from doeff_time import (
    Delay,
    DelayEffect,
    GetTime,
    GetTimeEffect,
    async_time_handler,
    sim_time_handler,
)

START = datetime(2026, 11, 30, 9, 0, tzinfo=timezone.utc)


@do
def nap(seconds: float):  # asks for time twice, and never says which clock
    yield Delay(seconds)
    return (yield GetTime())


@do
def two_naps(slow: float = 20, fast: float = 10):  # two tasks wait at once
    start = yield GetTime()
    slow_task = yield Spawn(nap(slow))
    fast_task = yield Spawn(nap(fast))
    woke = yield Gather(slow_task, fast_task)
    return [(at - start).total_seconds() for at in woke]


def ticking(start: datetime):  # a clock of our own: resume k at once, move the hands
    now = start

    @handler
    def clock(effect: DelayEffect | GetTimeEffect, k):
        nonlocal now
        if isinstance(effect, DelayEffect):
            now += timedelta(seconds=effect.seconds)
            return (yield Resume(k, None))
        return (yield Resume(k, now))

    return clock


def wall_clock(program):  # the wall clock, waiting on asyncio
    return scheduled(await_handler()(async_time_handler()(program)))


if __name__ == "__main__":
    print("-- one task on our clock:")
    print(run(ticking(START)(nap(20))))
    print("-- two tasks on our clock:")
    print(run(scheduled(ticking(START)(two_naps()))))
    print("-- two tasks on the virtual clock:")
    print(run(scheduled(sim_time_handler(start_time=START)(two_naps()))))
