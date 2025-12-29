"""Tests for the trampolined interpreter and continuation invariants."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from doeff.do import do
from doeff.handlers import HandlerScope
from doeff.interpreter_v2 import (
    ContinuationFrame,
    FrameResult,
    FrameState,
    InterpreterState,
    InterpretationPhase,
    InterpretationStats,
    InvalidFrameStateError,
    TrampolinedInterpreter,
)
from doeff.program import Program
from doeff.types import ExecutionContext


def _simple_generator() -> Generator[int, int | None, int]:
    value = yield 1
    return value or 0


def _failing_generator() -> Generator[int, int | None, int]:
    yield 1
    raise ValueError("boom")


def _new_frame(gen: Generator[int, int | None, int]) -> ContinuationFrame:
    ctx = ExecutionContext()
    return ContinuationFrame(
        generator=gen,
        context_snapshot=ctx.copy(),
        handler_scope=HandlerScope.SHARED,
        source_info=None,
    )


def test_frame_state_transitions() -> None:
    frame = _new_frame(_simple_generator())

    result = frame.resume(None)
    assert isinstance(result, FrameResult.Yield)
    assert frame.state is FrameState.ACTIVE

    result = frame.resume(5)
    assert isinstance(result, FrameResult.Return)
    assert frame.state is FrameState.COMPLETED

    with pytest.raises(InvalidFrameStateError):
        frame.resume(1)


def test_frame_throw_marks_failed() -> None:
    frame = _new_frame(_failing_generator())
    assert isinstance(frame.resume(None), FrameResult.Yield)

    result = frame.throw(ValueError("boom"))
    assert isinstance(result, FrameResult.Raise)
    assert frame.state is FrameState.FAILED


def test_close_idempotent() -> None:
    frame = _new_frame(_simple_generator())
    frame.close()
    frame.close()
    frame.close()
    assert frame.state is FrameState.CANCELLED


def test_generator_not_accessible_after_close() -> None:
    frame = _new_frame(_simple_generator())
    frame.close()
    assert frame.generator.gi_frame is None


def test_context_snapshot_frozen() -> None:
    ctx = ExecutionContext(env={"key": "value"})
    frame = ContinuationFrame(
        generator=_simple_generator(),
        context_snapshot=ctx.copy(),
        handler_scope=HandlerScope.SHARED,
        source_info=None,
    )
    ctx.env["key"] = "changed"
    assert frame.context_snapshot.env["key"] == "value"


def test_stack_depth_tracking() -> None:
    ctx = ExecutionContext()
    state = InterpreterState(
        continuation_stack=[],
        current_item=None,
        context=ctx,
        phase=InterpretationPhase.INITIALIZING,
        stats=InterpretationStats(),
    )

    frame1 = _new_frame(_simple_generator())
    frame2 = _new_frame(_simple_generator())

    state.stats.total_frames_created += 2
    state.push_frame(frame1)
    state.push_frame(frame2)

    assert state.stack_depth == 2
    assert state.stats.max_stack_depth == 2
    assert frame2.is_current
    assert not frame1.is_current

    state.pop_frame()
    assert frame1.is_current


@pytest.mark.asyncio
async def test_stack_safety_deep_chain() -> None:
    depth = 10000
    program = Program.pure(0)
    for _ in range(depth):
        program = program.flat_map(lambda value: Program.pure(value + 1))

    engine = TrampolinedInterpreter()
    result = await engine.run_async(program)

    assert result.is_ok
    assert result.value == depth


@pytest.mark.asyncio
async def test_no_active_frames_after_completion() -> None:
    @do
    def program() -> Generator[int, None, int]:
        return 42

    engine = TrampolinedInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    state = engine._last_state
    assert state is not None
    assert not state.continuation_stack
