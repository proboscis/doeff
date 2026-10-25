# Cancel is delivered into the task: the task gets TaskCancelledError where it is suspended, its finally block
# runs (and may perform effects), and the waiter sees TaskCancelledError only after that. Prints the order of
# events, to back the Cancel scene of the Async Effects video (the example of docs/04-async-effects.md).
from datetime import datetime, timezone

from doeff_core_effects.handlers import try_handler
from doeff_core_effects.scheduler import scheduled
from doeff_time import Delay, GetTime, sim_time_handler

from doeff import Cancel, CreatePromise, Err, Spawn, Try, Wait, do, run

LINES = []


@do
def note(text):
    now = yield GetTime()
    LINES.append(f"{now:%H:%M:%S} {text}")


@do
def work():
    lease = "lease-1"
    yield note(f"work got {lease}, waits for a reply")
    try:
        reply = yield CreatePromise()  # a reply that never arrives
        return (yield Wait(reply.future))
    except BaseException as e:
        yield note(f"work receives {type(e).__name__} at its Wait")
        raise
    finally:
        yield Delay(1)  # cleanup may perform effects
        yield note(f"work finally: released {lease}")


@do
def main():
    task = yield Spawn(work())
    yield Delay(2)
    yield Cancel(task)
    yield note("main Cancel(task) returned")
    result = yield Try(Wait(task))
    match result:
        case Err(error=e):
            yield note(f"main Wait(task) -> {type(e).__name__}")


START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
for i in range(3):
    LINES.clear()
    run(scheduled(sim_time_handler(start_time=START)(try_handler(main()))))
    print(f"run {i + 1}:")
    for line in LINES:
        print("  ", line)
