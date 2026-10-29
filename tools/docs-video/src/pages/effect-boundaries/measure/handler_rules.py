# A custom handler keeps the boundary strict: it answers its domain effect inside doeff, and when the answer really
# needs Python async it performs Await itself, which travels out to await_handler. One program, Fetch(url), under a
# handler that answers from a table, then under one that awaits a coroutine, then the same without await_handler and
# with await_handler on the wrong side. Backs the "custom handler rules" scene of the Effect Boundaries video.
import asyncio
import threading
import time
from dataclasses import dataclass

from doeff_core_effects import await_handler, scheduled

from doeff import Await, EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str


@do
def page(url):
    return (yield Fetch(url))


@handler
def from_table(effect: Fetch, k):  # answers inside doeff
    return (yield Resume(k, f"<html>{effect.url}</html>"))


async def http_get(url):
    await asyncio.sleep(0.05)
    return f"<html>{url}</html> from {threading.current_thread().name}"


@handler
def over_async(effect: Fetch, k):  # needs Python async, so it performs Await; await_handler answers it
    body = yield Await(http_get(effect.url))
    return (yield Resume(k, body))


CASES = [
    ("scheduled(from_table(prog))", lambda: run(scheduled(from_table(page("a.dev"))))),
    ("scheduled(await_handler()(over_async(prog)))", lambda: run(scheduled(await_handler()(over_async(page("a.dev")))))),
    ("scheduled(over_async(prog))", lambda: run(scheduled(over_async(page("a.dev"))))),
    ("scheduled(over_async(await_handler()(prog)))", lambda: run(scheduled(over_async(await_handler()(page("a.dev")))))),
]

for i in range(3):
    for name, go in CASES:
        start = time.perf_counter()
        try:
            outcome = f"-> {go()!r}"
        except Exception as error:  # an unhandled effect: show the first line and the hint
            lines = str(error).splitlines()
            hint = next((line.strip() for line in lines if line.strip().startswith("hint:")), "")
            outcome = f"raises {type(error).__name__}: {lines[0]} | {hint}"
        ms = (time.perf_counter() - start) * 1000
        print(f"run {i + 1} {name:<46} {outcome} ({ms:.1f} ms)")
