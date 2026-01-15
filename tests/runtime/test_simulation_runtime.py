"""Tests for SimulationRuntime prototype."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from doeff import Program, do
from doeff.effects import Get, Put


class TestSimulationRuntimeBasic:
    def test_simple_program(self):
        from doeff.runtimes.simulation import SimulationRuntime
        
        @do
        def simple_program() -> Program[int]:
            yield Put("x", 10)
            x = yield Get("x")
            return x + 1
        
        runtime = SimulationRuntime()
        result = runtime.run(simple_program())
        assert result == 11

    def test_pure_program(self):
        from doeff.runtimes.simulation import SimulationRuntime
        
        runtime = SimulationRuntime()
        result = runtime.run(Program.pure(42))
        assert result == 42

    def test_start_time(self):
        from doeff.runtimes.simulation import SimulationRuntime
        
        start = datetime(2024, 1, 1, 12, 0, 0)
        runtime = SimulationRuntime(start_time=start)
        assert runtime.current_time == start
        
        result = runtime.run(Program.pure(1))
        assert result == 1
