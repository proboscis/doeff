"""Non-blocking await handler using ExternalPromise.

Handles PythonAsyncioAwaitEffect by submitting awaitables to a persistent
background asyncio event loop thread, returning results via ExternalPromise.

Unlike the built-in await_handler (which blocks the calling thread), this
handler is non-blocking from the scheduler's perspective — other spawned
tasks can make progress while an Await is pending.

Design:
    - A single daemon thread holds a persistent asyncio event loop.
    - All awaitables are submitted via asyncio.run_coroutine_threadsafe.
    - Results flow back through ExternalPromise.complete()/fail().
    - The handler is transparent: ``yield Await(coro())`` still returns the
      actual result (not a promise).

Usage::

    from doeff import do, run, default_handlers, Await
    from doeff.nonblocking_await import with_nonblocking_await

    @do
    def program():
        result = yield Await(some_coroutine())
        return result

    result = run(with_nonblocking_await(program()), handlers=default_handlers())

Note:
    If a single task does ``yield Await(...)``, that task will suspend at the
    ``Wait`` until the awaitable completes. To achieve concurrency, ``Spawn``
    multiple tasks that each ``yield Await(...)``.
"""

from __future__ import annotations

import asyncio
import atexit
import dataclasses
import threading
from typing import Any

import doeff_vm

from doeff.effects.external_promise import CreateExternalPromise
from doeff.effects.future import PythonAsyncioAwaitEffect
from doeff.effects.wait import Wait

# ---------------------------------------------------------------------------
# Singleton background event loop
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _LoopState:
    loop: asyncio.AbstractEventLoop | None = None
    thread: threading.Thread | None = None
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)


_state = _LoopState()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Return the singleton background event loop, starting it on first call."""
    if _state.loop is not None and _state.loop.is_running():
        return _state.loop

    with _state.lock:
        if _state.loop is not None and _state.loop.is_running():
            return _state.loop

        loop = asyncio.new_event_loop()

        def _run_loop() -> None:
            asyncio.set_event_loop(loop)
            try:
                loop.run_forever()
            finally:
                loop.close()

        thread = threading.Thread(
            target=_run_loop,
            daemon=True,
            name="doeff-async-bridge",
        )
        thread.start()

        _state.loop = loop
        _state.thread = thread

        atexit.register(_shutdown_loop)
        return loop


def _shutdown_loop() -> None:
    """Stop the background loop on interpreter exit."""
    loop = _state.loop
    thread = _state.thread
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(loop.stop)
    if thread is not None:
        thread.join(timeout=5.0)
    _state.loop = None
    _state.thread = None


def get_loop() -> asyncio.AbstractEventLoop:
    """Public accessor for the singleton background event loop."""
    return _ensure_loop()


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def _nonblocking_await_handler(effect: PythonAsyncioAwaitEffect, k: Any):
    """Handle PythonAsyncioAwaitEffect by submitting to the background loop."""
    if not isinstance(effect, PythonAsyncioAwaitEffect):
        yield doeff_vm.Pass()
        return

    promise = yield CreateExternalPromise()

    awaitable = effect.awaitable

    async def _run() -> Any:
        return await awaitable

    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(_run(), loop)

    def _on_done(fut: Any) -> None:
        try:
            result = fut.result()
            promise.complete(result)
        except BaseException as exc:
            promise.fail(exc)

    future.add_done_callback(_on_done)

    value = yield Wait(promise.future)

    return (yield doeff_vm.Resume(k, value))


def with_nonblocking_await(program: Any) -> Any:
    """Wrap a program to use non-blocking await handling.

    Installs a handler that intercepts ``PythonAsyncioAwaitEffect`` and runs
    awaitables on a persistent background event loop, communicating results
    via ExternalPromise.
    """
    from doeff import WithHandler

    return WithHandler(
        handler=_nonblocking_await_handler,
        expr=program,
    )


nonblocking_await_handler = _nonblocking_await_handler

__all__ = [
    "get_loop",
    "nonblocking_await_handler",
    "with_nonblocking_await",
]
