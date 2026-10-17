# Await は coroutine の結果の型を付ける
from collections.abc import Generator
from typing import Any, assert_type

from doeff import Await, do


async def fetch() -> bytes:
    return b""


@do
def prog() -> Generator[Any, Any, int]:
    body = yield from Await(fetch())
    assert_type(body, bytes)
    return len(body)
