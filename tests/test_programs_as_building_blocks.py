"""Test that Programs are the building blocks - no .generator_func access needed."""

from typing import Generator, Any
import pytest

from doeff import (
    ProgramInterpreter,
    ExecutionContext,
    Effect,
    Program,
    do,
    Log,
    Get,
    Put,
    Catch,
    Local,
    Listen,
    ListenResult,
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

        # Test failure case with recovery - pass Program via lambda
        failure_result = yield Catch(
            lambda: risky_program(True),  # Thunk that returns Program
            lambda e: error_handler_program(e),  # Handler returns Program
        )

        # Test with simple value return from handler
        simple_recovery = yield Catch(
            risky_program(True),
            lambda e: "simple recovery",  # Handler returns value directly
        )

        error_handled = yield Get("error_handled")

        return {
            "success": success_result,
            "failure": failure_result,
            "simple": simple_recovery,
            "error_handled": error_handled,
        }

    context = ExecutionContext()
    result = await engine.run(main_catch_test(), context)

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

        # Test with thunk
        result2 = yield Local(
            {"config": "another"},
            lambda: env_dependent_program(),  # Thunk that returns Program
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
    result = await engine.run(main_local_test(), context)

    assert result.is_ok
    assert result.value["result1"] == "Config was: modified"
    assert result.value["result2"] == "Config was: another"
    assert result.value["original"] == "base"  # Env is preserved
    assert result.value["base"] == "base"

    # Test passed


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

        # Test with thunk
        result2 = yield Listen(
            lambda: sub_program_with_log()  # Thunk that returns Program
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
    result = await engine.run(main_listen_test(), context)

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
        result = yield Catch(risky_program(False), lambda e: "caught")
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
    result = await engine.run(deeply_nested(), context)

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

    # Create a Program
    program = user_program()

    # Verify it's a Program
    assert isinstance(program, Program)

    # We should NEVER do this:
    assert hasattr(program, "generator_func")  # It exists but we don't use it!

    # Instead, we use the Program directly with the engine
    engine = ProgramInterpreter()
    result = await engine.run(program, ExecutionContext())

    assert result.is_ok
    assert result.value == "done"

    # Test passed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
