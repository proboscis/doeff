# @do preserves the argument types and the result type
from collections.abc import Generator
from typing import Any, assert_type

from doeff import Ask, Expand, do


@do
def greet(name: str, times: int) -> Generator[Ask, Any, str]:
    prefix = yield from Ask("prefix")
    return f"{prefix} {name}" * times


assert_type(greet("a", 2), Expand[str, Ask])
greet(1, 2)  # expect-error (wrong argument type)
greet("a")  # expect-error (missing argument)
p: Expand[int, Any] = greet("a", 1)  # expect-error (wrong result type)
