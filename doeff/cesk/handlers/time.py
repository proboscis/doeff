"""Time effect handlers: delay, get_time."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from doeff.cesk.frames import ContinueValue

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk.state import TaskState
    from doeff.cesk.types import Store
    from doeff.cesk.frames import FrameResult


def handle_delay(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    return ContinueValue(
        value=None,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


def handle_get_time(
    effect: EffectBase,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    current_time = store.get("__current_time__")
    if current_time is None:
        current_time = datetime.now()
    return ContinueValue(
        value=current_time,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


__all__ = [
    "handle_delay",
    "handle_get_time",
]
