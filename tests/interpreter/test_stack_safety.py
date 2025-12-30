"""
Interpreter tests for stack safety and deep nesting.

Adapted from test_comprehensive_stack_safety.py - tests deep chains,
nested operations, and monad composition patterns.

Parameterized to run against both CESK interpreter and ProgramInterpreter.
"""

import asyncio
from typing import TYPE_CHECKING

import pytest

from doeff import (
    Ask,
    Await,
    Catch,
    Fail,
    Get,
    Listen,
    ListenResult,
    Log,
    Modify,
    Put,
    do,
    EffectGenerator,
)

if TYPE_CHECKING:
    from tests.conftest import Interpreter


@pytest.mark.asyncio
async def test_deep_mixed_effect_chain(interpreter: "Interpreter") -> None:
    """Test deep chains using multiple effect types."""
    engine = interpreter

    async def quick_async(n: int) -> int:
        return n * 2

    @do
    def maybe_fail(n: int) -> EffectGenerator[int]:
        if n == 600:  # Trigger within loop range for Catch recovery testing
            yield Fail(ValueError("Expected failure"))
        return n

    @do
    def deep_mixed_program() -> EffectGenerator[dict]:
        yield Put("total", 0)
        yield Log("Starting deep mixed chain")

        for i in range(1000):  # 1000 iterations
            # Reader
            multiplier = yield Ask("multiplier")

            # State
            current = yield Get("total")
            yield Put("total", current + multiplier)

            # Writer
            if i % 200 == 0:
                yield Log(f"Milestone: {i}")

            # Future (async)
            if i % 100 == 0:
                _ = yield Await(quick_async(i))

            # Note: Step/Annotate (Graph effects) not yet implemented in CESK
            # Removed graph effect usage for CESK compatibility

            # Error handling
            if i % 300 == 0:
                @do
                def error_recovery(e: Exception) -> EffectGenerator[int]:
                    yield Log(f"Recovered from error: {e}")
                    return -i
                _ = yield Catch(maybe_fail(i), error_recovery)

        final_total = yield Get("total")

        @do
        def sub_program() -> EffectGenerator[int]:
            yield Log("Sub computation")
            yield Put("sub_state", 42)
            return 42

        listen_result = yield Listen(sub_program())
        if isinstance(listen_result, ListenResult):
            value = listen_result.value
            sub_log = listen_result.log
        else:
            value, sub_log = listen_result

        return {
            "iterations": 1000,
            "final_total": final_total,
            "sub_value": value,
            "sub_log_size": len(sub_log),
        }

    result = await engine.run_async(
        deep_mixed_program(),
        env={"multiplier": 2}
    )

    assert result.is_ok
    assert result.value["iterations"] == 1000
    assert result.value["final_total"] == 2000  # 1000 * 2
    assert len(result.log) > 5
    # Verify error recovery happened exactly once (maybe_fail triggers at 600)
    recovery_logs = [log for log in result.log if "Recovered from error" in log]
    assert len(recovery_logs) == 1
    assert "Expected failure" in recovery_logs[0]


@pytest.mark.asyncio
async def test_nested_effect_operations(interpreter: "Interpreter") -> None:
    """Test deeply nested effect operations."""
    engine = interpreter

    @do
    def nested_program(depth: int) -> EffectGenerator[int]:
        if depth == 0:
            return 1

        yield Put(f"depth_{depth}", depth)
        yield Log(f"At depth {depth}")

        @do
        def next_level() -> EffectGenerator[int]:
            return (yield nested_program(depth - 1))

        @do
        def error_handler(e: Exception) -> EffectGenerator[int]:
            yield Log(f"Error at depth {depth}: {e}")
            return 0

        result = yield Catch(next_level(), error_handler)

        yield Modify(f"depth_{depth}", lambda x: x * 2)

        if depth % 10 == 0:
            yield Await(asyncio.sleep(0.001))

        return result + 1

    @do
    def run_nested() -> EffectGenerator[int]:
        return (yield nested_program(50))  # 50 levels deep

    result = await engine.run_async(run_nested())

    assert result.is_ok
    assert result.value == 51  # 1 + 50
    assert len(result.state) == 50  # One state entry per depth


@pytest.mark.asyncio
async def test_monad_composition_patterns(interpreter: "Interpreter") -> None:
    """Test various monad composition patterns."""
    engine = interpreter

    @do
    def composition_program() -> EffectGenerator[dict]:
        results = {}

        # Reader + State pattern
        _ = yield Ask("config")
        yield Put("configured", True)

        # Writer + Future pattern
        yield Log("Starting async operations")
        yield Await(asyncio.sleep(0.001))
        yield Log("Async completed")

        # State + Error handling pattern
        @do
        def stateful_program() -> EffectGenerator[int]:
            yield Put("computed", 42)
            value = yield Get("computed")
            return value

        @do
        def default_program(e: Exception) -> EffectGenerator[int]:
            yield Put("computed", 0)
            return 0

        try_result = yield Catch(stateful_program(), default_program)

        # Listen pattern
        @do
        def local_program() -> EffectGenerator[str]:
            yield Log("In local computation")
            return "local_result"

        listen_result = yield Listen(local_program())
        if isinstance(listen_result, ListenResult):
            value = listen_result.value
            log = listen_result.log
        else:
            value, log = listen_result

        results["try_result"] = try_result
        results["logged_value"] = value
        results["log_size"] = len(log)

        return results

    result = await engine.run_async(
        composition_program(),
        env={"config": {"key": "value"}}
    )

    assert result.is_ok
    assert result.value["try_result"] == 42
    assert result.value["logged_value"] == "local_result"
    assert result.value["log_size"] == 1


@pytest.mark.asyncio
async def test_error_recovery_chain(interpreter: "Interpreter") -> None:
    """Test chain of error recovery."""
    engine = interpreter

    @do
    def failing_at(n: int) -> EffectGenerator[int]:
        yield Log(f"Trying {n}")
        if n < 5:
            yield Fail(ValueError(f"Failed at {n}"))
        return n

    @do
    def try_with_recovery(n: int) -> EffectGenerator[int]:
        @do
        def handler(e: Exception) -> EffectGenerator[int]:
            yield Log(f"Recovering from {e}, trying {n + 1}")
            return (yield try_with_recovery(n + 1))

        result = yield Catch(failing_at(n), handler)
        return result

    @do
    def recovery_chain() -> EffectGenerator[int]:
        result = yield try_with_recovery(0)
        yield Log(f"Final result: {result}")
        return result

    result = await engine.run_async(recovery_chain())

    assert result.is_ok
    assert result.value == 5  # First success at n=5
    # Should have logs for attempts 0-4 (failures) and 5 (success)
    assert len(result.log) >= 6


@pytest.mark.asyncio
async def test_deeply_nested_listen(interpreter: "Interpreter") -> None:
    """Test deeply nested Listen effects."""
    engine = interpreter

    @do
    def nested_listen(depth: int) -> EffectGenerator[tuple]:
        if depth == 0:
            yield Log("Base level")
            return ("base", [])

        @do
        def inner() -> EffectGenerator[tuple]:
            return (yield nested_listen(depth - 1))

        result = yield Listen(inner())
        if isinstance(result, ListenResult):
            inner_value, inner_log = result.value, result.log
        else:
            inner_value, inner_log = result

        yield Log(f"Level {depth}")
        return (f"level_{depth}", inner_log)

    @do
    def run_nested_listen() -> EffectGenerator[tuple]:
        return (yield nested_listen(5))

    result = await engine.run_async(run_nested_listen())

    assert result.is_ok
    value, _ = result.value  # captured_log unused, focus on value assertion
    assert value == "level_5"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
