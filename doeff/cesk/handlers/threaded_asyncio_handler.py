"""Threaded asyncio handler for running async I/O in sync_run.

This handler intercepts SuspendForIOEffect and executes the awaitable
in a background asyncio thread, enabling non-blocking I/O without
requiring async/await in user code.

Usage:
    from doeff.cesk.run import sync_run, sync_handlers_preset

    @do
    def my_program():
        response = yield Await(aiohttp.get("https://api.example.com"))
        yield Delay(1.0)  # Non-blocking sleep
        return response

    # sync_handlers_preset includes sync_await_handler which uses this
    result = sync_run(my_program(), sync_handlers_preset)

Architecture:
    The handler sits between async_effects_handler and scheduler_handler:

    User yields: Await(coro) or Delay(seconds)
         ↓
    async_effects_handler: converts to SuspendForIOEffect(awaitable=coro)
         ↓
    threaded_asyncio_handler: intercepts, runs in background thread, returns result
         ↓
    (SuspendForIOEffect never reaches scheduler_handler when using this handler)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff._types_internal import EffectBase
from doeff.cesk.state import CESKState
from doeff.cesk.threading.asyncio_thread import get_asyncio_thread
from doeff.do import do

if TYPE_CHECKING:
    from doeff.cesk.handler_frame import HandlerContext


def threaded_asyncio_handler(effect: EffectBase, ctx: HandlerContext):
    """Handle async effects by delegating to background asyncio thread.

    This handler intercepts _AsyncEscapeIntercepted and executes the awaitable
    in a dedicated background thread running an asyncio event loop.

    Per SPEC-CESK-EFFECT-BOUNDARIES.md: python_async_syntax_escape_handler returns
    PythonAsyncSyntaxEscape directly. When that escape bubbles through
    handlers, HandlerFrame.on_value invokes the handler with
    _AsyncEscapeIntercepted. This handler intercepts that, runs the
    awaitable in a background thread, and resumes synchronously.

    Effects Handled:
        - _AsyncEscapeIntercepted: Executes escape's awaitable in background thread
        - _SchedulerSuspendForIO: Legacy support (deprecated)

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
    from doeff.effects.scheduler_internal import _AsyncEscapeIntercepted, _SchedulerSuspendForIO
    from doeff.program import Program

    if isinstance(effect, _AsyncEscapeIntercepted):
        from doeff.cesk.result import DirectState

        escape = effect.escape
        awaitable = escape.awaitable

        if awaitable is None:
            @do
            def forward_multi_task_escape():
                result = yield effect
                # Return plain value - HandlerResultFrame constructs CESKState with current store
                return result

            return forward_multi_task_escape()

        thread = get_asyncio_thread()

        try:
            result = thread.submit(awaitable)
            # escape.resume may return DirectState (if wrapped by HandlerResultFrame)
            # or CESKState (if not wrapped). Ensure we return DirectState for HRF passthrough.
            resume_result = escape.resume(result, ctx.store)
            if isinstance(resume_result, DirectState):
                return Program.pure(resume_result)
            return Program.pure(DirectState(resume_result))
        except Exception as e:
            # Same for error case
            error_result = escape.resume_error(e)
            if isinstance(error_result, DirectState):
                return Program.pure(error_result)
            return Program.pure(DirectState(error_result))

    if isinstance(effect, _SchedulerSuspendForIO):
        # Legacy support (deprecated)
        awaitable = effect.awaitable
        thread = get_asyncio_thread()

        try:
            result = thread.submit(awaitable)
            return Program.pure(CESKState.with_value(result, ctx.env, ctx.store, ctx.k))
        except Exception as e:
            return Program.pure(CESKState.with_error(e, ctx.env, ctx.store, ctx.k))

    # Forward other effects to outer handlers
    @do
    def forward_effect():
        result = yield effect
        # Return plain value - HandlerResultFrame constructs CESKState with current store
        return result

    return forward_effect()


def wrap_with_threaded_async(program: Any) -> Any:
    """Wrap program with threaded asyncio stack for Await/Delay in SyncRuntime."""
    from typing import cast

    from doeff.cesk.handler_frame import Handler, WithHandler
    from doeff.cesk.handlers.python_async_syntax_escape_handler import python_async_syntax_escape_handler

    return WithHandler(
        handler=cast(Handler, threaded_asyncio_handler),
        program=WithHandler(
            handler=cast(Handler, python_async_syntax_escape_handler),
            program=program,
        ),
    )


__all__ = [
    "threaded_asyncio_handler",
    "wrap_with_threaded_async",
]
