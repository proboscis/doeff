"""Tests for CESK step module.

Tests for the handler-based step function that dispatches all effects through handlers.
"""

import pytest

from doeff._vendor import FrozenDict
from doeff.cesk.result import Done, Failed, Suspended
from doeff.cesk.state import CESKState, Error, Value
from doeff.cesk.step import step
from doeff.program import Program


class TestStep:
    """Tests for the handler-based step function."""

    def test_step_value_with_empty_k_returns_done(self) -> None:
        state = CESKState(C=Value(42), E=FrozenDict(), S={}, K=[])

        result = step(state)

        assert isinstance(result, Done)
        assert result.value == 42

    def test_step_error_with_empty_k_returns_failed(self) -> None:
        error = ValueError("test error")
        state = CESKState(C=Error(error), E=FrozenDict(), S={}, K=[])

        result = step(state)

        assert isinstance(result, Failed)
        assert result.exception is error

    def test_step_program_control_starts_execution(self) -> None:
        program = Program.pure(42)
        state = CESKState.initial(program)

        result = step(state)

        assert isinstance(result, (CESKState, Suspended, Done))
