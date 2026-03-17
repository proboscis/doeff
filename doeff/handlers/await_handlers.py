"""Await handlers for sync and async VM drivers."""

import asyncio
import atexit
import threading
from collections.abc import Awaitable
from typing import Any

import doeff_vm

from doeff.do import do
from doeff.effects.base import Effect
from doeff.effects.external_promise import ExternalPromise
from doeff.effects.external_promise import CreateExternalPromise
from doeff.effects.wait import wait

PythonAsyncioAwaitEffect = doeff_vm.PythonAsyncioAwaitEffect
AWAIT_SHIM_ATTR = "__doeff_await_shim__"

_loop_lock = threading.Lock()
_loop_thread: threading.Thread | None = None
_loop: asyncio.AbstractEventLoop | None = None
_ASYNCIO_LOOP_AFFINE_CLASSES = frozenset(
    {
        "Semaphore",
        "BoundedSemaphore",
        "Lock",
        "Event",
        "Condition",
        "Queue",
        "PriorityQueue",
        "LifoQueue",
    }
)


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


def _detect_asyncio_loop_affine(awaitable: Awaitable[Any]) -> str | None:
    # Coroutines from class methods like Semaphore.acquire or Lock.acquire.
    if not asyncio.iscoroutine(awaitable):
        if isinstance(awaitable, asyncio.Future):
            return "asyncio.Future"
        return None

    qualname = getattr(awaitable, "__qualname__", "") or ""
    parts = qualname.split(".")
    if len(parts) >= 2 and parts[0] in _ASYNCIO_LOOP_AFFINE_CLASSES:
        return qualname

    if isinstance(awaitable, asyncio.Future):
        return "asyncio.Future"

    return None


def _submit_awaitable(awaitable: Awaitable[Any], promise: Any) -> None:
    loop = _ensure_background_loop()
    detected = _detect_asyncio_loop_affine(awaitable)
    if detected is not None:
        if asyncio.iscoroutine(awaitable):
            awaitable.close()
        raise RuntimeError(
            f"Await({detected}) is not safe under Spawn/Gather. "
            "asyncio synchronization primitives have thread-affine sync methods "
            "(release/set/notify/cancel) that silently break when called from a "
            "different thread than their event loop. "
            "Use doeff native effects instead:\n"
            "  sem = yield CreateSemaphore(n)\n"
            "  yield AcquireSemaphore(sem)\n"
            "  yield ReleaseSemaphore(sem)"
        )

    async def _run() -> object:
        return await awaitable

    future = asyncio.run_coroutine_threadsafe(_run(), loop)

    def _on_done(completed: Any) -> None:
        try:
            promise.complete(completed.result())
        except BaseException as exc:  # pragma: no cover - defensive bridge path
            promise.fail(exc)

    future.add_done_callback(_on_done)


def _external_promise_from_handle(handle: Any) -> ExternalPromise[Any]:
    if isinstance(handle, ExternalPromise):
        return handle

    # TODO(scheduler-gather-quadratic-v2): stop reconstructing ExternalPromise from
    # a raw handle dict once Rust returns a strongly typed bridge object directly.
    if not isinstance(handle, dict) or handle.get("type") != "ExternalPromise":
        raise TypeError(
            "Expected ExternalPromise handle dict or ExternalPromise instance, "
            f"got {type(handle).__name__}"
        )

    promise_id = handle.get("promise_id")
    if not isinstance(promise_id, int):
        raise TypeError("ExternalPromise handle missing integer promise_id")

    completion_queue = handle.get("completion_queue")
    if completion_queue is None:
        raise TypeError("ExternalPromise handle missing completion_queue")

    return ExternalPromise(
        _handle=handle,
        _completion_queue=completion_queue,
        _id=promise_id,
    )


def _submit_awaitable_handle(awaitable: Awaitable[Any], handle: Any) -> None:
    _submit_awaitable(awaitable, _external_promise_from_handle(handle))


@do
def sync_await_handler(effect: Effect, k: Any):
    """Handle Await effects via background-loop bridge for sync execution."""
    if isinstance(effect, PythonAsyncioAwaitEffect):
        promise = yield CreateExternalPromise()
        _submit_awaitable(effect.awaitable, promise)
        value = yield wait(promise.future)
        return (yield doeff_vm.Resume(k, value))

    yield doeff_vm.Pass()


setattr(sync_await_handler, AWAIT_SHIM_ATTR, True)

@do
def async_await_handler(effect: Effect, k: Any):
    """Handle Await effects in async execution on the caller event loop."""
    if isinstance(effect, PythonAsyncioAwaitEffect):
        promise = yield CreateExternalPromise()

        async def _run_and_complete() -> None:
            try:
                result = await effect.awaitable
                promise.complete(result)
            except BaseException as exc:
                promise.fail(exc)

        async def _kickoff() -> None:
            asyncio.get_running_loop().create_task(_run_and_complete())

        _ = yield doeff_vm.PythonAsyncSyntaxEscape(action=_kickoff)
        value = yield wait(promise.future)
        return (yield doeff_vm.Resume(k, value))

    yield doeff_vm.Pass()


setattr(async_await_handler, AWAIT_SHIM_ATTR, True)


# Backward-compat alias. New code should use async_await_handler.
python_async_syntax_escape_handler = async_await_handler


__all__ = [
    "async_await_handler",
    "python_async_syntax_escape_handler",
    "sync_await_handler",
]
