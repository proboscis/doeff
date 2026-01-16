"""Integration tests for the unified CESK architecture."""

import pytest
from doeff import do, Program
from doeff.effects import Ask, Get, Put, Log, Pure, gather
from doeff.cesk.runtime import SyncRuntime, SimulationRuntime
from doeff.cesk.handlers import default_handlers


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
        
        result = runtime.run(program(), env={"key": "hello"})
        assert result == "hello"
    
    def test_state_effects(self):
        runtime = SyncRuntime()
        
        @do
        def program():
            yield Put("counter", 0)
            count = yield Get("counter")
            yield Put("counter", count + 1)
            final = yield Get("counter")
            return final
        
        result = runtime.run(program())
        assert result == 1
    
    def test_log_effect(self):
        runtime = SyncRuntime()
        
        @do
        def program():
            yield Log("message 1")
            yield Log("message 2")
            return "done"
        
        result = runtime.run(program())
        assert result == "done"
    
    def test_gather_effect(self):
        runtime = SyncRuntime()
        
        @do
        def program():
            results = yield gather(
                Program.pure(1),
                Program.pure(2),
                Program.pure(3),
            )
            return list(results)
        
        result = runtime.run(program())
        assert result == [1, 2, 3]
    
    def test_nested_programs(self):
        runtime = SyncRuntime()
        
        @do
        def inner(x):
            yield Log(f"inner: {x}")
            return x * 2
        
        @do
        def outer():
            a = yield inner(5)
            b = yield inner(10)
            return a + b
        
        result = runtime.run(outer())
        assert result == 30
    
    def test_map_program(self):
        runtime = SyncRuntime()
        
        prog = Program.pure(10).map(lambda x: x * 2)
        result = runtime.run(prog)
        assert result == 20
    
    def test_flat_map_program(self):
        runtime = SyncRuntime()
        
        prog = Program.pure(10).flat_map(lambda x: Program.pure(x * 2))
        result = runtime.run(prog)
        assert result == 20


class TestSimulationRuntime:
    def test_pure_value(self):
        runtime = SimulationRuntime()
        result = runtime.run(Program.pure(42))
        assert result == 42
    
    def test_state_effects(self):
        runtime = SimulationRuntime()
        
        @do
        def program():
            yield Put("x", 100)
            val = yield Get("x")
            return val
        
        result = runtime.run(program())
        assert result == 100


class TestHandlers:
    def test_default_handlers_exist(self):
        handlers = default_handlers()
        assert len(handlers) > 0
    
    def test_handler_types(self):
        from doeff.effects import (
            AskEffect,
            PureEffect,
            StateGetEffect,
            StatePutEffect,
        )
        
        handlers = default_handlers()
        assert PureEffect in handlers
        assert AskEffect in handlers
        assert StateGetEffect in handlers
        assert StatePutEffect in handlers


class TestTypes:
    def test_task_id_types(self):
        from doeff.cesk.types import TaskId, FutureId, SpawnId
        
        task_id = TaskId(1)
        future_id = FutureId(2)
        spawn_id = SpawnId(3)
        
        assert task_id == 1
        assert future_id == 2
        assert spawn_id == 3
    
    def test_handle_types(self):
        from doeff.cesk.types import TaskHandle, FutureHandle, SpawnHandle, TaskId, FutureId, SpawnId
        
        task_handle = TaskHandle(TaskId(1))
        future_handle = FutureHandle(FutureId(2))
        spawn_handle = SpawnHandle(SpawnId(3))
        
        assert task_handle.task_id == 1
        assert future_handle.future_id == 2
        assert spawn_handle.spawn_id == 3


class TestState:
    def test_cesk_state_initial(self):
        from doeff.cesk.state import CESKState
        
        prog = Program.pure(42)
        state, main_id = CESKState.initial(prog)
        
        assert main_id in state.tasks
        assert state.next_task_id == 1
    
    def test_task_state_initial(self):
        from doeff.cesk.state import TaskState, ProgramControl, ReadyStatus
        
        prog = Program.pure(42)
        task = TaskState.initial(prog)
        
        assert isinstance(task.control, ProgramControl)
        assert isinstance(task.status, ReadyStatus)
        assert task.kontinuation == []
    
    def test_cesk_state_create_task(self):
        from doeff.cesk.state import CESKState
        
        prog = Program.pure(1)
        state, main_id = CESKState.initial(prog)
        
        child_id, new_state = state.create_task(Program.pure(2))
        
        assert child_id != main_id
        assert len(new_state.tasks) == 2
        assert new_state.next_task_id == 2
