"""Time-related effect handlers: Delay, WaitUntil, GetTime."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from doeff.cesk.actions import Delay as DelayAction, Resume, WaitUntil as WaitUntilAction

if TYPE_CHECKING:
    from doeff.cesk.step import HandlerContext
    from doeff.effects import DelayEffect, GetTimeEffect, WaitUntilEffect


def handle_delay(effect: DelayEffect, ctx: HandlerContext) -> tuple[DelayAction, ...]:
    return (DelayAction(timedelta(seconds=effect.seconds)),)


def handle_wait_until(effect: WaitUntilEffect, ctx: HandlerContext) -> tuple[WaitUntilAction, ...]:
    return (WaitUntilAction(effect.target_time),)


def handle_get_time(effect: GetTimeEffect, ctx: HandlerContext) -> tuple[Resume, ...]:
    return (Resume(datetime.now()),)


__all__ = [
    "handle_delay",
    "handle_wait_until",
    "handle_get_time",
]
