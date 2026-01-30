"""Handler factories for external suspension effects.

This module provides factory functions that create handlers for effects
requiring external suspension (Await, Delay, WaitUntil). The handlers
close over an AsyncExecutor and use ctx.suspend to signal completion
from background threads.

Usage:
    executor = ThreadedAsyncioExecutor()
    handlers = {
        FutureAwaitEffect: make_await_handler(executor),
        DelayEffect: make_delay_handler(executor),
        WaitUntilEffect: make_wait_until_handler(executor),
    }
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from doeff.cesk.frames import ContinueError, FrameResult, SuspendOn

if TYPE_CHECKING:
    from doeff.cesk.handlers import Handler
    from doeff.cesk.runtime.context import HandlerContext
    from doeff.cesk.runtime.executor import AsyncExecutor
    from doeff.effects.future import FutureAwaitEffect
    from doeff.effects.time import DelayEffect, WaitUntilEffect


def make_await_handler(executor: AsyncExecutor) -> Handler:
    """Create a handler for FutureAwaitEffect that uses external suspension.

    Args:
        executor: The AsyncExecutor to submit awaitables to.

    Returns:
        A handler function that can be registered for FutureAwaitEffect.

    The handler:
    1. Submits the awaitable to the executor with ctx.suspend callbacks
    2. Returns SuspendOn to signal the runtime to park the task
    3. When the awaitable completes, ctx.suspend.complete/fail wakes the task
    """

    def handle_await(effect: FutureAwaitEffect, ctx: HandlerContext) -> FrameResult:
        if ctx.suspend is None:
            return ContinueError(
                error=RuntimeError(
                    "Await effect requires suspension support. "
                    "Ensure the runtime provides a SuspensionHandle via ctx.suspend."
                ),
                env=ctx.task_state.env,
                store=ctx.store,
                k=ctx.task_state.kontinuation,
            )

        executor.submit(effect.awaitable, ctx.suspend.complete, ctx.suspend.fail)
        return SuspendOn()

    return handle_await


def make_delay_handler(executor: AsyncExecutor) -> Handler:
    """Create a handler for DelayEffect that uses external suspension.

    Args:
        executor: The AsyncExecutor to submit the delay to.

    Returns:
        A handler function that can be registered for DelayEffect.

    The handler submits an asyncio.sleep coroutine to the executor.
    """

    def handle_delay(effect: DelayEffect, ctx: HandlerContext) -> FrameResult:
        if ctx.suspend is None:
            return ContinueError(
                error=RuntimeError(
                    "Delay effect requires suspension support. "
                    "Ensure the runtime provides a SuspensionHandle via ctx.suspend."
                ),
                env=ctx.task_state.env,
                store=ctx.store,
                k=ctx.task_state.kontinuation,
            )

        async def do_delay() -> None:
            await asyncio.sleep(effect.seconds)

        def on_success(_: Any) -> None:
            ctx.suspend.complete(None)  # type: ignore[union-attr]

        def on_error(error: BaseException) -> None:
            ctx.suspend.fail(error)  # type: ignore[union-attr]

        executor.submit(do_delay(), on_success, on_error)
        return SuspendOn()

    return handle_delay


def make_wait_until_handler(executor: AsyncExecutor) -> Handler:
    """Create a handler for WaitUntilEffect that uses external suspension.

    Args:
        executor: The AsyncExecutor to submit the wait to.

    Returns:
        A handler function that can be registered for WaitUntilEffect.

    The handler calculates the delay until the target time and submits
    an asyncio.sleep coroutine to the executor.
    """

    def handle_wait_until(effect: WaitUntilEffect, ctx: HandlerContext) -> FrameResult:
        if ctx.suspend is None:
            return ContinueError(
                error=RuntimeError(
                    "WaitUntil effect requires suspension support. "
                    "Ensure the runtime provides a SuspensionHandle via ctx.suspend."
                ),
                env=ctx.task_state.env,
                store=ctx.store,
                k=ctx.task_state.kontinuation,
            )

        async def do_wait_until() -> None:
            now = datetime.now()
            if effect.target_time > now:
                delay_seconds = (effect.target_time - now).total_seconds()
                await asyncio.sleep(delay_seconds)

        def on_success(_: Any) -> None:
            ctx.suspend.complete(None)  # type: ignore[union-attr]

        def on_error(error: BaseException) -> None:
            ctx.suspend.fail(error)  # type: ignore[union-attr]

        executor.submit(do_wait_until(), on_success, on_error)
        return SuspendOn()

    return handle_wait_until


__all__ = [
    "make_await_handler",
    "make_delay_handler",
    "make_wait_until_handler",
]
