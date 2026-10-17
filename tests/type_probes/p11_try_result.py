# Try の答えの型と Ok / Err の読み方
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
    assert_type(r.error, Exception)  # 抜け 5 — 直った
    s: str = r.value  # expect-error (Err に value は無い)
    return 0
