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
from doeff.cesk.frames import Kontinuation
from doeff.cesk.types import Environment, Store

if TYPE_CHECKING:
    from doeff.cesk.state import CESKState
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
    - Access inherited handlers for spawning child tasks

    Attributes:
        store: The current store (mutable state)
        env: The current environment (reader context)
        delimited_k: The continuation from the effect site up to this handler
        handler_depth: The depth of this handler in the handler stack (0 = outermost)
        outer_k: The continuation beyond this handler (for full task suspension)
        inherited_handlers: All handler frames from the original K (for spawn inheritance)
    """

    store: Store
    env: Environment
    delimited_k: Kontinuation
    handler_depth: int
    outer_k: Kontinuation = field(default_factory=list)
    inherited_handlers: Kontinuation = field(default_factory=list)

    @property
    def k(self) -> Kontinuation:
        """Full continuation: delimited_k + outer_k.

        Use this when constructing CESKState in handlers.
        """
        return list(self.delimited_k) + list(self.outer_k)


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
    with the provided one and continues execution with the given value or error.

    Attributes:
        k: The continuation to switch to
        value: The value to resume with (default: None)
        env: The environment to use (default: current)
        store: The store to use (default: current)
        error: If set, resume by throwing this error instead of sending value
    """

    k: Kontinuation
    value: Any = None
    env: Environment | None = None
    store: Store | None = None
    error: BaseException | None = None


# ============================================
# Handler Type
# ============================================

Handler: TypeAlias = Callable[["EffectBase", HandlerContext], "Program[CESKState | ResumeK]"]


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
    ) -> "CESKState":
        from doeff.cesk.state import CESKState

        return CESKState.with_value(value, self.saved_env, store, k_rest)

    def on_error(
        self,
        error: BaseException,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> "CESKState":
        from doeff.cesk.state import CESKState
        return CESKState.with_error(error, self.saved_env, store, k_rest)


@dataclass(frozen=True)
class HandlerResultFrame:
    """Interprets handler program output when it completes.

    After a handler program finishes executing, this frame processes its
    result (CESKState, ResumeK) and applies it to resume the original
    program appropriately.

    Attributes:
        original_effect: The effect that triggered handler invocation
        handler_depth: Depth of the handler that produced this frame
        handled_program_k: The continuation of the program being handled
        inherited_handlers: Handler frames to inherit when spawning (preserved across forwarding)
    """

    original_effect: EffectBase
    handler_depth: int
    handled_program_k: Kontinuation
    inherited_handlers: Kontinuation = field(default_factory=list)

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> "CESKState":
        from doeff.cesk.result import DirectState
        from doeff.cesk.state import CESKState

        # DirectState: pass through unchanged (direct jumps)
        if isinstance(value, DirectState):
            return value.state

        # Handler returned CESKState directly - merge handled_program_k and k_rest into K
        if isinstance(value, CESKState):
            full_k = list(self.handled_program_k) + list(k_rest)
            return CESKState(
                C=value.C,
                E=value.E,
                S=value.S,
                K=full_k,
            )

        if isinstance(value, ResumeK):
            # ResumeK switches to a different continuation (e.g., task switching).
            # The value.k IS the complete continuation to use - don't add k_rest.
            # k_rest belongs to the CURRENT context, not the TARGET context.
            # Adding it would pollute the target continuation with handlers from
            # the current context, causing exponential handler growth in spawn/wait.
            result_store = value.store if value.store is not None else store
            result_env = value.env if value.env is not None else env
            if value.error is not None:
                return CESKState.with_error(value.error, result_env, result_store, list(value.k))
            return CESKState.with_value(
                value.value,
                result_env,
                result_store,
                list(value.k),
            )

        # Plain value from handler - use handled_program_k
        full_k = list(self.handled_program_k) + list(k_rest)
        return CESKState.with_value(value, env, store, full_k)

    def on_error(
        self,
        error: BaseException,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> "CESKState":
        from doeff.cesk.state import CESKState

        full_k = list(self.handled_program_k) + list(k_rest)
        return CESKState.with_error(error, env, store, full_k)


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
        def my_handler(effect: Effect, ctx: HandlerContext) -> Program[CESKState]:
            if isinstance(effect, MyEffect):
                # Handle MyEffect - return CESKState directly
                return Program.pure(CESKState.resume_value(effect.compute(), ctx))
            else:
                # Forward unhandled effects
                result = yield effect
                return CESKState.resume_value(result, ctx)

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
