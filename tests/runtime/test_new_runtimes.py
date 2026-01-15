"""Tests for runtime implementations: AsyncioRuntime, SyncRuntime, SimulationRuntime."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from doeff import Program, do
from doeff.effects import Get, Put, Ask, Log
from doeff.runtimes import (
    AsyncioRuntime,
    SyncRuntime,
    SimulationRuntime,
    AsyncEffectInSyncRuntimeError,
    EffectError,
)


# =============================================================================
# Test Programs
# =============================================================================

@do
def stateful_program() -> Program[int]:
    yield Put("x", 10)
    x = yield Get("x")
    return x + 1


@do
def reader_program() -> Program[str]:
    name = yield Ask("name")
    return f"Hello, {name}"


@do
def writer_program() -> Program[int]:
    yield Log("Starting")
    yield Put("count", 0)
    yield Log("Incrementing")
    yield Put("count", 1)
    yield Log("Done")
    return (yield Get("count"))


@do
def combined_effects_program() -> Program[str]:
    name = yield Ask("name")
    yield Log(f"Processing {name}")
    yield Put("processed", True)
    is_processed = yield Get("processed")
    return f"{name}: {is_processed}"


@do
def failing_program() -> Program[int]:
    yield Put("x", 10)
    raise ValueError("test error")


# =============================================================================
# AsyncioRuntime Tests
# =============================================================================

class TestAsyncioRuntime:
    @pytest.mark.asyncio
    async def test_pure_program(self):
        runtime = AsyncioRuntime()
        result = await runtime.run(Program.pure(42))
        assert result == 42

    @pytest.mark.asyncio
    async def test_state_effects(self):
        runtime = AsyncioRuntime()
        result = await runtime.run(stateful_program())
        assert result == 11

    @pytest.mark.asyncio
    async def test_reader_effects(self):
        runtime = AsyncioRuntime()
        result = await runtime.run(reader_program(), env={"name": "World"})
        assert result == "Hello, World"

    @pytest.mark.asyncio
    async def test_writer_effects(self):
        runtime = AsyncioRuntime()
        result = await runtime.run(writer_program())
        assert result == 1

    @pytest.mark.asyncio
    async def test_combined_effects(self):
        runtime = AsyncioRuntime()
        result = await runtime.run(combined_effects_program(), env={"name": "Test"})
        assert result == "Test: True"

    @pytest.mark.asyncio
    async def test_run_safe_success(self):
        runtime = AsyncioRuntime()
        result = await runtime.run_safe(stateful_program())
        assert result.is_ok
        assert result.unwrap() == 11

    @pytest.mark.asyncio
    async def test_run_safe_failure(self):
        runtime = AsyncioRuntime()
        result = await runtime.run_safe(failing_program())
        assert result.is_err
        assert isinstance(result.unwrap_err(), ValueError)

    @pytest.mark.asyncio
    async def test_raises_effect_error_on_failure(self):
        runtime = AsyncioRuntime()
        with pytest.raises(EffectError) as exc_info:
            await runtime.run(failing_program())
        assert "test error" in str(exc_info.value)


# =============================================================================
# SyncRuntime Tests
# =============================================================================

class TestSyncRuntime:
    def test_pure_program(self):
        runtime = SyncRuntime()
        result = runtime.run(Program.pure(42))
        assert result == 42

    def test_state_effects(self):
        runtime = SyncRuntime()
        result = runtime.run(stateful_program())
        assert result == 11

    def test_reader_effects(self):
        runtime = SyncRuntime()
        result = runtime.run(reader_program(), env={"name": "World"})
        assert result == "Hello, World"

    def test_writer_effects(self):
        runtime = SyncRuntime()
        result = runtime.run(writer_program())
        assert result == 1

    def test_combined_effects(self):
        runtime = SyncRuntime()
        result = runtime.run(combined_effects_program(), env={"name": "Test"})
        assert result == "Test: True"

    def test_run_safe_success(self):
        runtime = SyncRuntime()
        result = runtime.run_safe(stateful_program())
        assert result.is_ok
        assert result.unwrap() == 11

    def test_run_safe_failure(self):
        runtime = SyncRuntime()
        result = runtime.run_safe(failing_program())
        assert result.is_err
        assert isinstance(result.unwrap_err(), ValueError)


# =============================================================================
# SimulationRuntime Tests
# =============================================================================

class TestSimulationRuntime:
    def test_pure_program(self):
        runtime = SimulationRuntime()
        result = runtime.run(Program.pure(42))
        assert result == 42

    def test_state_effects(self):
        runtime = SimulationRuntime()
        result = runtime.run(stateful_program())
        assert result == 11

    def test_reader_effects(self):
        runtime = SimulationRuntime()
        result = runtime.run(reader_program(), env={"name": "World"})
        assert result == "Hello, World"

    def test_writer_effects(self):
        runtime = SimulationRuntime()
        result = runtime.run(writer_program())
        assert result == 1

    def test_combined_effects(self):
        runtime = SimulationRuntime()
        result = runtime.run(combined_effects_program(), env={"name": "Test"})
        assert result == "Test: True"

    def test_start_time_preserved(self):
        start = datetime(2024, 1, 1, 12, 0, 0)
        runtime = SimulationRuntime(start_time=start)
        assert runtime.current_time == start
        runtime.run(Program.pure(1))
        assert runtime.current_time == start

    def test_run_safe_success(self):
        runtime = SimulationRuntime()
        result = runtime.run_safe(stateful_program())
        assert result.is_ok
        assert result.unwrap() == 11

    def test_run_safe_failure(self):
        runtime = SimulationRuntime()
        result = runtime.run_safe(failing_program())
        assert result.is_err
        assert isinstance(result.unwrap_err(), ValueError)


# =============================================================================
# RuntimeResult Tests
# =============================================================================

class TestRuntimeResult:
    def test_display_ok(self):
        runtime = SyncRuntime()
        result = runtime.run_safe(Program.pure(42))
        display = result.display()
        assert "Ok" in display
        assert "42" in display

    def test_display_err(self):
        runtime = SyncRuntime()
        result = runtime.run_safe(failing_program())
        display = result.display()
        assert "Err" in display
        assert "test error" in display
