"""Kontinuation frame types for the unified CESK machine.

This module provides Frame types that implement the Frame protocol with
on_value and on_error methods for unified continuation handling.

Each frame type represents a computation context that:
1. Can receive a value (on_value) to continue normal execution
2. Can receive an error (on_error) to handle exceptions
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, runtime_checkable

from doeff.cesk.types import Environment, Store, TaskId

if TYPE_CHECKING:
    from doeff.effects._program_types import ProgramLike
    from doeff.program import KleisliProgramCall, ProgramBase
    from doeff.types import Effect


# ============================================
# Frame Protocol
# ============================================


@runtime_checkable
class Frame(Protocol):
    """Protocol for continuation frames.

    Each frame type implements on_value and on_error to define
    how it handles values and errors during continuation unwinding.
    """

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        """Handle a value being passed through this frame.

        Args:
            value: The value to process
            env: Current environment
            store: Shared store
            k_rest: Remaining continuation frames

        Returns:
            FrameResult indicating next state
        """
        ...

    def on_error(
        self,
        error: BaseException,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        """Handle an error being passed through this frame.

        Args:
            error: The exception to process
            env: Current environment
            store: Shared store
            k_rest: Remaining continuation frames

        Returns:
            FrameResult indicating next state
        """
        ...


# ============================================
# Frame Result Types
# ============================================


@dataclass(frozen=True)
class ContinueValue:
    """Continue execution with a value."""

    value: Any
    env: Environment
    store: Store
    k: Kontinuation


@dataclass(frozen=True)
class ContinueError:
    """Continue execution with an error."""

    error: BaseException
    env: Environment
    store: Store
    k: Kontinuation
    captured_traceback: Any | None = None  # CapturedTraceback


@dataclass(frozen=True)
class ContinueProgram:
    """Continue execution with a new program."""

    program: ProgramLike
    env: Environment
    store: Store
    k: Kontinuation


@dataclass(frozen=True)
class ContinueGenerator:
    """Continue execution by sending to a generator."""

    generator: Generator[Any, Any, Any]
    send_value: Any | None
    throw_error: BaseException | None
    env: Environment
    store: Store
    k: Kontinuation
    program_call: KleisliProgramCall | None = None


@dataclass(frozen=True)
class SuspendOn:
    """Handler signals: task is suspended, will be woken by ctx.suspend.complete/fail.
    
    This is returned by handlers that need to suspend the current task and wait
    for external completion (e.g., Await handler waiting for asyncio, external
    executors like Ray or Dask, etc.).
    
    When a handler returns SuspendOn, the runtime:
    1. Parks the task in the pending set
    2. Moves to the next ready task
    3. When ctx.suspend.complete(value) or ctx.suspend.fail(error) is called,
       the task is moved back to the ready queue and resumed
    
    The handler MUST have registered completion callbacks via HandlerContext.suspend
    before returning SuspendOn, otherwise the task will be parked forever.
    """
    pass


FrameResult: TypeAlias = ContinueValue | ContinueError | ContinueProgram | ContinueGenerator | SuspendOn


# ============================================
# Concrete Frame Types
# ============================================


@dataclass
class ReturnFrame:
    """Resume generator with value/error.

    This frame represents a suspended generator that can be resumed
    by sending it a value or throwing an exception into it.
    """

    generator: Generator[Any, Any, Any]
    saved_env: Environment
    program_call: KleisliProgramCall | None = None

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        """Resume generator with a value."""
        return ContinueGenerator(
            generator=self.generator,
            send_value=value,
            throw_error=None,
            env=self.saved_env,
            store=store,
            k=k_rest,
            program_call=self.program_call,
        )

    def on_error(
        self,
        error: BaseException,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        """Throw error into generator."""
        return ContinueGenerator(
            generator=self.generator,
            send_value=None,
            throw_error=error,
            env=self.saved_env,
            store=store,
            k=k_rest,
            program_call=self.program_call,
        )


@dataclass(frozen=True)
class LocalFrame:
    """Restore environment after scoped execution.

    This frame captures the original environment before entering
    a local scope, and restores it when the scope completes.
    """

    restore_env: Environment

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        """Restore original environment and continue with value."""
        return ContinueValue(
            value=value,
            env=self.restore_env,
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
        """Restore original environment and propagate error."""
        return ContinueError(
            error=error,
            env=self.restore_env,
            store=store,
            k=k_rest,
        )


@dataclass(frozen=True)
class InterceptFrame:
    """Transform effects passing through.

    This frame applies transformation functions to effects that
    pass through it, allowing effect interception and modification.
    """

    transforms: tuple[Callable[[Effect], Effect | ProgramBase | None], ...]

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        """Values pass through unchanged."""
        return ContinueValue(
            value=value,
            env=env,
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
        """Errors pass through unchanged."""
        return ContinueError(
            error=error,
            env=env,
            store=store,
            k=k_rest,
        )


@dataclass(frozen=True)
class InterceptBypassFrame:
    """Bypass a specific InterceptFrame for a specific effect object.

    When an intercept transform returns a Program, this frame tracks both the
    InterceptFrame to bypass AND the effect object ID that triggered it.
    Only that exact effect (by object identity) is bypassed when re-yielded.
    """

    bypassed_frame: InterceptFrame
    bypassed_effect_id: int

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        return ContinueValue(value=value, env=env, store=store, k=k_rest)

    def on_error(
        self,
        error: BaseException,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        return ContinueError(error=error, env=env, store=store, k=k_rest)


@dataclass(frozen=True)
class ListenFrame:
    """Capture log output from sub-computation.

    This frame records the starting index of the log, so that
    log entries produced during the sub-computation can be captured.
    """

    log_start_index: int

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        """Capture log entries and wrap result with ListenResult."""
        from doeff._types_internal import ListenResult
        from doeff.utils import BoundedLog

        current_log = store.get("__log__", [])
        captured = current_log[self.log_start_index :]
        listen_result = ListenResult(value=value, log=BoundedLog(captured))

        return ContinueValue(
            value=listen_result,
            env=env,
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
        """Errors propagate through unchanged."""
        return ContinueError(
            error=error,
            env=env,
            store=store,
            k=k_rest,
        )


@dataclass(frozen=True)
class GatherFrame:
    """Collect results from sequential program execution.

    This frame manages the execution of multiple programs in sequence,
    collecting their results into a list.
    """

    remaining_programs: list[ProgramLike]
    collected_results: list[Any]
    saved_env: Environment

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        """Collect result and continue with next program or return all results."""
        new_results = self.collected_results + [value]

        if not self.remaining_programs:
            # All programs complete, return collected results
            return ContinueValue(
                value=new_results,
                env=self.saved_env,
                store=store,
                k=k_rest,
            )

        # Continue with next program
        next_prog, *rest = self.remaining_programs
        new_frame = GatherFrame(
            remaining_programs=rest,
            collected_results=new_results,
            saved_env=self.saved_env,
        )

        return ContinueProgram(
            program=next_prog,
            env=self.saved_env,
            store=store,
            k=[new_frame] + k_rest,
        )

    def on_error(
        self,
        error: BaseException,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        """Errors abort the gather and propagate."""
        return ContinueError(
            error=error,
            env=self.saved_env,
            store=store,
            k=k_rest,
        )


@dataclass(frozen=True)
class SafeFrame:
    """Safe boundary - captures K stack on error, returns Result.

    This frame provides error isolation, converting exceptions
    into Result.Err values instead of propagating them.
    """

    saved_env: Environment

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        """Wrap successful value in Ok."""
        from doeff._vendor import Ok

        return ContinueValue(
            value=Ok(value),
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
        """Convert error to Err result instead of propagating."""
        from doeff._types_internal import capture_traceback, get_captured_traceback
        from doeff._vendor import NOTHING, Err, Some

        # Capture traceback if not already captured
        captured = get_captured_traceback(error)
        if captured is None:
            captured = capture_traceback(error)

        captured_maybe = Some(captured) if captured else NOTHING
        err_result = Err(error, captured_traceback=captured_maybe)

        return ContinueValue(
            value=err_result,
            env=self.saved_env,
            store=store,
            k=k_rest,
        )


@dataclass(frozen=True)
class RaceFrame:
    """Race frame for handling first-to-complete semantics.

    Tracks which tasks are racing and cancels losers when winner completes.
    """

    task_ids: tuple[TaskId, ...]
    saved_env: Environment

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        """First value wins the race."""
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
        """Errors propagate from the race."""
        return ContinueError(
            error=error,
            env=self.saved_env,
            store=store,
            k=k_rest,
        )


@dataclass(frozen=True)
class GatherWaiterFrame:
    gather_effect: Any
    saved_env: Environment

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        from doeff.do import do
        
        @do
        def retry_gather():
            return (yield self.gather_effect)
        
        return ContinueProgram(
            program=retry_gather(),
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
class RaceWaiterFrame:
    race_effect: Any
    saved_env: Environment

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        from doeff.do import do
        
        @do
        def retry_race():
            return (yield self.race_effect)
        
        return ContinueProgram(
            program=retry_race(),
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
class GraphCaptureFrame:
    """Capture graph nodes produced by sub-computation."""

    graph_start_index: int

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        current_graph = store.get("__graph__", [])
        captured = current_graph[self.graph_start_index :]
        return ContinueValue(
            value=(value, captured),
            env=env,
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
            env=env,
            store=store,
            k=k_rest,
        )


@dataclass(frozen=True)
class AskLazyFrame:
    """Cache the result of lazy Ask evaluation for a Program value.

    When Ask encounters a Program in the environment, this frame is pushed
    before evaluating the program. When the program completes, the result
    is cached in the store for subsequent Ask calls with the same key.

    Per SPEC-EFF-001-reader.md:
    - Scope: Per runtime.run() invocation (stored in Store)
    - Key: Same as Ask key (any hashable)
    - Invalidation: Local override with different Program object
    - Errors: Program failure = entire run() fails

    Cache structure: store["__ask_lazy_cache__"][key] = (program, value)
    where program is the actual Program object (for identity comparison).

    Design trade-off: Key-only caching means after Local override exits,
    the original program's cached result is lost and must be re-evaluated.
    This prevents unbounded cache growth from repeated Local overrides.

    Concurrency note: Safe under current scheduler implementation which
    steps one task at a time. The _ASK_IN_PROGRESS marker is set before
    ContinueProgram returns, so no race condition with sequential dispatch.
    """

    ask_key: Any  # The original Ask key (any hashable)
    program: ProgramBase  # The Program object itself (for identity-based cache validation)

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        """Cache the computed value and continue with it."""
        cache = store.get("__ask_lazy_cache__", {})
        # Store (program_object, cached_value) tuple keyed by ask_key only
        new_cache = {**cache, self.ask_key: (self.program, value)}
        new_store = {**store, "__ask_lazy_cache__": new_cache}

        return ContinueValue(
            value=value,
            env=env,
            store=new_store,
            k=k_rest,
        )

    def on_error(
        self,
        error: BaseException,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> FrameResult:
        """Errors propagate up - per spec, Program failure = entire run() fails.

        Note: We clear the in-progress marker on error so the key can be retried
        (e.g., after a Safe boundary or Local override with different program).
        """
        cache = store.get("__ask_lazy_cache__", {})
        # Remove the in-progress entry so future attempts can retry
        if self.ask_key in cache:
            new_cache = {k: v for k, v in cache.items() if k != self.ask_key}
            new_store = {**store, "__ask_lazy_cache__": new_cache}
        else:
            new_store = store

        return ContinueError(
            error=error,
            env=env,
            store=new_store,
            k=k_rest,
        )


# ============================================
# Kontinuation Type
# ============================================

# Type alias for the continuation stack
# Using list for mutable efficiency; conceptually this is a stack (LIFO)
Kontinuation: TypeAlias = list[Any]


__all__ = [
    "AskLazyFrame",
    "ContinueError",
    "ContinueGenerator",
    "ContinueProgram",
    "ContinueValue",
    "Frame",
    "FrameResult",
    "GatherFrame",
    "GatherWaiterFrame",
    "GraphCaptureFrame",
    "InterceptBypassFrame",
    "InterceptFrame",
    "Kontinuation",
    "ListenFrame",
    "LocalFrame",
    "RaceFrame",
    "RaceWaiterFrame",
    "ReturnFrame",
    "SafeFrame",
    "SuspendOn",
]
