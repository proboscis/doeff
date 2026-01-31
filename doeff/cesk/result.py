"""CESK machine step results and public result types."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeAlias, TypeVar

from doeff._types_internal import EffectBase
from doeff._vendor import Err, Ok, Result
from doeff.cesk.state import CESKState
from doeff.cesk.types import Store

if TYPE_CHECKING:
    from doeff.cesk_traceback import CapturedTraceback

T = TypeVar("T")


@dataclass(frozen=True)
class Done:
    """Terminal: computation completed successfully.

    Carries final store for correctness - state from the last pure effect
    or merge is preserved in the terminal result.
    """

    value: Any
    store: Store


@dataclass(frozen=True)
class Failed:
    """Terminal: computation failed with exception.

    Carries final store for correctness - state at error point is preserved.
    """

    exception: BaseException
    store: Store
    captured_traceback: CapturedTraceback | None = None


@dataclass(frozen=True)
class Suspended:
    """Suspend: need external handling to continue.

    Continuation-based suspension for async operations. The effect is handled
    externally, then the appropriate continuation is called with the result.

    Per spec: continuations take (value, new_store) to incorporate handler's
    store updates. On error, resume_error uses the original store (S) from
    before the effect - effectful handlers should NOT mutate S in-place.

    When pending_io is set, the runtime should await the first completion from
    the pending_io dict and resume that specific task with its continuation.
    """

    effect: EffectBase
    resume: Callable[[Any, Store], CESKState]
    resume_error: Callable[[BaseException], CESKState]
    pending_io: dict[Any, Any] | None = None
    stored_store: dict[str, Any] | None = None


Terminal: TypeAlias = Done | Failed
StepResult: TypeAlias = CESKState | Terminal | Suspended


@dataclass(frozen=True)
class CESKResult(Generic[T]):
    """Result from CESK interpreter execution with optional traceback.

    This is the public result type returned by run_sync() and run().
    It wraps the standard Result[T] with additional traceback information
    captured during error conditions.

    Attributes:
        result: The standard Ok[T] | Err result
        captured_traceback: Traceback captured on error, None on success
    """

    result: Result[T]
    captured_traceback: CapturedTraceback | None = None

    def is_ok(self) -> bool:
        """Return True when the result is successful."""
        return isinstance(self.result, Ok)

    def is_err(self) -> bool:
        """Return True when the result represents a failure."""
        return isinstance(self.result, Err)

    @property
    def value(self) -> T:
        """Get success value. Raises if error."""
        return self.result.ok()

    @property
    def error(self) -> BaseException:
        """Get error. Raises if success."""
        return self.result.err()


__all__ = [
    "CESKResult",
    "Done",
    "Failed",
    "StepResult",
    "Suspended",
    "Terminal",
]
