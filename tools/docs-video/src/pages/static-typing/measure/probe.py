# pyright: strict
# What a type checker sees in the page's example: check_types.py runs pyright (strict) and mypy (--strict) on this
# file. The reveal_type lines ask for a type; the lines marked "error" are mistakes the checkers must refuse.
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, reveal_type

from doeff_core_effects import Ask, Get

from doeff import EffectBase, K, Resume, do, handler, run, typed_resume


@dataclass(frozen=True)
class ReadClock(EffectBase[int]):
    pass


@dataclass(frozen=True)
class SleepSeconds(EffectBase[None]):
    seconds: float


@do  # nosemgrep: doeff-no-direct-generator-in-do — the page teaches yield from (it gives the answer its type)
def elapsed(since: int) -> Generator[ReadClock, Any, int]:
    now = yield from ReadClock()
    reveal_type(now)
    loose = yield ReadClock()
    reveal_type(loose)
    return now - since


@do  # nosemgrep: doeff-no-direct-generator-in-do — the page teaches yield from (it gives the answer its type)
def label(since: int) -> Generator[ReadClock, Any, str]:
    spent = yield from elapsed(since)
    reveal_type(spent)
    return f"{spent} ms"


reveal_type(elapsed)
reveal_type(elapsed(400))
elapsed("400")  # error: wrong argument


@do  # nosemgrep: doeff-no-direct-generator-in-do — the page teaches yield from (it gives the answer its type)
def strays(since: int) -> Generator[ReadClock, Any, int]:
    yield from SleepSeconds(1.0)  # error: SleepSeconds is not in the declared effects
    return since


@handler
def clock(effect: ReadClock, k: K) -> Generator[Any, Any, Any]:
    return (yield typed_resume(effect, k, 1_000))


@handler
def noon(effect: ReadClock, k: K) -> Generator[Any, Any, Any]:
    return (yield typed_resume(effect, k, "noon"))  # error: the answer must be an int


@handler  # error: no effect is a str
def wrong(effect: str, k: K) -> Generator[Any, Any, Any]:
    return (yield Resume(k, None))


reveal_type(run(clock(elapsed(400))))


@do  # nosemgrep: doeff-no-direct-generator-in-do — the page teaches yield from (it gives the answer its type)
def greet() -> Generator[Ask | Get, Any, str]:
    name = yield from Ask("name")
    reveal_type(name)
    count = yield from Get("count")
    reveal_type(count)
    return f"hello {name} #{count}"


reveal_type(run(elapsed(400)))  # no handler for ReadClock: not a type error
