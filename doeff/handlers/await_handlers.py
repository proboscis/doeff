"""Await handlers for sync and async VM drivers."""


import asyncio
import threading
from typing import Any

import doeff_vm

from doeff.do import do
from doeff.effects.base import Effect
from doeff.effects.external_promise import CreateExternalPromise
from doeff.effects.wait import Wait

PythonAsyncioAwaitEffect = doeff_vm.PythonAsyncioAwaitEffect

sync_await_handler = doeff_vm.sync_await_handler


def _run_awaitable_sync(awaitable: object) -> object:
    if asyncio.isfuture(awaitable):
        raise RuntimeError(
            "Await(asyncio.Future) is not safe under Spawn/Gather in sync run(); use "
            "CreateSemaphore or async_run"
        )

    qualname = getattr(awaitable, "__qualname__", None)
    if isinstance(qualname, str):
        loop_affine_prefixes = (
            "Semaphore",
            "BoundedSemaphore",
            "Lock",
            "Event",
            "Condition",
            "Queue",
            "PriorityQueue",
            "LifoQueue",
        )
        if any(qualname == prefix or qualname.startswith(f"{prefix}.") for prefix in loop_affine_prefixes):
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise RuntimeError(
                f"Await({qualname}) is not safe under Spawn/Gather in sync run(); use CreateSemaphore"
            )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result: dict[str, object] = {}

    def _worker() -> None:
        try:
            result["value"] = asyncio.run(awaitable)
        except BaseException as exc:
            result["error"] = exc

    thread = threading.Thread(target=_worker, name="doeff-await-bridge", daemon=True)
    thread.start()
    thread.join()

    if "error" in result:
        raise result["error"]  # type: ignore[misc]
    return result.get("value")


def _submit_awaitable(awaitable: object, promise: object) -> None:
    def _worker() -> None:
        try:
            result = _run_awaitable_sync(awaitable)
            promise.complete(result)
        except BaseException as exc:
            promise.fail(exc)

    thread = threading.Thread(target=_worker, name="doeff-await-bridge", daemon=True)
    thread.start()


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
        value = yield Wait(promise.future)
        return (yield doeff_vm.Resume(k, value))

    yield doeff_vm.Pass()


# Backward-compat alias. New code should use async_await_handler.
python_async_syntax_escape_handler = async_await_handler


__all__ = [
    "async_await_handler",
    "python_async_syntax_escape_handler",
    "sync_await_handler",
]
