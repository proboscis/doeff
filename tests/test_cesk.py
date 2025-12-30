"""
Comprehensive tests for the CESK machine implementation.

Tests cover:
- Step function transitions (one test per rule)
- Pure handlers in isolation
- Error propagation through all frame types
- Interception semantics
- Main loop with async boundaries
"""

import pytest
import asyncio
from collections.abc import Generator
from typing import Any

from doeff._vendor import Err, FrozenDict, Ok
from doeff.do import do
from doeff.program import Program
from doeff.effects import state, reader, writer
from doeff.effects.result import (
    ResultCatchEffect,
    ResultRecoverEffect,
    ResultFinallyEffect,
    ResultSafeEffect,
)
from doeff.cesk import (
    # State components
    Environment,
    Store,
    Control,
    Value,
    Error,
    EffectControl,
    ProgramControl,
    # Frames
    ReturnFrame,
    CatchFrame,
    RecoverFrame,
    FinallyFrame,
    LocalFrame,
    InterceptFrame,
    ListenFrame,
    GatherFrame,
    # State
    CEKSState,
    # Step results
    Done,
    Failed,
    Suspended,  # Continuation-based suspension (replaces NeedAsync/NeedParallel)
    # Classification
    is_control_flow_effect,
    is_pure_effect,
    is_effectful,
    has_intercept_frame,
    find_intercept_frame_index,
    # Handlers
    handle_pure,
    UnhandledEffectError,
    InterpreterInvariantError,
    # Step function
    step,
    # Main loop
    run,
    run_sync,
)


# ============================================================================
# Test Effect Classification
# ============================================================================


class TestEffectClassification:
    """Test effect classification predicates."""

    def test_is_control_flow_effect_catch(self):
        """ResultCatchEffect is a control flow effect."""
        effect = ResultCatchEffect(
            sub_program=Program.pure(42),
            handler=lambda e: Program.pure(0),
        )
        assert is_control_flow_effect(effect) is True

    def test_is_control_flow_effect_recover(self):
        """ResultRecoverEffect is a control flow effect."""
        effect = ResultRecoverEffect(
            sub_program=Program.pure(42),
            fallback=0,
        )
        assert is_control_flow_effect(effect) is True

    def test_is_control_flow_effect_finally(self):
        """ResultFinallyEffect is a control flow effect."""
        effect = ResultFinallyEffect(
            sub_program=Program.pure(42),
            finalizer=Program.pure(None),
        )
        assert is_control_flow_effect(effect) is True

    def test_is_pure_effect_state_get(self):
        """StateGetEffect is a pure effect."""
        effect = state.StateGetEffect(key="test")
        assert is_pure_effect(effect) is True
        assert is_effectful(effect) is False

    def test_is_pure_effect_state_put(self):
        """StatePutEffect is a pure effect."""
        effect = state.StatePutEffect(key="test", value=42)
        assert is_pure_effect(effect) is True

    def test_is_pure_effect_ask(self):
        """AskEffect is a pure effect."""
        effect = reader.AskEffect(key="test")
        assert is_pure_effect(effect) is True

    def test_is_pure_effect_tell(self):
        """WriterTellEffect is a pure effect."""
        effect = writer.WriterTellEffect(message="hello")
        assert is_pure_effect(effect) is True

    def test_is_effectful_io(self):
        """IOPerformEffect is an effectful effect."""
        from doeff.effects.io import IOPerformEffect

        effect = IOPerformEffect(action=lambda: 42)
        assert is_effectful(effect) is True
        assert is_pure_effect(effect) is False


# ============================================================================
# Test Pure Handlers
# ============================================================================


class TestPureHandlers:
    """Test pure effect handlers in isolation."""

    def test_handle_state_get_existing_key(self):
        """StateGetEffect returns value for existing key."""
        effect = state.StateGetEffect(key="counter")
        store = {"counter": 42}
        env = FrozenDict()

        result, new_store = handle_pure(effect, env, store)

        assert result == 42
        assert new_store == store  # Store unchanged for Get

    def test_handle_state_get_missing_key(self):
        """StateGetEffect returns None for missing key."""
        effect = state.StateGetEffect(key="missing")
        store = {}
        env = FrozenDict()

        result, new_store = handle_pure(effect, env, store)

        assert result is None
        assert new_store == store

    def test_handle_state_put(self):
        """StatePutEffect updates store."""
        effect = state.StatePutEffect(key="counter", value=100)
        store = {"counter": 42}
        env = FrozenDict()

        result, new_store = handle_pure(effect, env, store)

        assert result is None
        assert new_store["counter"] == 100

    def test_handle_state_modify(self):
        """StateModifyEffect applies function and returns new value."""
        effect = state.StateModifyEffect(key="counter", func=lambda x: (x or 0) + 1)
        store = {"counter": 42}
        env = FrozenDict()

        result, new_store = handle_pure(effect, env, store)

        assert result == 43
        assert new_store["counter"] == 43

    def test_handle_ask_existing_key(self):
        """AskEffect returns value for existing key."""
        effect = reader.AskEffect(key="config")
        env = FrozenDict({"config": "value"})
        store = {}

        result, new_store = handle_pure(effect, env, store)

        assert result == "value"
        assert new_store == store

    def test_handle_ask_missing_key(self):
        """AskEffect raises KeyError for missing key."""
        effect = reader.AskEffect(key="missing")
        env = FrozenDict()
        store = {}

        with pytest.raises(KeyError, match="Missing environment key"):
            handle_pure(effect, env, store)

    def test_handle_tell(self):
        """WriterTellEffect appends to log."""
        effect = writer.WriterTellEffect(message="hello")
        env = FrozenDict()
        store = {"__log__": ["previous"]}

        result, new_store = handle_pure(effect, env, store)

        assert result is None
        assert new_store["__log__"] == ["previous", "hello"]

    def test_handle_tell_empty_log(self):
        """WriterTellEffect creates log if missing."""
        effect = writer.WriterTellEffect(message="first")
        env = FrozenDict()
        store = {}

        result, new_store = handle_pure(effect, env, store)

        assert result is None
        assert new_store["__log__"] == ["first"]


# ============================================================================
# Test Step Function - Terminal States
# ============================================================================


class TestStepTerminalStates:
    """Test step function terminal state transitions."""

    def test_value_empty_k_returns_done(self):
        """Value with empty K returns Done."""
        state = CEKSState(C=Value(42), E=FrozenDict(), S={}, K=[])

        result = step(state)

        assert isinstance(result, Done)
        assert result.value == 42

    def test_error_empty_k_returns_failed(self):
        """Error with empty K returns Failed."""
        exc = ValueError("test error")
        state = CEKSState(C=Error(exc), E=FrozenDict(), S={}, K=[])

        result = step(state)

        assert isinstance(result, Failed)
        assert result.exception is exc


# ============================================================================
# Test Step Function - Pure Effect Handling
# ============================================================================


class TestStepPureEffects:
    """Test step function handling of pure effects."""

    def test_pure_effect_state_get(self):
        """Pure StateGetEffect is handled synchronously."""
        effect = state.StateGetEffect(key="counter")
        state_obj = CEKSState(
            C=EffectControl(effect),
            E=FrozenDict(),
            S={"counter": 42},
            K=[],
        )

        result = step(state_obj)

        assert isinstance(result, CEKSState)
        assert isinstance(result.C, Value)
        assert result.C.v == 42

    def test_pure_effect_ask_error_becomes_error_state(self):
        """Pure effect raising exception becomes Error state."""
        effect = reader.AskEffect(key="missing")
        state_obj = CEKSState(
            C=EffectControl(effect),
            E=FrozenDict(),
            S={},
            K=[],
        )

        result = step(state_obj)

        assert isinstance(result, CEKSState)
        assert isinstance(result.C, Error)
        assert isinstance(result.C.ex, KeyError)


# ============================================================================
# Test Step Function - Control Flow Effects
# ============================================================================


class TestStepControlFlowEffects:
    """Test step function handling of control flow effects."""

    def test_catch_effect_pushes_catch_frame(self):
        """ResultCatchEffect pushes CatchFrame onto K."""
        handler = lambda e: Program.pure(0)
        effect = ResultCatchEffect(
            sub_program=Program.pure(42),
            handler=handler,
        )
        state_obj = CEKSState(
            C=EffectControl(effect),
            E=FrozenDict(),
            S={},
            K=[],
        )

        result = step(state_obj)

        assert isinstance(result, CEKSState)
        assert isinstance(result.C, ProgramControl)
        assert len(result.K) == 1
        assert isinstance(result.K[0], CatchFrame)
        assert result.K[0].handler is handler

    def test_recover_effect_pushes_catch_frame(self):
        """ResultRecoverEffect pushes CatchFrame with fallback handler onto K."""
        effect = ResultRecoverEffect(
            sub_program=Program.pure(42),
            fallback=0,
        )
        state_obj = CEKSState(
            C=EffectControl(effect),
            E=FrozenDict(),
            S={},
            K=[],
        )

        result = step(state_obj)

        assert isinstance(result, CEKSState)
        assert isinstance(result.C, ProgramControl)
        assert len(result.K) == 1
        # Recover uses CatchFrame with fallback handler (not RecoverFrame)
        assert isinstance(result.K[0], CatchFrame)

    def test_local_effect_updates_environment(self):
        """LocalEffect updates environment and pushes LocalFrame."""
        from doeff.effects.reader import LocalEffect

        effect = LocalEffect(
            env_update={"key": "value"},
            sub_program=Program.pure(42),
        )
        state_obj = CEKSState(
            C=EffectControl(effect),
            E=FrozenDict({"existing": "data"}),
            S={},
            K=[],
        )

        result = step(state_obj)

        assert isinstance(result, CEKSState)
        assert result.E["key"] == "value"
        assert result.E["existing"] == "data"
        assert len(result.K) == 1
        assert isinstance(result.K[0], LocalFrame)


# ============================================================================
# Test Step Function - Value Propagation Through Frames
# ============================================================================


class TestStepValuePropagation:
    """Test value propagation through continuation frames."""

    def test_value_through_catch_frame(self):
        """Value passes through CatchFrame unchanged."""
        frame = CatchFrame(handler=lambda e: Program.pure(0), saved_env=FrozenDict())
        state_obj = CEKSState(
            C=Value(42),
            E=FrozenDict({"temp": "data"}),
            S={},
            K=[frame],
        )

        result = step(state_obj)

        assert isinstance(result, CEKSState)
        assert isinstance(result.C, Value)
        assert result.C.v == 42
        assert result.K == []

    def test_value_through_recover_frame_wraps_in_ok(self):
        """Value through RecoverFrame becomes Ok(value)."""
        frame = RecoverFrame(saved_env=FrozenDict())
        state_obj = CEKSState(
            C=Value(42),
            E=FrozenDict(),
            S={},
            K=[frame],
        )

        result = step(state_obj)

        assert isinstance(result, CEKSState)
        assert isinstance(result.C, Value)
        assert isinstance(result.C.v, Ok)
        assert result.C.v.value == 42

    def test_value_through_local_frame_restores_env(self):
        """Value through LocalFrame restores saved environment."""
        original_env = FrozenDict({"original": "env"})
        frame = LocalFrame(restore_env=original_env)
        state_obj = CEKSState(
            C=Value(42),
            E=FrozenDict({"modified": "env"}),
            S={},
            K=[frame],
        )

        result = step(state_obj)

        assert isinstance(result, CEKSState)
        assert result.E == original_env
        assert isinstance(result.C, Value)
        assert result.C.v == 42


# ============================================================================
# Test Step Function - Error Propagation Through Frames
# ============================================================================


class TestStepErrorPropagation:
    """Test error propagation through continuation frames."""

    def test_error_through_catch_frame_invokes_handler(self):
        """Error through CatchFrame invokes handler."""
        handler = lambda e: Program.pure(f"recovered: {e}")
        frame = CatchFrame(handler=handler, saved_env=FrozenDict())
        exc = ValueError("test error")
        state_obj = CEKSState(
            C=Error(exc),
            E=FrozenDict(),
            S={},
            K=[frame],
        )

        result = step(state_obj)

        assert isinstance(result, CEKSState)
        assert isinstance(result.C, ProgramControl)
        # Handler was called

    def test_error_through_recover_frame_wraps_in_err(self):
        """Error through RecoverFrame becomes Err(exception)."""
        frame = RecoverFrame(saved_env=FrozenDict())
        exc = ValueError("test error")
        state_obj = CEKSState(
            C=Error(exc),
            E=FrozenDict(),
            S={},
            K=[frame],
        )

        result = step(state_obj)

        assert isinstance(result, CEKSState)
        assert isinstance(result.C, Value)
        assert isinstance(result.C.v, Err)
        assert result.C.v.error is exc

    def test_error_through_local_frame_restores_env_and_propagates(self):
        """Error through LocalFrame restores env and continues propagating."""
        original_env = FrozenDict({"original": "env"})
        frame = LocalFrame(restore_env=original_env)
        exc = ValueError("test error")
        state_obj = CEKSState(
            C=Error(exc),
            E=FrozenDict({"modified": "env"}),
            S={},
            K=[frame],
        )

        result = step(state_obj)

        assert isinstance(result, CEKSState)
        assert result.E == original_env
        assert isinstance(result.C, Error)
        assert result.C.ex is exc


# ============================================================================
# Test Step Function - Intercept Frame
# ============================================================================


class TestStepInterception:
    """Test effect interception."""

    def test_intercept_frame_presence(self):
        """has_intercept_frame detects InterceptFrame."""
        K = [InterceptFrame(transforms=())]
        assert has_intercept_frame(K) is True

        K_empty = []
        assert has_intercept_frame(K_empty) is False

    def test_find_intercept_frame_index(self):
        """find_intercept_frame_index returns correct index."""
        K = [
            LocalFrame(restore_env=FrozenDict()),
            InterceptFrame(transforms=()),
            CatchFrame(handler=lambda e: Program.pure(0), saved_env=FrozenDict()),
        ]
        assert find_intercept_frame_index(K) == 1


# ============================================================================
# Test Main Loop
# ============================================================================


class TestMainLoop:
    """Test the main interpreter loop."""

    @pytest.mark.asyncio
    async def test_run_simple_value(self):
        """Run a program that returns a simple value."""
        program = Program.pure(42)

        result = await run(program)

        assert isinstance(result, Ok)
        assert result.value == 42

    @pytest.mark.asyncio
    async def test_run_with_state_effects(self):
        """Run a program using state effects."""

        @do
        def program():
            yield state.Put("counter", 10)
            value = yield state.Get("counter")
            yield state.Put("counter", value + 5)
            final = yield state.Get("counter")
            return final

        result = await run(program())

        assert isinstance(result, Ok)
        assert result.value == 15

    @pytest.mark.asyncio
    async def test_run_with_ask_effect(self):
        """Run a program using Ask effect."""

        @do
        def program():
            value = yield reader.Ask("config")
            return f"got: {value}"

        env = FrozenDict({"config": "hello"})
        result = await run(program(), env=env)

        assert isinstance(result, Ok)
        assert result.value == "got: hello"

    @pytest.mark.asyncio
    async def test_run_with_tell_effect(self):
        """Run a program using Tell effect."""

        @do
        def program():
            yield writer.Tell("log entry 1")
            yield writer.Tell("log entry 2")
            return "done"

        result = await run(program())

        assert isinstance(result, Ok)
        assert result.value == "done"

    @pytest.mark.asyncio
    async def test_run_error_propagation(self):
        """Run a program that raises an error."""

        @do
        def program():
            yield state.Put("x", 1)
            raise ValueError("test error")

        result = await run(program())

        assert isinstance(result, Err)
        assert isinstance(result.error, ValueError)

    @pytest.mark.asyncio
    async def test_run_catch_effect(self):
        """Run a program with error catching."""
        from doeff.effects.result import Catch

        @do
        def failing():
            raise ValueError("oops")

        @do
        def program():
            result = yield Catch(
                failing(),
                handler=lambda e: Program.pure(f"caught: {type(e).__name__}"),
            )
            return result

        result = await run(program())

        assert isinstance(result, Ok)
        assert result.value == "caught: ValueError"

    @pytest.mark.asyncio
    async def test_run_safe_effect_success(self):
        """Run a program with Safe effect on success."""
        from doeff.effects.result import Safe

        @do
        def program():
            result = yield Safe(Program.pure(42))
            return result

        result = await run(program())

        assert isinstance(result, Ok)
        inner_result = result.value
        assert isinstance(inner_result, Ok)
        assert inner_result.value == 42

    @pytest.mark.asyncio
    async def test_run_safe_effect_failure(self):
        """Run a program with Safe effect on failure."""
        from doeff.effects.result import Safe

        @do
        def failing():
            raise ValueError("oops")

        @do
        def program():
            result = yield Safe(failing())
            return result

        result = await run(program())

        assert isinstance(result, Ok)
        inner_result = result.value
        assert isinstance(inner_result, Err)
        assert isinstance(inner_result.error, ValueError)

    @pytest.mark.asyncio
    async def test_run_local_effect(self):
        """Run a program with Local effect."""
        from doeff.effects.reader import Local

        @do
        def program():
            outer = yield reader.Ask("value")

            @do
            def inner():
                return (yield reader.Ask("value"))

            inner_result = yield Local({"value": "inner"}, inner())
            final = yield reader.Ask("value")
            return (outer, inner_result, final)

        env = FrozenDict({"value": "outer"})
        result = await run(program(), env=env)

        assert isinstance(result, Ok)
        assert result.value == ("outer", "inner", "outer")


class TestSyncRun:
    """Test synchronous run wrapper."""

    def test_run_sync_simple(self):
        """run_sync works for simple programs."""
        program = Program.pure(42)

        result = run_sync(program)

        assert isinstance(result, Ok)
        assert result.value == 42


# ============================================================================
# Test Integration with @do decorator
# ============================================================================


class TestDoDecoratorIntegration:
    """Test CESK machine integration with @do decorated functions."""

    @pytest.mark.asyncio
    async def test_nested_do_functions(self):
        """CESK machine handles nested @do functions."""

        @do
        def inner(x: int):
            yield state.Put("temp", x * 2)
            return (yield state.Get("temp"))

        @do
        def outer():
            yield state.Put("counter", 0)
            result1 = yield inner(5)
            result2 = yield inner(10)
            return (result1, result2)

        result = await run(outer())

        assert isinstance(result, Ok)
        assert result.value == (10, 20)

    @pytest.mark.asyncio
    async def test_generator_exception_handling(self):
        """CESK machine properly throws exceptions into generators."""

        @do
        def program():
            try:
                yield reader.Ask("missing")
            except KeyError:
                return "caught keyerror"

        result = await run(program())

        assert isinstance(result, Ok)
        assert result.value == "caught keyerror"


class TestProgramParallelEffect:
    """Test ProgramParallelEffect for program-level parallelism."""

    @pytest.mark.asyncio
    async def test_program_parallel_effect_basic(self):
        """ProgramParallelEffect runs programs in parallel."""
        from doeff.cesk import ProgramParallelEffect

        @do
        def program():
            results = yield ProgramParallelEffect(
                programs=(
                    Program.pure(1),
                    Program.pure(2),
                    Program.pure(3),
                )
            )
            return results

        result = await run(program())

        assert isinstance(result, Ok)
        assert result.value == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_program_parallel_effect_empty(self):
        """ProgramParallelEffect with empty programs returns empty list."""
        from doeff.cesk import ProgramParallelEffect

        @do
        def program():
            results = yield ProgramParallelEffect(programs=())
            return results

        result = await run(program())

        assert isinstance(result, Ok)
        assert result.value == []

    @pytest.mark.asyncio
    async def test_program_parallel_effect_with_state_merging(self):
        """ProgramParallelEffect merges child state back in program order."""
        from doeff.cesk import ProgramParallelEffect

        @do
        def child1():
            yield state.Put("x", 100)
            yield state.Put("from_child1", "yes")
            return (yield state.Get("x"))

        @do
        def child2():
            yield state.Put("x", 200)  # Overwrites child1's x (program order: last wins)
            yield state.Put("from_child2", "yes")
            return (yield state.Get("x"))

        @do
        def program():
            yield state.Put("x", 0)
            results = yield ProgramParallelEffect(
                programs=(child1(), child2())
            )
            # After merge: x=200 (child2 last), both child keys present
            parent_x = yield state.Get("x")
            c1 = yield state.Get("from_child1")
            c2 = yield state.Get("from_child2")
            return (results, parent_x, c1, c2)

        result = await run(program())

        assert isinstance(result, Ok)
        results, parent_x, c1, c2 = result.value
        assert results == [100, 200]
        assert parent_x == 200  # Last-write-wins in program order
        assert c1 == "yes"  # Merged from child1
        assert c2 == "yes"  # Merged from child2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
