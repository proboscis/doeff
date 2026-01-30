"""Time effect handlers: delay, get_time, wait_until."""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING

from doeff.cesk.frames import ContinueValue, FrameResult
from doeff.effects.time import DelayEffect, GetTimeEffect, WaitUntilEffect

if TYPE_CHECKING:
    from doeff.cesk.runtime.context import HandlerContext


def handle_delay(
    effect: DelayEffect,
    ctx: HandlerContext,
) -> FrameResult:
    time.sleep(effect.seconds)

    new_store = ctx.store
    if "__current_time__" in ctx.store:
        new_store = {**ctx.store, "__current_time__": datetime.now()}

    return ContinueValue(
        value=None,
        env=ctx.task_state.env,
        store=new_store,
        k=ctx.task_state.kontinuation,
    )


def handle_get_time(
    effect: GetTimeEffect,
    ctx: HandlerContext,
) -> FrameResult:
    current_time = ctx.store.get("__current_time__")
    if current_time is None:
        current_time = datetime.now()
    return ContinueValue(
        value=current_time,
        env=ctx.task_state.env,
        store=ctx.store,
        k=ctx.task_state.kontinuation,
    )


def handle_wait_until(
    effect: WaitUntilEffect,
    ctx: HandlerContext,
) -> FrameResult:
    has_store_time = "__current_time__" in ctx.store
    current_time = ctx.store.get("__current_time__")
    if current_time is None:
        current_time = datetime.now()

    if effect.target_time > current_time:
        wait_seconds = (effect.target_time - current_time).total_seconds()
        time.sleep(wait_seconds)

    new_store = ctx.store
    if has_store_time:
        new_store = {**ctx.store, "__current_time__": datetime.now()}

    return ContinueValue(
        value=None,
        env=ctx.task_state.env,
        store=new_store,
        k=ctx.task_state.kontinuation,
    )


__all__ = [
    "handle_delay",
    "handle_get_time",
    "handle_wait_until",
]
