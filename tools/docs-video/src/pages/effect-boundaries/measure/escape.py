# Await is the one effect that leaves doeff. Two tasks Await a coroutine (asyncio.sleep of 0.2 s and 0.1 s) while a
# third task performs Step effects that a handler answers inside doeff. Every line notes the milliseconds since the run began and the thread it
# was printed on: the program's side on the run's thread, the coroutines on the shared await bridge's event loop.
# Backs the "Await leaves doeff" scene of the Effect Boundaries video.
import asyncio
import threading
import time

from doeff_core_effects import await_handler
from doeff_core_effects import scheduled

from dataclasses import dataclass

from doeff import Await, EffectBase, Gather, Resume, Spawn, do, handler, run

START = [0.0]
LOG = []


def note(text):
    ms = (time.perf_counter() - START[0]) * 1000
    LOG.append((ms, threading.current_thread().name, text))


async def download(name, seconds):
    note(f"{name}: coroutine starts, loop running: {asyncio.get_running_loop().is_running()}")
    await asyncio.sleep(seconds)
    note(f"{name}: coroutine returns")
    return f"<{name}>"


@do
def fetch(name, seconds):
    note(f"{name}: yield Await(download({name!r}, {seconds}))")
    page = yield Await(download(name, seconds))
    note(f"{name}: resumed with {page!r}")
    return page


@dataclass(frozen=True)
class Step(EffectBase):
    n: int


@handler
def stepped(effect: Step, k):  # answers Step inside doeff
    note(f"c: Step({effect.n}) answered by stepped")
    return (yield Resume(k, effect.n))


@do
def steps():
    for n in (1, 2, 3):
        yield Step(n)
    return "c"


@do
def main():
    a = yield Spawn(fetch("a", 0.2))
    b = yield Spawn(fetch("b", 0.1))
    c = yield Spawn(steps())
    values = yield Gather(a, b, c)
    note(f"main: Gather -> {values}")
    return values


def at(text):
    return next(ms for ms, _, line in LOG if line.startswith(text))


for i in range(10):
    LOG.clear()
    START[0] = time.perf_counter()
    result = run(scheduled(await_handler(stepped(main()))))
    total = (time.perf_counter() - START[0]) * 1000
    print(f"run {i + 1}: result {result} in {total:.1f} ms")
    if i == 0:
        for ms, thread, text in LOG:
            print(f"  {ms:7.2f} ms  {thread:<18}  {text}")
    steps_done = max(ms for ms, _, text in LOG if text.startswith("c: Step"))
    print(
        f"  c's 3 Steps done by {steps_done:.2f} ms | b resumed {at('b: resumed'):.1f} ms | a resumed {at('a: resumed'):.1f} ms"
        f" | coroutines on {sorted({t for _, t, x in LOG if 'coroutine' in x})} | program on {sorted({t for _, t, x in LOG if 'coroutine' not in x})}"
    )
