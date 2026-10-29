# drive.py
import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class Ask:  # a request a coroutine hands up through await
    key: str

    def __await__(self):
        return (yield self)  # whoever calls send() gets this object


async def greet_async():  # a greeting with whatever name the driver answers
    name = await Ask("name")
    return f"Hello, {name}"


async def twice_async():  # never sees the Asks that pass through it
    return (await greet_async(), await greet_async())


def drive(coroutine, answers: dict[str, str]):  # the one place every Ask reaches
    value = None
    while True:
        try:
            request = coroutine.send(value)
        except StopIteration as stop:
            return stop.value
        value = answers[request.key]


if __name__ == "__main__":
    print("-- drive: the caller of send() answers every Ask")
    print(drive(twice_async(), {"name": "Ada"}))

    print("-- asyncio: the event loop is the caller of send()")
    try:
        asyncio.run(twice_async())
    except RuntimeError as error:
        print(f"RuntimeError: {error}")

    print("-- once: a coroutine object runs once")
    greeting = greet_async()
    print(drive(greeting, {"name": "Ada"}))
    try:
        drive(greeting, {"name": "Ada"})
    except RuntimeError as error:
        print(f"RuntimeError: {error}")
