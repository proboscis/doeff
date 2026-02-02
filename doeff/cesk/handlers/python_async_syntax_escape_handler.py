"""Handler that produces PythonAsyncSyntaxEscape for async_run.

!! THIS IS THE ONLY HANDLER THAT MAY PRODUCE PythonAsyncSyntaxEscape !!

This handler exists because Python's `await` is SYNTAX, not a function call.
When users explicitly choose async_run and do `yield Await(coroutine)`, we must
escape from the CESK machine to execute the await in Python's async runtime.

EXCLUSIVITY
-----------
This handler is the SOLE producer of PythonAsyncSyntaxEscape. No other handler
may produce this type. This is an architectural constraint, not a guideline.

If you think you need a new handler that produces PythonAsyncSyntaxEscape:
1. You are wrong
2. Rethink your approach
3. If blocking is needed, do it directly in the handler's generator:

    # WRONG - don't create new escape producers
    return Program.pure(PythonAsyncSyntaxEscape(...))

    # RIGHT - block directly
    result = blocking_call()  # next(gen) blocks until this returns

EFFECT HANDLED
--------------
- FutureAwaitEffect: yield Await(coroutine)

Other time-based effects (Delay, WaitUntil) should be implemented via Await,
not handled directly here. The current implementation handles them for backwards
compatibility but this should be migrated.

For sync_run, use threaded_asyncio_handler instead, which handles these effects
by running them in a background asyncio thread (no escape needed).

WHY NOT JUST BLOCK?
-------------------
Unlike custom blocking (queue.get(), etc.), Python's await MUST propagate up
to an async function. You cannot await inside a sync generator. This escape
is the bridge between doeff's sync CESK machine and Python's async runtime.
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
    """Convert Await effect to PythonAsyncSyntaxEscape for async_run.

    !! SOLE PRODUCER OF PythonAsyncSyntaxEscape !!

    This is the ONLY handler that may produce PythonAsyncSyntaxEscape.
    Do not create additional handlers that produce this type.

    Handled effect:
    - FutureAwaitEffect → escape with awaitable

    Note: DelayEffect and WaitUntilEffect are handled here for backwards
    compatibility, but should be migrated to use Await internally.

    All other effects are forwarded to outer handlers.

    For sync_run, use threaded_asyncio_handler instead (no escape needed).
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
