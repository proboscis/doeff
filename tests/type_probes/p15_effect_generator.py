# EffectGenerator[T](effect を書かない短い形)
from typing import Any, assert_type

from doeff import Ask, EffectGenerator, Expand, do


@do
def name() -> EffectGenerator[str]:
    v = yield from Ask("name")
    return str(v)


@do
def caller() -> EffectGenerator[int]:
    n = yield from name()
    assert_type(n, str)
    bad: int = yield from name()  # expect-error (str を int へ)
    return len(n)


assert_type(caller(), Expand[int, Any])
