"""Tests for new CESK handlers and runtimes."""

from datetime import datetime, timedelta

import pytest

from doeff import do
from doeff.cesk.runtime.context import HandlerContext
from doeff.effects import (
    IO,
    Ask,
    Delay,
    Gather,
    Get,
    GetTime,
    Listen,
    Local,
    Modify,
    Pure,
    Put,
    Safe,
    Tell,
    WaitUntil,
    intercept_program_effect,
)
from doeff.program import Program


class TestSyncRuntime:
    def test_pure_value(self) -> None:
        from doeff.cesk.runtime import SyncRuntime

        runtime = SyncRuntime()
        result = runtime.run(Program.pure(42))
        assert result.value == 42

    def test_ask_effect(self) -> None:
        from doeff.cesk.runtime import SyncRuntime

        runtime = SyncRuntime()

        @do
        def program():
            value = yield Ask("key")
            return value

        result = runtime.run(program(), env={"key": "value"})
        assert result.value == "value"

    def test_ask_missing_key_raises(self) -> None:
        from doeff.cesk.runtime import SyncRuntime

        runtime = SyncRuntime()

        @do
        def program():
            value = yield Ask("missing_key")
            return value

        with pytest.raises(KeyError, match="missing_key"):
            runtime.run(program(), env={}).value

    def test_get_effect(self) -> None:
        from doeff.cesk.runtime import SyncRuntime

        runtime = SyncRuntime()

        @do
        def program():
            value = yield Get("counter")
            return value

        result = runtime.run(program(), store={"counter": 10})
        assert result.value == 10

    def test_put_effect(self) -> None:
        from doeff.cesk.runtime import SyncRuntime

        runtime = SyncRuntime()

        @do
        def program():
            yield Put("counter", 42)
            value = yield Get("counter")
            return value

        result = runtime.run(program())
        assert result.value == 42

    def test_modify_effect(self) -> None:
        from doeff.cesk.runtime import SyncRuntime

        runtime = SyncRuntime()

        @do
        def program():
            yield Put("counter", 10)
            new_value = yield Modify("counter", lambda x: x + 5)
            return new_value

        result = runtime.run(program())
        assert result.value == 15

    def test_program_with_pure_returns_result(self) -> None:
        from doeff.cesk.runtime import SyncRuntime

        runtime = SyncRuntime()

        @do
        def program():
            yield Pure(None)
            return "done"

        result = runtime.run(program())
        assert result.value == "done"

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
        assert result.value == 42


class TestSimulationRuntime:
    def test_pure_value(self) -> None:
        from doeff.cesk.runtime import SimulationRuntime

        runtime = SimulationRuntime()
        result = runtime.run(Program.pure(42))
        assert result.value == 42

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
        assert result.value == start_time

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
        assert result.value == expected_time
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
        from doeff.effects.io import IOPerformEffect
        from doeff.effects.pure import PureEffect
        from doeff.effects.reader import AskEffect
        from doeff.effects.state import StateGetEffect, StateModifyEffect, StatePutEffect

        handlers = default_handlers()

        assert PureEffect in handlers
        assert AskEffect in handlers
        assert StateGetEffect in handlers
        assert StatePutEffect in handlers
        assert StateModifyEffect in handlers
        assert IOPerformEffect in handlers

    def test_handler_type_alias(self) -> None:

        from doeff.cesk.handlers import Handler

        assert Handler is not None


class TestCoreHandlers:
    def test_handle_pure(self) -> None:
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers.core import handle_pure
        from doeff.cesk.state import TaskState
        from doeff.effects.pure import PureEffect

        effect = PureEffect(value=42)
        task_state = TaskState.initial(Program.pure(0))
        store = {}
        ctx = HandlerContext(task_state=task_state, store=store)

        result = handle_pure(effect, ctx)

        assert isinstance(result, ContinueValue)
        assert result.value == 42

    def test_handle_ask(self) -> None:
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers.core import handle_ask
        from doeff.cesk.state import TaskState
        from doeff.effects.reader import AskEffect

        effect = AskEffect(key="test_key")
        task_state = TaskState.initial(Program.pure(0), env={"test_key": "test_value"})
        store = {}
        ctx = HandlerContext(task_state=task_state, store=store)

        result = handle_ask(effect, ctx)

        assert isinstance(result, ContinueValue)
        assert result.value == "test_value"

    def test_handle_state_get(self) -> None:
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers.core import handle_state_get
        from doeff.cesk.state import TaskState
        from doeff.effects.state import StateGetEffect

        effect = StateGetEffect(key="counter")
        task_state = TaskState.initial(Program.pure(0))
        store = {"counter": 100}
        ctx = HandlerContext(task_state=task_state, store=store)

        result = handle_state_get(effect, ctx)

        assert isinstance(result, ContinueValue)
        assert result.value == 100

    def test_handle_state_put(self) -> None:
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers.core import handle_state_put
        from doeff.cesk.state import TaskState
        from doeff.effects.state import StatePutEffect

        effect = StatePutEffect(key="counter", value=42)
        task_state = TaskState.initial(Program.pure(0))
        store = {}
        ctx = HandlerContext(task_state=task_state, store=store)

        result = handle_state_put(effect, ctx)

        assert isinstance(result, ContinueValue)
        assert result.value is None
        assert result.store["counter"] == 42

    def test_handle_state_modify(self) -> None:
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers.core import handle_state_modify
        from doeff.cesk.state import TaskState
        from doeff.effects.state import StateModifyEffect

        effect = StateModifyEffect(key="counter", func=lambda x: (x or 0) + 10)
        task_state = TaskState.initial(Program.pure(0))
        store = {"counter": 5}
        ctx = HandlerContext(task_state=task_state, store=store)

        result = handle_state_modify(effect, ctx)

        assert isinstance(result, ContinueValue)
        assert result.value == 15
        assert result.store["counter"] == 15


class TestIOHandlers:
    def test_handle_io(self) -> None:
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers.io import handle_io
        from doeff.cesk.state import TaskState
        from doeff.effects.io import IOPerformEffect

        effect = IOPerformEffect(action=lambda: 42)
        task_state = TaskState.initial(Program.pure(0))
        store = {}
        ctx = HandlerContext(task_state=task_state, store=store)

        result = handle_io(effect, ctx)

        assert isinstance(result, ContinueValue)
        assert result.value == 42

    def test_handle_io_error(self) -> None:
        from doeff.cesk.frames import ContinueError
        from doeff.cesk.handlers.io import handle_io
        from doeff.cesk.state import TaskState
        from doeff.effects.io import IOPerformEffect

        def failing_action():
            raise ValueError("io failed")

        effect = IOPerformEffect(action=failing_action)
        task_state = TaskState.initial(Program.pure(0))
        store = {}
        ctx = HandlerContext(task_state=task_state, store=store)

        result = handle_io(effect, ctx)

        assert isinstance(result, ContinueError)
        assert isinstance(result.error, ValueError)

    def test_handle_cache_put_and_get(self) -> None:
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers.io import handle_cache_get, handle_cache_put
        from doeff.cesk.state import TaskState
        from doeff.effects.cache import CacheGetEffect, cache_put

        put_effect = cache_put(key="cached_key", value="cached_value")
        task_state = TaskState.initial(Program.pure(0))
        store = {}
        ctx = HandlerContext(task_state=task_state, store=store)

        put_result = handle_cache_put(put_effect, ctx)
        assert isinstance(put_result, ContinueValue)

        get_effect = CacheGetEffect(key="cached_key")
        ctx_with_store = HandlerContext(task_state=task_state, store=put_result.store)
        get_result = handle_cache_get(get_effect, ctx_with_store)
        assert isinstance(get_result, ContinueValue)
        assert get_result.value == "cached_value"

    def test_handle_cache_exists(self) -> None:
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers.io import handle_cache_exists, handle_cache_put
        from doeff.cesk.state import TaskState
        from doeff.effects.cache import CacheExistsEffect, cache_put

        task_state = TaskState.initial(Program.pure(0))
        store = {}
        ctx = HandlerContext(task_state=task_state, store=store)

        exists_effect = CacheExistsEffect(key="test_key")
        result = handle_cache_exists(exists_effect, ctx)
        assert isinstance(result, ContinueValue)
        assert result.value is False

        put_effect = cache_put(key="test_key", value="value")
        put_result = handle_cache_put(put_effect, ctx)

        ctx_with_store = HandlerContext(task_state=task_state, store=put_result.store)
        result = handle_cache_exists(exists_effect, ctx_with_store)
        assert isinstance(result, ContinueValue)
        assert result.value is True

    def test_handle_cache_delete(self) -> None:
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers.io import (
            handle_cache_delete,
            handle_cache_exists,
            handle_cache_put,
        )
        from doeff.cesk.state import TaskState
        from doeff.effects.cache import CacheDeleteEffect, CacheExistsEffect, cache_put

        task_state = TaskState.initial(Program.pure(0))
        store = {}
        ctx = HandlerContext(task_state=task_state, store=store)

        put_effect = cache_put(key="test_key", value="value")
        put_result = handle_cache_put(put_effect, ctx)
        store = put_result.store

        ctx_with_store = HandlerContext(task_state=task_state, store=store)
        delete_effect = CacheDeleteEffect(key="test_key")
        delete_result = handle_cache_delete(delete_effect, ctx_with_store)
        store = delete_result.store

        ctx_final = HandlerContext(task_state=task_state, store=store)
        exists_effect = CacheExistsEffect(key="test_key")
        result = handle_cache_exists(exists_effect, ctx_final)
        assert isinstance(result, ContinueValue)
        assert result.value is False


class TestTimeHandlers:
    def test_handle_delay(self, monkeypatch) -> None:
        import doeff.cesk.handlers.time as time_module
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers.time import handle_delay
        from doeff.cesk.state import TaskState
        from doeff.effects.time import DelayEffect

        sleep_calls = []
        monkeypatch.setattr(time_module.time, "sleep", lambda s: sleep_calls.append(s))

        effect = DelayEffect(seconds=5.0)
        task_state = TaskState.initial(Program.pure(0))
        store = {}
        ctx = HandlerContext(task_state=task_state, store=store)

        result = handle_delay(effect, ctx)

        assert isinstance(result, ContinueValue)
        assert result.value is None
        assert sleep_calls == [5.0]

    def test_handle_delay_updates_store_time(self, monkeypatch) -> None:
        import doeff.cesk.handlers.time as time_module
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers.time import handle_delay
        from doeff.cesk.state import TaskState
        from doeff.effects.time import DelayEffect

        monkeypatch.setattr(time_module.time, "sleep", lambda s: None)

        effect = DelayEffect(seconds=1.0)
        task_state = TaskState.initial(Program.pure(0))
        initial_time = datetime(2025, 1, 1, 12, 0, 0)
        store = {"__current_time__": initial_time}
        ctx = HandlerContext(task_state=task_state, store=store)

        result = handle_delay(effect, ctx)

        assert isinstance(result, ContinueValue)
        assert "__current_time__" in result.store
        assert result.store["__current_time__"] != initial_time

    def test_handle_get_time_from_store(self) -> None:
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers.time import handle_get_time
        from doeff.cesk.state import TaskState
        from doeff.effects.time import GetTimeEffect

        effect = GetTimeEffect()
        task_state = TaskState.initial(Program.pure(0))
        test_time = datetime(2025, 1, 1, 12, 0, 0)
        store = {"__current_time__": test_time}
        ctx = HandlerContext(task_state=task_state, store=store)

        result = handle_get_time(effect, ctx)

        assert isinstance(result, ContinueValue)
        assert result.value == test_time

    def test_handle_get_time_default(self) -> None:
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers.time import handle_get_time
        from doeff.cesk.state import TaskState
        from doeff.effects.time import GetTimeEffect

        effect = GetTimeEffect()
        task_state = TaskState.initial(Program.pure(0))
        store = {}
        ctx = HandlerContext(task_state=task_state, store=store)

        before = datetime.now()
        result = handle_get_time(effect, ctx)
        after = datetime.now()

        assert isinstance(result, ContinueValue)
        assert before <= result.value <= after


class TestHandlerIntegration:
    def test_custom_ask_handler_overrides_default(self) -> None:
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers import default_handlers
        from doeff.cesk.runtime import SyncRuntime
        from doeff.effects.reader import AskEffect

        def custom_ask_handler(effect, ctx):
            return ContinueValue(
                value=f"intercepted:{effect.key}",
                env=ctx.task_state.env,
                store=ctx.store,
                k=ctx.task_state.kontinuation,
            )

        @do
        def program():
            result = yield Ask("key")
            return result

        default_result = SyncRuntime().run(program(), env={"key": "value"})
        assert default_result.value == "value"

        custom_handlers = default_handlers()
        custom_handlers[AskEffect] = custom_ask_handler
        custom_result = SyncRuntime(handlers=custom_handlers).run(program(), env={"key": "value"})
        assert custom_result.value == "intercepted:key"

    def test_custom_get_handler_overrides_default(self) -> None:
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers import default_handlers
        from doeff.cesk.runtime import SyncRuntime
        from doeff.effects.state import StateGetEffect

        def custom_get_handler(effect, ctx):
            actual = ctx.store.get(effect.key)
            return ContinueValue(
                value=f"wrapped:{actual}",
                env=ctx.task_state.env,
                store=ctx.store,
                k=ctx.task_state.kontinuation,
            )

        @do
        def program():
            yield Put("x", 42)
            result = yield Get("x")
            return result

        default_result = SyncRuntime().run(program())
        assert default_result.value == 42

        custom_handlers = default_handlers()
        custom_handlers[StateGetEffect] = custom_get_handler
        custom_result = SyncRuntime(handlers=custom_handlers).run(program())
        assert custom_result.value == "wrapped:42"

    def test_custom_io_handler_overrides_default(self) -> None:
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers import default_handlers
        from doeff.cesk.runtime import SyncRuntime
        from doeff.effects.io import IOPerformEffect

        call_count = [0]

        def counting_io_handler(effect, ctx):
            call_count[0] += 1
            result = effect.action()
            return ContinueValue(
                value=result,
                env=ctx.task_state.env,
                store=ctx.store,
                k=ctx.task_state.kontinuation,
            )

        @do
        def program():
            result = yield IO(lambda: 100)
            return result

        custom_handlers = default_handlers()
        custom_handlers[IOPerformEffect] = counting_io_handler
        result = SyncRuntime(handlers=custom_handlers).run(program())

        assert result.value == 100
        assert call_count[0] == 1

    def test_handlers_shared_across_runs(self) -> None:
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers import default_handlers
        from doeff.cesk.runtime import SyncRuntime
        from doeff.effects.pure import PureEffect

        run_counter = [0]

        def counting_pure_handler(effect, ctx):
            run_counter[0] += 1
            return ContinueValue(
                value=effect.value,
                env=ctx.task_state.env,
                store=ctx.store,
                k=ctx.task_state.kontinuation,
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
        from doeff.cesk.frames import ContinueValue
        from doeff.cesk.handlers import default_handlers
        from doeff.cesk.runtime import SimulationRuntime
        from doeff.effects.reader import AskEffect

        def sim_ask_handler(effect, ctx):
            return ContinueValue(
                value=f"sim:{effect.key}",
                env=ctx.task_state.env,
                store=ctx.store,
                k=ctx.task_state.kontinuation,
            )

        @do
        def program():
            result = yield Ask("test")
            return result

        custom_handlers = default_handlers()
        custom_handlers[AskEffect] = sim_ask_handler
        runtime = SimulationRuntime(handlers=custom_handlers)

        result = runtime.run(program(), env={"test": "original"})
        assert result.value == "sim:test"


class TestControlHandlers:
    def test_local_effect(self) -> None:
        from doeff.cesk.runtime import SyncRuntime

        runtime = SyncRuntime()

        @do
        def inner():
            value = yield Ask("key")
            return value

        @do
        def program():
            outer_value = yield Ask("key")
            local_value = yield Local({"key": "local_value"}, inner())
            after_value = yield Ask("key")
            return (outer_value, local_value, after_value)

        result = runtime.run(program(), env={"key": "outer_value"})
        assert result.value == ("outer_value", "local_value", "outer_value")

    def test_tell_effect(self) -> None:
        from doeff.cesk.runtime import SyncRuntime

        runtime = SyncRuntime()

        @do
        def program():
            yield Tell("message1")
            yield Tell("message2")
            yield Put("result", 42)
            return (yield Get("result"))

        result = runtime.run(program())
        assert result.value == 42

    def test_listen_effect(self) -> None:
        from doeff.cesk.runtime import SyncRuntime

        runtime = SyncRuntime()

        @do
        def inner():
            yield Tell("log1")
            yield Tell("log2")
            return "inner_result"

        @do
        def program():
            listen_result = yield Listen(inner())  # type: ignore[arg-type]
            return listen_result

        result = runtime.run(program())
        listen_result = result.value  # The Listen effect returns a ListenResult
        assert listen_result.value == "inner_result"
        assert list(listen_result.log) == ["log1", "log2"]

    def test_safe_effect_success(self) -> None:
        from doeff.cesk.runtime import SyncRuntime

        runtime = SyncRuntime()

        @do
        def inner():
            return 42

        @do
        def program():
            result = yield Safe(inner())  # type: ignore[arg-type]
            return result

        result = runtime.run(program())
        safe_result = result.value  # The Safe effect returns a Result
        assert safe_result.is_ok()
        assert safe_result.unwrap() == 42

    def test_safe_effect_failure(self) -> None:
        from doeff.cesk.runtime import SyncRuntime

        runtime = SyncRuntime()

        @do
        def inner():
            raise ValueError("test error")

        @do
        def program():
            result = yield Safe(inner())  # type: ignore[arg-type]
            return result

        result = runtime.run(program())
        safe_result = result.value  # The Safe effect returns a Result
        assert safe_result.is_err()
        assert isinstance(safe_result.error, ValueError)

    def test_intercept_effect(self) -> None:
        from doeff.cesk.runtime import SyncRuntime
        from doeff.effects import AskEffect
        from doeff.effects.base import create_effect_with_trace
        from doeff.effects.pure import PureEffect

        runtime = SyncRuntime()

        def transform_ask(effect):
            if isinstance(effect, AskEffect):
                return create_effect_with_trace(PureEffect(value=f"intercepted:{effect.key}"))
            return effect

        @do
        def inner():
            value = yield Ask("key")
            return value

        @do
        def program():
            result = yield intercept_program_effect(inner(), (transform_ask,))
            return result

        result = runtime.run(program(), env={"key": "original"})
        assert result.value == "intercepted:key"


class TestGatherHandlers:
    def test_gather_empty(self) -> None:
        from doeff.cesk.runtime import SyncRuntime

        runtime = SyncRuntime()

        @do
        def program():
            results = yield Gather()
            return results

        result = runtime.run(program())
        assert result.value == []

    @pytest.mark.skip(
        reason="Gather now requires Futures from Spawn, SyncRuntime doesn't support Spawn yet. "
        "NOTE: SyncRuntime could implement Spawn/Gather via cooperative scheduling in the future."
    )
    def test_gather_single(self) -> None:
        pass

    @pytest.mark.skip(
        reason="Gather now requires Futures from Spawn, SyncRuntime doesn't support Spawn yet. "
        "NOTE: SyncRuntime could implement Spawn/Gather via cooperative scheduling in the future."
    )
    def test_gather_multiple(self) -> None:
        pass


class TestWaitUntilHandler:
    def test_wait_until_sync_runtime(self) -> None:
        from doeff.cesk.runtime import SyncRuntime

        runtime = SyncRuntime()

        @do
        def program():
            target = datetime.now() + timedelta(milliseconds=10)
            yield WaitUntil(target)
            return "done"

        result = runtime.run(program())
        assert result.value == "done"

    def test_wait_until_past_time(self) -> None:
        from doeff.cesk.runtime import SyncRuntime

        runtime = SyncRuntime()

        @do
        def program():
            target = datetime.now() - timedelta(seconds=10)
            yield WaitUntil(target)
            return "done"

        result = runtime.run(program())
        assert result.value == "done"

    def test_wait_until_simulation_runtime(self) -> None:
        from doeff.cesk.runtime import SimulationRuntime

        start_time = datetime(2025, 1, 1, 12, 0, 0)
        target_time = datetime(2025, 1, 1, 13, 0, 0)
        runtime = SimulationRuntime(start_time=start_time)

        @do
        def program():
            yield WaitUntil(target_time)
            now = yield GetTime()
            return now

        result = runtime.run(program())
        assert result.value == target_time
        assert runtime.current_time == target_time

    def test_wait_until_handler_registered(self) -> None:
        from doeff.cesk.handlers import default_handlers
        from doeff.effects.time import WaitUntilEffect

        handlers = default_handlers()
        assert WaitUntilEffect in handlers
