# Three spawned tasks add 1 to one shared counter 100 times each, with Get then Put (the example in "Combining
# Advanced Effects" of docs/09-advanced-effects.md). The state handler sits outside the scheduler, so every task
# reads and writes the same store. A task runs until it waits, so a Get and its Put with no wait between them are
# never split by another task: the counter ends at 300. Wait between the Get and the Put, and the other tasks read
# the same old value in the gap: their updates are lost. Hold a CreateSemaphore(1) across the Get, the wait and the
# Put, and the count is 300 again. Backs the shared-state scene of the Advanced Effects video.
from datetime import datetime, timezone

from doeff_core_effects import Get, Put
from doeff_core_effects.handlers import state
from doeff_core_effects.scheduler import scheduled
from doeff_time import Delay, sim_time_handler

from doeff import AcquireSemaphore, CreateSemaphore, Gather, ReleaseSemaphore, Spawn, do, run


# One task: adds 1 to the shared count n times, optionally waiting between its read and its write.
@do
def add_one(name, n, wait, lock, show):
    for _ in range(n):
        if lock is not None:
            yield AcquireSemaphore(lock)
        current = yield Get("count")  # never None: counter Puts 0 first
        if wait:
            yield Delay(1)  # a wait between the read and the write
        yield Put("count", current + 1)
        if lock is not None:
            yield ReleaseSemaphore(lock)
        if show:
            print("  ", f"{name} read {current}, wrote {current + 1}")
    return name


# The program: three tasks on one count, the count once they are all done.
@do
def counter(n, wait, locked, show):
    yield Put("count", 0)
    lock = (yield CreateSemaphore(1)) if locked else None
    tasks = []
    for name in ("a", "b", "c"):
        tasks = [*tasks, (yield Spawn(add_one(name, n, wait, lock, show)))]
    yield Gather(*tasks)
    return (yield Get("count"))


START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


# One run with the handlers stacked: the state outside the scheduler, the virtual clock inside it.
def once(n, wait, locked=False, show=False):
    return run(state()(scheduled(sim_time_handler(start_time=START)(counter(n, wait, locked, show)))))


for i in range(3):
    print(f"run {i + 1}: no wait between Get and Put: count = {once(100, False)} (3 tasks x 100)")
    print(f"run {i + 1}: Delay(1) between Get and Put: count = {once(100, True)} (3 tasks x 100)")
    print(f"run {i + 1}: Delay(1) inside CreateSemaphore(1): count = {once(100, True, True)} (3 tasks x 100)")
print("the steps of 3 tasks x 2 with no wait:")
print("   count =", once(2, False, show=True))
print("the steps of 3 tasks x 2 with Delay(1) between Get and Put:")
print("   count =", once(2, True, show=True))
print("the steps of 3 tasks x 2 with Delay(1) inside CreateSemaphore(1):")
print("   count =", once(2, True, True, show=True))
