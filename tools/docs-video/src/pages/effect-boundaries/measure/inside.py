# The page's pipeline, run under the core handlers. An observer handler, innermost, sees every effect the program
# and its tasks perform, notes the thread it is on and whether a Python event loop is running there, and passes the
# effect on. Prints the effects in order and where they ran, to back the "inside doeff" scene of the Effect
# Boundaries video.
import asyncio
import threading
from collections import Counter

from doeff_core_effects.handlers import reader, state, try_handler, writer
from doeff_core_effects.scheduler import scheduled

from doeff import Ask, Gather, Get, Pass, Put, Spawn, Tell, Try, Wait, do, handler, run

SEEN = []


def loop_running():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@handler
def observed(effect, k):  # notes each effect, then passes it on to the handler that answers it
    SEEN.append((type(effect).__name__, threading.current_thread().name, loop_running()))
    yield Pass(effect, k)


@do
def worker(name):
    count = yield Get("count")
    yield Put("count", count + 1)
    yield Tell(f"{name} done")
    return name.upper()


@do
def pipeline():
    who = yield Ask("who")
    t1 = yield Spawn(worker("a"))
    t2 = yield Spawn(worker("b"))
    values = yield Gather(t1, t2)
    first = yield Wait(t1)
    safe = yield Try(worker("c"))
    return who, values, first, safe


for i in range(3):
    SEEN.clear()
    result = run(scheduled(try_handler(state({"count": 0})(writer(reader({"who": "pipeline"})(observed(pipeline())))))))
    names = [name for name, _, _ in SEEN]
    threads = sorted({thread for _, thread, _ in SEEN})
    loops = sorted({loop for _, _, loop in SEEN})
    print(f"run {i + 1}: result {result}")
    print(f"  {len(SEEN)} effects in order: {names}")
    print(f"  by kind: {dict(Counter(names))}")
    print(f"  threads: {threads} | event loop running: {loops} | threads alive at end: {threading.active_count()}")
