from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.cesk.frames import ContinueValue, FrameResult
from doeff.effects.gather import GatherEffect
from doeff.effects.race import RaceEffect

if TYPE_CHECKING:
    from doeff.cesk.runtime.context import HandlerContext


def handle_gather(
    effect: GatherEffect,
    ctx: HandlerContext,
) -> FrameResult:
    if not effect.futures:
        return ContinueValue(
            value=[],
            env=ctx.task_state.env,
            store=ctx.store,
            k=ctx.task_state.kontinuation,
        )

    raise NotImplementedError(
        "Gather with Futures not supported in SyncRuntime. Use AsyncRuntime."
    )


def handle_race(
    effect: RaceEffect,
    ctx: HandlerContext,
) -> FrameResult:
    raise NotImplementedError(
        "Race not supported in SyncRuntime. Use AsyncRuntime for Spawn/Race."
    )


__all__ = [
    "handle_gather",
    "handle_race",
]
