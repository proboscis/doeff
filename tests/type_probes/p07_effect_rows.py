# 宣言した effect の外を出すと誤りになる・呼んだ program の effect が呼び手の宣言に要る
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, assert_type

from doeff import EffectBase, do


@dataclass(frozen=True)
class ReadClock(EffectBase[int]):
    pass


@dataclass(frozen=True)
class Delete(EffectBase[None]):
    key: str


@do
def elapsed(since: int) -> Generator[ReadClock, Any, int]:
    now = yield from ReadClock()
    return now - since


@do
def only_clock() -> Generator[ReadClock, Any, int]:
    yield from Delete("row")  # expect-error (宣言に無い effect)
    return 0


@do
def caller_without_clock() -> Generator[Delete, Any, int]:
    spent = yield from elapsed(1)  # expect-error (呼んだ program の effect が宣言に無い)
    return spent


@do
def caller_ok() -> Generator[ReadClock | Delete, Any, int]:
    spent = yield from elapsed(1)
    assert_type(spent, int)
    label: str = yield from elapsed(1)  # expect-error (結果の型違い)
    return spent
