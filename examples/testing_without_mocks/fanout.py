# fanout.py
import asyncio
import time
from dataclasses import dataclass

from doeff import Await, EffectBase, Gather, Resume, Spawn, do, handler, run
from doeff_core_effects import await_handler
from doeff_core_effects import scheduled


@dataclass(frozen=True)
class FetchPage(EffectBase):
    url: str


@do
def title(url: str):
    page = yield FetchPage(url)
    return page.split("<h1>")[1].split("</h1>")[0]


@do
def titles(urls: list[str]):
    tasks = []
    for url in urls:
        tasks.append((yield Spawn(title(url))))
    return (yield Gather(*tasks))


@handler
def fake_web(effect: FetchPage, k):  # test: answers at once
    return (yield Resume(k, f"<h1>{effect.url}</h1>"))


async def slow_get(url: str) -> str:  # stand-in for an async HTTP client
    await asyncio.sleep(0.2)
    return f"<h1>{url.upper()}</h1>"


@handler
def http_web(effect: FetchPage, k):  # production: real async I/O
    page = yield Await(slow_get(effect.url))
    return (yield Resume(k, page))


URLS = ["a.example", "b.example", "c.example"]

if __name__ == "__main__":
    print(run(scheduled()(fake_web(titles(URLS)))))
    started = time.perf_counter()
    print(run(scheduled()(await_handler(http_web(titles(URLS))))))
    print(f"{time.perf_counter() - started:.2f} s")
