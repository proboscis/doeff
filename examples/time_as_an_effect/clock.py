# clock.py
import asyncio
import heapq
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import count

from doeff import Await, EffectBase, Gather, Resume, Spawn, do, handler, run
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import (
    CompletePromise,
    CreatePromise,
    TaskPriority,
    Wait,
    scheduled,
)


@dataclass(frozen=True)
class Delay(EffectBase):  # "wait this many seconds"
    seconds: float


@dataclass(frozen=True)
class WaitUntil(EffectBase):  # "wait until this time"
    target: datetime


@dataclass(frozen=True)
class GetTime(EffectBase):  # "what time is it?"
    pass


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
    def clock(effect: Delay | GetTime, k):
        nonlocal now
        if isinstance(effect, Delay):
            now += timedelta(seconds=effect.seconds)
            return (yield Resume(k, None))
        return (yield Resume(k, now))

    return clock


def virtual_clock(start: datetime):  # waits take no time, and end in time order
    now = start
    sleepers = []  # (wake-up time, order, promise), earliest first
    order = count()
    advancing = False

    @do
    def advance():  # idle priority: runs while every other task waits
        nonlocal now, advancing
        while sleepers:
            now, _, promise = heapq.heappop(sleepers)
            yield CompletePromise(promise, None)
        advancing = False

    @handler
    def clock(effect: Delay | WaitUntil | GetTime, k):
        nonlocal advancing
        if isinstance(effect, GetTime):
            return (yield Resume(k, now))
        if isinstance(effect, Delay):
            wake_up = now + timedelta(seconds=effect.seconds)
        else:
            wake_up = max(now, effect.target)
        promise = yield CreatePromise()
        heapq.heappush(sleepers, (wake_up, next(order), promise))
        if not advancing:
            advancing = True
            yield Spawn(advance(), priority=TaskPriority.IDLE, daemon=True)
        yield Wait(promise.future)
        return (yield Resume(k, None))

    return clock


@handler
def wall_clock(effect: Delay | WaitUntil | GetTime, k):  # waits on asyncio
    now = datetime.now(timezone.utc)
    if isinstance(effect, GetTime):
        return (yield Resume(k, now))
    if isinstance(effect, Delay):
        seconds = effect.seconds
    else:
        seconds = (effect.target - now).total_seconds()
    yield Await(asyncio.sleep(max(seconds, 0)))
    return (yield Resume(k, None))


def real(program):  # the wall clock, with asyncio under the scheduler
    return scheduled(await_handler(wall_clock(program)))


if __name__ == "__main__":
    print("-- one task on our clock:")
    print(run(ticking(START)(nap(20))))
    print("-- two tasks on our clock:")
    print(run(scheduled(ticking(START)(two_naps()))))
    print("-- two tasks on the virtual clock:")
    print(run(scheduled(virtual_clock(START)(two_naps()))))
