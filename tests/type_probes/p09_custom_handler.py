# Custom handler: value type of typed_resume, and result type after attaching with handler()
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, assert_type

from doeff import EffectBase, K, Program, Resume, do, handler, run, typed_resume


@dataclass(frozen=True)
class ReadClock(EffectBase[int]):
    pass


@handler
def clock(effect: ReadClock, k: K) -> Generator[Any, Any, Any]:
    return (yield typed_resume(effect, k, 1700))


@do
def bad_clock(effect: ReadClock, k: K) -> Generator[Any, Any, Any]:
    return (yield typed_resume(effect, k, "now"))  # expect-error (answering an int effect with str)


@do
def untyped_resume(effect: ReadClock, k: K) -> Generator[Any, Any, Any]:
    return (yield Resume(k, "now"))  # Resume does not check the value type — rule of docs/23 (typed_resume checks types)


@handler  # expect-error (annotation that does not match the effect — gap 7 — fixed; rejected where @handler is applied)
def wrong_filter(effect: str, k: K) -> Generator[Any, Any, Any]:
    return (yield Resume(k, 1))


@do
def body() -> Generator[ReadClock, Any, int]:
    now = yield from ReadClock()
    return now


installed = handler(clock)
assert_type(installed(body()), Program[int, Any])
assert_type(run(installed(body())), int)
