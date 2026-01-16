"""Time effect handlers: Delay, GetTime."""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING

from doeff.cesk.frames import ContinueValue

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk.frames import FrameResult
    from doeff.cesk.state import TaskState
    from doeff.cesk.types import Store


def handle_delay(effect: EffectBase, task: TaskState, store: Store) -> FrameResult:
    from doeff.effects.time import DelayEffect

    if not isinstance(effect, DelayEffect):
        raise TypeError(f"Expected DelayEffect, got {type(effect).__name__}")

    time.sleep(effect.seconds)

    return ContinueValue(
        value=None,
        env=task.env,
        store=store,
        k=task.kontinuation,
    )


def handle_get_time(effect: EffectBase, task: TaskState, store: Store) -> FrameResult:
    from doeff.effects.time import GetTimeEffect

    if not isinstance(effect, GetTimeEffect):
        raise TypeError(f"Expected GetTimeEffect, got {type(effect).__name__}")

    current_time = store.get("__current_time__")
    if current_time is None:
        current_time = datetime.now()

    return ContinueValue(
        value=current_time,
        env=task.env,
        store=store,
        k=task.kontinuation,
    )


__all__ = [
    "handle_delay",
    "handle_get_time",
]
