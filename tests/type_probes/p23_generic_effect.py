# Parent of a custom effect with type arguments (the correct way to write WriteEffect of docs/23)
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, assert_type

from doeff import EffectBase, do

T = TypeVar("T")


class WriteEffect(EffectBase[T], Generic[T]):
    pass


@dataclass(frozen=True)
class WriteShared(WriteEffect[bool]):
    key: str


@do
def prog() -> Generator[WriteShared, Any, bool]:
    ok = yield from WriteShared("k")
    assert_type(ok, bool)
    return ok
