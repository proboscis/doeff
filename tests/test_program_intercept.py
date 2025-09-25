"""Tests for Program.intercept handling nested Programs and Effects."""

from __future__ import annotations

import pytest

from doeff import Program, ProgramInterpreter, Ask, Local, Gather, Log, do
from doeff.effects import AskEffect
from doeff.types import Effect


def _intercept_transform(effect: Effect) -> Effect | Program:
    if isinstance(effect, AskEffect):
        return Program.pure("intercepted")
    return effect


@pytest.mark.asyncio
async def test_intercept_rewrites_local_subprogram():
    """Intercept should transform Ask deep inside Local effect payloads."""

    @do
    def inner_program():
        return (yield Ask("some_key"))

    @do
    def outer_program():
        return (yield Local({}, inner_program()))

    intercepted = outer_program().intercept(_intercept_transform)  # type: ignore[arg-type]

    interpreter = ProgramInterpreter()
    result = await interpreter.run(intercepted)

    assert result.is_ok
    assert result.value == "intercepted"


@pytest.mark.asyncio
async def test_intercept_rewrites_gathered_programs():
    """Intercept should reach Programs stored inside gather effects."""

    @do
    def child_program(index: int):
        return (yield Ask(f"key-{index}"))

    @do
    def gather_program():
        return (yield Gather(child_program(1), child_program(2)))

    intercepted = gather_program().intercept(_intercept_transform)  # type: ignore[arg-type]

    interpreter = ProgramInterpreter()
    result = await interpreter.run(intercepted)

    assert result.is_ok
    assert result.value == ["intercepted", "intercepted"]


@pytest.mark.asyncio
async def test_intercept_calls_transform_once_per_effect():
    """Program.intercept should invoke the transformer at most once per effect instance."""

    @do
    def repeated_effect_program():
        log_effect = Log("repeat")
        yield log_effect
        yield log_effect
        return None

    call_counts: dict[int, int] = {}

    def transformer(effect):
        call_counts[id(effect)] = call_counts.get(id(effect), 0) + 1
        return effect

    interpreter = ProgramInterpreter()
    result = await interpreter.run(repeated_effect_program().intercept(transformer))

    assert result.is_ok
    assert result.log == ["repeat", "repeat"]  # Each effect still executes
    assert all(count == 1 for count in call_counts.values())
