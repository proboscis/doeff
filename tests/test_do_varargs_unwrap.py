"""Ensure @do auto-unwrapping respects vararg Program and Effect annotations."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from doeff import Effect, ExecutionContext, Program, ProgramInterpreter, do
from doeff.effects import Ask
from doeff.program import ProgramBase
from doeff.types import EffectBase


@do
def collect_program_varargs(*items: Program) -> Generator[Effect | Program, Any, list[int]]:
    """Varargs annotated as Program should arrive as Program instances."""

    assert all(isinstance(item, ProgramBase) for item in items)
    results: list[int] = []
    for item in items:
        value = yield item
        results.append(value)
    return results


@do
def sum_typed_program_varargs(
    *items: Program[int],
) -> Generator[Effect | Program, Any, int]:
    """Parametrised Program annotations should also disable auto-unwrapping."""

    assert all(isinstance(item, ProgramBase) for item in items)
    total = 0
    for item in items:
        value = yield item
        total += value
    return total


def test_do_varargs_program_annotation_preserves_program_instances():
    interpreter = ProgramInterpreter()

    program = collect_program_varargs(Program.pure(1), Program.pure(2))
    result = interpreter.run(program)

    assert result.is_ok
    assert result.value == [1, 2]


def test_do_varargs_typed_program_annotation_preserves_program_instances():
    interpreter = ProgramInterpreter()

    program = sum_typed_program_varargs(Program.pure(3), Program.pure(7))
    result = interpreter.run(program)

    assert result.is_ok
    assert result.value == 10


# =============================================================================
# Effect annotation tests - Effects should NOT be auto-unwrapped when annotated
# =============================================================================


@do
def execute_effect(e: Effect) -> Generator[Effect | Program, Any, Any]:
    """Effect annotated as Effect should arrive as Effect instance (not unwrapped)."""
    assert isinstance(e, EffectBase), f"Expected EffectBase, got {type(e)}"
    val = yield e
    return val


@do
def execute_effect_base(e: EffectBase) -> Generator[Effect | Program, Any, Any]:
    """Effect annotated as EffectBase should arrive as EffectBase instance."""
    assert isinstance(e, EffectBase), f"Expected EffectBase, got {type(e)}"
    val = yield e
    return val


@do
def collect_effect_varargs(*effects: Effect) -> Generator[Effect | Program, Any, list[Any]]:
    """Varargs annotated as Effect should arrive as Effect instances."""
    assert all(isinstance(e, EffectBase) for e in effects)
    results: list[Any] = []
    for e in effects:
        value = yield e
        results.append(value)
    return results


def test_do_effect_annotation_preserves_effect_instance():
    """Single Effect-annotated parameter should not be auto-unwrapped."""
    interpreter = ProgramInterpreter()

    ask_effect = Ask("key")
    program = execute_effect(ask_effect)
    ctx = ExecutionContext(env={"key": 42})
    result = interpreter.run(program, ctx)

    assert result.is_ok
    assert result.value == 42


def test_do_effect_base_annotation_preserves_effect_instance():
    """Single EffectBase-annotated parameter should not be auto-unwrapped."""
    interpreter = ProgramInterpreter()

    ask_effect = Ask("key")
    program = execute_effect_base(ask_effect)
    ctx = ExecutionContext(env={"key": "hello"})
    result = interpreter.run(program, ctx)

    assert result.is_ok
    assert result.value == "hello"


def test_do_varargs_effect_annotation_preserves_effect_instances():
    """Varargs annotated as Effect should arrive as Effect instances."""
    interpreter = ProgramInterpreter()

    program = collect_effect_varargs(Ask("a"), Ask("b"), Ask("c"))
    ctx = ExecutionContext(env={"a": 1, "b": 2, "c": 3})
    result = interpreter.run(program, ctx)

    assert result.is_ok
    assert result.value == [1, 2, 3]
