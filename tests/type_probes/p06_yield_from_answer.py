# Type of an effect's answer: yield from gives the answer a type, and yield makes it Any (the rule of docs/23)
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
    bad: str = yield from ReadClock()  # expect-error (int to str)
    plain = yield ReadClock()
    assert_type(plain, Any)  # the answer of yield (not yield from) is Any -- the rule of docs/23
    env = yield from Ask("k")
    assert_type(env, Any)  # the answer of Ask is Any -- the rule of docs/23
    return now + len(name)
