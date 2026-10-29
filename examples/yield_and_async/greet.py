# greet.py
import asyncio
from dataclasses import dataclass

from doeff import EffectBase, Gather, Resume, Spawn, do, handler, run
from doeff_core_effects.scheduler import scheduled


# With async and await: the call names the code that answers.
async def read_name_async() -> str:  # the implementation itself
    await asyncio.sleep(0)
    return "Ada"


async def greet_async() -> str:  # a greeting with whatever name the call returns
    name = await read_name_async()  # runs read_name_async, here and now
    return f"Hello, {name}"


# With @do and yield: the call names an operation, and a handler answers.
@dataclass(frozen=True)
class ReadName(EffectBase):  # an effect: "a name, please"; it says nothing about how
    pass


@do
def greet():  # a greeting with whatever name the handler answers
    name = yield ReadName()  # pauses; the nearest handler of ReadName answers
    return f"Hello, {name}"


@handler
def ada(effect: ReadName, k):  # answer "Ada", and run the rest of greet
    return (yield Resume(k, "Ada"))


# Handlers that callers install around parts of a program.
@handler
def grace(effect: ReadName, k):  # the same effect, another answer
    return (yield Resume(k, "Grace"))


@handler
def titled(effect: ReadName, k):  # ask the next handler out, then answer
    name = yield effect
    return (yield Resume(k, f"Dr. {name}"))


@do
def twice():  # two greetings, the second under its own handler
    first = yield greet()  # like `await greet_async()`: the child runs here
    second = yield grace(greet())  # this call alone runs with grace around it
    return first, second


# How the paused code goes on is the handler's decision.
@handler
def closed(effect: ReadName, k):  # never resume k: the run ends here
    return "closed today"


@do
def together():  # two tasks, each under the handlers around it
    first = yield Spawn(greet())
    second = yield Spawn(grace(greet()))
    return (yield Gather(first, second))


if __name__ == "__main__":
    print("-- both: the same greeting, two ways")
    print(asyncio.run(greet_async()))
    print(run(ada(greet())))

    print("-- nested: handlers around parts of twice")
    print(run(ada(twice())))
    print(run(ada(titled(twice()))))

    print("-- resume: the handler decides how greet goes on")
    print(run(closed(twice())))
    print(run(scheduled(ada(together()))))

    print("-- again: one program, run twice")
    hello = greet()
    print(run(ada(hello)))
    print(run(grace(hello)))
