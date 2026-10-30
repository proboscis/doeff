# stream.py
import asyncio
from collections.abc import AsyncIterator

from doeff import Await, Listen, do, run
from doeff_core_effects.handlers import await_handler, listen_handler
from doeff_core_effects.scheduler import scheduled


async def tokens() -> AsyncIterator[str]:  # stand-in for the stream of an async SDK
    for chunk in ("Hello", ",", "", " world"):
        await asyncio.sleep(0.01)
        yield chunk


@do
def collect(stream: AsyncIterator[str]):  # the loop is a program: one Await per chunk
    chunks = aiter(stream)
    text = ""
    while True:
        chunk = yield Await(anext(chunks, None))
        if chunk is None:  # None is the end; "" is a chunk
            return text
        text += chunk


async def collect_async(stream: AsyncIterator[str]) -> str:  # the whole loop in one coroutine
    text = ""
    async for chunk in stream:
        text += chunk
    return text


@do
def collect_in_one(stream: AsyncIterator[str]):  # one Await around all of it
    return (yield Await(collect_async(stream)))


@do
def counted(program):  # the result, and every Await that program yielded
    return (yield Listen(program, types=(Await,)))


def bridged(program):  # Await answered on asyncio, Listen inside it
    return scheduled(await_handler(listen_handler(program)))


if __name__ == "__main__":
    print("-- per chunk: every wait reaches the handlers")
    text, waits = run(bridged(counted(collect(tokens()))))
    print(repr(text), len(waits))

    print("-- in one: the waits stay inside asyncio")
    text, waits = run(bridged(counted(collect_in_one(tokens()))))
    print(repr(text), len(waits))
