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

    The runtime awaits using ONE of these patterns:
    1. Single awaitable: `awaitable` field is set, await it, call resume(value)
    2. Multiple awaitables: `awaitables` dict is set, await first completion,
       call resume((task_id, value)) where task_id is the key that completed

    This design keeps the runtime GENERIC - it has no knowledge of task IDs,
    AllTasksSuspendedEffect, or scheduling logic. All routing decisions are
    made in the resume callback (created by handlers).

    Per spec: continuations take (value, new_store) to incorporate handler's
    store updates. On error, resume_error uses the original store (S) from
    before the effect - effectful handlers should NOT mutate S in-place.
    """

    # Resume callbacks
    resume: Callable[[Any, Store], CESKState]
    resume_error: Callable[[BaseException], CESKState]

    # Single awaitable (simple case: Await, Delay, etc.)
    awaitable: Any | None = None

    # Multiple awaitables (multi-task case: dict[task_id, Awaitable])
    # Runtime awaits FIRST_COMPLETED, returns (task_id, value) to resume
    awaitables: dict[Any, Any] | None = None

    # Store to pass to resume (handlers may need current store for merging)
    store: Store | None = None

    # Legacy: effect field kept for compatibility but not used by runtime
    effect: EffectBase | None = None


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
