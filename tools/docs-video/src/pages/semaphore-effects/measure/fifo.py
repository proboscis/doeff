# FIFO fairness and direct transfer on a mutex (CreateSemaphore(1)): main holds the permit and three tasks queue
# in the order they asked. main then spawns a late task and releases. The late task is ready to run when the permit
# comes back, but the release hands the permit straight to the oldest waiter (it never sits free), so the late task
# cannot take it and queues behind the others. Backs the FIFO scene of the Semaphore Effects video.
from datetime import datetime, timezone

from doeff_core_effects.scheduler import scheduled
from doeff_time import Delay, GetTime, sim_time_handler

from doeff import AcquireSemaphore, CreateSemaphore, Gather, ReleaseSemaphore, Spawn, do, run

LINES = []


@do
def note(text):
    now = yield GetTime()
    LINES.append(f"{now:%H:%M:%S} {text}")


@do
def worker(sem, name, late=False):
    if late:
        yield Delay(0)  # ready, but runs only after main's release
    yield note(f"{name} asks")
    yield AcquireSemaphore(sem)
    yield note(f"{name} holds the permit")
    yield Delay(1)
    yield ReleaseSemaphore(sem)


@do
def main():
    sem = yield CreateSemaphore(1)
    yield AcquireSemaphore(sem)
    yield note("main holds the permit")
    first = yield Spawn(worker(sem, "first"))
    second = yield Spawn(worker(sem, "second"))
    third = yield Spawn(worker(sem, "third"))
    yield Delay(1)
    late = yield Spawn(worker(sem, "late", late=True))
    yield note("main releases")
    yield ReleaseSemaphore(sem)
    yield Gather(first, second, third, late)


START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
for i in range(3):
    LINES.clear()
    run(scheduled(sim_time_handler(start_time=START)(main())))
    print(f"run {i + 1}:")
    for line in LINES:
        print("  ", line)
