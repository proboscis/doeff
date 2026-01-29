from __future__ import annotations

from doeff.cesk.frames import ContinueValue, FrameResult
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store
from doeff.effects.gather import GatherEffect
from doeff.effects.race import RaceEffect


def handle_gather(
    effect: GatherEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    if not effect.futures:
        return ContinueValue(
            value=[],
            env=task_state.env,
            store=store,
            k=task_state.kontinuation,
        )

    raise NotImplementedError(
        "Gather with Futures not supported in SyncRuntime. Use AsyncRuntime."
    )


def handle_race(
    effect: RaceEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    raise NotImplementedError(
        "Race not supported in SyncRuntime. Use AsyncRuntime for Spawn/Race."
    )


__all__ = [
    "handle_gather",
    "handle_race",
]
