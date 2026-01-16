"""Integration tests for the new CESK architecture.

These tests verify end-to-end functionality with real programs,
testing the full stack: handlers -> step -> runtime.
"""

from __future__ import annotations

import pytest
from doeff import do
from doeff.effects import (
    AskEffect,
    GetTimeEffect,
    IOPerformEffect,
    PureEffect,
    StateGetEffect,
    StateModifyEffect,
    StatePutEffect,
)
from doeff.cesk.runtime import SimulationRuntime, SyncRuntime
from doeff.cesk.handlers import default_handlers


# ============================================================================
# Basic Program Execution Tests
# ============================================================================


class TestBasicExecution:
    """Tests for basic program execution patterns."""

    def test_pure_value(self) -> None:
        """Programs that return pure values work."""
        @do
        def prog():
            x = yield from PureEffect(value=42)
            return x

        runtime = SyncRuntime(default_handlers())
        result = runtime.run(prog())
        assert result == 42

    def test_multiple_pure_effects(self) -> None:
        """Programs can chain multiple pure effects."""
        @do
        def prog():
            a = yield from PureEffect(value=10)
            b = yield from PureEffect(value=20)
            c = yield from PureEffect(value=30)
            return a + b + c

        runtime = SyncRuntime(default_handlers())
        result = runtime.run(prog())
        assert result == 60

    def test_nested_program_calls(self) -> None:
        """Programs can call other programs."""
        @do
        def inner(x: int) -> int:
            return (yield from PureEffect(value=x * 2))

        @do
        def outer(y: int) -> int:
            a = yield from inner(y)
            b = yield from inner(y + 1)
            return a + b

        runtime = SyncRuntime(default_handlers())
        result = runtime.run(outer(5))
        # inner(5) = 10, inner(6) = 12, total = 22
        assert result == 22

    def test_deep_nesting(self) -> None:
        """Programs can have deep nesting."""
        @do
        def level3(x: int):
            return (yield from PureEffect(value=x + 1))

        @do
        def level2(x: int):
            return (yield from level3(x + 10))

        @do
        def level1(x: int):
            return (yield from level2(x + 100))

        runtime = SyncRuntime(default_handlers())
        result = runtime.run(level1(1))
        # 1 + 100 + 10 + 1 = 112
        assert result == 112


# ============================================================================
# State Effect Tests
# ============================================================================


class TestStateEffects:
    """Tests for state effect integration."""

    def test_state_put_and_get(self) -> None:
        """Can put and get state values."""
        @do
        def prog():
            yield from StatePutEffect(key="counter", value=0)
            yield from StateModifyEffect(key="counter", func=lambda x: x + 1)
            yield from StateModifyEffect(key="counter", func=lambda x: x + 1)
            yield from StateModifyEffect(key="counter", func=lambda x: x + 1)
            result = yield from StateGetEffect(key="counter")
            return result

        runtime = SyncRuntime(default_handlers())
        result = runtime.run(prog())
        assert result == 3

    def test_multiple_state_keys(self) -> None:
        """Can manage multiple state keys independently."""
        @do
        def prog():
            yield from StatePutEffect(key="a", value=10)
            yield from StatePutEffect(key="b", value=20)
            a = yield from StateGetEffect(key="a")
            b = yield from StateGetEffect(key="b")
            yield from StatePutEffect(key="c", value=a + b)
            return (yield from StateGetEffect(key="c"))

        runtime = SyncRuntime(default_handlers())
        result = runtime.run(prog())
        assert result == 30

    def test_state_across_nested_calls(self) -> None:
        """State is shared across nested program calls."""
        @do
        def increment():
            current = yield from StateGetEffect(key="value")
            yield from StatePutEffect(key="value", value=current + 1)
            return current + 1

        @do
        def main():
            yield from StatePutEffect(key="value", value=0)
            a = yield from increment()
            b = yield from increment()
            c = yield from increment()
            return (a, b, c)

        runtime = SyncRuntime(default_handlers())
        result = runtime.run(main())
        assert result == (1, 2, 3)


# ============================================================================
# Reader (Ask) Effect Tests
# ============================================================================


class TestReaderEffects:
    """Tests for reader/ask effect integration."""

    def test_ask_from_environment(self) -> None:
        """Can read values from environment."""
        @do
        def prog():
            name = yield from AskEffect(key="name")
            greeting = yield from AskEffect(key="greeting")
            return f"{greeting}, {name}!"

        runtime = SyncRuntime(default_handlers())
        result = runtime.run(prog(), env={"name": "World", "greeting": "Hello"})
        assert result == "Hello, World!"

    def test_ask_returns_none_for_missing(self) -> None:
        """Missing keys return None."""
        @do
        def prog():
            missing = yield from AskEffect(key="not_present")
            return missing

        runtime = SyncRuntime(default_handlers())
        result = runtime.run(prog(), env={})
        assert result is None

    def test_ask_combined_with_state(self) -> None:
        """Reader and state effects can be combined."""
        @do
        def prog():
            multiplier = yield from AskEffect(key="multiplier")
            yield from StatePutEffect(key="value", value=10)
            value = yield from StateGetEffect(key="value")
            return value * multiplier

        runtime = SyncRuntime(default_handlers())
        result = runtime.run(prog(), env={"multiplier": 5})
        assert result == 50


# ============================================================================
# I/O Effect Tests
# ============================================================================


class TestIOEffects:
    """Tests for I/O effect integration."""

    def test_io_callable_executed(self) -> None:
        """I/O operations are executed."""
        call_log = []

        def my_io():
            call_log.append("called")
            return "io_result"

        @do
        def prog():
            result = yield from IOPerformEffect(action=my_io)
            return result

        runtime = SyncRuntime(default_handlers())
        result = runtime.run(prog())

        assert result == "io_result"
        assert call_log == ["called"]

    def test_io_with_state_effects(self) -> None:
        """I/O can be combined with state effects."""
        io_calls = []

        def log_io(message):
            def _log():
                io_calls.append(message)
                return len(io_calls)
            return _log

        @do
        def prog():
            yield from StatePutEffect(key="count", value=0)

            count1 = yield from IOPerformEffect(action=log_io("first"))
            yield from StatePutEffect(key="count", value=count1)

            count2 = yield from IOPerformEffect(action=log_io("second"))
            yield from StatePutEffect(key="count", value=count2)

            final = yield from StateGetEffect(key="count")
            return final

        runtime = SyncRuntime(default_handlers())
        result = runtime.run(prog())

        assert result == 2
        assert io_calls == ["first", "second"]


# ============================================================================
# Error Handling Tests
# ============================================================================


class TestErrorHandling:
    """Tests for error propagation and handling."""

    def test_exception_propagates(self) -> None:
        """Exceptions in programs propagate to runtime."""
        @do
        def prog():
            yield from PureEffect(value=1)
            raise ValueError("test error")
            return 0  # noqa: B950

        runtime = SyncRuntime(default_handlers())

        with pytest.raises(ValueError, match="test error"):
            runtime.run(prog())

    def test_io_exception_propagates(self) -> None:
        """Exceptions from I/O operations propagate."""
        def failing_io():
            raise IOError("io failed")

        @do
        def prog():
            result = yield from IOPerformEffect(action=failing_io)
            return result

        runtime = SyncRuntime(default_handlers())

        with pytest.raises(IOError, match="io failed"):
            runtime.run(prog())

    def test_exception_in_nested_call(self) -> None:
        """Exceptions in nested calls propagate up."""
        @do
        def inner():
            yield from PureEffect(value=1)
            raise RuntimeError("nested error")

        @do
        def outer():
            result = yield from inner()
            return result

        runtime = SyncRuntime(default_handlers())

        with pytest.raises(RuntimeError, match="nested error"):
            runtime.run(outer())


# ============================================================================
# Simulation Runtime Tests
# ============================================================================


class TestSimulationRuntime:
    """Tests for simulation runtime with controlled time."""

    def test_simulation_time_control(self) -> None:
        """Simulation runtime provides controlled time."""
        from datetime import datetime, timezone

        @do
        def prog():
            time = yield from GetTimeEffect()
            return time

        runtime = SimulationRuntime(default_handlers(), initial_time=1000.0)
        result = runtime.run(prog())

        assert isinstance(result, datetime)
        assert result.timestamp() == 1000.0

    def test_simulation_io_mocking(self) -> None:
        """Simulation runtime can mock I/O operations."""
        def real_io():
            return "real result"

        @do
        def prog():
            result = yield from IOPerformEffect(action=real_io)
            return result

        runtime = SimulationRuntime(default_handlers())
        runtime.set_io_mock(real_io, lambda: "mocked result")

        result = runtime.run(prog())
        assert result == "mocked result"

    def test_simulation_time_advance(self) -> None:
        """Can advance simulated time."""
        runtime = SimulationRuntime(default_handlers(), initial_time=0.0)

        assert runtime.current_time == 0.0

        runtime.advance_time(10.0)
        assert runtime.current_time == 10.0

        runtime.advance_time(5.5)
        assert runtime.current_time == 15.5


# ============================================================================
# Complex Program Tests
# ============================================================================


class TestComplexPrograms:
    """Tests for complex, real-world-like programs."""

    def test_counter_service(self) -> None:
        """Simulate a counter service with multiple operations."""
        @do
        def init_counter(name: str, initial: int):
            yield from StatePutEffect(key=f"counter:{name}", value=initial)
            return name

        @do
        def increment(name: str):
            current = yield from StateGetEffect(key=f"counter:{name}")
            yield from StatePutEffect(key=f"counter:{name}", value=current + 1)
            return current + 1

        @do
        def get_value(name: str):
            return (yield from StateGetEffect(key=f"counter:{name}"))

        @do
        def main():
            yield from init_counter("visits", 0)
            yield from init_counter("errors", 0)

            # Simulate some operations
            for _ in range(5):
                yield from increment("visits")

            yield from increment("errors")
            yield from increment("errors")

            visits = yield from get_value("visits")
            errors = yield from get_value("errors")

            return {"visits": visits, "errors": errors}

        runtime = SyncRuntime(default_handlers())
        result = runtime.run(main())

        assert result == {"visits": 5, "errors": 2}

    def test_config_based_computation(self) -> None:
        """Program behavior based on environment config."""
        @do
        def compute():
            mode = yield from AskEffect(key="mode")
            base = yield from AskEffect(key="base_value")

            if mode == "double":
                return base * 2
            elif mode == "square":
                return base * base
            else:
                return base

        runtime = SyncRuntime(default_handlers())

        result1 = runtime.run(compute(), env={"mode": "double", "base_value": 5})
        assert result1 == 10

        result2 = runtime.run(compute(), env={"mode": "square", "base_value": 5})
        assert result2 == 25

        result3 = runtime.run(compute(), env={"mode": "other", "base_value": 5})
        assert result3 == 5

    def test_recursive_computation(self) -> None:
        """Recursive program patterns work."""
        @do
        def factorial(n: int):
            if n <= 1:
                return (yield from PureEffect(value=1))
            sub_result = yield from factorial(n - 1)
            return n * sub_result

        runtime = SyncRuntime(default_handlers())

        assert runtime.run(factorial(0)) == 1
        assert runtime.run(factorial(1)) == 1
        assert runtime.run(factorial(5)) == 120

    def test_accumulator_pattern(self) -> None:
        """Accumulator with state and iteration."""
        @do
        def sum_range(start: int, end: int):
            yield from StatePutEffect(key="sum", value=0)

            for i in range(start, end + 1):
                current = yield from StateGetEffect(key="sum")
                yield from StatePutEffect(key="sum", value=current + i)

            return (yield from StateGetEffect(key="sum"))

        runtime = SyncRuntime(default_handlers())
        result = runtime.run(sum_range(1, 10))

        # Sum of 1+2+3+...+10 = 55
        assert result == 55


# ============================================================================
# Runtime Protocol Tests
# ============================================================================


class TestRuntimeProtocol:
    """Tests verifying runtime protocol compliance."""

    def test_sync_runtime_run_signature(self) -> None:
        """SyncRuntime.run() accepts program and optional env/store."""
        @do
        def prog():
            return (yield from PureEffect(value=42))

        runtime = SyncRuntime(default_handlers())

        # All calling patterns should work
        assert runtime.run(prog()) == 42
        assert runtime.run(prog(), env={}) == 42
        assert runtime.run(prog(), env={"key": "value"}) == 42
        assert runtime.run(prog(), store={}) == 42

    def test_simulation_runtime_run_signature(self) -> None:
        """SimulationRuntime.run() accepts program and optional env/store."""
        @do
        def prog():
            return (yield from PureEffect(value=42))

        runtime = SimulationRuntime(default_handlers())

        assert runtime.run(prog()) == 42
        assert runtime.run(prog(), env={}) == 42
        assert runtime.run(prog(), env={"key": "value"}) == 42
