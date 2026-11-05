# The Spawn child's effects must be in the caller's declaration (docs/23)
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, assert_type

from doeff import EffectBase, Spawn, do
from doeff_core_effects import Task


@dataclass(frozen=True)
class ReadClock(EffectBase[int]):
    pass


@do
def child() -> Generator[ReadClock, Any, int]:
    return (yield from ReadClock())


@do
def parent_missing() -> Generator[Spawn[Any, Any], Any, None]:
    yield from Spawn(child())  # expect-error (the child's ReadClock is not in the declaration)


@do
def parent_ok() -> Generator[Spawn[Any, Any] | ReadClock, Any, None]:
    t = yield from Spawn(child())
    assert_type(t, Task[int])
