"""Tests for new CESK handlers and runtimes."""

import pytest
from datetime import datetime, timedelta

from doeff.program import Program
from doeff import do
from doeff.effects import Ask, Get, Put, Modify, Pure, IO, Delay, GetTime


class TestSyncRuntime:
    def test_pure_value(self) -> None:
        from doeff.cesk.runtime import SyncRuntime
        
        runtime = SyncRuntime()
        result = runtime.run(Program.pure(42))
        assert result == 42

    def test_ask_effect(self) -> None:
        from doeff.cesk.runtime import SyncRuntime
        
        runtime = SyncRuntime()
        
        @do
        def program():
            value = yield Ask("key")
            return value
        
        result = runtime.run(program(), env={"key": "value"})
        assert result == "value"

    def test_ask_missing_key_raises(self) -> None:
        from doeff.cesk.runtime import SyncRuntime
        
        runtime = SyncRuntime()
        
        @do
        def program():
            value = yield Ask("missing_key")
            return value
        
        with pytest.raises(KeyError, match="missing_key"):
            runtime.run(program(), env={})

    def test_get_effect(self) -> None:
        from doeff.cesk.runtime import SyncRuntime
        
        runtime = SyncRuntime()
        
        @do
        def program():
            value = yield Get("counter")
            return value
        
        result = runtime.run(program(), store={"counter": 10})
        assert result == 10

    def test_put_effect(self) -> None:
        from doeff.cesk.runtime import SyncRuntime
        
        runtime = SyncRuntime()
        
        @do
        def program():
            yield Put("counter", 42)
            value = yield Get("counter")
            return value
        
        result = runtime.run(program())
        assert result == 42

    def test_modify_effect(self) -> None:
        from doeff.cesk.runtime import SyncRuntime
        
        runtime = SyncRuntime()
        
        @do
        def program():
            yield Put("counter", 10)
            new_value = yield Modify("counter", lambda x: x + 5)
            return new_value
        
        result = runtime.run(program())
        assert result == 15

    def test_program_with_pure_returns_result(self) -> None:
        from doeff.cesk.runtime import SyncRuntime
        
        runtime = SyncRuntime()
        
        @do
        def program():
            yield Pure(None)
            return "done"
        
        result = runtime.run(program())
        assert result == "done"

    def test_chained_effects(self) -> None:
        from doeff.cesk.runtime import SyncRuntime
        
        runtime = SyncRuntime()
        
        @do
        def program():
            config = yield Ask("config")
            yield Put("value", config["initial"])
            new_value = yield Modify("value", lambda x: x * 2)
            return new_value
        
        result = runtime.run(
            program(),
            env={"config": {"initial": 21}},
        )
        assert result == 42


class TestSimulationRuntime:
    def test_pure_value(self) -> None:
        from doeff.cesk.runtime import SimulationRuntime
        
        runtime = SimulationRuntime()
        result = runtime.run(Program.pure(42))
        assert result == 42

    def test_initial_time(self) -> None:
        from doeff.cesk.runtime import SimulationRuntime
        
        start_time = datetime(2025, 1, 1, 12, 0, 0)
        runtime = SimulationRuntime(start_time=start_time)
        
        assert runtime.current_time == start_time

    def test_get_time_effect(self) -> None:
        from doeff.cesk.runtime import SimulationRuntime
        
        start_time = datetime(2025, 1, 1, 12, 0, 0)
        runtime = SimulationRuntime(start_time=start_time)
        
        @do
        def program():
            now = yield GetTime()
            return now
        
        result = runtime.run(program())
        assert result == start_time

    def test_delay_advances_time(self) -> None:
        from doeff.cesk.runtime import SimulationRuntime
        
        start_time = datetime(2025, 1, 1, 12, 0, 0)
        runtime = SimulationRuntime(start_time=start_time)
        
        @do
        def program():
            yield Delay(seconds=60.0)
            now = yield GetTime()
            return now
        
        result = runtime.run(program())
        expected_time = start_time + timedelta(seconds=60)
        assert result == expected_time
        assert runtime.current_time == expected_time

    def test_advance_time_method(self) -> None:
        from doeff.cesk.runtime import SimulationRuntime
        
        start_time = datetime(2025, 1, 1, 12, 0, 0)
        runtime = SimulationRuntime(start_time=start_time)
        
        runtime.advance_time(timedelta(hours=1))
        assert runtime.current_time == datetime(2025, 1, 1, 13, 0, 0)

    def test_set_time_method(self) -> None:
        from doeff.cesk.runtime import SimulationRuntime
        
        start_time = datetime(2025, 1, 1, 12, 0, 0)
        runtime = SimulationRuntime(start_time=start_time)
        
        new_time = datetime(2025, 6, 15, 18, 30, 0)
        runtime.set_time(new_time)
        assert runtime.current_time == new_time


class TestHandlers:
    def test_default_handlers_registry(self) -> None:
        from doeff.cesk.handlers import default_handlers
        from doeff.effects.pure import PureEffect
        from doeff.effects.reader import AskEffect
        from doeff.effects.state import StateGetEffect, StatePutEffect, StateModifyEffect
        from doeff.effects.io import IOPerformEffect
        
        handlers = default_handlers()
        
        assert PureEffect in handlers
        assert AskEffect in handlers
        assert StateGetEffect in handlers
        assert StatePutEffect in handlers
        assert StateModifyEffect in handlers
        assert IOPerformEffect in handlers

    def test_handler_type_alias(self) -> None:
        from doeff.cesk.handlers import Handler
        from typing import get_type_hints
        
        assert Handler is not None


class TestCoreHandlers:
    def test_handle_pure(self) -> None:
        from doeff.cesk.handlers.core import handle_pure
        from doeff.cesk.state import TaskState
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.pure import PureEffect
        
        effect = PureEffect(value=42)
        task_state = TaskState.initial(Program.pure(0))
        store = {}
        
        result = handle_pure(effect, task_state, store)
        
        assert isinstance(result, ContinueValue)
        assert result.value == 42

    def test_handle_ask(self) -> None:
        from doeff.cesk.handlers.core import handle_ask
        from doeff.cesk.state import TaskState
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.reader import AskEffect
        from doeff._vendor import FrozenDict
        
        effect = AskEffect(key="test_key")
        task_state = TaskState.initial(Program.pure(0), env={"test_key": "test_value"})
        store = {}
        
        result = handle_ask(effect, task_state, store)
        
        assert isinstance(result, ContinueValue)
        assert result.value == "test_value"

    def test_handle_state_get(self) -> None:
        from doeff.cesk.handlers.core import handle_state_get
        from doeff.cesk.state import TaskState
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.state import StateGetEffect
        
        effect = StateGetEffect(key="counter")
        task_state = TaskState.initial(Program.pure(0))
        store = {"counter": 100}
        
        result = handle_state_get(effect, task_state, store)
        
        assert isinstance(result, ContinueValue)
        assert result.value == 100

    def test_handle_state_put(self) -> None:
        from doeff.cesk.handlers.core import handle_state_put
        from doeff.cesk.state import TaskState
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.state import StatePutEffect
        
        effect = StatePutEffect(key="counter", value=42)
        task_state = TaskState.initial(Program.pure(0))
        store = {}
        
        result = handle_state_put(effect, task_state, store)
        
        assert isinstance(result, ContinueValue)
        assert result.value is None
        assert result.store["counter"] == 42

    def test_handle_state_modify(self) -> None:
        from doeff.cesk.handlers.core import handle_state_modify
        from doeff.cesk.state import TaskState
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.state import StateModifyEffect
        
        effect = StateModifyEffect(key="counter", func=lambda x: (x or 0) + 10)
        task_state = TaskState.initial(Program.pure(0))
        store = {"counter": 5}
        
        result = handle_state_modify(effect, task_state, store)
        
        assert isinstance(result, ContinueValue)
        assert result.value == 15
        assert result.store["counter"] == 15


class TestIOHandlers:
    def test_handle_io(self) -> None:
        from doeff.cesk.handlers.io import handle_io
        from doeff.cesk.state import TaskState
        from doeff.cesk.frames import ContinueValue, ContinueError
        from doeff.effects.io import IOPerformEffect
        
        effect = IOPerformEffect(action=lambda: 42)
        task_state = TaskState.initial(Program.pure(0))
        store = {}
        
        result = handle_io(effect, task_state, store)
        
        assert isinstance(result, ContinueValue)
        assert result.value == 42

    def test_handle_io_error(self) -> None:
        from doeff.cesk.handlers.io import handle_io
        from doeff.cesk.state import TaskState
        from doeff.cesk.frames import ContinueError
        from doeff.effects.io import IOPerformEffect
        
        def failing_action():
            raise ValueError("io failed")
        
        effect = IOPerformEffect(action=failing_action)
        task_state = TaskState.initial(Program.pure(0))
        store = {}
        
        result = handle_io(effect, task_state, store)
        
        assert isinstance(result, ContinueError)
        assert isinstance(result.error, ValueError)

    def test_handle_cache_put_and_get(self) -> None:
        from doeff.cesk.handlers.io import handle_cache_put, handle_cache_get
        from doeff.cesk.state import TaskState
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.cache import cache_put, CacheGetEffect
        
        put_effect = cache_put(key="cached_key", value="cached_value")
        task_state = TaskState.initial(Program.pure(0))
        store = {}
        
        put_result = handle_cache_put(put_effect, task_state, store)
        assert isinstance(put_result, ContinueValue)
        
        get_effect = CacheGetEffect(key="cached_key")
        get_result = handle_cache_get(get_effect, task_state, put_result.store)
        assert isinstance(get_result, ContinueValue)
        assert get_result.value == "cached_value"

    def test_handle_cache_exists(self) -> None:
        from doeff.cesk.handlers.io import handle_cache_exists, handle_cache_put
        from doeff.cesk.state import TaskState
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.cache import CacheExistsEffect, cache_put
        
        task_state = TaskState.initial(Program.pure(0))
        store = {}
        
        exists_effect = CacheExistsEffect(key="test_key")
        result = handle_cache_exists(exists_effect, task_state, store)
        assert isinstance(result, ContinueValue)
        assert result.value is False
        
        put_effect = cache_put(key="test_key", value="value")
        put_result = handle_cache_put(put_effect, task_state, store)
        
        result = handle_cache_exists(exists_effect, task_state, put_result.store)
        assert isinstance(result, ContinueValue)
        assert result.value is True

    def test_handle_cache_delete(self) -> None:
        from doeff.cesk.handlers.io import handle_cache_delete, handle_cache_put, handle_cache_exists
        from doeff.cesk.state import TaskState
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.cache import CacheDeleteEffect, cache_put, CacheExistsEffect
        
        task_state = TaskState.initial(Program.pure(0))
        store = {}
        
        put_effect = cache_put(key="test_key", value="value")
        store = handle_cache_put(put_effect, task_state, store).store
        
        delete_effect = CacheDeleteEffect(key="test_key")
        store = handle_cache_delete(delete_effect, task_state, store).store
        
        exists_effect = CacheExistsEffect(key="test_key")
        result = handle_cache_exists(exists_effect, task_state, store)
        assert isinstance(result, ContinueValue)
        assert result.value is False


class TestTimeHandlers:
    def test_handle_delay(self) -> None:
        from doeff.cesk.handlers.time import handle_delay
        from doeff.cesk.state import TaskState
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.time import DelayEffect
        
        effect = DelayEffect(seconds=10.0)
        task_state = TaskState.initial(Program.pure(0))
        store = {}
        
        result = handle_delay(effect, task_state, store)
        
        assert isinstance(result, ContinueValue)
        assert result.value is None

    def test_handle_get_time_from_store(self) -> None:
        from doeff.cesk.handlers.time import handle_get_time
        from doeff.cesk.state import TaskState
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.time import GetTimeEffect
        
        effect = GetTimeEffect()
        task_state = TaskState.initial(Program.pure(0))
        test_time = datetime(2025, 1, 1, 12, 0, 0)
        store = {"__current_time__": test_time}
        
        result = handle_get_time(effect, task_state, store)
        
        assert isinstance(result, ContinueValue)
        assert result.value == test_time

    def test_handle_get_time_default(self) -> None:
        from doeff.cesk.handlers.time import handle_get_time
        from doeff.cesk.state import TaskState
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.time import GetTimeEffect
        
        effect = GetTimeEffect()
        task_state = TaskState.initial(Program.pure(0))
        store = {}
        
        before = datetime.now()
        result = handle_get_time(effect, task_state, store)
        after = datetime.now()
        
        assert isinstance(result, ContinueValue)
        assert before <= result.value <= after


class TestHandlerIntegration:
    def test_custom_ask_handler_overrides_default(self) -> None:
        from doeff.cesk.runtime import SyncRuntime
        from doeff.cesk.handlers import default_handlers
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.reader import AskEffect
        
        def custom_ask_handler(effect, task_state, store):
            return ContinueValue(
                value=f"intercepted:{effect.key}",
                env=task_state.env,
                store=store,
                k=task_state.kontinuation,
            )
        
        @do
        def program():
            result = yield Ask("key")
            return result
        
        default_result = SyncRuntime().run(program(), env={"key": "value"})
        assert default_result == "value"
        
        custom_handlers = default_handlers()
        custom_handlers[AskEffect] = custom_ask_handler
        custom_result = SyncRuntime(handlers=custom_handlers).run(program(), env={"key": "value"})
        assert custom_result == "intercepted:key"

    def test_custom_get_handler_overrides_default(self) -> None:
        from doeff.cesk.runtime import SyncRuntime
        from doeff.cesk.handlers import default_handlers
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.state import StateGetEffect
        
        def custom_get_handler(effect, task_state, store):
            actual = store.get(effect.key)
            return ContinueValue(
                value=f"wrapped:{actual}",
                env=task_state.env,
                store=store,
                k=task_state.kontinuation,
            )
        
        @do
        def program():
            yield Put("x", 42)
            result = yield Get("x")
            return result
        
        default_result = SyncRuntime().run(program())
        assert default_result == 42
        
        custom_handlers = default_handlers()
        custom_handlers[StateGetEffect] = custom_get_handler
        custom_result = SyncRuntime(handlers=custom_handlers).run(program())
        assert custom_result == "wrapped:42"

    def test_custom_io_handler_overrides_default(self) -> None:
        from doeff.cesk.runtime import SyncRuntime
        from doeff.cesk.handlers import default_handlers
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.io import IOPerformEffect
        
        call_count = [0]
        
        def counting_io_handler(effect, task_state, store):
            call_count[0] += 1
            result = effect.action()
            return ContinueValue(
                value=result,
                env=task_state.env,
                store=store,
                k=task_state.kontinuation,
            )
        
        @do
        def program():
            result = yield IO(lambda: 100)
            return result
        
        custom_handlers = default_handlers()
        custom_handlers[IOPerformEffect] = counting_io_handler
        result = SyncRuntime(handlers=custom_handlers).run(program())
        
        assert result == 100
        assert call_count[0] == 1

    def test_handlers_shared_across_runs(self) -> None:
        from doeff.cesk.runtime import SyncRuntime
        from doeff.cesk.handlers import default_handlers
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.pure import PureEffect
        
        run_counter = [0]
        
        def counting_pure_handler(effect, task_state, store):
            run_counter[0] += 1
            return ContinueValue(
                value=effect.value,
                env=task_state.env,
                store=store,
                k=task_state.kontinuation,
            )
        
        custom_handlers = default_handlers()
        custom_handlers[PureEffect] = counting_pure_handler
        runtime = SyncRuntime(handlers=custom_handlers)
        
        @do
        def program():
            yield Pure(None)
            return "done"
        
        runtime.run(program())
        runtime.run(program())
        runtime.run(program())
        
        assert run_counter[0] == 3

    def test_simulation_runtime_uses_custom_handlers(self) -> None:
        from doeff.cesk.runtime import SimulationRuntime
        from doeff.cesk.handlers import default_handlers
        from doeff.cesk.frames import ContinueValue
        from doeff.effects.reader import AskEffect
        
        def sim_ask_handler(effect, task_state, store):
            return ContinueValue(
                value=f"sim:{effect.key}",
                env=task_state.env,
                store=store,
                k=task_state.kontinuation,
            )
        
        @do
        def program():
            result = yield Ask("test")
            return result
        
        custom_handlers = default_handlers()
        custom_handlers[AskEffect] = sim_ask_handler
        runtime = SimulationRuntime(handlers=custom_handlers)
        
        result = runtime.run(program(), env={"test": "original"})
        assert result == "sim:test"
