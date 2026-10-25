# Time is effects too: the same program yields Delay and GetTime, and the time handler decides what they mean
# (the "Time Effects Runtime Matrix" of docs/09-advanced-effects.md). Under sync_time_handler, Delay(1.5) really
# sleeps; under sim_time_handler it moves a virtual clock and returns at once. Prints the program's clock readings
# and the wall time each run took, to back the time scene of the Advanced Effects video.
import time
from datetime import datetime, timezone

from doeff_core_effects.scheduler import scheduled
from doeff_time import Delay, GetTime, sim_time_handler, sync_time_handler

from doeff import do, run


@do
def nap():
    before = yield GetTime()
    yield Delay(1.5)
    after = yield GetTime()
    return (after - before).total_seconds()


@do
def long_nap():
    before = yield GetTime()
    yield Delay(3600)
    after = yield GetTime()
    return f"{before:%H:%M:%S} -> {after:%H:%M:%S}"


START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def timed(program):
    t0 = time.perf_counter()
    value = run(scheduled(program))
    return value, (time.perf_counter() - t0) * 1000


for i in range(3):
    value, ms = timed(sync_time_handler()(nap()))
    print(f"run {i + 1} sync_time_handler, Delay(1.5): clock moved {value:.2f} s, wall {ms:.0f} ms")
    value, ms = timed(sim_time_handler(start_time=START)(nap()))
    print(f"run {i + 1} sim_time_handler, Delay(1.5): clock moved {value:.2f} s, wall {ms:.2f} ms")
    value, ms = timed(sim_time_handler(start_time=START)(long_nap()))
    print(f"run {i + 1} sim_time_handler, Delay(3600): clock {value}, wall {ms:.2f} ms")
