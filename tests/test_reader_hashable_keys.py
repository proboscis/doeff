from __future__ import annotations

from typing import Protocol

import pytest

from doeff import Local, ProgramInterpreter, ask, do
from doeff.types import EffectGenerator


class SomeFunc(Protocol):
    def __call__(self) -> str: ...


def _impl() -> str:
    return "ok"


@pytest.mark.asyncio
async def test_reader_accepts_hashable_class_keys() -> None:
    @do
    def program() -> EffectGenerator[str]:
        func: SomeFunc = yield ask(SomeFunc)
        return func()

    interpreter = ProgramInterpreter()
    result = await interpreter.run_async(Local({SomeFunc: _impl}, program()))

    assert result.is_ok
    assert result.value == "ok"


def test_ask_rejects_unhashable_keys() -> None:
    with pytest.raises(TypeError, match=r"key must be hashable"):
        ask([])  # type: ignore[arg-type]
