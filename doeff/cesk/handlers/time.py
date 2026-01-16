"""Time effect handlers: delay, get_time."""

from __future__ import annotations

from datetime import datetime

from doeff.cesk.frames import ContinueValue, FrameResult
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store
from doeff.effects.time import DelayEffect, GetTimeEffect


def handle_delay(
    effect: DelayEffect,
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
    effect: GetTimeEffect,
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
