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
