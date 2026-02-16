"""Future/async effects."""

from __future__ import annotations

import atexit
import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
import threading
from typing import Any

import doeff_vm

from .external_promise import CreateExternalPromise
from .gather import gather

from ._validators import ensure_awaitable
from .base import Effect, EffectBase, create_effect_with_trace


@dataclass(frozen=True)
class PythonAsyncioAwaitEffect(EffectBase):
    """Await a Python asyncio awaitable.

    This effect is specifically for Python's asyncio awaitables (coroutines,
    Tasks, Futures). It is NOT a generic "future" abstraction.

    Handled by:
    - python_async_syntax_escape_handler (async_run): produces PythonAsyncSyntaxEscape
    - sync_await_handler (sync_run): runs in background thread

    Usage:
        result = yield Await(some_coroutine())
    """

    awaitable: Awaitable[Any]

    def __post_init__(self) -> None:
        ensure_awaitable(self.awaitable, name="awaitable")


@dataclass(frozen=True)
class AllTasksSuspendedEffect(EffectBase):
    """Signal that all tasks are suspended waiting for I/O.

    Used by the scheduler when all tasks are blocked on async I/O
    and the runtime needs to use asyncio.wait to await them all.
    """

    pending_io: dict[Any, Any]
    store: dict[str, Any]


# NOTE: For parallel execution, use asyncio.create_task + Await + Gather pattern
# See the doeff documentation for examples of concurrent execution patterns


_loop_lock = threading.Lock()
_loop_thread: threading.Thread | None = None
_loop: asyncio.AbstractEventLoop | None = None


def _shutdown_background_loop() -> None:
    global _loop
    global _loop_thread

    loop = _loop
    thread = _loop_thread
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(loop.stop)
    if thread is not None:
        thread.join(timeout=1.0)
    if loop is not None:
        try:
            loop.close()
        except Exception:
            pass
    _loop = None
    _loop_thread = None


def _ensure_background_loop() -> asyncio.AbstractEventLoop:
    global _loop
    global _loop_thread

    if _loop is not None and _loop.is_running():
        return _loop

    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop

        loop = asyncio.new_event_loop()

        def _loop_main() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(target=_loop_main, daemon=True, name="doeff-await-bridge")
        thread.start()
        _loop = loop
        _loop_thread = thread
        atexit.register(_shutdown_background_loop)
        return loop


def _submit_awaitable(awaitable: Awaitable[Any], promise: Any) -> None:
    loop = _ensure_background_loop()

    async def _run() -> Any:
        return await awaitable

    future = asyncio.run_coroutine_threadsafe(_run(), loop)

    def _on_done(completed: Any) -> None:
        try:
            promise.complete(completed.result())
        except BaseException as exc:  # pragma: no cover - defensive bridge path
            promise.fail(exc)

    future.add_done_callback(_on_done)


def sync_await_handler(effect: Any, k: Any):
    """Handle Await effects via background-loop bridge (sync-compatible)."""
    if isinstance(effect, PythonAsyncioAwaitEffect):
        promise = yield CreateExternalPromise()
        _submit_awaitable(effect.awaitable, promise)
        values = yield gather(promise.future)
        return (yield doeff_vm.Resume(k, values[0]))

    yield doeff_vm.Delegate()


def python_async_syntax_escape_handler(effect: Any, k: Any):
    """Handle Await effects in async mode using PythonAsyncSyntaxEscape kickoff.

    The actual awaitable is executed on a background loop thread so scheduler
    waits can be satisfied without depending on the host event loop.
    """
    if isinstance(effect, PythonAsyncioAwaitEffect):
        promise = yield CreateExternalPromise()

        async def _kickoff() -> None:
            _submit_awaitable(effect.awaitable, promise)

        _ = yield doeff_vm.PythonAsyncSyntaxEscape(action=_kickoff)
        values = yield gather(promise.future)
        return (yield doeff_vm.Resume(k, values[0]))

    yield doeff_vm.Delegate()


def await_(awaitable: Awaitable[Any]) -> PythonAsyncioAwaitEffect:
    return create_effect_with_trace(PythonAsyncioAwaitEffect(awaitable=awaitable))


def Await(awaitable: Awaitable[Any]) -> Effect:
    return create_effect_with_trace(PythonAsyncioAwaitEffect(awaitable=awaitable), skip_frames=3)


__all__ = [
    "AllTasksSuspendedEffect",
    "Await",
    "PythonAsyncioAwaitEffect",
    "await_",
    "python_async_syntax_escape_handler",
    "sync_await_handler",
]
