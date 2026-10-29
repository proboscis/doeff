# Two monitors run at once under the test handlers: the scheduler interleaves them by the virtual clock.
# Prints each run's lines and wall time, to back the scheduling scene of the concept video.
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from doeff_core_effects import Gather, Spawn, scheduled
from doeff_time import Delay, GetTime, sim_time_handler

from doeff import EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str


@dataclass(frozen=True)
class Log(EffectBase):
    line: str


@do
def monitor(url, minutes):
    page = yield Fetch(url)
    yield Log(f"{url} {len(page)} bytes")
    yield Delay(minutes * 60)
    page = yield Fetch(url)
    yield Log(f"{url} {len(page)} bytes")


@do
def both():
    a = yield Spawn(monitor("a.example", 30))
    b = yield Spawn(monitor("b.example", 10))
    yield Gather(a, b)


PAGES = {"a.example": "<p>up</p>", "b.example": "<p>ok!</p>"}
LINES = []


@handler
def fake_web(effect: Fetch, k):  # answers Fetch from a dict
    return (yield Resume(k, PAGES[effect.url]))


@handler
def printed(effect: Log, k):  # prints the line with the time the clock gives
    now = yield GetTime()
    LINES.append(f"{now:%H:%M} {effect.line}")
    return (yield Resume(k, None))


START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
for i in range(7):
    LINES.clear()
    t = time.perf_counter()
    run(scheduled(sim_time_handler(start_time=START)(printed(fake_web(both())))))
    ms = (time.perf_counter() - t) * 1000
    print(f"run {i + 1}: {ms:.2f} ms {LINES}")
