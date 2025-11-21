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


@pytest.mark.asyncio
async def test_cli_discovered_env_available_via_interpreter_ask() -> None:
    """Default env merged via Local should be visible to programs run by the interpreter obtained from Ask."""

    @do
    def read_env_and_interpreter() -> EffectGenerator[tuple[ProgramInterpreter, dict[str, str]]]:
        engine = yield Ask("__interpreter__")
        value = yield Local({"foo": "from-default-env"}, Ask("foo"))
        return engine, {"value": value}

    interpreter = ProgramInterpreter()
    result = await interpreter.run_async(read_env_and_interpreter())

    assert result.is_ok
    engine, payload = result.value
    assert engine is interpreter
    assert payload["value"] == "from-default-env"
