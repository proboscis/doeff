# Pure・Local・Listen・Tell の型
from collections.abc import Generator
from typing import Any, assert_type

from doeff import Listen, Local, Pure, Tell, do
from doeff_core_effects.effects import WriterTellEffect


@do
def sub() -> Generator[Any, Any, int]:
    return 1
    yield


@do
def main() -> Generator[Any, Any, int]:
    x = yield from Pure(3)
    assert_type(x, int)
    y = yield from Local({"k": 1}, sub())
    assert_type(y, int)
    z = yield from Listen(sub())
    assert_type(z, tuple[int, list[Any]])
    yield from Tell("log")
    assert_type(Tell("log"), WriterTellEffect)
    return x
