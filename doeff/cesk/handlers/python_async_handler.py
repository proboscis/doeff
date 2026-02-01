from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from doeff._types_internal import EffectBase
from doeff.cesk.frames import ContinueValue, SuspendOn
from doeff.do import do
from doeff.program import Program

if TYPE_CHECKING:
    from doeff.cesk.handler_frame import HandlerContext


@do
def async_effects_handler(effect: EffectBase, ctx: "HandlerContext"):
    from doeff.effects.future import FutureAwaitEffect
    from doeff.effects.queue import SuspendForIOEffect
    from doeff.effects.time import DelayEffect, WaitUntilEffect
    
    if isinstance(effect, FutureAwaitEffect):
        result = yield SuspendForIOEffect(awaitable=effect.awaitable)
        return ContinueValue(
            value=result,
            env=ctx.env,
            store=None,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, DelayEffect):
        async def do_delay() -> None:
            await asyncio.sleep(effect.seconds)
        result = yield SuspendForIOEffect(awaitable=do_delay())
        return ContinueValue(
            value=result,
            env=ctx.env,
            store=None,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, WaitUntilEffect):
        async def do_wait_until() -> None:
            now = datetime.now()
            if effect.target_time > now:
                delay_seconds = (effect.target_time - now).total_seconds()
                await asyncio.sleep(delay_seconds)
        result = yield SuspendForIOEffect(awaitable=do_wait_until())
        return ContinueValue(
            value=result,
            env=ctx.env,
            store=None,
            k=ctx.delimited_k,
        )
    
    result = yield effect
    return ContinueValue(
        value=result,
        env=ctx.env,
        store=None,
        k=ctx.delimited_k,
    )


__all__ = [
    "async_effects_handler",
]
