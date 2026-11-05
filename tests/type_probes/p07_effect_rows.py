# Yielding an effect outside the declared ones is an error; the effects of a called program must be in the caller's declaration
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
    yield from Delete("row")  # expect-error (effect not in the declaration)
    return 0


@do
def caller_without_clock() -> Generator[Delete, Any, int]:
    spent = yield from elapsed(1)  # expect-error (the called program's effect is not in the declaration)
    return spent


@do
def caller_ok() -> Generator[ReadClock | Delete, Any, int]:
    spent = yield from elapsed(1)
    assert_type(spent, int)
    label: str = yield from elapsed(1)  # expect-error (wrong result type)
    return spent
