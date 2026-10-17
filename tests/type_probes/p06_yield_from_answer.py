# effect の答えの型: yield from は答えの型を付け、yield は Any になる(docs/23 の決まり)
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, assert_type

from doeff import Ask, EffectBase, do


@dataclass(frozen=True)
class ReadClock(EffectBase[int]):
    pass


@dataclass(frozen=True)
class ReadKey(EffectBase[str]):
    key: str


@do
def body() -> Generator[ReadClock | ReadKey | Ask, Any, int]:
    now = yield from ReadClock()
    assert_type(now, int)
    name = yield from ReadKey("user")
    assert_type(name, str)
    bad: str = yield from ReadClock()  # expect-error (int を str へ)
    plain = yield ReadClock()
    assert_type(plain, Any)  # yield(yield from でない)の答えは Any — docs/23 の決まり
    env = yield from Ask("k")
    assert_type(env, Any)  # Ask の答えは Any — docs/23 の決まり
    return now + len(name)
