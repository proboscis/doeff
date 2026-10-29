# A pool of 2 permits shared by 4 tasks: at most 2 are inside at once, the others park their k in the semaphore's
# queue and enter as permits come back. Prints the order of events on the virtual clock, to back the pool scene
# of the Semaphore Effects video (the connection-pool pattern of docs/21-semaphore-effects.md).
from datetime import datetime, timezone

from doeff_core_effects import scheduled
from doeff_time import Delay, GetTime, sim_time_handler

from doeff import AcquireSemaphore, CreateSemaphore, Gather, ReleaseSemaphore, Spawn, do, run

LINES = []
INSIDE = set()
MOST = [0]


@do
def note(text):
    now = yield GetTime()
    LINES.append(f"{now:%H:%M:%S} {text}")


@do
def call(sem, name, seconds):
    yield note(f"{name} asks for a permit")
    yield AcquireSemaphore(sem)
    INSIDE.add(name)
    MOST[0] = max(MOST[0], len(INSIDE))
    yield note(f"{name} in    (inside: {' '.join(sorted(INSIDE))})")
    try:
        yield Delay(seconds)
        return name
    finally:
        INSIDE.discard(name)
        yield note(f"{name} out")
        yield ReleaseSemaphore(sem)


@do
def main():
    sem = yield CreateSemaphore(2)
    tasks = []
    for name, seconds in (("A", 3), ("B", 1), ("C", 1), ("D", 2)):
        tasks.append((yield Spawn(call(sem, name, seconds))))
    done = yield Gather(*tasks)
    yield note(f"Gather -> {done}")


START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
for i in range(3):
    LINES.clear()
    MOST[0] = 0
    run(scheduled(sim_time_handler(start_time=START)(main())))
    print(f"run {i + 1}: (most inside at once: {MOST[0]})")
    for line in LINES:
        print("  ", line)
