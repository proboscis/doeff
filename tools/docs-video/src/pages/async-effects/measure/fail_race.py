# Gather is fail-fast, and Race returns the first completion; neither cancels the other tasks. Tasks wait on the
# virtual clock, so the times below are the same on every run. Backs the Gather/Race scene of the Async Effects
# video.
from datetime import datetime, timezone

from doeff_core_effects.scheduler import scheduled
from doeff_time import Delay, GetTime, sim_time_handler

from doeff import Cancel, Err, Gather, Race, Spawn, Try, Wait, do, run
from doeff_core_effects.handlers import try_handler

LINES = []


@do
def note(text):
    now = yield GetTime()
    LINES.append(f"{now:%H:%M:%S} {text}")


@do
def job(name, seconds):
    yield Delay(seconds)
    yield note(f"{name} done")
    return name


@do
def broken(name, seconds):
    yield Delay(seconds)
    yield note(f"{name} raises ValueError")
    raise ValueError("boom")


@do
def gather_fails():
    a = yield Spawn(job("a", 3))
    b = yield Spawn(broken("b", 1))
    c = yield Spawn(job("c", 2))
    result = yield Try(Gather(a, b, c))
    match result:
        case Err(error=e):
            yield note(f"main Gather raised {e!r}")
    value = yield Wait(c)
    yield note(f"main Wait(c) -> {value!r}, c was not cancelled")
    yield Cancel(a)
    yield note("main Cancel(a)")
    yield Try(Wait(a))  # wait until the cancelled task has unwound


@do
def race():
    a = yield Spawn(job("a", 3))
    b = yield Spawn(job("b", 1))
    winner = yield Race(a, b)
    yield note(f"main Race -> {winner!r}")
    yield Delay(5)  # a, the loser, keeps running unless cancelled
    yield note("main after 5 more seconds")


@do
def race_then_cancel():
    a = yield Spawn(job("a", 3))
    b = yield Spawn(job("b", 1))
    winner = yield Race(a, b)
    yield note(f"main Race -> {winner!r}")
    yield Cancel(a)
    yield note("main Cancel(a)")
    yield Try(Wait(a))
    yield Delay(5)
    yield note("main after 5 more seconds")


START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
for i in range(3):
    for program in (gather_fails, race, race_then_cancel):
        LINES.clear()
        run(scheduled(sim_time_handler(start_time=START)(try_handler(program()))))
        print(f"run {i + 1} {program.__name__}:")
        for line in LINES:
            print("  ", line)
