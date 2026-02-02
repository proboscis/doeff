"""Synchronous await handler for running async effects in a background thread.

This handler is for SyncRuntime ONLY. It handles async effects (Await, Delay,
WaitUntil) by running them in a background asyncio thread, returning the result
directly as a CESKState.

Per SPEC-CESK-EFFECT-BOUNDARIES.md: SyncRunner should NEVER see PythonAsyncSyntaxEscape.
This handler handles Await directly, so no escape is ever produced.

Usage:
    # In SyncRuntime's handler stack:
    handlers = [
        scheduler_state_handler,
        task_scheduler_handler,
        sync_await_handler,  # <-- handles async effects directly
        core_handler,
    ]
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from doeff._types_internal import EffectBase
from doeff.cesk.state import CESKState
from doeff.cesk.threading.asyncio_thread import get_asyncio_thread
from doeff.do import do
from doeff.program import Program

if TYPE_CHECKING:
    from doeff.cesk.handler_frame import HandlerContext


def sync_await_handler(effect: EffectBase, ctx: "HandlerContext"):
    """Handle async effects by running them in a background thread.

    This handler is for SyncRuntime ONLY. It intercepts async effects and
    executes them in a dedicated asyncio background thread, returning the
    result directly. No PythonAsyncSyntaxEscape is ever produced.

    Effects Handled:
        - PythonAsyncioAwaitEffect: Runs the awaitable in background thread
        - DelayEffect: Sleeps in background thread
        - WaitUntilEffect: Waits until target time in background thread

    All other effects are forwarded to outer handlers.

    Error Handling:
        - Exceptions from the awaitable are caught and returned as error state
        - asyncio.CancelledError is re-raised as-is
        - TimeoutError from the background thread is propagated

    Thread Safety:
        - Only the awaitable is sent to the background thread
        - HandlerContext is NOT shared across threads
        - Result is returned via thread-safe future

    Args:
        effect: The effect to handle
        ctx: The handler context with store, env, and delimited_k

    Returns:
        CESKState with result or error, or forwards to outer handler for unhandled effects
    """
    from doeff.effects.future import PythonAsyncioAwaitEffect
    from doeff.effects.time import DelayEffect, WaitUntilEffect

    if isinstance(effect, PythonAsyncioAwaitEffect):
        thread = get_asyncio_thread()
        try:
            result = thread.submit(effect.awaitable)
            return Program.pure(CESKState.with_value(result, ctx.env, ctx.store, ctx.k))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return Program.pure(CESKState.with_error(e, ctx.env, ctx.store, ctx.k))

    if isinstance(effect, DelayEffect):
        async def do_delay() -> None:
            await asyncio.sleep(effect.seconds)

        thread = get_asyncio_thread()
        try:
            thread.submit(do_delay())
            return Program.pure(CESKState.with_value(None, ctx.env, ctx.store, ctx.k))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return Program.pure(CESKState.with_error(e, ctx.env, ctx.store, ctx.k))

    if isinstance(effect, WaitUntilEffect):
        async def do_wait_until() -> None:
            now = datetime.now()
            if effect.target_time > now:
                delay_seconds = (effect.target_time - now).total_seconds()
                await asyncio.sleep(delay_seconds)

        thread = get_asyncio_thread()
        try:
            thread.submit(do_wait_until())
            return Program.pure(CESKState.with_value(None, ctx.env, ctx.store, ctx.k))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return Program.pure(CESKState.with_error(e, ctx.env, ctx.store, ctx.k))

    # Unhandled effect - forward to outer handlers
    @do
    def forward_effect():
        result = yield effect
        return result

    return forward_effect()


__all__ = [
    "sync_await_handler",
]
