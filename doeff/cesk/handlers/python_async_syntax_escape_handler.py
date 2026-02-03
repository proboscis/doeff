"""Handler that bridges doeff's Await effect to Python's asyncio.

!! THIS IS THE ONLY HANDLER THAT MAY PRODUCE PythonAsyncSyntaxEscape !!

This handler exists because asyncio.create_task() requires a running event loop.
When users explicitly choose async_run and do `yield Await(coroutine)`, we must
escape from the CESK machine to execute create_task in async_run's context.

HOW IT WORKS
------------
1. Creates an ExternalPromise for the scheduler to track
2. Yields PythonAsyncSyntaxEscape with action=lambda: asyncio.create_task(...)
3. async_run executes the action (fire and forget)
4. The task runs in background, completes the promise when done
5. Waits on the promise via normal Wait effect
6. Returns the result to the caller

EXCLUSIVITY
-----------
This handler is the SOLE producer of PythonAsyncSyntaxEscape. No other handler
may produce this type. This is an architectural constraint, not a guideline.

EFFECT HANDLED
--------------
- PythonAsyncioAwaitEffect: yield Await(coroutine)
- DelayEffect: yield Delay(seconds)
- WaitUntilEffect: yield WaitUntil(datetime)

For sync_run, use threaded_asyncio_handler instead (no escape needed).

See SPEC-CESK-005-simplify-async-escape.md for full architecture.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
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
    """Handle Await effect by creating asyncio task with ExternalPromise.

    !! SOLE PRODUCER OF PythonAsyncSyntaxEscape !!

    This is the ONLY handler that may produce PythonAsyncSyntaxEscape.
    Do not create additional handlers that produce this type.

    Flow:
    1. Create ExternalPromise for scheduler coordination
    2. Yield escape with action to create asyncio task
    3. async_run executes action (fire and forget)
    4. Task runs in background, completes promise when done
    5. Wait on promise (scheduler handles coordination)
    6. Return result

    For sync_run, use threaded_asyncio_handler instead (no escape needed).
    """
    from doeff.effects.future import PythonAsyncioAwaitEffect
    from doeff.effects.time import DelayEffect, WaitUntilEffect

    if isinstance(effect, PythonAsyncioAwaitEffect):
        # Create external promise for scheduler coordination
        promise = yield CreateExternalPromise()

        # Create async function that awaits and completes promise
        awaitable = effect.awaitable

        async def fire_task():
            try:
                result = await awaitable
                promise.complete(result)
            except BaseException as e:
                promise.fail(e)

        def create_task():
            return asyncio.create_task(fire_task())

        # Escape to async_run to create the task
        yield PythonAsyncSyntaxEscape(
            action=create_task
        )

        # Wait on promise (scheduler handles coordination)
        result = yield Wait(promise.future)
        return result

    if isinstance(effect, DelayEffect):
        # Create external promise for scheduler coordination
        promise = yield CreateExternalPromise()

        async def fire_delay():
            try:
                await asyncio.sleep(effect.seconds)
                promise.complete(None)
            except BaseException as e:
                promise.fail(e)

        # Escape to async_run to create the task
        yield PythonAsyncSyntaxEscape(
            action=lambda: asyncio.create_task(fire_delay())
        )

        # Wait on promise
        yield Wait(promise.future)
        return None

    if isinstance(effect, WaitUntilEffect):
        # Create external promise for scheduler coordination
        promise = yield CreateExternalPromise()

        async def fire_wait_until():
            try:
                now = datetime.now()
                if effect.target_time > now:
                    delay_seconds = (effect.target_time - now).total_seconds()
                    await asyncio.sleep(delay_seconds)
                promise.complete(None)
            except BaseException as e:
                promise.fail(e)

        # Escape to async_run to create the task
        yield PythonAsyncSyntaxEscape(
            action=lambda: asyncio.create_task(fire_wait_until())
        )

        # Wait on promise
        yield Wait(promise.future)
        return None

    # Unhandled effect - forward to outer handlers
    result = yield effect
    return result


# Backwards compatibility aliases (deprecated)
python_async_handler = python_async_syntax_escape_handler
async_effects_handler = python_async_syntax_escape_handler


__all__ = [
    "python_async_syntax_escape_handler",
    # Deprecated aliases
    "async_effects_handler",
    "python_async_handler",
]
