"""Tests for effect stack trace construction."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from doeff.do import do
from doeff.effects import Catch, Fail, Spawn
from doeff.interpreter_v2 import TrampolinedInterpreter
from doeff.types import EffectStackFrameType


@pytest.mark.asyncio
async def test_simple_kleisli_chain() -> None:
    @do
    def inner() -> Generator[object, object, int]:
        yield Fail(ValueError("boom"))
        return 0

    @do
    def outer() -> Generator[object, object, int]:
        return (yield inner())

    engine = TrampolinedInterpreter()
    result = await engine.run_async(outer())

    assert result.is_err
    trace = result.effect_stack_trace
    assert trace is not None

    names = [
        frame.name
        for frame in trace.frames
        if frame.frame_type == EffectStackFrameType.KLEISLI_CALL
    ]
    assert "outer" in names
    assert "inner" in names


@pytest.mark.asyncio
async def test_effect_yield_captured() -> None:
    @do
    def failer() -> Generator[object, object, None]:
        yield Fail(ValueError("boom"))
        return None

    engine = TrampolinedInterpreter()
    result = await engine.run_async(failer())

    assert result.is_err
    trace = result.effect_stack_trace
    assert trace is not None
    assert trace.frames
    assert trace.frames[-1].frame_type == EffectStackFrameType.EFFECT_YIELD
    assert trace.frames[-1].name == "ResultFailEffect"


@pytest.mark.asyncio
async def test_handler_boundary_shown() -> None:
    @do
    def failing() -> Generator[object, object, int]:
        yield Fail(ValueError("boom"))
        return 0

    @do
    def handler_program() -> Generator[object, object, int]:
        yield Fail(RuntimeError("handler boom"))
        return 0

    @do
    def program() -> Generator[object, object, int]:
        return (yield Catch(failing(), lambda err: handler_program()))

    engine = TrampolinedInterpreter()
    result = await engine.run_async(program())

    assert result.is_err
    trace = result.effect_stack_trace
    assert trace is not None

    handler_frames = [
        frame
        for frame in trace.frames
        if frame.frame_type == EffectStackFrameType.HANDLER_BOUNDARY
    ]
    assert handler_frames


@pytest.mark.asyncio
async def test_spawn_boundary_preserved() -> None:
    @do
    def child() -> Generator[object, object, int]:
        raise RuntimeError("spawned failure")

    @do
    def parent() -> Generator[object, object, int]:
        task = yield Spawn(child(), preferred_backend="thread")
        return (yield task.join())

    engine = TrampolinedInterpreter(allow_reentrancy=True)
    result = await engine.run_async(parent())

    assert result.is_err
    trace = result.effect_stack_trace
    assert trace is not None
    spawn_frames = [
        frame
        for frame in trace.frames
        if frame.frame_type == EffectStackFrameType.SPAWN_BOUNDARY
    ]
    assert spawn_frames
