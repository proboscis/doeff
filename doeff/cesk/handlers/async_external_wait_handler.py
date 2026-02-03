"""Async handler for WaitForExternalCompletion - run_in_executor via escape.

This handler is used in async_run's handler preset. When the scheduler yields
WaitForExternalCompletion (no runnable tasks, external promises pending),
this handler yields a PythonAsyncSyntaxEscape that uses run_in_executor
to do the blocking queue.get() in a thread pool without blocking the
asyncio event loop.

This allows asyncio tasks (started by python_async_syntax_escape_handler)
to continue running and complete their promises while we wait.

See SPEC-CESK-004-handler-owned-blocking.md for architecture.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from doeff._types_internal import EffectBase
from doeff.cesk.result import PythonAsyncSyntaxEscape
from doeff.do import do
from doeff.effects.scheduler_internal import WaitForExternalCompletion

if TYPE_CHECKING:
    from doeff.cesk.handler_frame import HandlerContext


@do
def async_external_wait_handler(effect: EffectBase, ctx: "HandlerContext"):
    """Handle WaitForExternalCompletion via run_in_executor escape.

    This is the async version - it escapes to async_run which awaits
    a thread pool executor doing the blocking queue.get().

    The flow:
    1. Handler yields PythonAsyncSyntaxEscape(action=wait_async)
    2. step() wraps action to return CESKState
    3. async_run awaits: state = await escape.action()
    4. Meanwhile, asyncio tasks can run and complete promises
    5. When queue.get() returns, handler receives the item via yield
    """
    if isinstance(effect, WaitForExternalCompletion):
        q = effect.queue

        async def wait_async():
            loop = asyncio.get_running_loop()
            # Run blocking queue.get() in thread pool - doesn't block event loop
            # Returns VALUE (step() wraps to return CESKState)
            return await loop.run_in_executor(None, q.get)

        # Yield escape - step() wraps action to return CESKState
        # Handler receives the queue item directly via yield
        item = yield PythonAsyncSyntaxEscape(action=wait_async)
        return item

    # Forward other effects to outer handlers
    result = yield effect
    return result


__all__ = [
    "async_external_wait_handler",
]
