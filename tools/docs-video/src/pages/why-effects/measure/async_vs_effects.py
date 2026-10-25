# Effects vs. plain async: the page's total_size, once as async def and once as a doeff program under an async
# handler and under one that answers at once. Prints each result and its wall time, to back the async scene.
import asyncio
import time
from dataclasses import dataclass

from doeff import Await, EffectBase, Gather, Resume, Spawn, do, handler, run
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import scheduled


async def fetch_async(url):             # the I/O is async…
    await asyncio.sleep(0.01)
    return f"<html>{url}</html>"


async def total_size_async(urls):       # …so is every caller…
    pages = await asyncio.gather(*(fetch_async(url) for url in urls))
    return sum(len(page) for page in pages)


@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str


@do
def fetch(url):
    return (yield Fetch(url))


@do
def total_size(urls):                   # no async, no await
    tasks = []
    for url in urls:
        tasks.append((yield Spawn(fetch(url))))
    pages = yield Gather(*tasks)
    return sum(len(page) for page in pages)


async def download(url):                # production: an async client
    await asyncio.sleep(0.01)
    return f"<html>{url}</html>"


@do
def async_http(effect: Fetch, k):
    return (yield Resume(k, (yield Await(download(effect.url)))))


@do
def instant_http(effect: Fetch, k):     # answers at once, without an event loop
    return (yield Resume(k, f"<html>{effect.url}</html>"))


urls = ["a.com", "b.com"]
for i in range(5):
    t = time.perf_counter()
    a = asyncio.run(total_size_async(urls))
    t_async = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    b = run(scheduled(await_handler()(handler(async_http)(total_size(urls)))))
    t_await = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    c = run(scheduled(handler(instant_http)(total_size(urls))))
    t_instant = (time.perf_counter() - t) * 1000
    print(f"run {i + 1}: asyncio {a} in {t_async:.2f} ms | doeff+async_http {b} in {t_await:.2f} ms | doeff+instant_http {c} in {t_instant:.2f} ms")
