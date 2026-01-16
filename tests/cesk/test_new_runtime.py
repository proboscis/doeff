"""Integration tests for new handler and runtime system."""

import pytest
from datetime import datetime

from doeff import do, Program
from doeff.cesk.runtime import SyncRuntime, SimulationRuntime
from doeff.effects import Ask, Get, Put, Modify, Pure, Local, gather


class TestSyncRuntime:
    def test_pure_value(self):
        runtime = SyncRuntime()
        result = runtime.run(Program.pure(42))
        assert result == 42

    def test_ask_effect(self):
        runtime = SyncRuntime()
        
        @do
        def program():
            value = yield Ask("key")
            return value
        
        result = runtime.run(program(), env={"key": "value"})
        assert result == "value"

    def test_get_put_effects(self):
        runtime = SyncRuntime()
        
        @do
        def program():
            yield Put("counter", 0)
            count = yield Get("counter")
            yield Put("counter", count + 1)
            return (yield Get("counter"))
        
        result = runtime.run(program())
        assert result == 1

    def test_modify_effect(self):
        runtime = SyncRuntime()
        
        @do
        def program():
            yield Put("value", 10)
            new_value = yield Modify("value", lambda x: x * 2)
            return new_value
        
        result = runtime.run(program())
        assert result == 20

    def test_local_effect(self):
        runtime = SyncRuntime()
        
        @do
        def inner():
            return (yield Ask("key"))
        
        @do
        def program():
            outer_val = yield Ask("key")
            inner_val = yield Local({"key": "inner"}, inner())
            outer_val_2 = yield Ask("key")
            return (outer_val, inner_val, outer_val_2)
        
        result = runtime.run(program(), env={"key": "outer"})
        assert result == ("outer", "inner", "outer")

    def test_gather_effect(self):
        runtime = SyncRuntime()
        
        @do
        def prog1():
            val = yield Pure(1)
            return val
        
        @do
        def prog2():
            val = yield Pure(2)
            return val
        
        @do
        def prog3():
            val = yield Pure(3)
            return val
        
        @do
        def program():
            results = yield gather(prog1(), prog2(), prog3())
            return results
        
        result = runtime.run(program())
        assert result == [1, 2, 3]


class TestSimulationRuntime:
    def test_pure_value(self):
        runtime = SimulationRuntime()
        result = runtime.run(Program.pure(42))
        assert result == 42

    def test_simulated_time(self):
        from doeff.effects import GetTime
        
        start_time = datetime(2025, 1, 1, 12, 0, 0)
        runtime = SimulationRuntime(start_time=start_time)
        
        @do
        def program():
            time = yield GetTime()
            return time
        
        result = runtime.run(program())
        assert result == start_time

    def test_all_effects(self):
        runtime = SimulationRuntime()
        
        @do
        def program():
            yield Put("counter", 0)
            
            val1 = yield Ask("name")
            val2 = yield Get("counter")
            
            yield Modify("counter", lambda x: x + 10)
            val3 = yield Get("counter")
            
            return (val1, val2, val3)
        
        result = runtime.run(program(), env={"name": "test"})
        assert result == ("test", 0, 10)
