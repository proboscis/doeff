"""
CESK interpreter tests for Programs as building blocks.

Tests Listen/Local/Catch/nested effects using the CESK interpreter.
Adapted from test_programs_as_building_blocks.py.
"""

from collections.abc import Generator
from typing import Any

import pytest

from doeff import (
    Ask,
    Catch,
    Effect,
    Get,
    Listen,
    ListenResult,
    Local,
    Log,
    Program,
    Put,
    do,
)
from doeff.cesk_adapter import CESKInterpreter


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
    config = yield Ask("config")
    yield Log(f"Config value: {config}")
    return f"Config was: {config}"


@pytest.mark.asyncio
async def test_catch_with_programs():
    """Test Catch effect with Programs (no .generator_func)."""
    engine = CESKInterpreter()

    @do
    def main_catch_test() -> Generator[Effect, Any, dict]:
        # Test successful case - pass Program directly
        success_result = yield Catch(
            risky_program(False),
            lambda e: f"Error: {e}",
        )

        # Test failure case with recovery - pass Program directly
        failure_result = yield Catch(
            risky_program(True),
            lambda e: error_handler_program(e),
        )

        # Test with simple value return from handler
        simple_recovery = yield Catch(
            risky_program(True),
            lambda _e: "simple recovery",
        )

        error_handled = yield Get("error_handled")

        return {
            "success": success_result,
            "failure": failure_result,
            "simple": simple_recovery,
            "error_handled": error_handled,
        }

    result = await engine.run_async(main_catch_test())

    assert result.is_ok
    assert result.value["success"] == "success"
    assert "Recovered from" in result.value["failure"]
    assert result.value["simple"] == "simple recovery"
    assert result.value["error_handled"] is True


@pytest.mark.asyncio
async def test_local_with_programs():
    """Test Local effect with Programs (no .generator_func)."""
    engine = CESKInterpreter(env={"config": "base"})

    @do
    def main_local_test() -> Generator[Effect, Any, dict]:
        # Check base env
        base_config = yield Ask("config")

        # Test with Program directly
        result1 = yield Local(
            {"config": "modified"},
            env_dependent_program(),
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

    result = await engine.run_async(main_local_test())

    assert result.is_ok
    assert result.value["result1"] == "Config was: modified"
    assert result.value["result2"] == "Config was: another"
    assert result.value["original"] == "base"
    assert result.value["base"] == "base"


@pytest.mark.asyncio
async def test_listen_with_programs():
    """Test Listen effect with Programs (no .generator_func)."""
    engine = CESKInterpreter()

    @do
    def main_listen_test() -> Generator[Effect, Any, dict]:
        # Test with Program directly
        result1 = yield Listen(sub_program_with_log())

        # Check it's a ListenResult with __iter__ for unpacking
        assert isinstance(result1, ListenResult)
        value1, log1 = result1

        # Test with another Program instance
        result2 = yield Listen(sub_program_with_log())

        # Can also access as attributes
        value2 = result2.value
        log2 = result2.log

        return {
            "value1": value1,
            "log1": list(log1),
            "value2": value2,
            "log2": list(log2),
        }

    result = await engine.run_async(main_listen_test())

    assert result.is_ok
    assert result.value["value1"] == 42
    assert len(result.value["log1"]) == 2
    assert "Starting sub-program" in str(result.value["log1"][0])
    assert result.value["value2"] == 42


@pytest.mark.asyncio
async def test_nested_effects_with_programs():
    """Test nested effects all using Programs."""
    engine = CESKInterpreter()

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
            catch_program(),
        )
        return result

    @do
    def deeply_nested() -> Generator[Effect, Any, str]:
        # Listen to a Program that uses Local and Catch
        listen_result = yield Listen(nested_program())

        value, _log = listen_result
        return f"Nested result: {value}"

    result = await engine.run_async(deeply_nested())

    assert result.is_ok
    assert result.value == "Nested result: success"


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
    assert hasattr(program, "generator_func") or hasattr(program, "to_generator")

    # Instead, we use the Program directly with the engine
    engine = CESKInterpreter()
    result = await engine.run_async(program)

    assert result.is_ok
    assert result.value == "done"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
