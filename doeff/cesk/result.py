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
from doeff.cesk.state import CESKState, Error, Value
from doeff.cesk.types import Environment, Store

if TYPE_CHECKING:
    from doeff.cesk.frames import Kontinuation
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
class PythonAsyncSyntaxEscape:
    """Escape hatch for Python's async/await SYNTAX.

    !! RESTRICTED USE - READ CAREFULLY !!

    This type is produced by ONE handler only: python_async_syntax_escape_handler.
    No other handler may produce this type. This is not a guideline - it is a
    hard architectural constraint.

    WHY THIS EXISTS
    ---------------
    Python's `await` is SYNTAX, not a function call. You cannot write:

        def sync_function():
            result = await some_coroutine()  # SyntaxError!

    When a user explicitly chooses async_run and their code does `yield Await(coro)`,
    we must escape from the CESK machine to Python's async runtime to execute the
    await. This is the ONLY purpose of this type.

    WHAT THIS IS NOT
    ----------------
    - NOT a general "escape hatch" for handlers
    - NOT for custom blocking (do blocking I/O in handler's generator directly)
    - NOT for task coordination (use effects and handlers)
    - NOT for "optimizations" or "special cases"

    If you think you need to produce this type from a new handler, you are wrong.
    Rethink your approach. The handler's generator can do blocking I/O directly:

        # WRONG - don't create escape types
        return PythonAsyncSyntaxEscape(awaitable=queue.get(), ...)

        # RIGHT - block directly in handler
        result = queue.get()  # next(gen) blocks here

    ALLOWED PRODUCER
    ----------------
    python_async_syntax_escape_handler ONLY, for this effect ONLY:
    - PythonAsyncioAwaitEffect (yield Await(coroutine))

    Other time-based effects (Delay, WaitUntil) should be implemented via Await,
    not handled directly by the escape handler.

    HOW IT WORKS
    ------------
    The runtime awaits using ONE of these patterns:
    1. Single awaitable: `awaitable` field is set, await it, call resume(value)
    2. Multiple awaitables: `awaitables` dict is set, await first completion,
       call resume((task_id, value)) where task_id is the key that completed

    This design keeps the runtime GENERIC - it has no knowledge of task IDs,
    scheduling logic, or handler internals. All routing decisions are made
    in the resume callback (created by handlers).
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
    
    # Marker: True when escape has been wrapped and is propagating through handler stack
    _propagating: bool = False
    
    # Marker: True when escape should exit handler stack immediately (single-task case)
    _is_final: bool = False
    
    # Stored continuation data (for scheduler interception)
    _stored_k: Any = None
    _stored_env: Any = None
    _stored_store: Any = None




def python_async_escape(
    awaitable: Any,
    stored_k: Kontinuation,
    stored_env: Environment,
    stored_store: Store,
) -> PythonAsyncSyntaxEscape:
    """Build PythonAsyncSyntaxEscape with resume callbacks.

    Used by python_async_syntax_escape_handler for async_run.
    The callbacks are built from the stored continuation data.
    """
    def resume(value: Any, new_store: Store) -> CESKState:
        merged_store = dict(new_store)
        for key, val in stored_store.items():
            if key not in merged_store:
                merged_store[key] = val
        return CESKState(
            C=Value(value),
            E=stored_env,
            S=merged_store,
            K=list(stored_k),
        )

    def resume_error(error: BaseException) -> CESKState:
        return CESKState(
            C=Error(error),
            E=stored_env,
            S=stored_store,
            K=list(stored_k),
        )

    return PythonAsyncSyntaxEscape(
        resume=resume,
        resume_error=resume_error,
        awaitable=awaitable,
        store=stored_store,
        _stored_k=stored_k,
        _stored_env=stored_env,
        _stored_store=stored_store,
    )


def multi_task_async_escape(
    stored_k: Kontinuation,
    stored_env: Environment,
    stored_store: Store,
) -> PythonAsyncSyntaxEscape:
    """Build PythonAsyncSyntaxEscape for multi-task async escape.
    
    Used by task_scheduler_handler when all tasks are waiting on I/O.
    The store must contain PENDING_IO_KEY with task awaitable info.
    
    Runtime awaits FIRST_COMPLETED from awaitables dict, then calls
    resume((task_id, value)) to route to the correct task's continuation.
    """
    from doeff.cesk.handlers.scheduler_state_handler import (
        CURRENT_TASK_KEY,
        PENDING_IO_KEY,
    )
    
    pending_io = stored_store.get(PENDING_IO_KEY, {})
    
    awaitables_dict = {
        task_id: info["awaitable"]
        for task_id, info in pending_io.items()
    }
    
    def resume_multi(value: Any, new_store: Store) -> CESKState:
        task_id, result = value
        
        task_info = pending_io.get(task_id)
        if task_info is None:
            raise RuntimeError(f"Task {task_id} not found in pending_io")
        
        task_k = task_info["k"]
        task_store_snapshot = task_info.get("store_snapshot", {})
        
        new_pending = dict(pending_io)
        del new_pending[task_id]
        
        merged_store = dict(task_store_snapshot)
        for key, val in stored_store.items():
            if isinstance(key, str) and key.startswith("__scheduler_"):
                merged_store[key] = val
        merged_store[PENDING_IO_KEY] = new_pending
        merged_store[CURRENT_TASK_KEY] = task_id
        
        return CESKState(
            C=Value(result),
            E=stored_env,
            S=merged_store,
            K=task_k,
        )
    
    def resume_error_multi(error_info: Any) -> CESKState:
        task_id, error = error_info
        
        task_info = pending_io.get(task_id)
        if task_info is None:
            return CESKState(
                C=Error(error),
                E=stored_env,
                S=stored_store,
                K=list(stored_k),
            )
        
        task_k = task_info["k"]
        task_store_snapshot = task_info.get("store_snapshot", {})
        
        new_pending = dict(pending_io)
        del new_pending[task_id]
        
        merged_store = dict(task_store_snapshot)
        for key, val in stored_store.items():
            if isinstance(key, str) and key.startswith("__scheduler_"):
                merged_store[key] = val
        merged_store[PENDING_IO_KEY] = new_pending
        merged_store[CURRENT_TASK_KEY] = task_id
        
        return CESKState(
            C=Error(error),
            E=stored_env,
            S=merged_store,
            K=task_k,
        )
    
    return PythonAsyncSyntaxEscape(
        resume=resume_multi,
        resume_error=resume_error_multi,
        awaitables=awaitables_dict,
        store=stored_store,
    )


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
    "multi_task_async_escape",
    "python_async_escape",
]
