"""Unit tests for SimulationRuntime."""

from __future__ import annotations

import pytest
from doeff.cesk.runtime.simulation import SimulationRuntime, TimerEntry
from doeff.cesk.handlers import default_handlers
from doeff.cesk.types import TaskId


# ============================================================================
# Test SimulationRuntime Basic Execution
# ============================================================================


class TestSimulationRuntimeBasic:
    """Tests for basic SimulationRuntime functionality."""

    def test_run_simple_pure_effect(self) -> None:
        """Can run a program with PureEffect."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def simple_prog() -> int:
            value = yield from PureEffect(value=42)
            return value

        runtime = SimulationRuntime(default_handlers())
        result = runtime.run(simple_prog())

        assert result == 42

    def test_run_with_state_effects(self) -> None:
        """Can run a program with state effects."""
        from doeff import do
        from doeff.effects import StateGetEffect, StatePutEffect

        @do
        def state_prog() -> int:
            yield from StatePutEffect(key="counter", value=10)
            value = yield from StateGetEffect(key="counter")
            return value

        runtime = SimulationRuntime(default_handlers())
        result = runtime.run(state_prog())

        assert result == 10

    def test_run_with_ask_effect(self) -> None:
        """Can run a program with AskEffect from environment."""
        from doeff import do
        from doeff.effects import AskEffect

        @do
        def ask_prog() -> int:
            value = yield from AskEffect(key="x")
            return value

        runtime = SimulationRuntime(default_handlers())
        result = runtime.run(ask_prog(), env={"x": 99})

        assert result == 99

    def test_run_program_that_raises(self) -> None:
        """Running a program that raises propagates the exception."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def failing_prog():
            yield from PureEffect(value=1)
            raise ValueError("test error")
            return 0

        runtime = SimulationRuntime(default_handlers())

        with pytest.raises(ValueError, match="test error"):
            runtime.run(failing_prog())


# ============================================================================
# Test SimulationRuntime Time Handling
# ============================================================================


class TestSimulationRuntimeTime:
    """Tests for SimulationRuntime time simulation."""

    def test_initial_time(self) -> None:
        """Runtime starts at initial time."""
        runtime = SimulationRuntime(default_handlers(), initial_time=1000.0)
        assert runtime.current_time == 1000.0

    def test_advance_time(self) -> None:
        """Can advance simulated time."""
        runtime = SimulationRuntime(default_handlers(), initial_time=0.0)
        runtime.advance_time(10.0)
        assert runtime.current_time == 10.0

        runtime.advance_time(5.0)
        assert runtime.current_time == 15.0

    def test_set_time(self) -> None:
        """Can set simulated time directly."""
        runtime = SimulationRuntime(default_handlers(), initial_time=0.0)
        runtime.set_time(100.0)
        assert runtime.current_time == 100.0

    def test_get_time_effect_returns_simulated_time(self) -> None:
        """GetTimeEffect returns the simulated time."""
        from doeff import do
        from doeff.effects import GetTimeEffect
        from datetime import datetime, timezone

        @do
        def time_prog():
            time = yield from GetTimeEffect()
            return time

        runtime = SimulationRuntime(default_handlers(), initial_time=1705320000.0)
        result = runtime.run(time_prog())

        assert isinstance(result, datetime)
        assert result.timestamp() == 1705320000.0


# ============================================================================
# Test SimulationRuntime I/O Mocking
# ============================================================================


class TestSimulationRuntimeIO:
    """Tests for SimulationRuntime I/O mocking."""

    def test_io_mock_callback(self) -> None:
        """Can mock I/O operations with callbacks."""
        from doeff import do
        from doeff.effects import IOPerformEffect

        def my_io():
            return "real io result"

        @do
        def io_prog():
            result = yield from IOPerformEffect(action=my_io)
            return result

        runtime = SimulationRuntime(default_handlers())
        runtime.set_io_mock(my_io, lambda: "mocked result")

        result = runtime.run(io_prog())
        assert result == "mocked result"

    def test_io_without_mock_calls_operation(self) -> None:
        """I/O without mock calls the operation directly."""
        from doeff import do
        from doeff.effects import IOPerformEffect

        call_count = [0]

        def my_io():
            call_count[0] += 1
            return "direct result"

        @do
        def io_prog():
            result = yield from IOPerformEffect(action=my_io)
            return result

        runtime = SimulationRuntime(default_handlers())
        result = runtime.run(io_prog())

        assert result == "direct result"
        assert call_count[0] == 1

    def test_io_mock_exception(self) -> None:
        """I/O mock can raise exceptions."""
        from doeff import do
        from doeff.effects import IOPerformEffect

        def my_io():
            return "result"

        @do
        def io_prog():
            result = yield from IOPerformEffect(action=my_io)
            return result

        runtime = SimulationRuntime(default_handlers())
        runtime.set_io_mock(my_io, lambda: (_ for _ in ()).throw(IOError("mocked error")))

        with pytest.raises(IOError, match="mocked error"):
            runtime.run(io_prog())


# ============================================================================
# Test TimerEntry
# ============================================================================


class TestTimerEntry:
    """Tests for TimerEntry dataclass."""

    def test_timer_entry_creation(self) -> None:
        """TimerEntry holds time and task_id."""
        entry = TimerEntry(time=10.0, task_id=TaskId(1))
        assert entry.time == 10.0
        assert entry.task_id == TaskId(1)

    def test_timer_entry_ordering(self) -> None:
        """TimerEntries are ordered by time."""
        entry1 = TimerEntry(time=10.0, task_id=TaskId(1))
        entry2 = TimerEntry(time=5.0, task_id=TaskId(2))
        entry3 = TimerEntry(time=15.0, task_id=TaskId(3))

        entries = [entry1, entry2, entry3]
        sorted_entries = sorted(entries)

        assert sorted_entries[0].time == 5.0
        assert sorted_entries[1].time == 10.0
        assert sorted_entries[2].time == 15.0
