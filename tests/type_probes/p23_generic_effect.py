# 型の引数を持つ自作の effect の親(docs/23 の WriteEffect を正しく書いた形)
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
