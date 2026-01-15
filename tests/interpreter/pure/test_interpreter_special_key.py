from __future__ import annotations

import pytest

from doeff import Ask, Local, CESKInterpreter, do
from doeff.effects import Await
from doeff.types import EffectGenerator


@pytest.mark.asyncio
async def test_interpreter_ask_returns_engine_and_resolves_nested_ask() -> None:
    """__interpreter__ should return the active engine that can run programs with Local/Ask."""

    @do
    def inner() -> EffectGenerator[str]:
        return (yield Local({"foo": "bar"}, Ask("foo")))

    @do
    def outer() -> EffectGenerator[CESKInterpreter]:
        return (yield Ask("__interpreter__"))

    interpreter = CESKInterpreter()
    result = await interpreter.run_async(outer())

    assert result.is_ok
    proxy = result.value
    assert proxy.engine is interpreter

    nested_result = await proxy.run_async(inner())
    assert nested_result.is_ok
    assert nested_result.value == "bar"


@pytest.mark.asyncio
async def test_cli_discovered_env_available_via_interpreter_ask() -> None:
    """Default env merged via Local should be visible to programs run by the interpreter obtained from Ask."""

    @do
    def read_env_and_interpreter() -> EffectGenerator[tuple[CESKInterpreter, dict[str, str]]]:
        engine = yield Ask("__interpreter__")
        value = yield Local({"foo": "from-default-env"}, Ask("foo"))
        return engine, {"value": value}

    interpreter = CESKInterpreter()
    result = await interpreter.run_async(read_env_and_interpreter())

    assert result.is_ok
    proxy, payload = result.value
    assert proxy.engine is interpreter
    assert payload["value"] == "from-default-env"


@pytest.mark.asyncio
async def test_interpreter_handle_reuses_current_env_in_nested_run() -> None:
    """Interpreter handle returned from Ask('__interpreter__') should preserve Local env."""

    @do
    def nested() -> EffectGenerator[str]:
        return (yield Ask("foo"))

    @do
    def outer() -> EffectGenerator[str]:
        proxy = yield Ask("__interpreter__")
        result = yield Await(proxy.run_async(nested()))
        assert result.is_ok
        return result.value

    interpreter = CESKInterpreter()
    program = Local({"foo": "bar"}, outer())
    result = await interpreter.run_async(program)

    assert result.is_ok
    assert result.value == "bar"
