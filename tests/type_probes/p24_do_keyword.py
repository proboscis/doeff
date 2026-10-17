# @do(non_tail=True) の形と、yield の無い関数
from collections.abc import Generator
from typing import Any, Never, assert_type

from doeff import Ask, Expand, do


@do(non_tail=True)
def prog(x: int) -> Generator[Ask, Any, int]:
    v = yield from Ask("k")
    return x + int(v)


@do
def plain(x: int) -> int:
    return x * 2


assert_type(prog(1), Expand[int, Ask])
assert_type(plain(1), Expand[int, Never])
prog("a")  # expect-error
