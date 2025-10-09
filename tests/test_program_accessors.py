"""Tests for attribute and item projection on programs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from doeff import Program
from doeff.interpreter import ProgramInterpreter


@pytest.fixture
def interpreter() -> ProgramInterpreter:
    return ProgramInterpreter()


def test_program_getitem(interpreter: ProgramInterpreter) -> None:
    program = Program.pure({"mask": 42})["mask"]

    result = interpreter.run(program)

    assert result.value == 42


def test_program_getattr(interpreter: ProgramInterpreter) -> None:
    namespace = SimpleNamespace(score=3.14)
    program = Program.pure(namespace).score

    result = interpreter.run(program)

    assert result.value == pytest.approx(3.14)


def test_program_getattr_missing(interpreter: ProgramInterpreter) -> None:
    program = Program.pure(object()).missing

    result = interpreter.run(program)

    assert result.is_err
    assert isinstance(result.result.error, AttributeError)


def test_program_chained_access(interpreter: ProgramInterpreter) -> None:
    namespace = SimpleNamespace(scores=[1, 2, 3])
    program = Program.pure({"node": namespace})["node"].scores[2]

    result = interpreter.run(program)

    assert result.value == 3


def test_program_call_with_args(interpreter: ProgramInterpreter) -> None:
    program = Program.pure(lambda x, y: x + y)

    result = interpreter.run(program(1, Program.pure(2)))

    assert result.value == 3


def test_program_call_flattens_nested_program(interpreter: ProgramInterpreter) -> None:
    program = Program.pure(lambda x: Program.pure(x * 2))

    result = interpreter.run(program(Program.pure(3)))

    assert result.value == 6


def test_program_call_non_callable(interpreter: ProgramInterpreter) -> None:
    program = Program.pure(42)

    result = interpreter.run(program())

    assert result.is_err
    assert isinstance(result.result.error, TypeError)
