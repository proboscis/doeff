"""Tests for PureEffect - the immediate value effect (Pure case of Free monad)."""

import pytest

from doeff import do
from doeff.effects.pure import Pure, PureEffect
from doeff.program import Program


@pytest.mark.asyncio
async def test_pure_effect_returns_value(parameterized_interpreter):
    effect = PureEffect(value=42)

    @do
    def pure_program() -> Program[int]:
        result = yield effect
        return result

    run_result = await parameterized_interpreter.run_async(pure_program())

    assert run_result.is_ok
    assert run_result.value == 42


@pytest.mark.asyncio
async def test_pure_effect_with_complex_value(parameterized_interpreter):
    complex_value = {"key": "value", "nested": [1, 2, 3]}
    effect = PureEffect(value=complex_value)

    @do
    def pure_program() -> Program[dict]:
        result = yield effect
        return result

    run_result = await parameterized_interpreter.run_async(pure_program())

    assert run_result.is_ok
    assert run_result.value == complex_value


@pytest.mark.asyncio
async def test_pure_effect_with_none(parameterized_interpreter):
    effect = PureEffect(value=None)

    @do
    def pure_program() -> Program[None]:
        result = yield effect
        return result

    run_result = await parameterized_interpreter.run_async(pure_program())

    assert run_result.is_ok
    assert run_result.value is None


@pytest.mark.asyncio
async def test_pure_factory_function(parameterized_interpreter):
    @do
    def pure_program() -> Program[str]:
        result = yield Pure("hello")
        return result

    run_result = await parameterized_interpreter.run_async(pure_program())

    assert run_result.is_ok
    assert run_result.value == "hello"


@pytest.mark.asyncio
async def test_pure_effect_in_composition(parameterized_interpreter):
    from doeff.effects import Ask

    @do
    def composed_program() -> Program[str]:
        name = yield Ask("name")
        greeting = yield Pure(f"Hello, {name}!")
        return greeting

    run_result = await parameterized_interpreter.run_async(composed_program(), env={"name": "World"})

    assert run_result.is_ok
    assert run_result.value == "Hello, World!"


def test_pure_effect_immutable():
    effect = PureEffect(value=42)

    with pytest.raises(AttributeError, match=r"can't set attribute|cannot assign to field"):
        effect.value = 100  # type: ignore
