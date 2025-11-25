"""Ensure @do auto-unwrapping respects vararg Program annotations."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from doeff import Effect, Program, ProgramInterpreter, do
from doeff.program import ProgramBase


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
