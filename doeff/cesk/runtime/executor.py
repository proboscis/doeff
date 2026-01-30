"""Async executor protocols and implementations for SyncRuntime external suspension."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


class AsyncExecutor(Protocol):
    def submit(
        self,
        awaitable: Awaitable[T],
        on_success: Callable[[T], None],
        on_error: Callable[[BaseException], None],
    ) -> None: ...

    def shutdown(self) -> None: ...


class ThreadedAsyncioExecutor:
    """Runs asyncio awaitables in a background thread.

    Creates a dedicated event loop in a background thread for executing
    async operations. When awaitables complete, callbacks are invoked
    on the background thread - callers should ensure thread safety.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._shutdown = False

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._started.set()
        self._loop.run_forever()

    def _ensure_started(self) -> asyncio.AbstractEventLoop:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            self._started.wait()
        assert self._loop is not None
        return self._loop

    def submit(
        self,
        awaitable: Awaitable[T],
        on_success: Callable[[T], None],
        on_error: Callable[[BaseException], None],
    ) -> None:
        loop = self._ensure_started()

        async def wrapper() -> None:
            try:
                result = await awaitable
                on_success(result)
            except BaseException as e:
                on_error(e)

        asyncio.run_coroutine_threadsafe(wrapper(), loop)

    def shutdown(self) -> None:
        if self._loop is not None and not self._shutdown:
            self._shutdown = True
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=5.0)


__all__ = [
    "AsyncExecutor",
    "ThreadedAsyncioExecutor",
]
