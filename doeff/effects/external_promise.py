"""ExternalPromise for external world integration.

ExternalPromise allows code outside the CESK machine (asyncio, threads, processes,
network) to complete a promise and wake up waiting doeff tasks.

IMPURE: ExternalPromise is explicitly impure. Do NOT use inside pure doeff programs.
Use regular Promise + CompletePromise/FailPromise effects for doeff-to-doeff communication.

See SPEC-EFF-010-external-promise.md for full design documentation.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, TypeVar
from uuid import UUID, uuid4

import doeff_vm

from doeff.effects.base import Effect, create_effect_with_trace

if TYPE_CHECKING:
    from doeff.effects.spawn import Future

T = TypeVar("T")


@dataclass
class ExternalPromise(Generic[T]):
    """Promise that can be completed from outside the CESK machine.

    IMPURE: This type is for bridging external world to doeff.
    Do NOT use inside pure doeff programs - use regular Promise instead.

    The completion methods (complete/fail) submit to a thread-safe queue
    that the scheduler checks during stepping.

    Attributes:
        _handle: Internal handle for scheduler waiter tracking
        _completion_queue: Reference to scheduler's thread-safe queue
        _id: Universal identifier (serializable for cross-process use)

    Example:
        @do
        def program():
            promise = yield CreateExternalPromise()

            # Pass promise to external code
            threading.Thread(target=worker, args=(promise,)).start()

            # Wait for external completion
            result = yield Wait(promise.future)
            return result

        def worker(promise):
            # Called from external thread
            result = do_work()
            promise.complete(result)  # Wakes up doeff task
    """

    _handle: Any = field(repr=False)
    _completion_queue: "queue.Queue[tuple[UUID, Any, BaseException | None]]" = field(repr=False)
    _id: UUID = field(default_factory=uuid4)

    @property
    def id(self) -> UUID:
        """Unique ID for this promise.

        Can be serialized (str(promise.id)) for cross-process communication.
        External code can use this ID to identify which promise to complete.
        """
        return self._id

    @property
    def future(self) -> "Future[T]":
        """The waitable side. Use `yield Wait(promise.future)` in doeff."""
        from doeff.effects.spawn import Future

        return Future(_handle=self._handle, _completion_queue=self._completion_queue)

    def complete(self, value: T) -> None:
        """Complete the promise with a value.

        Called from external code (threads, asyncio callbacks, etc.).
        Thread-safe - can be called from any thread.

        Args:
            value: The result value to complete with
        """
        self._completion_queue.put((self._id, value, None))

    def fail(self, error: BaseException) -> None:
        """Fail the promise with an error.

        Called from external code (threads, asyncio callbacks, etc.).
        Thread-safe - can be called from any thread.

        Args:
            error: The exception to fail with
        """
        if not isinstance(error, BaseException):
            raise TypeError(f"error must be BaseException, got {type(error).__name__}")
        self._completion_queue.put((self._id, None, error))


CreateExternalPromiseEffect = doeff_vm.CreateExternalPromiseEffect


def CreateExternalPromise() -> Any:
    """Create a promise that can be completed from outside doeff.

    Returns an ExternalPromise with complete()/fail() methods that can be
    called from external code (threads, asyncio, other processes, etc.).

    Returns:
        ExternalPromise[T] - use .future property to get the waitable Future

    Example:
        @do
        def fetch_async(url):
            promise = yield CreateExternalPromise()

            async def do_fetch():
                result = await aiohttp.get(url)
                promise.complete(result)

            asyncio.create_task(do_fetch())

            return (yield Wait(promise.future))
    """
    from doeff import do

    @do
    def _program():
        raw_handle = yield create_effect_with_trace(CreateExternalPromiseEffect(), skip_frames=3)

        if isinstance(raw_handle, ExternalPromise):
            return raw_handle

        if isinstance(raw_handle, dict) and raw_handle.get("type") == "ExternalPromise":
            return ExternalPromise(
                _handle=raw_handle,
                _completion_queue=queue.Queue(),
            )

        raise TypeError(
            f"CreateExternalPromise expected ExternalPromise handle, got {type(raw_handle).__name__}"
        )

    return _program()


__all__ = [
    "CreateExternalPromise",
    "CreateExternalPromiseEffect",
    "ExternalPromise",
]
