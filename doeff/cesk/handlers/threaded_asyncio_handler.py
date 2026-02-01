"""Threaded asyncio handler for running async I/O in SyncRuntime.

This handler intercepts SuspendForIOEffect and executes the awaitable
in a background asyncio thread, enabling non-blocking I/O without
requiring async/await in user code.

Usage:
    from doeff.cesk.runtime import SyncRuntime
    from doeff.cesk.handlers import threaded_asyncio_handler

    @do
    def my_program():
        response = yield Await(aiohttp.get("https://api.example.com"))
        yield Delay(1.0)  # Non-blocking sleep
        return response

    # Create runtime with threaded handler
    runtime = SyncRuntime()
    result = runtime.run(
        my_program(),
        handlers=[threaded_asyncio_handler]
    )

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
from doeff.cesk.frames import ContinueDirect, ContinueError, ContinueValue
from doeff.cesk.threading.asyncio_thread import get_asyncio_thread
from doeff.do import do

if TYPE_CHECKING:
    from doeff.cesk.handler_frame import HandlerContext


def threaded_asyncio_handler(effect: EffectBase, ctx: HandlerContext):
    """Handle async effects by delegating to background asyncio thread.

    This handler intercepts _AsyncEscapeIntercepted and executes the awaitable
    in a dedicated background thread running an asyncio event loop.

    Per SPEC-CESK-EFFECT-BOUNDARIES.md: python_async_handler returns
    PythonAsyncSyntaxEscape directly. When that escape bubbles through
    handlers, HandlerFrame.on_value invokes the handler with
    _AsyncEscapeIntercepted. This handler intercepts that, runs the
    awaitable in a background thread, and resumes synchronously.

    Effects Handled:
        - _AsyncEscapeIntercepted: Executes escape's awaitable in background thread
        - _SchedulerSuspendForIO: Legacy support (deprecated)

    All other effects are forwarded to outer handlers.

    Error Handling:
        - Exceptions from the awaitable are caught and returned as ContinueError
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
        ContinueValue with result, ContinueError on exception,
        or forwards to outer handler for unhandled effects
    """
    from doeff.effects.scheduler_internal import _AsyncEscapeIntercepted, _SchedulerSuspendForIO
    from doeff.program import Program

    if isinstance(effect, _AsyncEscapeIntercepted):
        escape = effect.escape
        awaitable = escape.awaitable
        
        if awaitable is None:
            @do
            def forward_multi_task_escape():
                result = yield effect
                return ContinueValue(
                    value=result,
                    env=ctx.env,
                    store=None,
                    k=ctx.delimited_k,
                )
            
            return forward_multi_task_escape()
        
        thread = get_asyncio_thread()

        try:
            result = thread.submit(awaitable)
            resumed_state = escape.resume(result, ctx.store)
            return Program.pure(ContinueDirect(
                value=resumed_state.C.v,
                env=resumed_state.E,
                store=resumed_state.S,
                k=list(resumed_state.K),
            ))
        except Exception as e:
            resumed_state = escape.resume_error(e)
            return Program.pure(ContinueError(
                error=resumed_state.C.ex,
                env=resumed_state.E,
                store=resumed_state.S,
                k=list(resumed_state.K),
            ))

    if isinstance(effect, _SchedulerSuspendForIO):
        # Legacy support (deprecated)
        awaitable = effect.awaitable
        thread = get_asyncio_thread()

        try:
            result = thread.submit(awaitable)
            return Program.pure(ContinueValue(
                value=result,
                env=ctx.env,
                store=None,  # Let outer handler manage store
                k=ctx.delimited_k,
            ))
        except Exception as e:
            return Program.pure(ContinueError(
                error=e,
                env=ctx.env,
                store=None,
                k=ctx.delimited_k,
            ))

    # Forward other effects to outer handlers
    from doeff.do import do
    
    @do
    def forward_effect():
        result = yield effect
        return ContinueValue(
            value=result,
            env=ctx.env,
            store=None,
            k=ctx.delimited_k,
        )
    
    return forward_effect()


def wrap_with_threaded_async(program: Any) -> Any:
    """Wrap program with threaded asyncio stack for Await/Delay in SyncRuntime."""
    from typing import cast

    from doeff.cesk.handler_frame import Handler, WithHandler
    from doeff.cesk.handlers.python_async_handler import python_async_handler

    return WithHandler(
        handler=cast(Handler, threaded_asyncio_handler),
        program=WithHandler(
            handler=cast(Handler, python_async_handler),
            program=program,
        ),
    )


__all__ = [
    "threaded_asyncio_handler",
    "wrap_with_threaded_async",
]
