from __future__ import annotations

import pytest

from doeff import Ask, Local, ProgramInterpreter, do
from doeff.types import EffectGenerator


@pytest.mark.asyncio
async def test_interpreter_ask_returns_engine_and_resolves_nested_ask() -> None:
    """__interpreter__ should return the active engine that can run programs with Local/Ask."""

    @do
    def inner() -> EffectGenerator[str]:
        return (yield Local({"foo": "bar"}, Ask("foo")))

    @do
    def outer() -> EffectGenerator[ProgramInterpreter]:
        return (yield Ask("__interpreter__"))

    interpreter = ProgramInterpreter()
    result = await interpreter.run_async(outer())

    assert result.is_ok
    engine = result.value
    assert engine is interpreter

    nested_result = await engine.run_async(inner())
    assert nested_result.is_ok
    assert nested_result.value == "bar"
