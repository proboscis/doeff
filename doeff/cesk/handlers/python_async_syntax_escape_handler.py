"""Handler that produces PythonAsyncSyntaxEscape for async_run.

This handler is EXCLUSIVE to async_run. It converts async effects (Await, Delay,
WaitUntil) into PythonAsyncSyntaxEscape, signaling the async runtime to escape
from the CESK machine and use Python's native async/await.

For sync_run, use threaded_asyncio_handler instead, which handles async effects
by running them in a background asyncio thread (no escape needed).

Per SPEC-CESK-EFFECT-BOUNDARIES.md: Python async escape is SEPARATE from
task scheduling. The scheduler intercepts escapes only when multi-task
coordination is needed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from doeff._types_internal import EffectBase
from doeff.cesk.result import python_async_escape
from doeff.program import Program

if TYPE_CHECKING:
    from doeff.cesk.handler_frame import HandlerContext


def python_async_syntax_escape_handler(effect: EffectBase, ctx: "HandlerContext"):
    """Convert async effects to PythonAsyncSyntaxEscape for async_run.

    This handler is for async_run ONLY. It produces PythonAsyncSyntaxEscape
    which tells the async runtime to escape and await the coroutine directly.

    For sync_run, use threaded_asyncio_handler instead.

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
        return result

    return forward_effect()


# Backwards compatibility aliases (deprecated)
python_async_handler = python_async_syntax_escape_handler
async_effects_handler = python_async_syntax_escape_handler


__all__ = [
    "python_async_syntax_escape_handler",
    # Deprecated aliases
    "async_effects_handler",
    "python_async_handler",
]
