"""Kontinuation frame types for the unified CESK machine.

This module provides Frame types that implement the Frame protocol with
on_value and on_error methods for unified continuation handling.

Per SPEC-CESK-003 (Minimal Frame Architecture):
- Only ReturnFrame and scheduler-specific frames are kept here
- User-facing patterns (Local, Safe, Listen, Intercept) are implemented
  via WithHandler in doeff.cesk.handlers.patterns

Each frame type represents a computation context that:
1. Can receive a value (on_value) to continue normal execution
2. Can receive an error (on_error) to handle exceptions

Frames return CESKState directly using utility methods:
- CESKState.with_value(value, env, store, k)
- CESKState.with_error(error, env, store, k)
- CESKState.with_program(program, env, store, k)
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, runtime_checkable

from doeff.cesk.types import Environment, Store, TaskId

if TYPE_CHECKING:
    from doeff.cesk.state import CESKState
    from doeff.effects._program_types import ProgramLike
    from doeff.program import KleisliProgramCall, ProgramBase


# ============================================
# Frame Protocol
# ============================================


@runtime_checkable
class Frame(Protocol):
    """Protocol for continuation frames.

    Each frame type implements on_value and on_error to define
    how it handles values and errors during continuation unwinding.

    Frames return CESKState directly using utility methods.
    """

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> "CESKState":
        """Handle a value being passed through this frame.

        Args:
            value: The value to process
            env: Current environment
            store: Shared store
            k_rest: Remaining continuation frames

        Returns:
            CESKState for next step
        """
        ...

    def on_error(
        self,
        error: BaseException,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> "CESKState":
        """Handle an error being passed through this frame.

        Args:
            error: The exception to process
            env: Current environment
            store: Shared store
            k_rest: Remaining continuation frames

        Returns:
            CESKState for next step
        """
        ...


# ============================================
# Core Frame Types
# ============================================


@dataclass
class ReturnFrame:
    """Resume generator with value/error.

    This frame represents a suspended generator that can be resumed
    by sending it a value or throwing an exception into it.

    Kleisli info fields (kleisli_*) are embedded here so they persist
    across multiple yields from the same @do function.

    Note: step.py handles ReturnFrame directly by calling generator.send/throw,
    so on_value/on_error are not used. They exist only for protocol compliance.
    """

    generator: Generator[Any, Any, Any]
    saved_env: Environment
    program_call: KleisliProgramCall | None = None
    kleisli_function_name: str | None = None
    kleisli_filename: str | None = None
    kleisli_lineno: int | None = None


# ============================================
# Scheduler Frame Types
# ============================================


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
    ) -> "CESKState":
        """Collect result and continue with next program or return all results."""
        from doeff.cesk.state import CESKState

        new_results = self.collected_results + [value]

        if not self.remaining_programs:
            # All programs complete, return collected results
            return CESKState.with_value(new_results, self.saved_env, store, k_rest)

        # Continue with next program
        next_prog, *rest = self.remaining_programs
        new_frame = GatherFrame(
            remaining_programs=rest,
            collected_results=new_results,
            saved_env=self.saved_env,
        )

        return CESKState.with_program(next_prog, self.saved_env, store, [new_frame] + k_rest)

    def on_error(
        self,
        error: BaseException,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> "CESKState":
        """Errors abort the gather and propagate."""
        from doeff.cesk.state import CESKState
        return CESKState.with_error(error, self.saved_env, store, k_rest)


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
    ) -> "CESKState":
        """First value wins the race."""
        from doeff.cesk.state import CESKState
        return CESKState.with_value(value, self.saved_env, store, k_rest)

    def on_error(
        self,
        error: BaseException,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> "CESKState":
        """Errors propagate from the race."""
        from doeff.cesk.state import CESKState
        return CESKState.with_error(error, self.saved_env, store, k_rest)


@dataclass(frozen=True)
class GatherWaiterFrame:
    """Frame for waiting on gather completion in multi-task scheduler."""

    gather_effect: Any
    saved_env: Environment

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> "CESKState":
        from doeff.cesk.state import CESKState
        from doeff.do import do

        @do
        def retry_gather():
            return (yield self.gather_effect)

        return CESKState.with_program(retry_gather(), self.saved_env, store, k_rest)

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
class RaceWaiterFrame:
    """Frame for waiting on race completion in multi-task scheduler."""

    race_effect: Any
    saved_env: Environment

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> "CESKState":
        from doeff.cesk.state import CESKState
        from doeff.do import do

        @do
        def retry_race():
            return (yield self.race_effect)

        return CESKState.with_program(retry_race(), self.saved_env, store, k_rest)

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
    with_program returns, so no race condition with sequential dispatch.
    """

    ask_key: Any  # The original Ask key (any hashable)
    program: ProgramBase  # The Program object itself (for identity-based cache validation)

    def on_value(
        self,
        value: Any,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> "CESKState":
        """Cache the computed value and continue with it."""
        from doeff.cesk.state import CESKState

        cache = store.get("__ask_lazy_cache__", {})
        # Store (program_object, cached_value) tuple keyed by ask_key only
        new_cache = {**cache, self.ask_key: (self.program, value)}
        new_store = {**store, "__ask_lazy_cache__": new_cache}

        return CESKState.with_value(value, env, new_store, k_rest)

    def on_error(
        self,
        error: BaseException,
        env: Environment,
        store: Store,
        k_rest: Kontinuation,
    ) -> "CESKState":
        """Errors propagate up - per spec, Program failure = entire run() fails.

        Note: We clear the in-progress marker on error so the key can be retried
        (e.g., after a Safe boundary or Local override with different program).
        """
        from doeff.cesk.state import CESKState

        cache = store.get("__ask_lazy_cache__", {})
        # Remove the in-progress entry so future attempts can retry
        if self.ask_key in cache:
            new_cache = {k: v for k, v in cache.items() if k != self.ask_key}
            new_store = {**store, "__ask_lazy_cache__": new_cache}
        else:
            new_store = store

        return CESKState.with_error(error, env, new_store, k_rest)


# ============================================
# Kontinuation Type
# ============================================

# Type alias for the continuation stack
# Using list for mutable efficiency; conceptually this is a stack (LIFO)
Kontinuation: TypeAlias = list[Any]


__all__ = [
    "AskLazyFrame",
    "Frame",
    "GatherFrame",
    "GatherWaiterFrame",
    "Kontinuation",
    "RaceFrame",
    "RaceWaiterFrame",
    "ReturnFrame",
]
