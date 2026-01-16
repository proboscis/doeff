"""Time-related effect handlers.

Handles effects for time operations:
- DelayEffect: Wait for a duration
- WaitUntilEffect: Wait until a specific time
- GetTimeEffect: Get current time
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from doeff.cesk.actions import WaitForDuration, WaitUntilTime
from doeff.cesk.handlers import HandlerContext, HandlerResult

if TYPE_CHECKING:
    from doeff.effects import DelayEffect, GetTimeEffect, WaitUntilEffect


def handle_delay(effect: DelayEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle DelayEffect: wait for a duration.

    Returns a WaitForDuration action that the runtime will handle.
    For simulation runtime, this advances simulated time.
    For asyncio runtime, this does an async sleep.
    """
    return HandlerResult((WaitForDuration(effect.seconds),))


def handle_wait_until(effect: WaitUntilEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle WaitUntilEffect: wait until a specific time.

    Returns a WaitUntilTime action that the runtime will handle.
    The target time is converted to Unix timestamp.
    """
    target = effect.target_time.timestamp()
    return HandlerResult((WaitUntilTime(target),))


def handle_get_time(effect: GetTimeEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle GetTimeEffect: get current time.

    Returns the current time from the store's __current_time__ key
    if running in simulation mode, otherwise uses real time.

    Note: The runtime may override this to provide simulated time.
    """
    # Check for simulated time first
    sim_time = ctx.store.get("__current_time__")
    if sim_time is not None:
        from datetime import datetime, timezone

        return HandlerResult.resume(datetime.fromtimestamp(sim_time, tz=timezone.utc))

    # Use real time
    from datetime import datetime, timezone

    return HandlerResult.resume(datetime.now(timezone.utc))


__all__ = [
    "handle_delay",
    "handle_wait_until",
    "handle_get_time",
]
