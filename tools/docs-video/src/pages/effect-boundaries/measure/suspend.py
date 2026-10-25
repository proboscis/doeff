# Scheduler suspension stays inside doeff; Await waits outside it. One task waits three ways, each timed on the wall
# clock, with the thread the waiting program ran on and the thread the wait was answered on: a Wait on a doeff
# Promise that another task completes, a Delay(3) on the virtual clock, and an Await of asyncio.sleep(0.3). Backs the
# "suspension vs leaving" scene of the Effect Boundaries video.
import asyncio
import threading
import time

from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import scheduled
from doeff_time import Delay, GetTime, sim_time_handler

from doeff import Await, CompletePromise, CreatePromise, Spawn, Wait, do, run

ANSWERED_ON = []


def here():
    return threading.current_thread().name


@do
def answer(promise):  # another task completes the promise, inside doeff
    ANSWERED_ON.append(here())
    yield CompletePromise(promise, "ready")


@do
def on_promise():
    promise = yield CreatePromise()
    helper = yield Spawn(answer(promise))
    value = yield Wait(promise.future)
    yield Wait(helper)
    return value


@do
def on_virtual_clock():
    before = yield GetTime()
    yield Delay(3)
    after = yield GetTime()
    ANSWERED_ON.append(here())
    return f"virtual clock moved {(after - before).total_seconds():.0f} s"


async def sleep_outside():  # the outside world: a coroutine on Python's event loop
    ANSWERED_ON.append(here())
    await asyncio.sleep(0.3)
    return "slept 0.3 s"


@do
def on_event_loop():
    return (yield Await(sleep_outside()))


CASES = [
    ("Wait(promise)", lambda: run(scheduled(on_promise()))),
    ("Delay(3)", lambda: run(scheduled(sim_time_handler()(on_virtual_clock())))),
    ("Await(asyncio.sleep(0.3))", lambda: run(scheduled(await_handler()(on_event_loop())))),
]

for i in range(5):
    for name, go in CASES:
        ANSWERED_ON.clear()
        start = time.perf_counter()
        value = go()
        ms = (time.perf_counter() - start) * 1000
        print(f"run {i + 1} {name:<26} -> {value!r:<28} wall {ms:7.2f} ms | program on {here()} | answered on {ANSWERED_ON}")
