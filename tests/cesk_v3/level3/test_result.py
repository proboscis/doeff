from __future__ import annotations

from doeff.cesk_v3.level2_algebraic_effects.primitives import WithHandler
from doeff.cesk_v3.level3_core_effects import (
    Err,
    Ok,
    Safe,
    SafeEffect,
    result_handler,
    state_handler,
    Get,
    Put,
)
from doeff.cesk_v3.run import sync_run
from doeff.do import do
from doeff.program import Program


class TestOkType:
    def test_ok_wraps_value(self):
        ok = Ok(42)
        assert ok.value == 42

    def test_ok_is_ok_returns_true(self):
        ok = Ok("value")
        assert ok.is_ok() is True

    def test_ok_is_err_returns_false(self):
        ok = Ok("value")
        assert ok.is_err() is False

    def test_ok_unwrap_returns_value(self):
        ok = Ok([1, 2, 3])
        assert ok.unwrap() == [1, 2, 3]

    def test_ok_with_none_value(self):
        ok = Ok(None)
        assert ok.value is None
        assert ok.is_ok() is True
        assert ok.unwrap() is None


class TestErrType:
    def test_err_wraps_exception(self):
        exc = ValueError("test error")
        err = Err(exc)
        assert err.error is exc

    def test_err_is_ok_returns_false(self):
        err = Err(RuntimeError("test"))
        assert err.is_ok() is False

    def test_err_is_err_returns_true(self):
        err = Err(RuntimeError("test"))
        assert err.is_err() is True

    def test_err_unwrap_raises_error(self):
        exc = ValueError("unwrap should raise this")
        err = Err(exc)
        try:
            err.unwrap()
            assert False, "Should have raised"
        except ValueError as e:
            assert e is exc


class TestSafeEffectType:
    def test_safe_creates_effect(self):
        prog = Program.pure(42)
        effect = Safe(prog)
        assert isinstance(effect, SafeEffect)
        assert effect.sub_program is prog


class TestResultHandler:
    def test_safe_success_returns_ok(self):
        @do
        def program() -> Program[Ok[int] | Err]:
            return (yield Safe(Program.pure(42)))

        result = sync_run(WithHandler(result_handler(), program()))
        outcome = result.unwrap()
        assert isinstance(outcome, Ok)
        assert outcome.value == 42

    def test_safe_exception_returns_err(self):
        @do
        def failing_program() -> Program[int]:
            raise ValueError("intentional failure")
            yield  # type: ignore

        @do
        def program() -> Program[Ok[int] | Err]:
            return (yield Safe(failing_program()))

        result = sync_run(WithHandler(result_handler(), program()))
        outcome = result.unwrap()
        assert isinstance(outcome, Err)
        assert isinstance(outcome.error, ValueError)
        assert str(outcome.error) == "intentional failure"

    def test_safe_with_computation(self):
        @do
        def compute() -> Program[int]:
            x = yield Program.pure(10)
            y = yield Program.pure(20)
            return x + y

        @do
        def program() -> Program[int]:
            result = yield Safe(compute())
            if result.is_ok():
                return result.unwrap()
            return -1

        result = sync_run(WithHandler(result_handler(), program()))
        assert result.unwrap() == 30

    def test_safe_with_state_effects(self):
        @do
        def stateful_compute() -> Program[int]:
            yield Put("counter", 10)
            value = yield Get("counter")
            return value * 2

        @do
        def program() -> Program[int]:
            result = yield Safe(stateful_compute())
            if result.is_ok():
                return result.unwrap()
            return -1

        result = sync_run(
            WithHandler(
                state_handler(),
                WithHandler(result_handler(), program()),
            )
        )
        assert result.unwrap() == 20

    def test_safe_error_with_state_effects(self):
        @do
        def failing_after_state() -> Program[int]:
            yield Put("key", "value")
            raise RuntimeError("fail after state")
            yield  # type: ignore

        @do
        def program() -> Program[str]:
            result = yield Safe(failing_after_state())
            if result.is_err():
                return f"caught: {type(result.error).__name__}"
            return "no error"

        result = sync_run(
            WithHandler(
                state_handler(),
                WithHandler(result_handler(), program()),
            )
        )
        assert result.unwrap() == "caught: RuntimeError"

    def test_multiple_safe_calls(self):
        @do
        def program() -> Program[tuple[bool, bool]]:
            r1 = yield Safe(Program.pure("success"))
            r2 = yield Safe(Program.pure(123))
            return (r1.is_ok(), r2.is_ok())

        result = sync_run(WithHandler(result_handler(), program()))
        assert result.unwrap() == (True, True)

    def test_safe_mixed_success_and_failure(self):
        @do
        def failing() -> Program[int]:
            raise KeyError("missing")
            yield  # type: ignore

        @do
        def program() -> Program[tuple[bool, bool]]:
            r1 = yield Safe(Program.pure("success"))
            r2 = yield Safe(failing())
            return (r1.is_ok(), r2.is_err())

        result = sync_run(WithHandler(result_handler(), program()))
        assert result.unwrap() == (True, True)

    def test_safe_sequential(self):
        @do
        def inner() -> Program[int]:
            return 42
            yield  # type: ignore

        @do
        def program() -> Program[tuple[bool, bool]]:
            r1 = yield Safe(inner())
            r2 = yield Safe(inner())
            return (r1.is_ok(), r2.is_ok())

        result = sync_run(WithHandler(result_handler(), program()))
        assert result.unwrap() == (True, True)

    def test_safe_preserves_error_type(self):
        class CustomError(Exception):
            pass

        @do
        def failing() -> Program[int]:
            raise CustomError("custom")
            yield  # type: ignore

        @do
        def program() -> Program[bool]:
            result = yield Safe(failing())
            return isinstance(result.error, CustomError) if result.is_err() else False

        result = sync_run(WithHandler(result_handler(), program()))
        assert result.unwrap() is True

    def test_safe_with_generator_exception(self):
        @do
        def gen_fail() -> Program[int]:
            x = yield Program.pure(1)
            if x == 1:
                raise StopIteration("generator stopped")
            return x

        @do
        def program() -> Program[str]:
            result = yield Safe(gen_fail())
            if result.is_err():
                return type(result.error).__name__
            return "ok"

        result = sync_run(WithHandler(result_handler(), program()))
        # Python 3.7+ converts StopIteration in generators to RuntimeError (PEP 479)
        assert result.unwrap() == "RuntimeError"


class TestResultPatternMatching:
    def test_ok_pattern_match(self):
        result: Ok[int] | Err = Ok(42)
        match result:
            case Ok(value=v):
                assert v == 42
            case Err():
                assert False, "Should not match Err"

    def test_err_pattern_match(self):
        result: Ok[int] | Err = Err(ValueError("test"))
        match result:
            case Ok():
                assert False, "Should not match Ok"
            case Err(error=e):
                assert isinstance(e, ValueError)


class TestResultEquality:
    def test_ok_equality(self):
        assert Ok(42) == Ok(42)
        assert Ok("a") == Ok("a")
        assert Ok(42) != Ok(43)
        assert Ok(42) != Ok("42")

    def test_err_equality(self):
        e1 = ValueError("test")
        e2 = ValueError("test")
        err1 = Err(e1)
        err2 = Err(e1)
        err3 = Err(e2)
        assert err1 == err2
        assert err1 != err3

    def test_ok_not_equal_to_err(self):
        assert Ok(42) != Err(ValueError("42"))
