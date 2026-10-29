# Cancel while waiting: holder keeps the only permit for 2 s, w1 and w2 queue for it, and main cancels w1 at 1 s.
# The scheduler takes w1 out of the queue and resumes it with TaskCancelledError; w1 consumed no permit, so when
# holder releases, the permit goes to w2. Backs the Cancel scene of the Semaphore Effects video.
from datetime import datetime, timezone

from doeff_core_effects import try_handler
from doeff_core_effects import scheduled
from doeff_time import Delay, GetTime, sim_time_handler

from doeff import AcquireSemaphore, Cancel, CreateSemaphore, Err, ReleaseSemaphore, Spawn, Try, Wait, do, run

LINES = []


@do
def note(text):
    now = yield GetTime()
    LINES.append(f"{now:%H:%M:%S} {text}")


@do
def holder(sem):
    yield AcquireSemaphore(sem)
    yield note("holder holds the permit")
    yield Delay(2)
    yield note("holder releases")
    yield ReleaseSemaphore(sem)


@do
def waiter(sem, name):
    yield note(f"{name} waits")
    try:
        yield AcquireSemaphore(sem)
    except BaseException as e:
        yield note(f"{name} receives {type(e).__name__} at its Acquire")
        raise
    yield note(f"{name} holds the permit")
    yield ReleaseSemaphore(sem)
    yield note(f"{name} releases")


@do
def main():
    sem = yield CreateSemaphore(1)
    yield Spawn(holder(sem))
    w1 = yield Spawn(waiter(sem, "w1"))
    w2 = yield Spawn(waiter(sem, "w2"))
    yield Delay(1)
    yield Cancel(w1)
    yield note("main Cancel(w1) returned")
    match (yield Try(Wait(w1))):
        case Err(error=e):
            yield note(f"main Wait(w1) -> {type(e).__name__}")
    yield Wait(w2)
    yield note("main Wait(w2) -> done")


START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
for i in range(3):
    LINES.clear()
    run(scheduled(sim_time_handler(start_time=START)(try_handler(main()))))
    print(f"run {i + 1}:")
    for line in LINES:
        print("  ", line)
