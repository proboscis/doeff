"""Kontinuation frame types for the CESK machine.

This module provides:
- FrameResult: Result of frame processing (Continue, PopAndContinue, Propagate)
- Frame protocol: Extensible frame interface with on_value/on_error
- Concrete frame types: ReturnFrame, LocalFrame, InterceptFrame, etc.
- Kontinuation: list[Frame] type alias
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, runtime_checkable

from doeff.cesk.types import Environment, FutureId, TaskId

if TYPE_CHECKING:
    from doeff.program import KleisliProgramCall, Program
    from doeff.types import Effect


# ============================================================================
# Frame Result Types
# ============================================================================


@dataclass(frozen=True)
class Continue:
    """Continue execution with new control state.

    Used when frame processes value/error and produces a new control.
    The frame remains on the stack (will be updated or popped by step).
    """

    control: Any  # Control type (Value, Error, EffectControl, ProgramControl)
    env: Environment
    actions: tuple[Any, ...] = ()  # Optional actions to emit


@dataclass(frozen=True)
class PopAndContinue:
    """Pop frame and continue with control.

    Used when frame is done and should be removed from stack.
    """

    control: Any  # Control type
    env: Environment
    actions: tuple[Any, ...] = ()


@dataclass(frozen=True)
class Propagate:
    """Propagate error through stack without modification.

    Used when a frame doesn't handle the error.
    """

    error: BaseException
    captured_traceback: Any | None = None


FrameResult: TypeAlias = Continue | PopAndContinue | Propagate


# ============================================================================
# Frame Protocol
# ============================================================================


@runtime_checkable
class FrameProtocol(Protocol):
    """Protocol for kontinuation frames.

    Frames implement on_value and on_error to handle control flow.
    This protocol allows adding new frame types without modifying step().
    """

    def on_value(self, value: Any, env: Environment, store: dict[str, Any]) -> FrameResult:
        """Handle a value arriving at this frame.

        Args:
            value: The value produced by sub-computation
            env: Current environment
            store: Current store (for read-only access)

        Returns:
            FrameResult indicating how to proceed
        """
        ...

    def on_error(
        self, error: BaseException, captured_traceback: Any | None, env: Environment, store: dict[str, Any]
    ) -> FrameResult:
        """Handle an error arriving at this frame.

        Args:
            error: The exception raised
            captured_traceback: Captured traceback if available
            env: Current environment
            store: Current store (for read-only access)

        Returns:
            FrameResult indicating how to proceed
        """
        ...


# ============================================================================
# Concrete Frame Types
# ============================================================================


@dataclass
class ReturnFrame:
    """Resume generator with value.

    When a sub-computation produces a value, send it to the generator.
    When an error occurs, throw it into the generator.
    """

    generator: Generator[Any, Any, Any]
    saved_env: Environment
    program_call: KleisliProgramCall | None = None

    def on_value(self, value: Any, env: Environment, store: dict[str, Any]) -> FrameResult:
        """Send value to generator and get next control."""
        # Note: Actual implementation in step.py handles generator interaction
        # This is here for protocol compliance
        return PopAndContinue(control=value, env=self.saved_env)

    def on_error(
        self, error: BaseException, captured_traceback: Any | None, env: Environment, store: dict[str, Any]
    ) -> FrameResult:
        """Throw error into generator."""
        return Propagate(error, captured_traceback)


@dataclass(frozen=True)
class LocalFrame:
    """Restore environment after scoped execution.

    Used by Local effect to temporarily modify environment.
    """

    restore_env: Environment

    def on_value(self, value: Any, env: Environment, store: dict[str, Any]) -> FrameResult:
        """Restore environment and continue with value."""
        return PopAndContinue(control=value, env=self.restore_env)

    def on_error(
        self, error: BaseException, captured_traceback: Any | None, env: Environment, store: dict[str, Any]
    ) -> FrameResult:
        """Restore environment and propagate error."""
        return Propagate(error, captured_traceback)


@dataclass(frozen=True)
class InterceptFrame:
    """Transform effects passing through.

    Used by Intercept effect to modify effects before handling.
    """

    transforms: tuple[Callable[[Effect], Effect | Program | None], ...]

    def on_value(self, value: Any, env: Environment, store: dict[str, Any]) -> FrameResult:
        """Pass through value unchanged."""
        return PopAndContinue(control=value, env=env)

    def on_error(
        self, error: BaseException, captured_traceback: Any | None, env: Environment, store: dict[str, Any]
    ) -> FrameResult:
        """Pass through error unchanged."""
        return Propagate(error, captured_traceback)


@dataclass(frozen=True)
class ListenFrame:
    """Capture log output from sub-computation.

    Used by Listen effect to collect log entries.
    """

    log_start_index: int

    def on_value(self, value: Any, env: Environment, store: dict[str, Any]) -> FrameResult:
        """Capture logs and wrap result in ListenResult."""
        from doeff._types_internal import ListenResult
        from doeff.utils import BoundedLog

        current_log = store.get("__log__", [])
        captured = current_log[self.log_start_index :]
        listen_result = ListenResult(value=value, log=BoundedLog(captured))
        return PopAndContinue(control=listen_result, env=env)

    def on_error(
        self, error: BaseException, captured_traceback: Any | None, env: Environment, store: dict[str, Any]
    ) -> FrameResult:
        """Propagate error without capturing logs."""
        return Propagate(error, captured_traceback)


@dataclass(frozen=True)
class GatherFrame:
    """Collect results from sequential program execution.

    Used by Gather effect to run multiple programs and collect results.
    """

    remaining_programs: list[Program]
    collected_results: list[Any]
    saved_env: Environment

    def on_value(self, value: Any, env: Environment, store: dict[str, Any]) -> FrameResult:
        """Collect value and continue with next program or finish."""
        new_results = self.collected_results + [value]

        if not self.remaining_programs:
            # All programs done
            return PopAndContinue(control=new_results, env=self.saved_env)

        # More programs to run - this is handled by step()
        # Return Continue to signal we need to run next program
        from doeff.cesk.state import ProgramControl

        next_prog, *rest = self.remaining_programs
        return Continue(
            control=ProgramControl(next_prog),
            env=self.saved_env,
        )

    def on_error(
        self, error: BaseException, captured_traceback: Any | None, env: Environment, store: dict[str, Any]
    ) -> FrameResult:
        """Abort gather on error."""
        return Propagate(error, captured_traceback)


@dataclass(frozen=True)
class SafeFrame:
    """Safe boundary - captures K stack on error, returns Result.

    Used by Safe effect to convert exceptions to Result[T, E].
    """

    saved_env: Environment

    def on_value(self, value: Any, env: Environment, store: dict[str, Any]) -> FrameResult:
        """Wrap successful value in Ok."""
        from doeff._vendor import Ok

        return PopAndContinue(control=Ok(value), env=self.saved_env)

    def on_error(
        self, error: BaseException, captured_traceback: Any | None, env: Environment, store: dict[str, Any]
    ) -> FrameResult:
        """Wrap error in Err with captured traceback."""
        from doeff._vendor import NOTHING, Err, Some

        captured_maybe = Some(captured_traceback) if captured_traceback else NOTHING
        err_result = Err(error, captured_traceback=captured_maybe)
        return PopAndContinue(control=err_result, env=self.saved_env)


# ============================================================================
# Multi-task Frame Types
# ============================================================================


@dataclass(frozen=True)
class JoinFrame:
    """Wait for a single task to complete.

    Used by TaskJoin effect to wait on a spawned task.
    """

    future_id: FutureId
    saved_env: Environment

    def on_value(self, value: Any, env: Environment, store: dict[str, Any]) -> FrameResult:
        """Resume with task result."""
        return PopAndContinue(control=value, env=self.saved_env)

    def on_error(
        self, error: BaseException, captured_traceback: Any | None, env: Environment, store: dict[str, Any]
    ) -> FrameResult:
        """Propagate task error."""
        return Propagate(error, captured_traceback)


@dataclass(frozen=True)
class MultiGatherFrame:
    """Wait for multiple concurrent tasks to complete (gather).

    Unlike GatherFrame which runs programs sequentially, this waits
    for concurrent tasks spawned earlier.
    """

    future_ids: tuple[FutureId, ...]
    completed_results: dict[FutureId, Any] = field(default_factory=dict)
    saved_env: Environment = field(default_factory=lambda: __import__("doeff._vendor", fromlist=["FrozenDict"]).FrozenDict())

    def on_child_done(
        self, future_id: FutureId, value: Any | None, error: BaseException | None
    ) -> tuple[MultiGatherFrame | None, FrameResult | None]:
        """Handle completion of one child task.

        Returns:
            (updated_frame, result) - updated frame if still waiting, or result if all done
        """
        if error is not None:
            # First error fails the whole gather
            return None, Propagate(error, None)

        new_completed = dict(self.completed_results)
        new_completed[future_id] = value

        if len(new_completed) == len(self.future_ids):
            # All done - collect results in order
            results = [new_completed[fid] for fid in self.future_ids]
            return None, PopAndContinue(control=results, env=self.saved_env)

        # Still waiting
        updated = MultiGatherFrame(
            future_ids=self.future_ids,
            completed_results=new_completed,
            saved_env=self.saved_env,
        )
        return updated, None

    def on_value(self, value: Any, env: Environment, store: dict[str, Any]) -> FrameResult:
        """Not used directly - results come through on_child_done."""
        return PopAndContinue(control=value, env=self.saved_env)

    def on_error(
        self, error: BaseException, captured_traceback: Any | None, env: Environment, store: dict[str, Any]
    ) -> FrameResult:
        return Propagate(error, captured_traceback)


@dataclass(frozen=True)
class RaceFrame:
    """Wait for first of multiple concurrent tasks to complete (race).

    First task to complete wins. Other tasks are cancelled.
    """

    future_ids: tuple[FutureId, ...]
    saved_env: Environment

    def on_child_done(
        self, future_id: FutureId, value: Any | None, error: BaseException | None
    ) -> tuple[None, FrameResult]:
        """Handle completion of first child task.

        Returns:
            (None, result) - race frame is always consumed on first completion
        """
        if error is not None:
            return None, Propagate(error, None)
        return None, PopAndContinue(control=value, env=self.saved_env)

    def on_value(self, value: Any, env: Environment, store: dict[str, Any]) -> FrameResult:
        return PopAndContinue(control=value, env=self.saved_env)

    def on_error(
        self, error: BaseException, captured_traceback: Any | None, env: Environment, store: dict[str, Any]
    ) -> FrameResult:
        return Propagate(error, captured_traceback)


# ============================================================================
# Type Aliases
# ============================================================================


Frame: TypeAlias = (
    ReturnFrame
    | LocalFrame
    | InterceptFrame
    | ListenFrame
    | GatherFrame
    | SafeFrame
    | JoinFrame
    | MultiGatherFrame
    | RaceFrame
)

Kontinuation: TypeAlias = list[Frame]


__all__ = [
    # Frame results
    "Continue",
    "PopAndContinue",
    "Propagate",
    "FrameResult",
    # Protocol
    "FrameProtocol",
    # Concrete frames
    "ReturnFrame",
    "LocalFrame",
    "InterceptFrame",
    "ListenFrame",
    "GatherFrame",
    "SafeFrame",
    # Multi-task frames
    "JoinFrame",
    "MultiGatherFrame",
    "RaceFrame",
    # Type aliases
    "Frame",
    "Kontinuation",
]
