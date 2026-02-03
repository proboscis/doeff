"""CESK machine step results and public result types.

Architecture Notes
==================

doeff is a COOPERATIVE SCHEDULING system with its own execution model.
It does NOT "support" asyncio - Python async is a WORKAROUND for users
who want `async def` syntax, not a core feature.

StepResult Types
----------------

The step() function returns one of these types:

- CESKState: Continue stepping (normal case)
- Done: Computation finished successfully
- Failed: Computation failed with exception
- PythonAsyncSyntaxEscape: SPECIAL - escape to Python async (see below)

Blocking Behavior
-----------------

When a handler has no work and is waiting for external I/O:
- The handler's generator does blocking I/O directly (e.g., queue.get())
- CESK stepping blocks at next(gen) until I/O completes
- This is correct - doeff blocks when there's nothing to do

DO NOT add more escape types. If blocking is needed, do it in the handler.
The run loop should remain simple: step until Done/Failed.

PythonAsyncSyntaxEscape
-----------------------

This escape exists ONLY because Python's `await` is SYNTAX, not a function.
You cannot hide `await` inside a sync function - it must bubble up.

RESTRICTIONS:
- ONLY python_async_syntax_escape_handler may produce this
- ONLY for Await effect (Delay/WaitUntil should use Await internally)
- ONLY when user explicitly chose async_run

DO NOT use this for:
- Custom blocking (use direct blocking in handler instead)
- Task coordination (use effects and handlers)
- Any other "escape hatch" purposes

This is NOT a general monad escape pattern. Adding more escape types
or using this for other purposes violates the architecture.
"""

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
class DirectState:
    """Marker wrapper for CESKState that should pass through unchanged.

    When a handler returns CESKState wrapped in DirectState, HandlerResultFrame
    passes it through without modifying K. This is used for:
    - Async escape resumption (escape's K is already complete)
    - Any case where the handler constructs the exact machine state to jump to

    For regular handler returns that need K reconstruction, return CESKState directly.
    """

    state: "CESKState"


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
class PythonAsyncSyntaxEscape(EffectBase):
    """Minimal escape: run an async action in async_run's context.

    !! RESTRICTED USE - READ CAREFULLY !!

    WHY THIS EXISTS
    ---------------
    asyncio.create_task() and other asyncio operations require a running event
    loop. The handler runs during step(), which is inside async_run, but we need
    to ensure asyncio context explicitly. This escape signals async_run to execute
    an async action.

    HOW IT WORKS (per SPEC-CESK-005)
    --------------------------------
    1. Handler yields PythonAsyncSyntaxEscape(action=some_async_fn)
       - Handler's action returns a VALUE
    2. step() wraps the action to return CESKState (capturing current E, S, K)
    3. async_run receives escape, executes: state = await escape.action()
    4. async_run continues stepping with the returned CESKState
    5. The value from action is sent back to handler via C=Value(value)

    This design separates concerns:
    - Handler: business logic (what async operation to perform)
    - step(): state management (how to construct CESKState)
    - async_run: execution (just await and continue)

    WHAT THIS IS NOT
    ----------------
    - NOT for storing continuations (scheduler handles via Wait/Promise)
    - NOT for multi-task coordination (scheduler handles)

    ALLOWED PRODUCERS
    -----------------
    - python_async_syntax_escape_handler: for Await/Delay effects
    - async_external_wait_handler: for WaitForExternalCompletion effect

    See SPEC-CESK-005-simplify-async-escape.md for full architecture.
    """

    # The async action to execute. Returns CESKState (step() wraps handler's
    # value-returning action to return state). async_run just does:
    #   state = await escape.action()
    action: Callable[[], Any]  # Awaitable[CESKState] after step() wraps it




Terminal: TypeAlias = Done | Failed

# StepResult: What step() can return
#
# - CESKState: Keep stepping (normal case, vast majority of steps)
# - Done: Computation finished successfully
# - Failed: Computation failed with exception
# - PythonAsyncSyntaxEscape: ONLY from python_async_syntax_escape_handler
#
# DO NOT ADD MORE TYPES HERE. If you need blocking, do it in the handler's
# generator. The run loop should remain: step until Done/Failed.
StepResult: TypeAlias = CESKState | Terminal | PythonAsyncSyntaxEscape


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
    "DirectState",
    "Done",
    "Failed",
    "PythonAsyncSyntaxEscape",
    "StepResult",
    "Terminal",
]
