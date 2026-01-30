"""Call stack introspection effect handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from doeff._types_internal import CallFrame
from doeff.cesk.frames import ContinueError, ContinueValue, FrameResult, ReturnFrame
from doeff.effects.callstack import ProgramCallFrameEffect, ProgramCallStackEffect

if TYPE_CHECKING:
    from doeff.cesk.runtime.context import HandlerContext


def handle_program_call_frame(
    effect: ProgramCallFrameEffect,
    ctx: HandlerContext,
) -> FrameResult:
    frames = []
    for idx, frame in enumerate(ctx.task_state.kontinuation):
        if isinstance(frame, ReturnFrame) and frame.program_call is not None:
            pc = frame.program_call
            call_frame = CallFrame(
                kleisli=pc.kleisli_source,
                function_name=pc.function_name,
                args=pc.args,
                kwargs=pc.kwargs,
                depth=idx,
                created_at=pc.created_at,
            )
            frames.append(call_frame)

    depth = effect.depth
    if depth >= len(frames):
        return ContinueError(
            error=IndexError(f"Call stack depth {depth} exceeds available frames ({len(frames)})"),
            env=ctx.task_state.env,
            store=ctx.store,
            k=ctx.task_state.kontinuation,
        )

    return ContinueValue(
        value=frames[depth],
        env=ctx.task_state.env,
        store=ctx.store,
        k=ctx.task_state.kontinuation,
    )


def handle_program_call_stack(
    effect: ProgramCallStackEffect,
    ctx: HandlerContext,
) -> FrameResult:
    frames = []
    for idx, frame in enumerate(ctx.task_state.kontinuation):
        if isinstance(frame, ReturnFrame) and frame.program_call is not None:
            pc = frame.program_call
            call_frame = CallFrame(
                kleisli=pc.kleisli_source,
                function_name=pc.function_name,
                args=pc.args,
                kwargs=pc.kwargs,
                depth=idx,
                created_at=pc.created_at,
            )
            frames.append(call_frame)

    return ContinueValue(
        value=tuple(frames),
        env=ctx.task_state.env,
        store=ctx.store,
        k=ctx.task_state.kontinuation,
    )


__all__ = [
    "handle_program_call_frame",
    "handle_program_call_stack",
]
