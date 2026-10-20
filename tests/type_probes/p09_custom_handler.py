# 自作の handler: typed_resume の値の型・handler() で付けた後の結果の型
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
    return (yield typed_resume(effect, k, "now"))  # expect-error (int の effect に str で答える)


@do
def untyped_resume(effect: ReadClock, k: K) -> Generator[Any, Any, Any]:
    return (yield Resume(k, "now"))  # Resume は値の型を見ない — docs/23 の決まり(型を見るのは typed_resume)


@handler
def wrong_filter(effect: str, k: K) -> Generator[Any, Any, Any]:
    return (yield Resume(k, 1))


@do
def body() -> Generator[ReadClock, Any, int]:
    now = yield from ReadClock()
    return now


installed = handler(clock)
assert_type(installed(body()), Program[int, Any])
assert_type(run(installed(body())), int)
handler(wrong_filter)  # expect-error (effect に当たらない注記 — 抜け 7 — 直った)
