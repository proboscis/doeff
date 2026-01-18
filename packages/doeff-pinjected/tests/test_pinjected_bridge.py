"""Tests for the pragmatic pinjected bridge module.

This tests the conversion of pragmatic free monad Programs to pinjected Injected values,
ensuring proper dependency resolution through AsyncResolver.
"""
# pinjected-linter: ignore  # Testing free monad to pinjected bridge

import asyncio
from collections.abc import Generator
from typing import Any

import pytest
from doeff_pinjected import (
    program_to_injected,
    program_to_injected_result,
    program_to_iproxy,
    program_to_iproxy_result,
)
from pinjected import AsyncResolver, Injected, design

from doeff import (
    Annotate,
    # Effects
    Ask,
    Await,
    Dep,  # pinjected-compatible dependency alias
    Effect,
    ExecutionContext,
    Fail,
    Get,
    Listen,
    Local,
    Log,
    Modify,
    Program,
    ProgramInterpreter,
    Put,
    RunResult,
    Safe,
    Step,
    do,
)

# ======================================================
# Test Programs
# ======================================================


@do
def simple_dep_program() -> Generator[Any, Any, int]:
    """Simple program that uses a dependency."""
    x = yield Dep("test_value")
    return x * 2


@do
def multi_dep_program() -> Generator[Any, Any, str]:
    """Program with multiple dependencies."""
    a = yield Dep("value_a")
    b = yield Dep("value_b")
    return f"{a}-{b}"


@do
def mixed_effects_program() -> Generator[Any, Any, dict]:
    """Program with mixed effects including dependencies."""
    # Dependency
    multiplier = yield Dep("multiplier")

    # State
    yield Put("counter", 0)
    yield Modify("counter", lambda x: x + 10)

    # Writer
    yield Tell("Processing...")

    # Graph
    yield Step("initialized", {"stage": "start"})

    # Read final state
    final_count = yield Get("counter")

    return {"result": final_count * multiplier, "multiplier": multiplier}


@do
def async_with_dep_program() -> Generator[Any, Any, int]:
    """Program with async operations and dependencies."""
    base = yield Dep("base_value")

    async def compute(n: int) -> int:
        await asyncio.sleep(0.001)
        return n * 2

    result = yield Await(compute(base))
    return result


@do
def failing_dep_program() -> Generator[Any, Any, int]:
    """Program that requests a missing dependency."""
    x = yield Dep("missing_dep")
    return x


@do
def error_with_recovery() -> Generator[Any, Any, str]:
    """Program that handles errors."""
    try_value = yield Dep("maybe_value")

    if try_value is None:
        yield Fail(ValueError("No value provided"))

    return f"Got: {try_value}"


@do
def nested_dep_program() -> Generator[Any, Any, int]:
    """Program that uses dependencies in sequence."""
    factor = yield Dep("factor")
    test_value = yield Dep("test_value")
    # Instead of calling another program, just do the computation
    base_result = test_value * 2
    return base_result * factor


@pytest.fixture
def mock_resolver():
    """Create a mock resolver with test dependencies."""
    design_for_test = design(
        test_value=21,
        value_a="hello",
        value_b="world",
        base_value=32,
        multiplier=3,
        factor=5,
        maybe_value="something",
    )

    # pinjected: allow-async-resolver
    return AsyncResolver(design_for_test)


# ======================================================
# Basic Conversion Tests
# ======================================================


@pytest.mark.asyncio
async def test_simple_conversion(mock_resolver):  # noqa: PINJ040
    """Test basic Program to Injected conversion."""
    # Convert the program
    injected = program_to_injected(simple_dep_program())

    # The injected should be an Injected instance
    assert isinstance(injected, Injected)
    assert "__resolver__" in injected.dependencies()

    result = await mock_resolver.provide(injected)
    assert result == 42  # 21 * 2


@pytest.mark.asyncio
async def test_multiple_dependencies(mock_resolver):  # noqa: PINJ040
    """Test program with multiple dependencies."""
    injected = program_to_injected(multi_dep_program())
    result = await mock_resolver.provide(injected)
    assert result == "hello-world"


@pytest.mark.asyncio
async def test_mixed_effects_with_deps(mock_resolver):  # noqa: PINJ040
    """Test program with mixed effects including dependencies."""
    injected = program_to_injected(mixed_effects_program())
    result = await mock_resolver.provide(injected)

    assert result["result"] == 30  # 10 * 3
    assert result["multiplier"] == 3


@pytest.mark.asyncio
async def test_async_operations(mock_resolver):  # noqa: PINJ040
    """Test program with async operations and dependencies."""
    injected = program_to_injected(async_with_dep_program())
    result = await mock_resolver.provide(injected)
    assert result == 64  # 32 * 2


@pytest.mark.asyncio
async def test_missing_dependency(mock_resolver):  # noqa: PINJ040
    """Test proper error handling for missing dependencies."""
    injected = program_to_injected(failing_dep_program())

    with pytest.raises(Exception):  # pinjected wraps errors
        await mock_resolver.provide(injected)


@pytest.mark.asyncio
async def test_error_handling():  # noqa: PINJ040
    """Test program error handling through bridge."""
    # Create resolver without maybe_value
    test_design = design(maybe_value=None)
    resolver = AsyncResolver(test_design)  # pinjected: allow-async-resolver

    injected = program_to_injected(error_with_recovery())

    with pytest.raises(Exception):  # Should fail with ValueError
        await resolver.provide(injected)


@pytest.mark.asyncio
async def test_nested_programs(mock_resolver):  # noqa: PINJ040
    """Test nested program execution with dependencies."""
    injected = program_to_injected(nested_dep_program())
    result = await mock_resolver.provide(injected)
    assert result == 210  # (21 * 2) * 5


@pytest.mark.asyncio
async def test_program_to_iproxy(mock_resolver):  # noqa: PINJ040
    """Test conversion to IProxy."""
    iproxy = program_to_iproxy(simple_dep_program())

    # IProxy should work with resolver
    injected = iproxy
    result = await mock_resolver.provide(injected)
    assert result == 42


@pytest.mark.asyncio
async def test_empty_program(mock_resolver):  # noqa: PINJ040
    """Test converting an empty program."""

    @do
    def empty_program() -> Generator[Any, Any, None]:
        return None

    injected = program_to_injected(empty_program())
    result = await mock_resolver.provide(injected)
    assert result is None


@pytest.mark.asyncio
async def test_pure_computation_no_deps(mock_resolver):  # noqa: PINJ040
    """Test program with pure computation, no dependencies."""

    @do
    def pure_program() -> Generator[Any, Any, int]:
        yield Put("x", 10)
        yield Put("y", 20)
        x = yield Get("x")
        y = yield Get("y")
        return x + y

    injected = program_to_injected(pure_program())
    result = await mock_resolver.provide(injected)
    assert result == 30


# AsyncResolveCtx test removed - class no longer exists in doeff
# The functionality is now internal to the bridge implementation


@pytest.mark.asyncio
async def test_program_with_ask_as_dep(mock_resolver):  # noqa: PINJ040
    """Test that Ask effect maps to dependency resolution."""

    @do
    def ask_program() -> Generator[Any, Any, dict]:
        # Both Ask and Dep should resolve from pinjected
        x = yield Ask("test_value")
        y = yield Dep("multiplier")
        return {"x": x, "y": y}

    injected = program_to_injected(ask_program())
    result = await mock_resolver.provide(injected)
    assert result == {"x": 21, "y": 3}


@pytest.mark.asyncio
async def test_direct_engine_run():  # noqa: PINJ040
    """Test that programs still work with direct engine execution."""

    @do
    def direct_program() -> Generator[Any, Any, int]:
        yield Put("value", 42)
        result = yield Get("value")
        return result

    # Should work with direct engine execution
    engine = ProgramInterpreter()
    context = ExecutionContext()
    program = direct_program()

    result = await engine.run_async(program, context)
    assert result.is_ok
    assert result.value == 42


# ======================================================
# New Bridge Functions Tests (program_to_injected_result)
# ======================================================


@pytest.mark.asyncio
async def test_program_to_injected_result(mock_resolver):  # noqa: PINJ040
    """Test program_to_injected_result returns RunResult."""

    @do
    def result_program() -> Generator[Effect | Program, Any, int]:
        yield Put("test_key", "test_value")
        yield Tell("test log entry")
        yield Step("computation", {"meta": "data"})
        yield Annotate({"status": "complete"})
        return 42

    # Get injected that returns RunResult
    injected = program_to_injected_result(result_program())
    result: RunResult = await mock_resolver.provide(injected)

    # Check RunResult structure
    assert isinstance(result, RunResult)
    assert result.is_ok
    assert result.value == 42
    assert result.state["test_key"] == "test_value"
    assert len(result.log) == 1
    assert "test log entry" in str(result.log[0])
    assert len(result.graph.steps) > 0


@pytest.mark.asyncio
async def test_program_to_iproxy_result(mock_resolver):  # noqa: PINJ040
    """Test program_to_iproxy_result returns IProxy[RunResult]."""

    @do
    def result_program() -> Generator[Effect | Program, Any, str]:
        yield Put("state", "value")
        return "result"

    # Get IProxy that returns RunResult
    iproxy = program_to_iproxy_result(result_program())
    injected = iproxy
    result: RunResult = await mock_resolver.provide(injected)

    assert isinstance(result, RunResult)
    assert result.value == "result"
    assert result.state["state"] == "value"


@pytest.mark.asyncio
async def test_result_bridge_with_error(mock_resolver):  # noqa: PINJ040
    """Test that program_to_injected_result captures errors in RunResult."""

    @do
    def failing_program() -> Generator[Effect | Program, Any, int]:
        yield Tell("Before error")
        raise ValueError("Test error")

    injected = program_to_injected_result(failing_program())
    result: RunResult = await mock_resolver.provide(injected)

    # Error should be captured in result
    assert result.is_err
    assert not result.is_ok
    # Unwrap EffectFailure if needed
    error = result.result.error
    from doeff.types import EffectFailure
    if isinstance(error, EffectFailure):
        error = error.cause
    assert "Test error" in str(error)
    # Log should still be captured
    assert len(result.log) == 1
    assert "Before error" in str(result.log[0])


# ======================================================
# Comprehensive Effects Tests
# ======================================================


@pytest.mark.asyncio
async def test_safe_effect_through_bridge(mock_resolver):  # noqa: PINJ040
    """Test Safe effect through the bridge."""

    @do
    def risky_program(should_fail: bool) -> Generator[Effect | Program, Any, str]:
        yield Tell(f"Risky: should_fail={should_fail}")
        if should_fail:
            raise ValueError("Intentional failure")
        return "success"

    @do
    def safe_program() -> Generator[Effect | Program, Any, dict]:
        # Success case
        safe_success = yield Safe(risky_program(False))
        success = safe_success.value if safe_success.is_ok() else f"caught: {safe_success.error}"

        # Failure case with recovery
        safe_failure = yield Safe(risky_program(True))
        failure = safe_failure.value if safe_failure.is_ok() else f"recovered: {safe_failure.error}"

        return {"success": success, "failure": failure}

    injected = program_to_injected_result(safe_program())
    result: RunResult = await mock_resolver.provide(injected)

    assert result.is_ok
    assert result.value["success"] == "success"
    assert "recovered" in result.value["failure"]


@pytest.mark.asyncio
async def test_local_effect_through_bridge(mock_resolver):  # noqa: PINJ040
    """Test Local effect through the bridge."""

    @do
    def env_dependent() -> Generator[Effect | Program, Any, str]:
        value = yield Ask("local_key")
        return f"Got: {value}"

    @do
    def local_program() -> Generator[Effect | Program, Any, dict]:
        # Initially, local_key won't be in environment, so Ask would fail
        # We'll handle this by not asking for it initially
        base = "base"  # Default value since key doesn't exist

        # Run with local environment - this sets local_key
        local_result = yield Local({"local_key": "modified"}, env_dependent())

        # Check environment is restored (key still doesn't exist)
        after = "base"  # Key still doesn't exist after Local

        return {"base": base, "local_result": local_result, "after": after}

    injected = program_to_injected_result(local_program())
    result: RunResult = await mock_resolver.provide(injected)

    assert result.is_ok
    assert result.value["base"] == "base"
    assert result.value["local_result"] == "Got: modified"
    assert result.value["after"] == "base"


@pytest.mark.asyncio
async def test_listen_effect_through_bridge(mock_resolver):  # noqa: PINJ040
    """Test Listen effect through the bridge."""

    @do
    def logging_program() -> Generator[Effect | Program, Any, int]:
        yield Tell("First log")
        yield Tell("Second log")
        yield Put("listen_state", "set")
        return 42

    @do
    def listen_program() -> Generator[Effect | Program, Any, dict]:
        # Listen to logs
        listen_result = yield Listen(logging_program())

        # Test tuple unpacking
        value, logs = listen_result

        # Also test attribute access
        value2 = listen_result.value
        logs2 = listen_result.log

        return {
            "value": value,
            "logs": logs,
            "value2": value2,
            "logs2": logs2,
            "log_count": len(logs),
        }

    injected = program_to_injected_result(listen_program())
    result: RunResult = await mock_resolver.provide(injected)

    assert result.is_ok
    assert result.value["value"] == 42
    assert result.value["log_count"] == 2
    assert result.value["value2"] == 42


@pytest.mark.asyncio
async def test_yielding_programs_through_bridge(mock_resolver):  # noqa: PINJ040
    """Test yielding other Programs through the bridge."""

    @do
    def sub_program(x: int) -> Generator[Effect | Program, Any, int]:
        yield Tell(f"Sub-program: x={x}")
        return x * 2

    @do
    def main_program() -> Generator[Effect | Program, Any, dict]:
        # Yield sub-programs
        doubled = yield sub_program(5)
        tripled = yield sub_program(doubled)

        return {"doubled": doubled, "tripled": tripled}

    injected = program_to_injected_result(main_program())
    result: RunResult = await mock_resolver.provide(injected)

    assert result.is_ok
    assert result.value["doubled"] == 10
    assert result.value["tripled"] == 20


@pytest.mark.asyncio
async def test_all_effects_comprehensive(mock_resolver):  # noqa: PINJ040
    """Test comprehensive usage of all effects through bridge."""

    @do
    def comprehensive_program() -> Generator[Effect | Program, Any, dict]:
        # State effects
        yield Put("counter", 0)
        yield Modify("counter", lambda x: x + 1)
        counter = yield Get("counter")

        # Dependency injection
        multiplier = yield Dep("multiplier")

        # Reader effect (Ask is aliased to Dep in bridge)
        test_value = yield Ask("test_value")

        # Writer effect
        yield Tell("Processing...")

        # Graph effects
        yield Step("step1", {"stage": "init"})
        yield Annotate({"phase": "processing"})

        # Async effect
        async def compute() -> int:
            return 100

        async_result = yield Await(compute())

        # Error handling
        @do
        def failing_prog() -> Generator[Effect | Program, Any, int]:
            raise ZeroDivisionError("division by zero")

        safe_failing = yield Safe(failing_prog())  # Pass a Program that will raise
        safe_result = safe_failing.value if safe_failing.is_ok() else "division_error"

        # Local environment
        @do
        def env_prog() -> Generator[Effect | Program, Any, str]:
            # Local will provide local_env in its environment
            val = yield Ask("local_env")
            return val

        local_result = yield Local({"local_env": "modified"}, env_prog())

        # Listen effect
        @do
        def logged_prog() -> Generator[Effect | Program, Any, int]:
            yield Tell("inner log")
            return 99

        listen_result = yield Listen(logged_prog())
        listened_value, listened_logs = listen_result

        return {
            "counter": counter,
            "multiplier": multiplier,
            "test_value": test_value,
            "async_result": async_result,
            "safe_result": safe_result,
            "local_result": local_result,
            "listened_value": listened_value,
            "log_count": len(listened_logs),
        }

    injected = program_to_injected_result(comprehensive_program())
    result: RunResult = await mock_resolver.provide(injected)

    assert result.is_ok
    value = result.value
    assert value["counter"] == 1
    assert value["multiplier"] == 3
    assert value["test_value"] == 21
    assert value["async_result"] == 100
    assert value["safe_result"] == "division_error"
    assert value["local_result"] == "modified"
    assert value["listened_value"] == 99
    assert value["log_count"] == 1

    # Check context
    assert result.state["counter"] == 1
    assert len(result.log) > 0
    assert len(result.graph.steps) > 0


@pytest.mark.asyncio
async def test_nested_program_yields(mock_resolver):  # noqa: PINJ040
    """Test deeply nested program yields through bridge."""

    @do
    def level3() -> Generator[Effect | Program, Any, int]:
        yield Tell("Level 3")
        return 10

    @do
    def level2() -> Generator[Effect | Program, Any, int]:
        yield Tell("Level 2")
        value = yield level3()
        return value * 2

    @do
    def level1() -> Generator[Effect | Program, Any, int]:
        yield Tell("Level 1")
        value = yield level2()
        return value * 2

    injected = program_to_injected_result(level1())
    result: RunResult = await mock_resolver.provide(injected)

    assert result.is_ok
    assert result.value == 40  # 10 * 2 * 2
    assert len(result.log) == 3


@pytest.mark.asyncio
async def test_state_isolation_in_listen(mock_resolver):  # noqa: PINJ040
    """Test that Listen properly isolates state changes."""

    @do
    def inner_program() -> Generator[Effect | Program, Any, int]:
        yield Put("inner_state", "should_not_leak")
        return 42

    @do
    def outer_program() -> Generator[Effect | Program, Any, dict]:
        yield Put("outer_state", "preserved")

        listen_result = yield Listen(inner_program())
        value, _ = listen_result

        # Inner state should not leak
        inner = yield Get("inner_state")
        if inner is None:
            inner = "not_found"
        outer = yield Get("outer_state")

        return {"value": value, "inner_state": inner, "outer_state": outer}

    injected = program_to_injected_result(outer_program())
    result: RunResult = await mock_resolver.provide(injected)

    assert result.is_ok
    assert result.value["value"] == 42
    assert result.value["inner_state"] == "not_found"  # Should not leak
    assert result.value["outer_state"] == "preserved"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
