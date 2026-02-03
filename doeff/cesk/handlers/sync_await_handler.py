"""Synchronous await handler for running async effects in a background thread.

This handler is for SyncRuntime ONLY. It handles PythonAsyncioAwaitEffect
by running awaitables in a background asyncio thread.

Per SPEC-CESK-EFFECT-BOUNDARIES.md: SyncRunner should NEVER see PythonAsyncSyntaxEscape.
This handler handles Await directly, so no escape is ever produced.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from doeff._types_internal import EffectBase
from doeff.cesk.state import CESKState
from doeff.cesk.threading.asyncio_thread import get_asyncio_thread
from doeff.do import do
from doeff.program import Program

if TYPE_CHECKING:
    from doeff.cesk.handler_frame import HandlerContext


def sync_await_handler(effect: EffectBase, ctx: "HandlerContext"):
    """Handle PythonAsyncioAwaitEffect by running in background thread."""
    from doeff.effects.future import PythonAsyncioAwaitEffect

    if isinstance(effect, PythonAsyncioAwaitEffect):
        thread = get_asyncio_thread()
        try:
            result = thread.submit(effect.awaitable)
            return Program.pure(CESKState.with_value(result, ctx.env, ctx.store, ctx.k))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return Program.pure(CESKState.with_error(e, ctx.env, ctx.store, ctx.k))

    @do
    def forward_effect():
        result = yield effect
        return result

    return forward_effect()


__all__ = [
    "sync_await_handler",
]
