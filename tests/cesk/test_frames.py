"""Unit tests for doeff.cesk.frames module."""

from __future__ import annotations

from typing import Any

import pytest
from doeff._vendor import FrozenDict
from doeff.cesk.frames import (
    Continue,
    Frame,
    FrameProtocol,
    FrameResult,
    GatherFrame,
    InterceptFrame,
    JoinFrame,
    Kontinuation,
    ListenFrame,
    LocalFrame,
    MultiGatherFrame,
    PopAndContinue,
    Propagate,
    RaceFrame,
    ReturnFrame,
    SafeFrame,
)
from doeff.cesk.types import Environment, FutureId


class TestFrameResultTypes:
    """Tests for FrameResult types."""

    def test_continue_result(self) -> None:
        """Continue holds control and environment."""
        from doeff.cesk.state import Value

        env: Environment = FrozenDict({"x": 1})
        result = Continue(control=Value(42), env=env)

        assert result.control == Value(42)
        assert result.env == env
        assert result.actions == ()

    def test_continue_with_actions(self) -> None:
        """Continue can hold actions."""
        from doeff.cesk.state import Value

        env: Environment = FrozenDict()
        result = Continue(control=Value(42), env=env, actions=("action1", "action2"))

        assert len(result.actions) == 2

    def test_pop_and_continue_result(self) -> None:
        """PopAndContinue signals frame removal."""
        from doeff.cesk.state import Value

        env: Environment = FrozenDict()
        result = PopAndContinue(control=Value(42), env=env)

        assert result.control == Value(42)
        assert result.actions == ()

    def test_propagate_result(self) -> None:
        """Propagate holds error information."""
        ex = ValueError("test error")
        result = Propagate(error=ex, captured_traceback=None)

        assert result.error is ex
        assert result.captured_traceback is None


class TestLocalFrame:
    """Tests for LocalFrame."""

    def test_on_value_restores_env(self) -> None:
        """on_value restores the saved environment."""
        restore_env: Environment = FrozenDict({"x": 1})
        current_env: Environment = FrozenDict({"x": 2, "y": 3})
        frame = LocalFrame(restore_env=restore_env)

        result = frame.on_value(42, current_env, {})

        assert isinstance(result, PopAndContinue)
        assert result.control == 42
        assert result.env == restore_env

    def test_on_error_propagates(self) -> None:
        """on_error propagates error with restored env."""
        restore_env: Environment = FrozenDict({"x": 1})
        frame = LocalFrame(restore_env=restore_env)
        ex = ValueError("test")

        result = frame.on_error(ex, None, FrozenDict(), {})

        assert isinstance(result, Propagate)
        assert result.error is ex


class TestInterceptFrame:
    """Tests for InterceptFrame."""

    def test_on_value_passes_through(self) -> None:
        """on_value passes value unchanged."""
        transform = lambda e: e
        frame = InterceptFrame(transforms=(transform,))
        env: Environment = FrozenDict()

        result = frame.on_value(42, env, {})

        assert isinstance(result, PopAndContinue)
        assert result.control == 42

    def test_on_error_propagates(self) -> None:
        """on_error propagates unchanged."""
        transform = lambda e: e
        frame = InterceptFrame(transforms=(transform,))
        ex = ValueError("test")

        result = frame.on_error(ex, None, FrozenDict(), {})

        assert isinstance(result, Propagate)
        assert result.error is ex


class TestListenFrame:
    """Tests for ListenFrame."""

    def test_on_value_captures_logs(self) -> None:
        """on_value captures logs since start index."""
        frame = ListenFrame(log_start_index=2)
        env: Environment = FrozenDict()
        store = {"__log__": ["log1", "log2", "log3", "log4"]}

        result = frame.on_value(42, env, store)

        assert isinstance(result, PopAndContinue)
        # Result should be ListenResult with captured logs
        from doeff._types_internal import ListenResult

        assert isinstance(result.control, ListenResult)
        assert result.control.value == 42
        assert list(result.control.log) == ["log3", "log4"]

    def test_on_value_empty_logs(self) -> None:
        """on_value handles empty log."""
        frame = ListenFrame(log_start_index=0)
        env: Environment = FrozenDict()
        store: dict[str, Any] = {}

        result = frame.on_value(42, env, store)

        assert isinstance(result, PopAndContinue)
        from doeff._types_internal import ListenResult

        assert isinstance(result.control, ListenResult)
        assert list(result.control.log) == []

    def test_on_error_propagates(self) -> None:
        """on_error propagates without capturing."""
        frame = ListenFrame(log_start_index=0)
        ex = ValueError("test")

        result = frame.on_error(ex, None, FrozenDict(), {})

        assert isinstance(result, Propagate)


class TestSafeFrame:
    """Tests for SafeFrame."""

    def test_on_value_wraps_in_ok(self) -> None:
        """on_value wraps result in Ok."""
        env: Environment = FrozenDict({"x": 1})
        frame = SafeFrame(saved_env=env)

        result = frame.on_value(42, FrozenDict(), {})

        assert isinstance(result, PopAndContinue)
        from doeff._vendor import Ok

        assert isinstance(result.control, Ok)
        assert result.control.unwrap() == 42
        assert result.env == env

    def test_on_error_wraps_in_err(self) -> None:
        """on_error wraps error in Err."""
        env: Environment = FrozenDict({"x": 1})
        frame = SafeFrame(saved_env=env)
        ex = ValueError("test error")

        result = frame.on_error(ex, None, FrozenDict(), {})

        assert isinstance(result, PopAndContinue)
        from doeff._vendor import Err

        assert isinstance(result.control, Err)
        assert result.control.error is ex
        assert result.env == env


class TestGatherFrame:
    """Tests for GatherFrame."""

    def test_on_value_collects_and_continues(self) -> None:
        """on_value with remaining programs returns Continue."""
        from doeff import do
        from doeff.effects import PureEffect

        @do
        def prog() -> int:
            yield from PureEffect(1)
            return 1

        env: Environment = FrozenDict()
        frame = GatherFrame(
            remaining_programs=[prog()],
            collected_results=[42],
            saved_env=env,
        )

        result = frame.on_value(100, env, {})

        assert isinstance(result, Continue)
        from doeff.cesk.state import ProgramControl

        assert isinstance(result.control, ProgramControl)

    def test_on_value_finishes(self) -> None:
        """on_value with no remaining programs returns all results."""
        env: Environment = FrozenDict()
        frame = GatherFrame(
            remaining_programs=[],
            collected_results=[1, 2],
            saved_env=env,
        )

        result = frame.on_value(3, env, {})

        assert isinstance(result, PopAndContinue)
        assert result.control == [1, 2, 3]

    def test_on_error_aborts(self) -> None:
        """on_error aborts gather."""
        env: Environment = FrozenDict()
        frame = GatherFrame(
            remaining_programs=[],
            collected_results=[1],
            saved_env=env,
        )
        ex = ValueError("test")

        result = frame.on_error(ex, None, env, {})

        assert isinstance(result, Propagate)
        assert result.error is ex


class TestJoinFrame:
    """Tests for JoinFrame."""

    def test_on_value(self) -> None:
        """on_value returns value with saved env."""
        env: Environment = FrozenDict({"x": 1})
        frame = JoinFrame(future_id=FutureId(1), saved_env=env)

        result = frame.on_value(42, FrozenDict(), {})

        assert isinstance(result, PopAndContinue)
        assert result.control == 42
        assert result.env == env

    def test_on_error(self) -> None:
        """on_error propagates."""
        env: Environment = FrozenDict()
        frame = JoinFrame(future_id=FutureId(1), saved_env=env)
        ex = ValueError("test")

        result = frame.on_error(ex, None, FrozenDict(), {})

        assert isinstance(result, Propagate)


class TestMultiGatherFrame:
    """Tests for MultiGatherFrame."""

    def test_on_child_done_partial(self) -> None:
        """on_child_done returns updated frame when not all complete."""
        env: Environment = FrozenDict()
        f1, f2, f3 = FutureId(1), FutureId(2), FutureId(3)
        frame = MultiGatherFrame(
            future_ids=(f1, f2, f3),
            saved_env=env,
        )

        updated, result = frame.on_child_done(f1, 100, None)

        assert updated is not None
        assert result is None
        assert f1 in updated.completed_results
        assert updated.completed_results[f1] == 100

    def test_on_child_done_complete(self) -> None:
        """on_child_done returns results when all complete."""
        env: Environment = FrozenDict()
        f1, f2 = FutureId(1), FutureId(2)
        frame = MultiGatherFrame(
            future_ids=(f1, f2),
            completed_results={f1: 100},
            saved_env=env,
        )

        updated, result = frame.on_child_done(f2, 200, None)

        assert updated is None
        assert result is not None
        assert isinstance(result, PopAndContinue)
        assert result.control == [100, 200]  # Ordered by future_ids

    def test_on_child_done_error(self) -> None:
        """on_child_done propagates first error."""
        env: Environment = FrozenDict()
        f1, f2 = FutureId(1), FutureId(2)
        frame = MultiGatherFrame(
            future_ids=(f1, f2),
            saved_env=env,
        )
        ex = ValueError("task failed")

        updated, result = frame.on_child_done(f1, None, ex)

        assert updated is None
        assert result is not None
        assert isinstance(result, Propagate)
        assert result.error is ex


class TestRaceFrame:
    """Tests for RaceFrame."""

    def test_on_child_done_value(self) -> None:
        """on_child_done returns winning value."""
        env: Environment = FrozenDict()
        f1, f2 = FutureId(1), FutureId(2)
        frame = RaceFrame(future_ids=(f1, f2), saved_env=env)

        updated, result = frame.on_child_done(f2, 42, None)

        assert updated is None
        assert isinstance(result, PopAndContinue)
        assert result.control == 42

    def test_on_child_done_error(self) -> None:
        """on_child_done propagates first error."""
        env: Environment = FrozenDict()
        f1, f2 = FutureId(1), FutureId(2)
        frame = RaceFrame(future_ids=(f1, f2), saved_env=env)
        ex = ValueError("first error")

        updated, result = frame.on_child_done(f1, None, ex)

        assert updated is None
        assert isinstance(result, Propagate)
        assert result.error is ex


class TestFrameProtocol:
    """Tests for FrameProtocol compliance."""

    def test_local_frame_implements_protocol(self) -> None:
        """LocalFrame implements FrameProtocol."""
        env: Environment = FrozenDict()
        frame = LocalFrame(restore_env=env)
        assert isinstance(frame, FrameProtocol)

    def test_intercept_frame_implements_protocol(self) -> None:
        """InterceptFrame implements FrameProtocol."""
        frame = InterceptFrame(transforms=())
        assert isinstance(frame, FrameProtocol)

    def test_listen_frame_implements_protocol(self) -> None:
        """ListenFrame implements FrameProtocol."""
        frame = ListenFrame(log_start_index=0)
        assert isinstance(frame, FrameProtocol)

    def test_safe_frame_implements_protocol(self) -> None:
        """SafeFrame implements FrameProtocol."""
        env: Environment = FrozenDict()
        frame = SafeFrame(saved_env=env)
        assert isinstance(frame, FrameProtocol)

    def test_join_frame_implements_protocol(self) -> None:
        """JoinFrame implements FrameProtocol."""
        env: Environment = FrozenDict()
        frame = JoinFrame(future_id=FutureId(1), saved_env=env)
        assert isinstance(frame, FrameProtocol)

    def test_race_frame_implements_protocol(self) -> None:
        """RaceFrame implements FrameProtocol."""
        env: Environment = FrozenDict()
        frame = RaceFrame(future_ids=(FutureId(1),), saved_env=env)
        assert isinstance(frame, FrameProtocol)


class TestReturnFrame:
    """Tests for ReturnFrame."""

    def test_return_frame_creation(self) -> None:
        """ReturnFrame can be created with generator and env."""

        def gen():
            yield 1
            return 2

        env: Environment = FrozenDict()
        g = gen()
        frame = ReturnFrame(generator=g, saved_env=env)

        assert frame.generator is g
        assert frame.saved_env == env
        assert frame.program_call is None


class TestKontinuation:
    """Tests for Kontinuation type."""

    def test_kontinuation_is_list(self) -> None:
        """Kontinuation is list of frames."""
        env: Environment = FrozenDict()
        k: Kontinuation = [
            LocalFrame(restore_env=env),
            SafeFrame(saved_env=env),
        ]

        assert len(k) == 2
        assert isinstance(k[0], LocalFrame)
        assert isinstance(k[1], SafeFrame)

    def test_empty_kontinuation(self) -> None:
        """Empty kontinuation is valid."""
        k: Kontinuation = []
        assert len(k) == 0
