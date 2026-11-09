"""The runs behind the article2-paused-code video: what asyncio's loop and a doeff handler can do with code paused at an
await or a yield. greet, ada, grace, closed, twice and together are the article's (examples/yield_and_async/greet.py);
refuse, the greetings with except and finally, and the handler that resumes twice are this program's own.

Usage (from the repository root): uv run python tools/docs-video/src/pages/article2-paused-code/measure/paused.py > tools/docs-video/src/pages/article2-paused-code/measure/paused.log
"""

import asyncio
import sys
from pathlib import Path

# The article's examples (drive, greet, stream) are imported through this path.
sys.path.insert(0, str(Path(__file__).resolve().parents[6] / "examples" / "yield_and_async"))

from doeff_core_effects import ReadWriterLog, scheduled, state, writer
from drive import drive, greet_async
from greet import ReadName, ada, closed, grace, greet, together, twice

from doeff import Gather, Resume, ResumeThrow, Spawn, Tell, do, handler, run


# p1: the two moves of asyncio's loop on a paused task: send the result once its future is done, or throw CancelledError.
async def waiting(future: asyncio.Future) -> str:
    try:
        return f"got {await future}"
    except asyncio.CancelledError:
        return "CancelledError at the await"


async def loop_moves() -> list[str]:
    loop = asyncio.get_running_loop()
    done = loop.create_future()
    first = asyncio.create_task(waiting(done))
    await asyncio.sleep(0)
    done.set_result("Ada")
    cancelled = asyncio.create_task(waiting(loop.create_future()))
    await asyncio.sleep(0)
    cancelled.cancel()
    return [await first, await cancelled]


print("p1 loop moves:", asyncio.run(loop_moves()))

# p3: Resume k with a value.
print("p3 ada(greet()):", run(ada(greet())))


# p4: throw into k; greet's own except clause catches it at its yield.
@do
def greet_or_stranger():
    try:
        name = yield ReadName()
    except LookupError as error:
        return f"Hello, stranger ({error})"
    return f"Hello, {name}"


@handler
def refuse(effect: ReadName, k):
    return (yield ResumeThrow(k, LookupError("no name")))


print("p4 refuse(greet_or_stranger()):", run(refuse(greet_or_stranger())))


# p5: scheduled keeps the k of each task; the order in which the tasks start, are answered and end, told to writer.
@handler
def ada_seen(effect: ReadName, k):
    yield Tell("ada answers task 1")
    return (yield Resume(k, "Ada"))


@handler
def grace_seen(effect: ReadName, k):
    yield Tell("grace answers task 2")
    return (yield Resume(k, "Grace"))


@do
def task(n: int):
    yield Tell(f"task {n} starts")
    greeting = yield greet()
    yield Tell(f"task {n} ends")
    return greeting


@do
def together_seen():
    first = yield Spawn(task(1))
    second = yield Spawn(grace_seen(task(2)))
    yield Tell("Gather")
    greetings = yield Gather(first, second)
    return greetings, (yield ReadWriterLog())


print("p5 scheduled(ada(together())):", run(scheduled(ada(together()))))
print("p5 the same, traced:", run(state()(writer(scheduled(ada_seen(together_seen()))))))


# p6: never resume k; the paused generators are closed, so their finally blocks run. A generator being closed cannot
# yield an effect, so the finally blocks print.
@do
def greet_finally():
    try:
        name = yield ReadName()
        return f"Hello, {name}"
    finally:
        print("p6 greet: finally")


@do
def twice_finally():
    try:
        first = yield greet_finally()
        second = yield grace(greet_finally())
        return first, second
    finally:
        print("p6 twice: finally")


print("p6 closed(twice()):", run(closed(twice())))
print("p6 closed(twice()), with finally:", run(closed(twice_finally())))


# p7: neither goes on twice.
@handler
def resume_twice(effect: ReadName, k):
    yield Resume(k, "Ada")
    return (yield Resume(k, "Ada"))


try:
    run(resume_twice(greet()))
except RuntimeError as error:
    print("p7 second Resume:", type(error).__name__, str(error)[:80])
greeting = greet_async()
print("p7 first drive:", drive(greeting, {"name": "Ada"}))
try:
    drive(greeting, {"name": "Ada"})
except RuntimeError as error:
    print("p7 second drive: RuntimeError:", error)

# p8: one program, two runs.
hello = greet()
print("p8 ada(hello):", run(ada(hello)))
print("p8 grace(hello):", run(grace(hello)))
