"""Call stack introspection effect handlers."""

from __future__ import annotations

from doeff._types_internal import CallFrame
from doeff.cesk.frames import ContinueValue, ContinueError, FrameResult, ReturnFrame
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store
from doeff.effects.callstack import ProgramCallFrameEffect, ProgramCallStackEffect


def handle_program_call_frame(
    effect: ProgramCallFrameEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    frames = []
    for idx, frame in enumerate(task_state.kontinuation):
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
            env=task_state.env,
            store=store,
            k=task_state.kontinuation,
        )

    return ContinueValue(
        value=frames[depth],
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


def handle_program_call_stack(
    effect: ProgramCallStackEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    frames = []
    for idx, frame in enumerate(task_state.kontinuation):
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
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


__all__ = [
    "handle_program_call_frame",
    "handle_program_call_stack",
]
