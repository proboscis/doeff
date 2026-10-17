# Spawn した子の effect が呼び手の宣言に要る(docs/23)
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, assert_type

from doeff import EffectBase, Spawn, do
from doeff_core_effects.scheduler import Task


@dataclass(frozen=True)
class ReadClock(EffectBase[int]):
    pass


@do
def child() -> Generator[ReadClock, Any, int]:
    return (yield from ReadClock())


@do
def parent_missing() -> Generator[Spawn[Any, Any], Any, None]:
    yield from Spawn(child())  # expect-error (子の ReadClock が宣言に無い)


@do
def parent_ok() -> Generator[Spawn[Any, Any] | ReadClock, Any, None]:
    t = yield from Spawn(child())
    assert_type(t, Task[int])
