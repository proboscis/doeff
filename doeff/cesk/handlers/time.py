"""Time-related effect handlers: Delay, WaitUntil, GetTime."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from doeff.cesk.state import (
    BlockedStatus,
    ReadyStatus,
    TaskState,
    TimeCondition,
    ValueControl,
)

if TYPE_CHECKING:
    from doeff.cesk.frames import Kontinuation
    from doeff.cesk.types import Environment, Store


def handle_delay(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    current_time = store.get("__current_time__", datetime.now())
    wake_time = current_time + effect.duration
    return TaskState(
        control=ValueControl(None),
        env=env,
        store=store,
        kontinuation=k,
        status=BlockedStatus(TimeCondition(wake_time)),
    )


def handle_wait_until(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    target_time = effect.target
    return TaskState(
        control=ValueControl(None),
        env=env,
        store=store,
        kontinuation=k,
        status=BlockedStatus(TimeCondition(target_time)),
    )


def handle_get_time(effect: Any, k: Kontinuation, env: Environment, store: Store) -> TaskState:
    current_time = store.get("__current_time__", datetime.now())
    return TaskState(
        control=ValueControl(current_time),
        env=env,
        store=store,
        kontinuation=k,
        status=ReadyStatus(None),
    )


__all__ = [
    "handle_delay",
    "handle_get_time",
    "handle_wait_until",
]
