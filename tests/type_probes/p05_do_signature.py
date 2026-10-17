# @do は引数の型と結果の型を保つ
from collections.abc import Generator
from typing import Any, assert_type

from doeff import Ask, Expand, do


@do
def greet(name: str, times: int) -> Generator[Ask, Any, str]:
    prefix = yield from Ask("prefix")
    return f"{prefix} {name}" * times


assert_type(greet("a", 2), Expand[str, Ask])
greet(1, 2)  # expect-error (引数の型違い)
greet("a")  # expect-error (引数が足りない)
p: Expand[int, Any] = greet("a", 1)  # expect-error (結果の型違い)
