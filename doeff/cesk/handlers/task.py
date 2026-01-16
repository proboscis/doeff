"""Task effect handlers: Gather, Race."""

from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.cesk.frames import ContinueProgram, GatherFrame

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk.frames import FrameResult
    from doeff.cesk.state import TaskState
    from doeff.cesk.types import Store


def handle_gather(effect: EffectBase, task: TaskState, store: Store) -> FrameResult:
    from doeff.effects.gather import GatherEffect

    if not isinstance(effect, GatherEffect):
        raise TypeError(f"Expected GatherEffect, got {type(effect).__name__}")

    programs = list(effect.programs)

    if not programs:
        from doeff.cesk.frames import ContinueValue

        return ContinueValue(
            value=[],
            env=task.env,
            store=store,
            k=task.kontinuation,
        )

    first_prog, *remaining = programs

    return ContinueProgram(
        program=first_prog,
        env=task.env,
        store=store,
        k=[
            GatherFrame(
                remaining_programs=remaining,
                collected_results=[],
                saved_env=task.env,
            )
        ]
        + task.kontinuation,
    )


def handle_race(effect: EffectBase, task: TaskState, store: Store) -> FrameResult:
    raise NotImplementedError(
        "handle_race requires multi-task runtime support (ISSUE-CORE-458)"
    )


__all__ = [
    "handle_gather",
    "handle_race",
]
