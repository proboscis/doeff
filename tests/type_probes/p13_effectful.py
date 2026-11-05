# @effectful and perform (docs/24)
from dataclasses import dataclass
from typing import assert_type

from doeff import EffectBase, Effects, Expand, effectful


@dataclass(frozen=True)
class ReadClock(EffectBase[int]):
    pass


@dataclass(frozen=True)
class Delete(EffectBase[None]):
    key: str


@effectful
def elapsed(perform: Effects[ReadClock], since: int) -> int:
    now = perform(ReadClock())
    assert_type(now, int)
    bad: str = perform(ReadClock())  # expect-error (int to str)
    perform(Delete("row"))  # expect-error (effect not in the declaration)
    return now - since


assert_type(elapsed(1), Expand[int, ReadClock])
elapsed("1")  # expect-error
