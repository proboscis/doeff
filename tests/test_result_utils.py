"""Tests for the Result type utility methods."""

import pytest

from doeff import EffectGenerator, Err, Ok, ProgramInterpreter, Result, do
from doeff.program import Program


class TestResultUnwrapOr:
    def test_unwrap_or_returns_value_on_ok(self):
        result: Result[int] = Ok(42)
        assert result.unwrap_or(0) == 42

    def test_unwrap_or_returns_default_on_err(self):
        result: Result[int] = Err(ValueError("error"))
        assert result.unwrap_or(0) == 0

    def test_unwrap_or_with_different_type(self):
        result: Result[int] = Err(ValueError("error"))
        assert result.unwrap_or("default") == "default"


class TestResultUnwrapOrElse:
    def test_unwrap_or_else_returns_value_on_ok(self):
        result: Result[int] = Ok(42)
        called = []

        def fallback(e: Exception) -> int:
            called.append(e)
            return 0

        assert result.unwrap_or_else(fallback) == 42
        assert called == []  # fallback was NOT called

    def test_unwrap_or_else_calls_fallback_on_err(self):
        error = ValueError("error")
        result: Result[int] = Err(error)

        def fallback(e: Exception) -> int:
            return len(str(e))

        assert result.unwrap_or_else(fallback) == 5  # len("error")

    def test_unwrap_or_else_receives_error(self):
        error = ValueError("test error")
        result: Result[int] = Err(error)
        received_errors: list[Exception] = []

        def fallback(e: Exception) -> int:
            received_errors.append(e)
            return 0

        result.unwrap_or_else(fallback)
        assert received_errors == [error]


class TestResultAndThen:
    def test_and_then_chains_on_ok(self):
        result: Result[int] = Ok(10)
        chained = result.and_then(lambda x: Ok(x * 2))
        assert isinstance(chained, Ok)
        assert chained.unwrap() == 20

    def test_and_then_skips_on_err(self):
        error = ValueError("error")
        result: Result[int] = Err(error)
        called = []

        def chain(x: int) -> Result[int]:
            called.append(x)
            return Ok(x * 2)

        chained = result.and_then(chain)
        assert isinstance(chained, Err)
        assert chained.error is error
        assert called == []  # chain was NOT called

    def test_and_then_can_return_err(self):
        result: Result[int] = Ok(10)
        new_error = RuntimeError("new error")
        chained = result.and_then(lambda _: Err(new_error))
        assert isinstance(chained, Err)
        assert chained.error is new_error

    def test_and_then_requires_result_return(self):
        result: Result[int] = Ok(10)
        with pytest.raises(TypeError, match="and_then must return a Result"):
            result.and_then(lambda x: x * 2)  # type: ignore[arg-type]


class TestResultRecover:
    def test_recover_returns_self_on_ok(self):
        result: Result[int] = Ok(42)
        called = []

        def recovery(e: Exception) -> int:
            called.append(e)
            return 0

        recovered = result.recover(recovery)
        assert isinstance(recovered, Ok)
        assert recovered.unwrap() == 42
        assert called == []  # recovery was NOT called

    def test_recover_wraps_fallback_value_on_err(self):
        error = ValueError("error")
        result: Result[int] = Err(error)

        def recovery(e: Exception) -> int:
            return len(str(e))

        recovered = result.recover(recovery)
        assert isinstance(recovered, Ok)
        assert recovered.unwrap() == 5

    def test_recover_receives_original_error(self):
        original_error = ValueError("original")
        result: Result[int] = Err(original_error)
        received_errors: list[Exception] = []

        def recovery(e: Exception) -> int:
            received_errors.append(e)
            return 0

        result.recover(recovery)
        assert received_errors == [original_error]


class TestResultRecoverK:
    @pytest.mark.asyncio
    async def test_recover_k_returns_pure_program_on_ok(self):
        result: Result[int] = Ok(42)

        @do
        def recovery(e: Exception) -> EffectGenerator[int]:
            return 0
            yield  # type: ignore[misc]

        program = result.recover_k(recovery)
        assert isinstance(program, Program)

        engine = ProgramInterpreter()
        run_result = await engine.run_async(program)
        assert run_result.is_ok
        assert run_result.value == 42

    @pytest.mark.asyncio
    async def test_recover_k_calls_kleisli_on_err(self):
        error = ValueError("error")
        result: Result[int] = Err(error)

        @do
        def recovery(e: Exception) -> EffectGenerator[int]:
            return len(str(e))
            yield  # type: ignore[misc]

        program = result.recover_k(recovery)
        assert isinstance(program, Program)

        engine = ProgramInterpreter()
        run_result = await engine.run_async(program)
        assert run_result.is_ok
        assert run_result.value == 5  # len("error")

    @pytest.mark.asyncio
    async def test_recover_k_receives_original_error(self):
        original_error = ValueError("original")
        result: Result[int] = Err(original_error)
        received_errors: list[Exception] = []

        @do
        def recovery(e: Exception) -> EffectGenerator[int]:
            received_errors.append(e)
            return 0
            yield  # type: ignore[misc]

        program = result.recover_k(recovery)
        engine = ProgramInterpreter()
        await engine.run_async(program)
        assert received_errors == [original_error]


class TestResultOrProgram:
    @pytest.mark.asyncio
    async def test_or_program_returns_pure_program_on_ok(self):
        result: Result[int] = Ok(42)

        @do
        def fallback() -> EffectGenerator[int]:
            return 0
            yield  # type: ignore[misc]

        program = result.or_program(fallback())
        assert isinstance(program, Program)

        engine = ProgramInterpreter()
        run_result = await engine.run_async(program)
        assert run_result.is_ok
        assert run_result.value == 42

    @pytest.mark.asyncio
    async def test_or_program_returns_fallback_on_err(self):
        result: Result[int] = Err(ValueError("error"))

        @do
        def fallback() -> EffectGenerator[int]:
            return 99
            yield  # type: ignore[misc]

        program = result.or_program(fallback())
        assert isinstance(program, Program)

        engine = ProgramInterpreter()
        run_result = await engine.run_async(program)
        assert run_result.is_ok
        assert run_result.value == 99

    @pytest.mark.asyncio
    async def test_or_program_fallback_not_evaluated_on_ok(self):
        """Fallback program is already created but its effects won't run on Ok."""
        result: Result[int] = Ok(42)
        executed = []

        @do
        def fallback() -> EffectGenerator[int]:
            executed.append(True)
            return 0
            yield  # type: ignore[misc]

        fallback_program = fallback()
        program = result.or_program(fallback_program)

        engine = ProgramInterpreter()
        run_result = await engine.run_async(program)
        assert run_result.value == 42
        assert executed == []  # fallback program was not executed


class TestResultOrOperator:
    def test_or_returns_left_on_ok(self):
        left: Result[int] = Ok(1)
        right: Result[int] = Ok(2)
        result = left | right
        assert isinstance(result, Ok)
        assert result.unwrap() == 1

    def test_or_returns_right_on_err(self):
        left: Result[int] = Err(ValueError("left error"))
        right: Result[int] = Ok(2)
        result = left | right
        assert isinstance(result, Ok)
        assert result.unwrap() == 2

    def test_or_returns_right_err_on_both_err(self):
        left_error = ValueError("left")
        right_error = RuntimeError("right")
        left: Result[int] = Err(left_error)
        right: Result[int] = Err(right_error)
        result = left | right
        assert isinstance(result, Err)
        assert result.error is right_error

    def test_or_chaining_multiple(self):
        """Test chaining multiple | operators."""
        err1: Result[int] = Err(ValueError("1"))
        err2: Result[int] = Err(ValueError("2"))
        ok3: Result[int] = Ok(3)

        result = err1 | err2 | ok3
        assert isinstance(result, Ok)
        assert result.unwrap() == 3


class TestResultAndThenK:
    @pytest.mark.asyncio
    async def test_and_then_k_chains_on_ok(self):
        result: Result[int] = Ok(21)

        @do
        def process(x: int) -> EffectGenerator[str]:
            return str(x * 2)
            yield  # type: ignore[misc]

        program = result.and_then_k(process)
        assert isinstance(program, Program)

        engine = ProgramInterpreter()
        run_result = await engine.run_async(program)
        assert run_result.is_ok
        assert run_result.value == "42"

    @pytest.mark.asyncio
    async def test_and_then_k_fails_on_err(self):
        error = ValueError("original error")
        result: Result[int] = Err(error)
        called = []

        @do
        def process(x: int) -> EffectGenerator[str]:
            called.append(x)
            return str(x * 2)
            yield  # type: ignore[misc]

        program = result.and_then_k(process)
        assert isinstance(program, Program)

        engine = ProgramInterpreter()
        run_result = await engine.run_async(program)
        assert run_result.is_err
        assert called == []  # process was NOT called

    @pytest.mark.asyncio
    async def test_and_then_k_receives_value(self):
        result: Result[int] = Ok(42)
        received_values: list[int] = []

        @do
        def process(x: int) -> EffectGenerator[int]:
            received_values.append(x)
            return x + 1
            yield  # type: ignore[misc]

        program = result.and_then_k(process)
        engine = ProgramInterpreter()
        await engine.run_async(program)
        assert received_values == [42]
