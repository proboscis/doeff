# The permit leak: a task acquires the only permit and fails before ReleaseSemaphore. Semaphores do not track who
# holds a permit, so without try/finally the permit is gone and the next task waits for it forever (here main gives
# up after 5 s with Race against a Delay, and cancels it). With try/finally the failing task still releases, and the
# next task enters at once. Backs the leak scene of the Semaphore Effects video.
from datetime import datetime, timezone

from doeff_core_effects import try_handler
from doeff_core_effects import scheduled
from doeff_time import Delay, GetTime, sim_time_handler

from doeff import AcquireSemaphore, Cancel, CreateSemaphore, Race, ReleaseSemaphore, Spawn, Try, Wait, do, run

LINES = []


@do
def note(text):
    now = yield GetTime()
    LINES.append(f"{now:%H:%M:%S} {text}")


@do
def leaky(sem):
    yield AcquireSemaphore(sem)
    yield note("leaky holds the permit")
    yield Delay(1)
    raise ValueError("boom")
    yield ReleaseSemaphore(sem)  # never reached


@do
def guarded(sem):
    yield AcquireSemaphore(sem)
    yield note("guarded holds the permit")
    try:
        yield Delay(1)
        raise ValueError("boom")
    finally:
        yield ReleaseSemaphore(sem)


@do
def next_task(sem):
    yield AcquireSemaphore(sem)
    yield note("next task got the permit")
    yield ReleaseSemaphore(sem)
    return "entered"


@do
def give_up(seconds):
    yield Delay(seconds)
    return "still waiting"


@do
def main(failing):
    sem = yield CreateSemaphore(1)
    first = yield Spawn(failing(sem))
    nxt = yield Spawn(next_task(sem))
    yield Try(Wait(first))
    yield note(f"{failing.__name__} raised ValueError('boom')")
    timer = yield Spawn(give_up(5))
    winner = yield Race(nxt, timer)
    yield note(f"next task: {winner}")
    loser = timer if winner == "entered" else nxt
    yield Cancel(loser)
    yield Try(Wait(loser))


START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
for i in range(3):
    print(f"run {i + 1}:")
    for failing in (leaky, guarded):
        LINES.clear()
        run(scheduled(sim_time_handler(start_time=START)(try_handler(main(failing)))))
        print(f"  {failing.__name__}:")
        for line in LINES:
            print("    ", line)
