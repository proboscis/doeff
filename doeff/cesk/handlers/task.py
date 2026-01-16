"""Task coordination effect handlers: gather, race."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.frames import ContinueProgram, ContinueValue, GatherFrame, RaceFrame
from doeff.cesk.types import TaskId

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk.state import TaskState
    from doeff.cesk.types import Store
    from doeff.cesk.frames import FrameResult


def handle_gather(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    programs = list(effect.programs)
    if not programs:
        return ContinueValue(
            value=[],
            env=task_state.env,
            store=store,
            k=task_state.kontinuation,
        )
    first, *rest = programs
    return ContinueProgram(
        program=first,
        env=task_state.env,
        store=store,
        k=[GatherFrame(rest, [], task_state.env)] + task_state.kontinuation,
    )


def handle_race(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    programs = list(effect.programs)
    if not programs:
        raise ValueError("RaceEffect requires at least one program")
    
    first, *rest = programs
    task_ids = tuple(TaskId.new() for _ in programs)
    return ContinueProgram(
        program=first,
        env=task_state.env,
        store=store,
        k=[RaceFrame(task_ids, task_state.env)] + task_state.kontinuation,
    )


__all__ = [
    "handle_gather",
    "handle_race",
]
