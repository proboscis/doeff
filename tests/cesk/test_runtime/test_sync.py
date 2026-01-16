"""Unit tests for SyncRuntime."""

from __future__ import annotations

import pytest
from doeff.cesk.runtime.sync import SyncRuntime
from doeff.cesk.handlers import default_handlers


# ============================================================================
# Test SyncRuntime Basic Execution
# ============================================================================


class TestSyncRuntimeBasic:
    """Tests for basic SyncRuntime functionality."""

    def test_run_simple_pure_effect(self) -> None:
        """Can run a program with PureEffect."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def simple_prog() -> int:
            value = yield from PureEffect(value=42)
            return value

        runtime = SyncRuntime(default_handlers())
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

        runtime = SyncRuntime(default_handlers())
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

        runtime = SyncRuntime(default_handlers())
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

        runtime = SyncRuntime(default_handlers())

        with pytest.raises(ValueError, match="test error"):
            runtime.run(failing_prog())


# ============================================================================
# Test SyncRuntime I/O Execution
# ============================================================================


class TestSyncRuntimeIO:
    """Tests for SyncRuntime I/O execution."""

    def test_io_callable_executed(self) -> None:
        """I/O operations are called directly."""
        from doeff import do
        from doeff.effects import IOPerformEffect

        call_count = [0]

        def my_io():
            call_count[0] += 1
            return "io result"

        @do
        def io_prog():
            result = yield from IOPerformEffect(action=my_io)
            return result

        runtime = SyncRuntime(default_handlers())
        result = runtime.run(io_prog())

        assert result == "io result"
        assert call_count[0] == 1

    def test_io_exception_propagated(self) -> None:
        """I/O exceptions are propagated."""
        from doeff import do
        from doeff.effects import IOPerformEffect

        def failing_io():
            raise IOError("io failed")

        @do
        def io_prog():
            result = yield from IOPerformEffect(action=failing_io)
            return result

        runtime = SyncRuntime(default_handlers())

        with pytest.raises(IOError, match="io failed"):
            runtime.run(io_prog())


# ============================================================================
# Test SyncRuntime Multiple Effects
# ============================================================================


class TestSyncRuntimeComposition:
    """Tests for composing multiple effects in SyncRuntime."""

    def test_sequence_of_effects(self) -> None:
        """Can sequence multiple effects."""
        from doeff import do
        from doeff.effects import PureEffect, StateGetEffect, StatePutEffect

        @do
        def sequence_prog() -> int:
            a = yield from PureEffect(value=10)
            yield from StatePutEffect(key="value", value=a * 2)
            b = yield from StateGetEffect(key="value")
            return b

        runtime = SyncRuntime(default_handlers())
        result = runtime.run(sequence_prog())

        assert result == 20

    def test_nested_programs(self) -> None:
        """Can call nested programs."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def inner() -> int:
            return (yield from PureEffect(value=5))

        @do
        def outer() -> int:
            a = yield from inner()
            b = yield from inner()
            return a + b

        runtime = SyncRuntime(default_handlers())
        result = runtime.run(outer())

        assert result == 10
