# The page's running example from docs/23-static-typing.md, run: the answer types read at runtime, the values the
# clock handler answers with (alone and under the scheduler), and a handler no effect can reach refused by @handler.
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, TypeVar

from doeff_core_effects import scheduled

from doeff import (
    EffectBase,
    Gather,
    K,
    Race,
    Resume,
    Spawn,
    do,
    effect_result_type,
    handler,
    handler_effect_types,
    program_signature,
    run,
    typed_resume,
)

T = TypeVar("T")


@dataclass(frozen=True)
class ReadClock(EffectBase[int]):
    pass


class WriteEffect(EffectBase[T]):
    pass


@dataclass(frozen=True)
class WriteShared(WriteEffect[bool]):
    key: str


@dataclass(frozen=True)
class Untyped(EffectBase):
    pass


@do  # nosemgrep: doeff-no-direct-generator-in-do — the page teaches yield from (it gives the answer its type)
def elapsed(since: int) -> Generator[ReadClock, Any, int]:
    now = yield from ReadClock()
    print(f"elapsed: sent back {now!r} ({type(now).__name__})")
    return now - since


@do  # nosemgrep: doeff-no-direct-generator-in-do — the page teaches yield from (it gives the answer its type)
def label(since: int) -> Generator[ReadClock, Any, str]:
    spent = yield from elapsed(since)
    return f"{spent} ms"


@do  # nosemgrep: doeff-no-direct-generator-in-do — the page teaches yield from (it gives the answer its type)
def both(since: int) -> Generator[
    Spawn[Any, Any] | Gather[int | str] | Race[int | str] | ReadClock, Any, list[int | str]
]:
    number = yield from Spawn(elapsed(since))
    text = yield from Spawn(label(since))
    together = yield from Gather(number, text)
    first = yield from Race(number, text)
    return [*together, first]


def now_ms() -> int:
    return 1_000


@handler
def clock(effect: ReadClock, k: K) -> Generator[Any, Any, Any]:
    print(f"clock: answers {type(effect).__name__} with {now_ms()!r}")
    return (yield typed_resume(effect, k, now_ms()))


print("effect_result_type(ReadClock):", effect_result_type(ReadClock))
print("effect_result_type(WriteShared):", effect_result_type(WriteShared))
print("effect_result_type(Untyped):", effect_result_type(Untyped))
print("program_signature(elapsed):", program_signature(elapsed))
print("program_signature(both):", program_signature(both))
# The page says (ReadClock,); on the installed handler the public reader answers None (the raw function is wrapped).
print("handler_effect_types(clock):", handler_effect_types(clock))
print("run(clock(elapsed(400))):", repr(run(clock(elapsed(400)))))
print("run(scheduled(clock(both(400)))):", repr(run(scheduled(clock(both(400))))))

try:

    @handler
    def wrong(effect: str, k: K):  # type: ignore
        return (yield Resume(k, None))

    print("wrong: accepted")
except TypeError as error:
    print("@handler wrong(effect: str, k):", type(error).__name__, "-", error)

try:
    run(elapsed(400))
except Exception as error:
    print("run(elapsed(400)) without clock:", type(error).__name__)
