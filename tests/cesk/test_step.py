import pytest

from doeff._vendor import FrozenDict
from doeff.cesk.actions import Resume
from doeff.cesk.events import (
    AllTasksComplete,
    EffectSuspended,
    Stepped,
    TaskCompleted,
    TaskFailed,
)
from doeff.cesk.state import Value
from doeff.cesk.unified_state import UnifiedCESKState as CESKState, TaskStatus
from doeff.cesk.unified_step import Handler, HandlerContext, unified_step as step
from doeff.cesk.types import TaskId
from doeff.effects import AskEffect, StateGetEffect
from doeff.program import Program


class TestStepWithPureProgram:
    def test_pure_program_completes_immediately(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        event = step(state)
        
        assert isinstance(event, Stepped)
        
        event = step(event.state)
        assert isinstance(event, TaskCompleted)
        assert event.value == 42
    
    def test_pure_program_with_env(self):
        prog = Program.pure("result")
        state = CESKState.initial(prog, env={"key": "value"})
        
        while True:
            event = step(state)
            if isinstance(event, TaskCompleted):
                assert event.value == "result"
                break
            assert isinstance(event, Stepped)
            state = event.state


class TestStepWithEffect:
    def test_effect_suspends_without_handler(self):
        from doeff import do
        
        @do
        def prog():
            value = yield AskEffect("key")
            return value
        
        state = CESKState.initial(prog())
        
        event = step(state)
        assert isinstance(event, Stepped)
        
        event = step(event.state)
        assert isinstance(event, EffectSuspended)
        assert isinstance(event.effect, AskEffect)
        assert event.effect.key == "key"
    
    def test_effect_resumes_with_handler(self):
        from doeff import do
        
        @do
        def prog():
            value = yield AskEffect("key")
            return value * 2
        
        def handle_ask(effect: AskEffect, ctx: HandlerContext):
            value = ctx.env.get(effect.key, None)
            return (Resume(value),)
        
        handlers: dict[type, Handler] = {
            AskEffect: handle_ask,
        }
        
        state = CESKState.initial(prog(), env={"key": 21})
        
        while True:
            event = step(state, handlers)
            if isinstance(event, TaskCompleted):
                assert event.value == 42
                break
            assert isinstance(event, Stepped)
            state = event.state


class TestStepWithErrorHandling:
    def test_unhandled_exception_fails_task(self):
        from doeff import do
        
        @do
        def prog():
            raise ValueError("test error")
            yield  # noqa: B901
        
        state = CESKState.initial(prog())
        
        event = step(state)
        assert isinstance(event, Stepped)
        
        event = step(event.state)
        assert isinstance(event, TaskFailed)
        assert isinstance(event.error, ValueError)
        assert str(event.error) == "test error"
    
    def test_caught_exception_continues(self):
        from doeff import do
        
        @do
        def prog():
            try:
                raise ValueError("caught")
            except ValueError:
                return "recovered"
            yield  # noqa: B901
        
        state = CESKState.initial(prog())
        
        while True:
            event = step(state)
            if isinstance(event, TaskCompleted):
                assert event.value == "recovered"
                break
            assert isinstance(event, Stepped)
            state = event.state


class TestStepWithMultipleYields:
    def test_multiple_effects_in_sequence(self):
        from doeff import do
        
        @do
        def prog():
            a = yield AskEffect("a")
            b = yield AskEffect("b")
            return a + b
        
        def handle_ask(effect: AskEffect, ctx: HandlerContext):
            value = ctx.env.get(effect.key, 0)
            return (Resume(value),)
        
        handlers: dict[type, Handler] = {AskEffect: handle_ask}
        state = CESKState.initial(prog(), env={"a": 10, "b": 32})
        
        while True:
            event = step(state, handlers)
            if isinstance(event, TaskCompleted):
                assert event.value == 42
                break
            assert isinstance(event, Stepped)
            state = event.state


class TestStepNoRunnableTasks:
    def test_all_tasks_complete_when_main_done(self):
        prog = Program.pure(42)
        state = CESKState.initial(prog)
        
        while True:
            event = step(state)
            if isinstance(event, TaskCompleted):
                break
            state = event.state
        
        final_state = event.state
        final_event = step(final_state)
        assert isinstance(final_event, AllTasksComplete)


class TestStepWithState:
    def test_handler_can_modify_store(self):
        from doeff import do
        
        @do
        def prog():
            yield StateGetEffect("counter")
            return "done"
        
        def handle_get(effect: StateGetEffect, ctx: HandlerContext):
            value = ctx.store.get(effect.key, 0)
            new_store = {**ctx.store, "accessed": True}
            return (Resume(value, new_store),)
        
        handlers: dict[type, Handler] = {StateGetEffect: handle_get}
        state = CESKState.initial(prog(), store={"counter": 42})
        
        while True:
            event = step(state, handlers)
            if isinstance(event, TaskCompleted):
                break
            assert isinstance(event, Stepped)
            state = event.state
        
        assert event.state.store.get("accessed") is True
