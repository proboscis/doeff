"""Time effect handlers: delay, get_time, wait_until."""

from __future__ import annotations

import time
from datetime import datetime

from doeff.cesk.frames import ContinueValue, FrameResult
from doeff.cesk.state import TaskState
from doeff.cesk.types import Store
from doeff.effects.time import DelayEffect, GetTimeEffect, WaitUntilEffect


def handle_delay(
    effect: DelayEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    time.sleep(effect.seconds)
    
    new_store = store
    if "__current_time__" in store:
        new_store = {**store, "__current_time__": datetime.now()}
    
    return ContinueValue(
        value=None,
        env=task_state.env,
        store=new_store,
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


def handle_wait_until(
    effect: WaitUntilEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    has_store_time = "__current_time__" in store
    current_time = store.get("__current_time__")
    if current_time is None:
        current_time = datetime.now()
    
    if effect.target_time > current_time:
        wait_seconds = (effect.target_time - current_time).total_seconds()
        time.sleep(wait_seconds)
    
    new_store = store
    if has_store_time:
        new_store = {**store, "__current_time__": datetime.now()}
    
    return ContinueValue(
        value=None,
        env=task_state.env,
        store=new_store,
        k=task_state.kontinuation,
    )


__all__ = [
    "handle_delay",
    "handle_get_time",
    "handle_wait_until",
]
