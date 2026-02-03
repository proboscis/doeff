"""Handler that bridges doeff's Await effect to Python's asyncio.

!! THIS IS THE ONLY HANDLER THAT MAY PRODUCE PythonAsyncSyntaxEscape !!

This handler exists because asyncio.create_task() requires a running event loop.
When users explicitly choose async_run and do `yield Await(coroutine)`, we must
escape from the CESK machine to execute create_task in async_run's context.

See SPEC-CESK-005-simplify-async-escape.md for full architecture.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from doeff._types_internal import EffectBase
from doeff.cesk.result import PythonAsyncSyntaxEscape
from doeff.do import do
from doeff.effects.external_promise import CreateExternalPromise
from doeff.effects.wait import Wait

if TYPE_CHECKING:
    from doeff.cesk.handler_frame import HandlerContext


@do
def python_async_syntax_escape_handler(effect: EffectBase, ctx: "HandlerContext"):
    """Handle PythonAsyncioAwaitEffect by creating asyncio task with ExternalPromise."""
    from doeff.effects.future import PythonAsyncioAwaitEffect

    if isinstance(effect, PythonAsyncioAwaitEffect):
        promise = yield CreateExternalPromise()
        awaitable = effect.awaitable

        async def fire_task():
            try:
                result = await awaitable
                promise.complete(result)
            except BaseException as e:
                promise.fail(e)

        def create_task():
            return asyncio.create_task(fire_task())

        yield PythonAsyncSyntaxEscape(action=create_task)
        result = yield Wait(promise.future)
        return result

    result = yield effect
    return result


python_async_handler = python_async_syntax_escape_handler
async_effects_handler = python_async_syntax_escape_handler


__all__ = [
    "python_async_syntax_escape_handler",
    "async_effects_handler",
    "python_async_handler",
]
