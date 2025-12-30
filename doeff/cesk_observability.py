"""
Execution observability API for the CESK interpreter.

This module provides types and utilities for monitoring workflow execution,
including inspection of the continuation (K) stack and current effect.

Public API:
    - ExecutionStatus: Literal type for execution states
    - CodeLocation: Location information for a K frame
    - KFrameSnapshot: Snapshot of a single K frame
    - ExecutionSnapshot: Point-in-time snapshot of execution state
    - ExecutionMonitor: Live monitor for workflow execution

Example usage (callback-based):
    def log_step(snapshot: ExecutionSnapshot):
        print(f"Step {snapshot.step_count}: {snapshot.status}")
        if snapshot.current_effect:
            print(f"  Processing: {snapshot.current_effect}")
        print(f"  K depth: {len(snapshot.k_stack)}")

    result = run_sync(
        my_workflow(),
        storage=SQLiteStorage("workflow.db"),
        on_step=log_step,
    )
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Generic, Literal, TypeVar

if TYPE_CHECKING:
    from doeff.cesk import CESKState, Frame, Kontinuation, Store
    from doeff.storage import DurableStorage

T = TypeVar("T")

logger = logging.getLogger(__name__)

# Execution status literals
ExecutionStatus = Literal["pending", "running", "paused", "completed", "failed"]


@dataclass(frozen=True)
class CodeLocation:
    """
    Location information for a code point.

    Attributes:
        filename: Source file path.
        line: Line number in the source file.
        function: Function name where the code is located.
        code: Optional source code snippet.
    """

    filename: str
    line: int
    function: str
    code: str | None = None

    def format(self) -> str:
        """Format as 'filename:line in function'."""
        return f"{self.filename}:{self.line} in {self.function}"


@dataclass(frozen=True)
class KFrameSnapshot:
    """
    Snapshot of a single K frame for observability.

    Captures the essential information about a continuation frame
    without exposing internal implementation details.

    Attributes:
        frame_type: Type name of the frame (e.g., "ReturnFrame", "CatchFrame").
        location: Optional code location where this frame was created.
        description: Human-readable description of the frame's purpose.
    """

    frame_type: str
    location: CodeLocation | None
    description: str

    @classmethod
    def from_frame(cls, frame: "Frame") -> "KFrameSnapshot":
        """Create a snapshot from a CESK frame."""
        from doeff.cesk import (
            CatchFrame,
            FinallyFrame,
            GatherFrame,
            InterceptFrame,
            ListenFrame,
            LocalFrame,
            ReturnFrame,
        )

        frame_type = type(frame).__name__
        location = None
        description = ""

        if isinstance(frame, ReturnFrame):
            description = "Awaiting generator continuation"
            # Try to extract location from generator
            gen = frame.generator
            if hasattr(gen, "gi_frame") and gen.gi_frame is not None:
                gi_frame = gen.gi_frame
                location = CodeLocation(
                    filename=gi_frame.f_code.co_filename,
                    line=gi_frame.f_lineno,
                    function=gi_frame.f_code.co_name,
                )

        elif isinstance(frame, CatchFrame):
            description = "Error handler boundary"

        elif isinstance(frame, FinallyFrame):
            description = "Cleanup handler (runs on success or error)"

        elif isinstance(frame, LocalFrame):
            description = "Environment restore point"

        elif isinstance(frame, InterceptFrame):
            n_transforms = len(frame.transforms)
            description = f"Effect interceptor ({n_transforms} transforms)"

        elif isinstance(frame, ListenFrame):
            description = f"Log capture (started at index {frame.log_start_index})"

        elif isinstance(frame, GatherFrame):
            remaining = len(frame.remaining_programs)
            collected = len(frame.collected_results)
            description = f"Gathering results ({collected} done, {remaining} remaining)"

        else:
            description = f"Unknown frame type: {frame_type}"

        return cls(
            frame_type=frame_type,
            location=location,
            description=description,
        )


@dataclass(frozen=True)
class ExecutionSnapshot:
    """
    Point-in-time snapshot of execution state.

    Provides a read-only view of the interpreter's current state,
    useful for debugging, monitoring, and observability.

    Attributes:
        status: Current execution status.
        k_stack: Tuple of K frame snapshots (continuation stack).
        current_effect: Effect currently being processed, or None.
        step_count: Number of interpreter steps executed so far.
        cache_keys: Tuple of keys currently in durable storage.
    """

    status: ExecutionStatus
    k_stack: tuple[KFrameSnapshot, ...]
    current_effect: Any | None
    step_count: int
    cache_keys: tuple[str, ...]

    @classmethod
    def from_state(
        cls,
        state: "CESKState",
        status: ExecutionStatus,
        step_count: int,
        storage: "DurableStorage | None" = None,
    ) -> "ExecutionSnapshot":
        """Create a snapshot from the current CESK state."""
        from doeff.cesk import EffectControl

        # Build K stack snapshots
        k_stack = tuple(KFrameSnapshot.from_frame(frame) for frame in state.K)

        # Get current effect if in effect control state
        current_effect = None
        if isinstance(state.C, EffectControl):
            current_effect = state.C.effect

        # Get cache keys from storage
        cache_keys: tuple[str, ...] = ()
        if storage is not None:
            try:
                cache_keys = tuple(storage.keys())
            except Exception as e:
                logger.warning("Failed to capture cache keys from storage: %s", e)
                # Continue with empty cache keys to prevent snapshot creation from failing

        return cls(
            status=status,
            k_stack=k_stack,
            current_effect=current_effect,
            step_count=step_count,
            cache_keys=cache_keys,
        )


@dataclass
class ExecutionMonitor(Generic[T]):
    """
    Live monitor for workflow execution.

    Provides read-only access to interpreter state.
    Thread-safe for external observation.

    This class is typically not instantiated directly; it's returned
    by run_workflow() for async monitoring scenarios.

    Attributes:
        status: Current execution status (property).
        k_stack: Current K stack as frame snapshots (property).
        current_effect: Effect currently being processed (property).
        step_count: Number of interpreter steps executed (property).
    """

    _state: "CESKState | None" = field(default=None)
    _status: ExecutionStatus = field(default="pending")
    _step_count: int = field(default=0)
    _storage: "DurableStorage | None" = field(default=None)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def snapshot(self) -> ExecutionSnapshot:
        """Get current execution state snapshot."""
        with self._lock:
            if self._state is None:
                return ExecutionSnapshot(
                    status=self._status,
                    k_stack=(),
                    current_effect=None,
                    step_count=self._step_count,
                    cache_keys=(),
                )
            return ExecutionSnapshot.from_state(
                self._state,
                self._status,
                self._step_count,
                self._storage,
            )

    @property
    def status(self) -> ExecutionStatus:
        """Current execution status."""
        with self._lock:
            return self._status

    @property
    def k_stack(self) -> tuple[KFrameSnapshot, ...]:
        """Current K stack (pending continuations)."""
        with self._lock:
            if self._state is None:
                return ()
            return tuple(KFrameSnapshot.from_frame(f) for f in self._state.K)

    @property
    def current_effect(self) -> Any | None:
        """Effect currently being processed, or None."""
        with self._lock:
            if self._state is None:
                return None
            from doeff.cesk import EffectControl

            if isinstance(self._state.C, EffectControl):
                return self._state.C.effect
            return None

    @property
    def step_count(self) -> int:
        """Number of interpreter steps executed."""
        with self._lock:
            return self._step_count

    def get_cache_entries(self) -> dict[str, Any]:
        """Get all cache entries (via storage)."""
        with self._lock:
            if self._storage is None:
                return {}
            try:
                return dict(self._storage.items())
            except Exception:
                return {}

    def _update(
        self,
        state: "CESKState | None" = None,
        status: ExecutionStatus | None = None,
        step_count: int | None = None,
    ) -> None:
        """Internal method to update monitor state (called by interpreter)."""
        with self._lock:
            if state is not None:
                self._state = state
            if status is not None:
                self._status = status
            if step_count is not None:
                self._step_count = step_count


# Type alias for on_step callback
OnStepCallback = Callable[[ExecutionSnapshot], None]


__all__ = [
    # Types
    "ExecutionStatus",
    "CodeLocation",
    "KFrameSnapshot",
    "ExecutionSnapshot",
    "ExecutionMonitor",
    "OnStepCallback",
]
