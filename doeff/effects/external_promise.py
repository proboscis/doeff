"""ExternalPromise for external world integration.

ExternalPromise allows code outside the Rust VM runtime (asyncio, threads, processes,
network) to complete a promise and wake up waiting doeff tasks.

IMPURE: ExternalPromise is explicitly impure. Do NOT use inside pure doeff programs.
Use regular Promise + CompletePromise/FailPromise effects for doeff-to-doeff communication.

See SPEC-EFF-010-external-promise.md for full design documentation.
"""


from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, TypeVar

import doeff_vm

from doeff.effects.base import Effect

if TYPE_CHECKING:
    from doeff.effects.spawn import Future

T = TypeVar("T")


@dataclass
class ExternalPromise(Generic[T]):
    """Promise that can be completed from outside the Rust VM runtime.

    IMPURE: This type is for bridging external world to doeff.
    Do NOT use inside pure doeff programs - use regular Promise instead.

    The completion methods (complete/fail) submit to a thread-safe queue
    that the scheduler checks during stepping.

    Attributes:
        _handle: Internal handle for scheduler waiter tracking
        _completion_queue: Reference to scheduler's thread-safe queue
        _id: Scheduler promise identifier

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
    _completion_queue: Any = field(repr=False)
    _id: int = field(repr=False)

    @property
    def id(self) -> int:
        """Scheduler promise ID for this promise."""
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
        raw_handle = yield CreateExternalPromiseEffect()

        if isinstance(raw_handle, ExternalPromise):
            return raw_handle

        if isinstance(raw_handle, dict) and raw_handle.get("type") == "ExternalPromise":
            promise_id = raw_handle.get("promise_id")
            if not isinstance(promise_id, int):
                raise TypeError(
                    "CreateExternalPromise expected ExternalPromise handle with integer promise_id"
                )

            completion_queue = raw_handle.get("completion_queue")
            if completion_queue is None:
                raise TypeError(
                    "CreateExternalPromise expected ExternalPromise handle with completion_queue"
                )

            return ExternalPromise(
                _handle=raw_handle,
                _completion_queue=completion_queue,
                _id=promise_id,
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
