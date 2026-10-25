# Hold it: the page's section 7, a virtual clock built on the reference scheduler `scheduled`, run 7 times. Prints each run's log,
# its wall time, and when each sleeper is parked and woken, to back the "hold k" scene of the video.
import heapq
import time
from dataclasses import dataclass
from itertools import count

from doeff import EffectBase, Gather, Resume, Spawn, do, handler, run
from doeff_core_effects import Tell
from doeff_core_effects.handlers import state, writer, writer_log
from doeff_core_effects.scheduler import (
    PRIORITY_IDLE,
    CompletePromise,
    CreatePromise,
    Wait,
    scheduled,
)


@dataclass(frozen=True)
class Sleep(EffectBase):
    seconds: float


@dataclass(frozen=True)
class Now(EffectBase):
    pass


trace = []


def virtual_clock():
    now = 0.0
    sleepers = []                       # (wake-up time, order, promise), earliest first
    order = count()
    advancing = False

    @do
    def advance():                      # idle priority: runs while every other task waits
        nonlocal now, advancing
        while sleepers:
            now, _, promise = heapq.heappop(sleepers)
            trace.append(f"wake {now / 3600:g}h")
            yield CompletePromise(promise, None)
        advancing = False

    @handler
    def clock(effect: Sleep | Now, k):
        nonlocal advancing
        if isinstance(effect, Now):
            return (yield Resume(k, now))
        promise = yield CreatePromise()
        heapq.heappush(sleepers, (now + effect.seconds, next(order), promise))
        trace.append(f"park until {(now + effect.seconds) / 3600:g}h")
        if not advancing:
            advancing = True
            yield Spawn(advance(), priority=PRIORITY_IDLE, daemon=True)
        yield Wait(promise.future)
        return (yield Resume(k, None))

    return clock


@do
def reminder(name, every_hours, times):
    for _ in range(times):
        yield Sleep(every_hours * 3600)
        hour = (yield Now()) / 3600
        yield Tell(f"{hour:g}h {name}")


@do
def day():
    tea = yield Spawn(reminder("tea", 2, 3))
    stretch = yield Spawn(reminder("stretch", 3, 2))
    yield Gather(tea, stretch)


@do
def logged(program):                    # runs program, then reads back what it told
    yield program
    return (yield writer_log())


for i in range(7):
    trace.clear()
    started = time.perf_counter()
    log = run(scheduled(state()(writer(virtual_clock()(logged(day()))))))
    ms = (time.perf_counter() - started) * 1000
    print(f"run {i + 1}: {ms:.2f} ms {log}")
print("trace of the last run:", trace)
