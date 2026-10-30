# monitor.py
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from doeff import Await, EffectBase, Gather, Pass, Resume, Spawn, Transfer, do, handler, run
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import scheduled
from doeff_time import Delay, GetTime, async_time_handler, sim_time_handler


# The program: two tasks check a page every 30 minutes.
@dataclass(frozen=True)
class Fetch(EffectBase):  # "get this page"
    url: str


@dataclass(frozen=True)
class Spend(EffectBase):  # "the next step costs this much"
    cost: int


@dataclass(frozen=True)
class Log(EffectBase):  # "write this line"
    line: str


@do
def check(url: str, rounds: int):
    sizes = []
    for round_ in range(rounds):
        if round_:
            yield Delay(30 * 60)  # wait half an hour between checks
        yield Spend(1)
        page = yield Fetch(url)
        now = yield GetTime()
        yield Log(f"{now:%H:%M} {url} {len(page)} bytes")
        sizes.append(len(page))
    return sizes


@do
def monitor(urls: list[str], rounds: int):
    tasks = []
    for url in urls:
        tasks.append((yield Spawn(check(url, rounds))))
    return (yield Gather(*tasks))


# Layers: each one gives one effect its meaning.
PAGES = {"a.example": "<p>up</p>", "b.example": "<p>all good</p>"}


@handler
def fake_web(effect: Fetch, k):  # I/O answered from a dict
    return (yield Resume(k, PAGES[effect.url]))


@handler
def printed(effect: Log, k):  # log lines go to stdout
    print(effect.line)
    return (yield Resume(k, None))


@handler
def free(effect: Spend, k):  # spending costs nothing
    yield Transfer(k, None)


def budget(limit: int):  # a task that finds the budget spent stops there
    left = limit

    @handler
    def on_spend(effect: Spend, k):
        nonlocal left
        if effect.cost > left:
            return "over budget"
        left -= effect.cost
        return (yield Resume(k, None))

    return on_spend


class Flaky(Exception):
    pass


def flaky_web(failures: int):  # the first few fetches fail
    left = failures

    @handler
    def on_fetch(effect: Fetch, k):
        nonlocal left
        if left > 0:
            left -= 1
            raise Flaky(f"503 from {effect.url}")
        return (yield Resume(k, PAGES[effect.url]))

    return on_fetch


def retrying(times: int):  # ask the next handler out again on Flaky
    @handler
    def retry(effect: Fetch, k):
        for attempt in range(1, times + 1):
            try:
                page = yield effect
                break
            except Flaky as error:
                if attempt == times:
                    raise
                yield Log(f"retry {attempt}: {error}")
        return (yield Resume(k, page))

    return retry


def recording(tape: list[str]):  # keep every effect the program yields
    @handler
    def record(effect, k):
        tape.append(type(effect).__name__)
        return (yield Pass(effect, k))

    return record


async def slow_get(url: str) -> str:  # stand-in for an async HTTP client
    await asyncio.sleep(0.01)
    return PAGES[url]


@handler
def http_web(effect: Fetch, k):  # I/O over an async client
    page = yield Await(slow_get(effect.url))
    return (yield Resume(k, page))


START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
URLS = ["a.example", "b.example"]


def simulated(program):  # a virtual clock: 30-minute waits take no time
    return scheduled(sim_time_handler(start_time=START)(program))


def real(program):  # the wall clock, waiting on asyncio
    return scheduled(await_handler(async_time_handler()(program)))


if __name__ == "__main__":
    print("-- test: fake I/O, virtual time")
    print(run(simulated(printed(free(fake_web(monitor(URLS, 2)))))))

    print("-- budget: 3 checks, then stop")
    print(run(simulated(printed(budget(3)(fake_web(monitor(URLS, 2)))))))

    print("-- retry: the first 2 fetches fail")
    print(run(simulated(printed(free(flaky_web(2)(retrying(3)(monitor(URLS, 2))))))))

    print("-- record: every effect, in order")
    tape: list[str] = []
    run(simulated(printed(free(fake_web(recording(tape)(monitor(URLS, 1)))))))
    print(tape)
