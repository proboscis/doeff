from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from doeff.cesk_v3.level1_cesk.frames import ReturnFrame
from doeff.cesk_v3.level2_algebraic_effects.frames import (
    DispatchingFrame,
    EffectBase,
    Handler,
    WithHandlerFrame,
)

if TYPE_CHECKING:
    from doeff.program import Program

T = TypeVar("T")

Frame = ReturnFrame | WithHandlerFrame | DispatchingFrame


class ControlPrimitive:
    pass


@dataclass(frozen=True)
class WithHandler(ControlPrimitive, Generic[T]):
    handler: Handler
    program: "Program[T] | WithHandler[T]"


@dataclass(frozen=True)
class Resume(ControlPrimitive):
    value: Any


@dataclass(frozen=True)
class Forward(ControlPrimitive):
    effect: EffectBase


@dataclass(frozen=True)
class Continuation:
    """A captured or created continuation that can be resumed later.

    Two kinds:
    1. Captured (via GetContinuation): started=True, frames contains K frames
    2. Created (via CreateContinuation): started=False, program and handlers set

    One-shot invariant: Each continuation can only be resumed once.
    """

    cont_id: int
    frames: tuple[Frame, ...] = ()
    program: "Program[Any] | None" = None
    started: bool = True
    handlers: tuple[Handler, ...] = ()


@dataclass(frozen=True)
class GetContinuation(ControlPrimitive):
    """Capture the current continuation as a first-class value.

    Returns a Continuation object to the handler. The DispatchingFrame
    is NOT consumed - handler can still Resume/Forward after capturing.

    This enables scheduler patterns where:
    1. Handler captures continuation (GetContinuation)
    2. Handler stores continuation for later (e.g., in a queue)
    3. Handler can Resume current computation OR switch to another

    Unlike Resume, GetContinuation does NOT immediately resume anything.
    It just returns the capability to resume later.
    """

    pass


@dataclass(frozen=True)
class ResumeContinuation(ControlPrimitive):
    """Resume a captured or created continuation with a value.

    Handles both captured continuations (from GetContinuation) and
    unstarted continuations (from CreateContinuation).

    For captured (started=True):
    1. The continuation frames become the new K
    2. Value is sent to the continuation

    For created (started=False):
    1. Build K from handlers in frames (as WHFs)
    2. Start the program with ProgramControl
    3. The value parameter is ignored (program starts fresh)

    One-shot invariant: The continuation must not have been resumed before.
    """

    continuation: Continuation
    value: Any


@dataclass(frozen=True)
class GetHandlers(ControlPrimitive):
    """Get the handlers from the yielder's scope (DispatchingFrame snapshot).

    Returns the handlers that were available to user code when it yielded,
    NOT the handlers visible to the handler code. This enables spawn patterns
    where child tasks inherit the parent's handler context.

    Must be called within handler context (during dispatch).
    """

    pass


@dataclass(frozen=True)
class CreateContinuation(ControlPrimitive):
    """Create an unstarted continuation from a program.

    Unlike GetContinuation (which captures an already-running continuation),
    CreateContinuation packages a program that hasn't started yet.

    The handlers parameter controls which handlers the new continuation sees:
    - Empty tuple: Fresh context, no handlers
    - From GetHandlers(): Inherit parent's handlers
    - Custom tuple: Explicit handler configuration
    """

    program: "Program[Any]"
    handlers: tuple[Handler, ...] = ()


@dataclass(frozen=True)
class PythonAsyncSyntaxEscape(ControlPrimitive):
    """Escape to Python's async event loop - a WORKAROUND for Python's async syntax.

    WHY THIS EXISTS:
    Python's asyncio APIs (asyncio.create_task, await, etc.) require:
    1. A running event loop
    2. Being called from an `async def` context

    Handlers run during step(), which is synchronous Python code. They CANNOT
    directly call asyncio APIs. This escape lets handlers say "please run this
    action in an async context" - which async_run provides.

    THIS IS NOT A FUNDAMENTAL PRIMITIVE:
    - sync_run is the clean, canonical implementation
    - doeff's cooperative scheduling works without async/await
    - If Python had first-class coroutines without syntax requirements,
      this primitive would not exist

    DUAL NATURE:
    - As ControlPrimitive: Yielded by handlers with VALUE-returning action
    - As StepResult: Returned by level2_step with STATE-returning action

    FLOW:
    1. Handler creates action that returns a VALUE (e.g., asyncio.Task)
    2. level2_step wraps action to return CESKState (capturing E, S, K)
    3. level2_step returns PythonAsyncSyntaxEscape as step result
    4. async_run awaits action, gets CESKState, continues stepping

    IMPORTANT: Only handlers designed for async_run should yield this primitive.
    sync_run will raise TypeError if it encounters this step result.
    """

    action: Callable[[], Awaitable[Any]]
