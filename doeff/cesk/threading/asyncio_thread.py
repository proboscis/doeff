"""Dedicated asyncio event loop in a background thread.

This module provides AsyncioThread, which manages an asyncio event loop
running in a dedicated background thread. This enables synchronous code
to execute async operations without blocking the entire process.

Usage:
    thread = AsyncioThread()
    thread.start()

    # Submit coroutine and block until complete
    result = thread.submit(async_function())

    # Clean up
    thread.stop()

The module also provides get_asyncio_thread() for lazy singleton access.
"""

from __future__ import annotations

import asyncio
import atexit
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


class AsyncioThread:
    """Manages a dedicated asyncio event loop in a background thread.

    This class provides a thread-safe way to run async operations from
    synchronous code. The event loop runs in a daemon thread, allowing
    non-blocking I/O without requiring async/await in user code.

    Thread Safety:
        - start() is thread-safe and idempotent
        - submit() is thread-safe
        - stop() is thread-safe

    Lifecycle:
        - Lazy initialization (loop starts on first submit if not started)
        - Daemon thread (auto-cleanup on process exit)
        - Optional explicit cleanup via stop()

    Example:
        thread = AsyncioThread()
        thread.start()

        async def fetch_data():
            await asyncio.sleep(0.1)
            return "data"

        result = thread.submit(fetch_data())  # Blocks until complete
        print(result)  # "data"

        thread.stop()
    """

    def __init__(self) -> None:
        """Initialize the AsyncioThread.

        Does not start the thread - call start() or let submit() auto-start.
        """
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._stopped = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the background asyncio thread.

        This method is thread-safe and idempotent. Multiple calls have no
        effect if the thread is already running.

        Blocks until the event loop is ready to accept work.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return

            self._stopped.clear()
            self._started.clear()

            self._thread = threading.Thread(
                target=self._run_loop,
                name="doeff-asyncio-thread",
                daemon=True,
            )
            self._thread.start()

        # Wait for loop to be ready (outside lock to avoid deadlock)
        self._started.wait()

    def _run_loop(self) -> None:
        """Run the asyncio event loop in the background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._started.set()

        try:
            self._loop.run_forever()
        finally:
            # Clean up pending tasks
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()

            # Give tasks a chance to handle cancellation
            if pending:
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )

            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()
            self._loop = None
            self._stopped.set()

    def submit(self, coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
        """Submit a coroutine and block until complete.

        Auto-starts the thread if not already running.

        Args:
            coro: The coroutine to execute
            timeout: Optional timeout in seconds. If None, blocks indefinitely.

        Returns:
            The result of the coroutine

        Raises:
            RuntimeError: If the thread has been stopped
            TimeoutError: If timeout expires
            Exception: Any exception raised by the coroutine
        """
        # Auto-start if needed
        if self._thread is None or not self._thread.is_alive():
            self.start()

        if self._loop is None:
            raise RuntimeError("AsyncioThread not started or already stopped")

        # Submit coroutine to the background loop
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)

        try:
            return future.result(timeout=timeout)
        except asyncio.CancelledError as e:
            # Re-raise as-is to preserve the cancellation
            raise e

    def stop(self, timeout: float = 5.0) -> bool:
        """Stop the background loop and wait for thread to finish.

        Args:
            timeout: Maximum time to wait for thread to stop (default: 5.0s)

        Returns:
            True if thread stopped cleanly, False if timed out
        """
        with self._lock:
            if self._loop is None or self._thread is None:
                return True

            # Request loop to stop
            self._loop.call_soon_threadsafe(self._loop.stop)

        # Wait for thread to finish
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            stopped = not self._thread.is_alive()
            if stopped:
                self._thread = None
            return stopped

        return True

    @property
    def is_running(self) -> bool:
        """Check if the background thread is running."""
        return (
            self._thread is not None
            and self._thread.is_alive()
            and self._loop is not None
        )


# Global singleton for lazy initialization
_asyncio_thread: AsyncioThread | None = None
_asyncio_thread_lock = threading.Lock()


def get_asyncio_thread() -> AsyncioThread:
    """Get the global AsyncioThread singleton.

    Creates and starts the thread on first call. The thread is a daemon
    thread and will be cleaned up on process exit.

    Returns:
        The global AsyncioThread instance

    Thread Safety:
        This function is thread-safe.
    """
    global _asyncio_thread

    if _asyncio_thread is None:
        with _asyncio_thread_lock:
            if _asyncio_thread is None:
                _asyncio_thread = AsyncioThread()
                _asyncio_thread.start()
                # Register cleanup on process exit
                atexit.register(_cleanup_asyncio_thread)

    return _asyncio_thread


def _cleanup_asyncio_thread() -> None:
    """Clean up the global asyncio thread on exit."""
    global _asyncio_thread
    if _asyncio_thread is not None:
        _asyncio_thread.stop(timeout=2.0)
        _asyncio_thread = None


__all__ = [
    "AsyncioThread",
    "get_asyncio_thread",
]
