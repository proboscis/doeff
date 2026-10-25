# Spawn starts three tasks that wait 3, 1 and 2 seconds on the virtual clock; Gather waits for all of them.
# Prints when each line happens, to back the Spawn/Gather scene of the Async Effects video: the parent goes on
# right after each Spawn, the tasks finish in time order, and Gather returns their values in input order.
import time
from datetime import datetime, timezone

from doeff_core_effects.scheduler import scheduled
from doeff_time import Delay, GetTime, sim_time_handler

from doeff import Gather, Spawn, do, run

LINES = []


@do
def note(text):
    now = yield GetTime()
    LINES.append(f"{now:%H:%M:%S} {text}")


@do
def fetch(name, seconds):
    yield note(f"{name} starts")
    yield Delay(seconds)
    yield note(f"{name} done")
    return name.upper()


@do
def program():
    a = yield Spawn(fetch("a", 3))
    b = yield Spawn(fetch("b", 1))
    c = yield Spawn(fetch("c", 2))
    yield note("main spawned a, b, c")
    values = yield Gather(a, b, c)
    yield note(f"main Gather -> {values}")
    return values


START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
runs = set()
for i in range(21):
    LINES.clear()
    t = time.perf_counter()
    result = run(scheduled(sim_time_handler(start_time=START)(program())))
    ms = (time.perf_counter() - t) * 1000
    print(f"run {i + 1}: {ms:.2f} ms result {result}")
    runs.add(tuple(LINES))
print(f"{len(runs)} distinct order(s) of lines in 21 runs:")
for lines in runs:
    for line in lines:
        print("  ", line)
