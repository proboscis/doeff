"""Tests for attribute and item projection on programs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from doeff import Program
from doeff.cesk_adapter import CESKInterpreter


@pytest.fixture
def interpreter() -> CESKInterpreter:
    return CESKInterpreter()


def test_program_getitem(interpreter: CESKInterpreter) -> None:
    program = Program.pure({"mask": 42})["mask"]

    result = interpreter.run(program)

    assert result.value == 42


def test_program_getattr(interpreter: CESKInterpreter) -> None:
    namespace = SimpleNamespace(score=3.14)
    program = Program.pure(namespace).score

    result = interpreter.run(program)

    assert result.value == pytest.approx(3.14)


def test_program_getattr_missing(interpreter: CESKInterpreter) -> None:
    from doeff._types_internal import EffectFailureError

    program = Program.pure(object()).missing

    result = interpreter.run(program)

    assert result.is_err
    error = result.result.error
    # Errors are now wrapped in EffectFailureError
    if isinstance(error, EffectFailureError):
        error = error.cause
    assert isinstance(error, AttributeError)


def test_program_chained_access(interpreter: CESKInterpreter) -> None:
    namespace = SimpleNamespace(scores=[1, 2, 3])
    program = Program.pure({"node": namespace})["node"].scores[2]

    result = interpreter.run(program)

    assert result.value == 3


def test_program_call_with_args(interpreter: CESKInterpreter) -> None:
    program = Program.pure(lambda x, y: x + y)

    result = interpreter.run(program(1, Program.pure(2)))

    assert result.value == 3


def test_program_call_flattens_nested_program(interpreter: CESKInterpreter) -> None:
    program = Program.pure(lambda x: Program.pure(x * 2))

    result = interpreter.run(program(Program.pure(3)))

    assert result.value == 6


def test_program_call_non_callable(interpreter: CESKInterpreter) -> None:
    from doeff._types_internal import EffectFailureError

    program = Program.pure(42)

    result = interpreter.run(program())

    assert result.is_err
    error = result.result.error
    # Errors are now wrapped in EffectFailureError
    if isinstance(error, EffectFailureError):
        error = error.cause
    assert isinstance(error, TypeError)
