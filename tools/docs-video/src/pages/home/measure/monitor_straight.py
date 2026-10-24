# The loop-free monitor of the video, run under the test handlers; prints its lines and the wall time of each run.
# The printed handler stamps each line with the time it gets from the clock handler around it.
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from doeff_core_effects.scheduler import scheduled
from doeff_time import Delay, GetTime, sim_time_handler

from doeff import EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str


@dataclass(frozen=True)
class Log(EffectBase):
    line: str


@do
def monitor(url):
    page = yield Fetch(url)
    yield Log(f"{len(page)} bytes")
    yield Delay(30 * 60)
    page = yield Fetch(url)
    yield Log(f"{len(page)} bytes")


PAGES = {"a.example": "<p>up</p>"}
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
    run(scheduled(sim_time_handler(start_time=START)(printed(fake_web(monitor("a.example"))))))
    ms = (time.perf_counter() - t) * 1000
    print(f"run {i + 1}: {ms:.2f} ms {LINES}")
