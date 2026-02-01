"""Handler frame types for the extensible effect handler system.

This module provides the core protocol definitions for the new handler system:
- WithHandler: Effect that runs a program with a handler installed
- HandlerFrame: Kontinuation frame that captures handler context
- HandlerResultFrame: Frame that interprets handler program output
- ResumeK: Frame result that switches to a different continuation
- HandlerContext: Primitives available to handlers

The handler system enables user-defined effect handlers as first-class programs,
replacing hardcoded isinstance checks in the interpreter.

See ISSUE-CORE-462 for full architecture context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Generic, TypeAlias, TypeVar

from doeff._types_internal import EffectBase
from doeff.cesk.frames import (
    ContinueError,
    ContinueProgram,
    ContinueValue,
    FrameResult,
    Kontinuation,
    SuspendOn,
)
from doeff.cesk.result import PythonAsyncSyntaxEscape
from doeff.cesk.types import Environment, Store

if TYPE_CHECKING:
    from doeff.program import Program

T = TypeVar("T")


# ============================================
# Handler Context
# ============================================


@dataclass
class HandlerContext:
    """Primitives available to handlers.

    This provides the context needed for handlers to process effects,
    including access to the store, environment, and delimited continuation.

    Handlers use this to:
    - Read/modify the store directly (for pure effects)
    - Access the environment for reader effects
    - Capture the delimited continuation up to the handler
    - Know their depth in the handler stack for forwarding
    - Access the outer continuation (beyond the handler) for full task suspension

    Attributes:
        store: The current store (mutable state)
        env: The current environment (reader context)
        delimited_k: The continuation from the effect site up to this handler
        handler_depth: The depth of this handler in the handler stack (0 = outermost)
        outer_k: The continuation beyond this handler (for full task suspension)
    """

    store: Store
    env: Environment
    delimited_k: Kontinuation
    handler_depth: int
    outer_k: Kontinuation = field(default_factory=list)


# ============================================
# Frame Results
# ============================================


@dataclass(frozen=True)
class ResumeK:
    """Switch execution to a different continuation.

    This FrameResult allows handlers to resume with an arbitrary continuation,
    enabling advanced control flow patterns like multi-shot continuations
    and effect forwarding.

    When the interpreter sees ResumeK, it replaces the current continuation
    with the provided one and continues execution with the given value.

    Attributes:
        k: The continuation to switch to
        value: The value to resume with (default: None)
        env: The environment to use (default: current)
        store: The store to use (default: current)
    """

    k: Kontinuation
    value: Any = None
    env: Environment | None = None
    store: Store | None = None


# ============================================
# Handler Type
# ============================================

Handler: TypeAlias = Callable[["EffectBase", HandlerContext], "Program[FrameResult | ResumeK]"]


# ============================================
# Handler Frames
# ============================================


@dataclass(frozen=True)
class HandlerFrame:
    """One handler per frame. Receives all effects, pattern-matches internally.

    When an effect bubbles up to this frame (during effect dispatch), the
    handler is invoked with the effect and a HandlerContext. The handler
    returns a Program that produces a FrameResult indicating how to proceed.

    The handler program itself can yield effects, which bubble to outer handlers.
    This enables effect composition and handler stacking.

    Attributes:
        handler: The handler function to invoke for effects
        saved_env: The environment at the time the handler was installed
    """

    handler: Handler
    saved_env: Environment

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        return ContinueValue(
            value=value,
            env=self.saved_env,
            store=store,
            k=k_rest,
        )

    def on_error(
        self,
        error: BaseException,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        return ContinueError(
            error=error,
            env=self.saved_env,
            store=store,
            k=k_rest,
        )


@dataclass(frozen=True)
class HandlerResultFrame:
    """Interprets handler program output when it completes.

    After a handler program finishes executing, this frame processes its
    result (ContinueValue, ContinueError, ResumeK) and applies it to
    resume the original program appropriately.

    Attributes:
        original_effect: The effect that triggered handler invocation
        handler_depth: Depth of the handler that produced this frame
        handled_program_k: The continuation of the program being handled
    """

    original_effect: EffectBase
    handler_depth: int
    handled_program_k: Kontinuation

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        if isinstance(value, ContinueValue):
            full_k = list(value.k) + list(k_rest)
            return ContinueValue(
                value=value.value,
                env=value.env if value.env is not None else env,
                store=value.store if value.store else store,
                k=full_k,
            )
        elif isinstance(value, ContinueError):
            full_k = list(value.k) + list(k_rest)
            return ContinueError(
                error=value.error,
                env=value.env if value.env is not None else env,
                store=value.store if value.store else store,
                k=full_k,
            )
        elif isinstance(value, ContinueProgram):
            # Handler wants to start a new sub-program (e.g., Safe, Local, Listen)
            # Merge the program's k with k_rest to preserve outer continuation
            full_k = list(value.k) + list(k_rest)
            return ContinueProgram(
                program=value.program,
                env=value.env if value.env is not None else env,
                store=value.store if value.store else store,
                k=full_k,
            )
        elif isinstance(value, PythonAsyncSyntaxEscape):
            if value.awaitables is not None:
                # Multi-task escape: continuation already complete in pending_io
                return value
            
            # Single-task escape: need to extend continuation with k_rest
            from doeff.cesk.state import CESKState
            
            original_resume = value.resume
            original_resume_error = value.resume_error
            captured_k_rest = list(k_rest)
            
            def wrapped_resume(v: Any, s: Store) -> CESKState:
                state = original_resume(v, s)
                return CESKState(
                    C=state.C,
                    E=state.E,
                    S=state.S,
                    K=list(state.K) + captured_k_rest,
                )
            
            def wrapped_resume_error(e: BaseException) -> CESKState:
                state = original_resume_error(e)
                return CESKState(
                    C=state.C,
                    E=state.E,
                    S=state.S,
                    K=list(state.K) + captured_k_rest,
                )
            
            return PythonAsyncSyntaxEscape(
                resume=wrapped_resume,
                resume_error=wrapped_resume_error,
                awaitable=value.awaitable,
                awaitables=value.awaitables,
                store=value.store,
            )
        elif isinstance(value, SuspendOn):
            # Legacy: handler returned SuspendOn - transform and pass through
            full_k = list(self.handled_program_k) + list(k_rest)
            result_store = value.stored_store if value.stored_store is not None else store
            return SuspendOn(
                awaitable=value.awaitable,
                stored_k=full_k,
                stored_env=env,
                stored_store=result_store,
            )
        elif isinstance(value, ResumeK):
            full_k = list(value.k) + list(k_rest)
            result_store = value.store if value.store is not None else store
            return ContinueValue(
                value=value.value,
                env=value.env if value.env is not None else env,
                store=result_store,
                k=full_k,
            )
        else:
            full_k = list(self.handled_program_k) + list(k_rest)
            return ContinueValue(
                value=value,
                env=env,
                store=store,
                k=full_k,
            )

    def on_error(
        self,
        error: BaseException,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        full_k = list(self.handled_program_k) + list(k_rest)
        return ContinueError(
            error=error,
            env=env,
            store=store,
            k=full_k,
        )


# ============================================
# WithHandler Effect
# ============================================


@dataclass(frozen=True, kw_only=True)
class WithHandler(EffectBase, Generic[T]):
    """Run program with handler installed.

    This effect installs a handler and runs the given program. Effects
    yielded by the program are first offered to this handler before
    bubbling to outer handlers.

    Example:
        @do
        def my_handler(effect: Effect, ctx: HandlerContext) -> Program[FrameResult]:
            if isinstance(effect, MyEffect):
                # Handle MyEffect
                return Program.pure(ContinueValue(
                    value=effect.compute(),
                    env=ctx.env,
                    store=ctx.store,
                    k=ctx.delimited_k,
                ))
            else:
                # Forward unhandled effects
                result = yield effect
                return ContinueValue(
                    value=result,
                    env=ctx.env,
                    store=ctx.store,
                    k=ctx.delimited_k,
                )

        result = yield WithHandler(handler=my_handler, program=my_program())

    Attributes:
        handler: The handler function to install
        program: The program to run with the handler
    """

    handler: Handler
    program: "Program[T]"


__all__ = [
    "Handler",
    "HandlerContext",
    "HandlerFrame",
    "HandlerResultFrame",
    "ResumeK",
    "WithHandler",
]
