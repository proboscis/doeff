"""Test that Programs are the building blocks - no .generator_func access needed."""

from collections.abc import Generator
from typing import Any

import pytest

from doeff import (
    Catch,
    Effect,
    ExecutionContext,
    Get,
    Listen,
    ListenResult,
    Local,
    Log,
    Program,
    ProgramInterpreter,
    Put,
    do,
)


@do
def risky_program(should_fail: bool) -> Generator[Effect, Any, str]:
    """A program that might fail."""
    yield Log(f"risky_program called with should_fail={should_fail}")
    if should_fail:
        raise ValueError("Intentional failure")
    return "success"


@do
def error_handler_program(e: Exception) -> Generator[Effect, Any, str]:
    """Error handler that returns a Program."""
    yield Log(f"Handling error: {e}")
    yield Put("error_handled", True)
    return f"Recovered from: {e}"


@do
def sub_program_with_log() -> Generator[Effect, Any, int]:
    """A sub-program that logs things."""
    yield Log("Starting sub-program")
    yield Put("sub_state", 42)
    yield Log("Ending sub-program")
    return 42


@do
def env_dependent_program() -> Generator[Effect, Any, str]:
    """A program that depends on environment."""
    from doeff import Ask

    config = yield Ask("config")  # Use Ask for environment, not Get for state!
    yield Log(f"Config value: {config}")
    return f"Config was: {config}"


@pytest.mark.asyncio
async def test_catch_with_programs():
    """Test Catch effect with Programs (no .generator_func)."""
    engine = ProgramInterpreter()

    @do
    def main_catch_test() -> Generator[Effect, Any, dict]:
        # Test successful case - pass Program directly
        success_result = yield Catch(
            risky_program(False),  # Pass Program directly!
            lambda e: f"Error: {e}",
        )

        # Test failure case with recovery - pass Program directly
        failure_result = yield Catch(
            risky_program(True),
            lambda e: error_handler_program(e),  # Handler returns Program
        )

        # Test with simple value return from handler
        simple_recovery = yield Catch(
            risky_program(True),
            lambda _e: "simple recovery",  # Handler returns value directly
        )

        error_handled = yield Get("error_handled")

        return {
            "success": success_result,
            "failure": failure_result,
            "simple": simple_recovery,
            "error_handled": error_handled,
        }

    context = ExecutionContext()
    result = await engine.run_async(main_catch_test(), context)

    assert result.is_ok
    assert result.value["success"] == "success"
    assert "Recovered from" in result.value["failure"]
    assert result.value["simple"] == "simple recovery"
    assert result.value["error_handled"] is True

    # Test passed


@pytest.mark.asyncio
async def test_local_with_programs():
    """Test Local effect with Programs (no .generator_func)."""
    engine = ProgramInterpreter()

    @do
    def main_local_test() -> Generator[Effect, Any, dict]:
        from doeff import Ask

        # Check base env
        base_config = yield Ask("config")

        # Test with Program directly
        result1 = yield Local(
            {"config": "modified"},
            env_dependent_program(),  # Pass Program directly!
        )

        # Test with another Program instance
        result2 = yield Local(
            {"config": "another"},
            env_dependent_program(),
        )

        # Check original env is preserved
        original = yield Ask("config")

        return {
            "result1": result1,
            "result2": result2,
            "original": original,
            "base": base_config,
        }

    context = ExecutionContext(env={"config": "base"})
    result = await engine.run_async(main_local_test(), context)

    assert result.is_ok
    assert result.value["result1"] == "Config was: modified"
    assert result.value["result2"] == "Config was: another"
    assert result.value["original"] == "base"  # Env is preserved
    assert result.value["base"] == "base"

    # Test passed


@pytest.mark.asyncio
async def test_ask_resolves_program_env_value():
    """Ask resolves Program-valued environment entries once and caches result."""
    engine = ProgramInterpreter()

    @do
    def config_provider() -> Generator[Effect, Any, str]:
        yield Log("computing config")
        return "computed"

    @do
    def main_program() -> Generator[Effect, Any, tuple[str, str]]:
        from doeff import Ask

        first = yield Ask("config")
        second = yield Ask("config")
        return first, second

    context = ExecutionContext(env={"config": config_provider()})
    result = await engine.run_async(main_program(), context)

    assert result.is_ok
    first, second = result.value
    assert first == second == "computed"
    assert result.context.env["config"] == "computed"


@pytest.mark.asyncio
async def test_listen_with_programs():
    """Test Listen effect with Programs (no .generator_func)."""
    engine = ProgramInterpreter()

    @do
    def main_listen_test() -> Generator[Effect, Any, dict]:
        # Test with Program directly
        result1 = yield Listen(
            sub_program_with_log()  # Pass Program directly!
        )

        # Check it's a ListenResult with __iter__ for unpacking
        assert isinstance(result1, ListenResult)
        value1, log1 = result1  # Should work with unpacking

        # Test with another Program instance
        result2 = yield Listen(
            sub_program_with_log()
        )

        # Can also access as attributes
        value2 = result2.value
        log2 = result2.log

        # Note: sub_state won't be visible here since Listen runs in isolated context
        # This is correct behavior - Listen doesn't leak state changes

        return {
            "value1": value1,
            "log1": log1,
            "value2": value2,
            "log2": log2,
        }

    context = ExecutionContext()
    result = await engine.run_async(main_listen_test(), context)

    assert result.is_ok
    assert result.value["value1"] == 42
    assert len(result.value["log1"]) == 2
    assert "Starting sub-program" in str(result.value["log1"][0])
    assert result.value["value2"] == 42
    # Note: sub_state is not visible in parent context (correct isolation)

    # Test passed


@pytest.mark.asyncio
async def test_nested_effects_with_programs():
    """Test nested effects all using Programs."""
    engine = ProgramInterpreter()

    @do
    def catch_program() -> Generator[Effect, Any, str]:
        """A program that uses Catch."""
        result = yield Catch(risky_program(False), lambda _e: "caught")
        return result

    @do
    def nested_program() -> Generator[Effect, Any, str]:
        """A program that uses Local with another Program."""
        result = yield Local(
            {"nested_env": "test"},
            catch_program(),  # Pass a Program to Local!
        )
        return result

    @do
    def deeply_nested() -> Generator[Effect, Any, str]:
        # Listen to a Program that uses Local and Catch
        listen_result = yield Listen(
            nested_program()  # Pass the Program!
        )

        value, _log = listen_result
        return f"Nested result: {value}"

    context = ExecutionContext()
    result = await engine.run_async(deeply_nested(), context)

    assert result.is_ok
    assert result.value == "Nested result: success"

    # Test passed


@pytest.mark.asyncio
async def test_no_generator_func_access():
    """Verify we never need to access .generator_func."""

    @do
    def user_program() -> Generator[Effect, Any, str]:
        yield Log("This is a user program")
        return "done"

    # Create a Program (or KleisliProgramCall)
    program = user_program()

    # Verify it's a Program or KleisliProgramCall
    from doeff.program import KleisliProgramCall
    assert isinstance(program, (Program, KleisliProgramCall))

    # We should NEVER do this in user code:
    assert hasattr(program, "generator_func") or hasattr(program, "to_generator")  # It exists but we don't use it!

    # Instead, we use the Program directly with the engine
    engine = ProgramInterpreter()
    result = await engine.run_async(program, ExecutionContext())

    assert result.is_ok
    assert result.value == "done"

    # Test passed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
