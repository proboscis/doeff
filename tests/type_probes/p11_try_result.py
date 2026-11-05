# The answer type of Try and how to read Ok / Err
from collections.abc import Generator
from typing import Any, assert_type

from doeff import Err, Ok, Try, do


@do
def risky(n: int) -> Generator[Any, Any, int]:
    if n < 0:
        raise ValueError(n)
    return n
    yield


@do
def main() -> Generator[Any, Any, int]:
    r = yield from Try(risky(1))
    assert_type(r, Ok[int] | Err[Exception])
    if isinstance(r, Ok):
        assert_type(r.value, int)
        return r.value
    assert_type(r, Err[Exception])
    assert_type(r.error, Exception)  # gap 5 -- fixed
    s: str = r.value  # expect-error (Err has no value)
    return 0
