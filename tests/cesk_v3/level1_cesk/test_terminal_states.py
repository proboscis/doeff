import pytest

from doeff.cesk_v3.level1_cesk.state import (
    CESKState,
    Value,
    Error,
    Done,
    Failed,
)
from doeff.cesk_v3.level1_cesk.step import cesk_step


class TestTerminalStates:
    def test_value_with_empty_k_produces_done(self, empty_env, empty_store, empty_k):
        state = CESKState(
            C=Value(42),
            E=empty_env,
            S=empty_store,
            K=empty_k,
        )

        result = cesk_step(state)

        assert isinstance(result, Done)
        assert result.value == 42

    def test_error_with_empty_k_produces_failed(self, empty_env, empty_store, empty_k):
        exc = RuntimeError("fatal error")
        state = CESKState(
            C=Error(exc),
            E=empty_env,
            S=empty_store,
            K=empty_k,
        )

        result = cesk_step(state)

        assert isinstance(result, Failed)
        assert result.error is exc

    def test_done_preserves_complex_value(self, empty_env, empty_store, empty_k):
        complex_value = {"key": [1, 2, 3], "nested": {"a": "b"}}
        state = CESKState(
            C=Value(complex_value),
            E=empty_env,
            S=empty_store,
            K=empty_k,
        )

        result = cesk_step(state)

        assert isinstance(result, Done)
        assert result.value == complex_value

    def test_failed_preserves_exception_type(self, empty_env, empty_store, empty_k):
        class CustomError(Exception):
            pass

        exc = CustomError("custom")
        state = CESKState(
            C=Error(exc),
            E=empty_env,
            S=empty_store,
            K=empty_k,
        )

        result = cesk_step(state)

        assert isinstance(result, Failed)
        assert isinstance(result.error, CustomError)
