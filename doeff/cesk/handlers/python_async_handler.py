"""Python async handler for async/await effects.

This handler produces PythonAsyncSyntaxEscape DIRECTLY for Python async effects.
Per spec (SPEC-CESK-EFFECT-BOUNDARIES.md): Python async escape is SEPARATE from
task scheduling. The scheduler intercepts escapes only when multi-task coordination
is needed.

Single-task: Escape goes directly to runner
Multi-task: Scheduler intercepts escape, coordinates, produces multi-task escape
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from doeff._types_internal import EffectBase
from doeff.cesk.result import python_async_escape
from doeff.cesk.state import CESKState
from doeff.program import Program

if TYPE_CHECKING:
    from doeff.cesk.handler_frame import HandlerContext


def python_async_handler(effect: EffectBase, ctx: "HandlerContext"):
    """Handle async/await effects by returning PythonAsyncSyntaxEscape directly.
    
    Per SPEC-CESK-EFFECT-BOUNDARIES.md: This handler returns PythonAsyncSyntaxEscape
    DIRECTLY, not via _SchedulerSuspendForIO. Python async escape and task scheduling
    are SEPARATE concerns.
    
    When running with multi-task scheduler:
    - The escape bubbles up through the handler stack
    - TaskSchedulerHandler's HandlerFrame intercepts it
    - Scheduler coordinates multi-task async
    
    When running without scheduler (single-task):
    - The escape goes directly to the runner
    - Runner awaits and resumes
    """
    from doeff.effects.future import FutureAwaitEffect
    from doeff.effects.time import DelayEffect, WaitUntilEffect
    
    if isinstance(effect, FutureAwaitEffect):
        # Return escape directly - scheduler intercepts if needed
        return Program.pure(python_async_escape(
            awaitable=effect.awaitable,
            stored_k=list(ctx.delimited_k),
            stored_env=ctx.env,
            stored_store=dict(ctx.store),
        ))
    
    if isinstance(effect, DelayEffect):
        async def do_delay() -> None:
            await asyncio.sleep(effect.seconds)
        
        return Program.pure(python_async_escape(
            awaitable=do_delay(),
            stored_k=list(ctx.delimited_k),
            stored_env=ctx.env,
            stored_store=dict(ctx.store),
        ))
    
    if isinstance(effect, WaitUntilEffect):
        async def do_wait_until() -> None:
            now = datetime.now()
            if effect.target_time > now:
                delay_seconds = (effect.target_time - now).total_seconds()
                await asyncio.sleep(delay_seconds)
        
        return Program.pure(python_async_escape(
            awaitable=do_wait_until(),
            stored_k=list(ctx.delimited_k),
            stored_env=ctx.env,
            stored_store=dict(ctx.store),
        ))
    
    # Unhandled effect - forward to outer handlers
    from doeff.do import do

    @do
    def forward_effect():
        result = yield effect
        # Return plain value - HandlerResultFrame constructs CESKState with current store
        return result

    return forward_effect()


# Backwards compatibility alias (deprecated)
async_effects_handler = python_async_handler


__all__ = [
    "async_effects_handler",
    "python_async_handler",
]
