"""Integration tests for the unified CESK architecture."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from doeff._vendor import FrozenDict, Ok, Err
from doeff.cesk import (
    UnifiedSimulationRuntime,
    UnifiedSyncRuntime,
    SyncRuntimeError,
    SimulationRuntimeError,
)
from doeff.do import do
from doeff.effects import (
    Pure,
    ask,
    get,
    put,
    modify,
    tell,
    safe,
    delay,
    get_time,
)


class TestUnifiedSyncRuntimeIntegration:
    def test_reader_state_writer_composition(self) -> None:
        @do
        def program():
            user = yield ask("user")
            yield put("greeting_count", 0)
            yield tell(f"Starting for {user}")
            
            count = yield modify("greeting_count", lambda x: x + 1)
            yield tell(f"Greeted {count} times")
            
            final_count = yield get("greeting_count")
            return f"Hello {user}! Count: {final_count}"
        
        runtime = UnifiedSyncRuntime()
        result = runtime.run(
            program(),
            env={"user": "Bob"},
            store={"__log__": []},
        )
        
        assert result == "Hello Bob! Count: 1"
    
    def test_nested_program_with_effects(self) -> None:
        @do
        def increment():
            count = yield get("counter")
            yield put("counter", count + 1)
            return count + 1
        
        @do
        def main():
            yield put("counter", 0)
            a = yield increment()
            b = yield increment()
            c = yield increment()
            return [a, b, c]
        
        runtime = UnifiedSyncRuntime()
        result = runtime.run(main())
        
        assert result == [1, 2, 3]
    
    def test_error_handling_with_safe(self) -> None:
        @do
        def might_fail(should_fail: bool):
            if should_fail:
                raise ValueError("intentional error")
            return "success"
        
        @do
        def program():
            result1 = yield safe(might_fail(False))
            result2 = yield safe(might_fail(True))
            return (result1, result2)
        
        runtime = UnifiedSyncRuntime()
        ok_result, err_result = runtime.run(program())
        
        assert isinstance(ok_result, Ok)
        assert ok_result.value == "success"
        assert isinstance(err_result, Err)
        assert "intentional error" in str(err_result.error)
    
    def test_deeply_nested_programs(self) -> None:
        @do
        def level3():
            x = yield Pure(3)
            return x
        
        @do
        def level2():
            x = yield level3()
            return x * 2
        
        @do
        def level1():
            x = yield level2()
            return x + 10
        
        runtime = UnifiedSyncRuntime()
        result = runtime.run(level1())
        
        assert result == 16


class TestUnifiedSimulationRuntimeIntegration:
    def test_simulated_delay(self) -> None:
        @do
        def program():
            start = yield get_time()
            yield delay(seconds=10.0)
            end = yield get_time()
            return (end - start).total_seconds()
        
        runtime = UnifiedSimulationRuntime(
            start_time=datetime(2025, 1, 1, 0, 0, 0)
        )
        result = runtime.run(program())
        
        assert result == 10.0
    
    def test_simulated_time_advances(self) -> None:
        @do
        def program():
            times = []
            times.append((yield get_time()))
            yield delay(seconds=5.0)
            times.append((yield get_time()))
            yield delay(seconds=3.0)
            times.append((yield get_time()))
            return times
        
        start = datetime(2025, 6, 15, 12, 0, 0)
        runtime = UnifiedSimulationRuntime(start_time=start)
        times = runtime.run(program())
        
        assert times[0] == start
        assert times[1] == start + timedelta(seconds=5)
        assert times[2] == start + timedelta(seconds=8)
    
    def test_simulation_with_state(self) -> None:
        @do
        def program():
            yield put("step", 0)
            
            yield delay(seconds=1.0)
            yield put("step", 1)
            
            yield delay(seconds=1.0)
            yield put("step", 2)
            
            step = yield get("step")
            time = yield get_time()
            return {"step": step, "elapsed": (time - datetime(2025, 1, 1)).total_seconds()}
        
        runtime = UnifiedSimulationRuntime(start_time=datetime(2025, 1, 1, 0, 0, 0))
        result = runtime.run(program())
        
        assert result["step"] == 2
        assert result["elapsed"] == 2.0


class TestCrossRuntimeCompatibility:
    def test_same_program_runs_on_both_runtimes(self) -> None:
        @do
        def program():
            yield put("x", 10)
            x = yield get("x")
            user = yield ask("name")
            return f"{user}: {x}"
        
        sync_runtime = UnifiedSyncRuntime()
        sync_result = sync_runtime.run(program(), env={"name": "Sync"})
        
        sim_runtime = UnifiedSimulationRuntime()
        sim_result = sim_runtime.run(program(), env={"name": "Sim"})
        
        assert sync_result == "Sync: 10"
        assert sim_result == "Sim: 10"


class TestImportFromCeskModule:
    def test_all_new_types_importable(self) -> None:
        from doeff.cesk import (
            TaskId,
            FutureId,
            SpawnId,
            TaskOk,
            TaskErr,
            SimulatedTime,
            IdGenerator,
            TaskState,
            TaskStatus,
            Condition,
            WaitingForFuture,
            WaitingForTime,
            Action,
            Resume,
            Event,
            TaskCompleted,
            Handler,
            HandlerContext,
            HandlerRegistry,
            default_handlers,
            Runtime,
        )
        
        assert TaskId is not None
        assert FutureId is not None
        assert SpawnId is not None
        assert TaskOk is not None
        assert TaskErr is not None
        assert SimulatedTime is not None
        assert IdGenerator is not None
        assert TaskState is not None
        assert TaskStatus is not None
        assert Condition is not None
        assert WaitingForFuture is not None
        assert WaitingForTime is not None
        assert Action is not None
        assert Resume is not None
        assert Event is not None
        assert TaskCompleted is not None
        assert Handler is not None
        assert HandlerContext is not None
        assert HandlerRegistry is not None
        assert default_handlers is not None
        assert Runtime is not None
