"""Task coordination effect handlers: gather, race."""

from __future__ import annotations

from doeff.cesk.frames import ContinueProgram, ContinueValue, FrameResult, GatherFrame, RaceFrame
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store, TaskId
from doeff.effects.gather import GatherEffect


def handle_gather(
    effect: GatherEffect,
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
    effect: GatherEffect,
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
