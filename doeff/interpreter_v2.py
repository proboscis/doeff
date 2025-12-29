"""
Trampolined interpreter with explicit continuation management.

This module contains the TrampolinedInterpreter that executes programs
using an explicit continuation stack instead of recursive calls.

Key properties:
- NO recursive calls to run/run_async
- Single async boundary per effect (not per sub-program)
- Explicit state machine with defined transitions
- Supports cancellation via UNWINDING phase
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Generator
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Generic, TypeVar, Union

from doeff._vendor import Err, Ok, WGraph, WNode, WStep
from doeff.effects import (
    AskEffect,
    AtomicGetEffect,
    AtomicUpdateEffect,
    CacheGetEffect,
    CachePutEffect,
    DepInjectEffect,
    FutureAwaitEffect,
    FutureParallelEffect,
    GatherDictEffect,
    GatherEffect,
    GraphAnnotateEffect,
    GraphCaptureEffect,
    GraphSnapshotEffect,
    GraphStepEffect,
    InterceptEffect,
    IOPerformEffect,
    IOPrintEffect,
    LocalEffect,
    MemoGetEffect,
    MemoPutEffect,
    ProgramCallFrameEffect,
    ProgramCallStackEffect,
    ResultCatchEffect,
    ResultFailEffect,
    ResultFinallyEffect,
    ResultFirstSuccessEffect,
    ResultRecoverEffect,
    ResultRetryEffect,
    ResultSafeEffect,
    ResultUnwrapEffect,
    SpawnBackend,
    SpawnEffect,
    StateGetEffect,
    StateModifyEffect,
    StatePutEffect,
    TaskJoinEffect,
    ThreadEffect,
    WriterListenEffect,
    WriterTellEffect,
)
from doeff.handlers import (
    AtomicEffectHandler,
    CacheEffectHandler,
    FutureEffectHandler,
    GraphEffectHandler,
    IOEffectHandler,
    MemoEffectHandler,
    ReaderEffectHandler,
    ResultEffectHandler,
    SpawnEffectHandler,
    StateEffectHandler,
    ThreadEffectHandler,
    WriterEffectHandler,
)
from doeff.program import KleisliProgramCall, Program
from doeff.types import (
    CallFrame,
    Effect,
    EffectFailure,
    EffectObservation,
    EnvKey,
    ExecutionContext,
    RunResult,
    capture_traceback,
)
from doeff.utils import BoundedLog

if TYPE_CHECKING:
    pass

T = TypeVar("T")

logger = logging.getLogger(__name__)

# Sentinel value to distinguish "no handler found" from "handler returned None"
_NO_HANDLER = object()


def _effect_is(effect: Effect, cls: type) -> bool:
    """Return True if effect is instance of cls, tolerant to module reloads."""
    return isinstance(effect, cls) or effect.__class__.__name__ == cls.__name__


# ============================================
# Frame State Enum
# ============================================

class FrameState(Enum):
    """Lifecycle state of a continuation frame."""
    ACTIVE = auto()      # Frame is running
    COMPLETED = auto()   # Frame returned normally
    FAILED = auto()      # Frame raised an exception
    CANCELLED = auto()   # Frame was cancelled


# ============================================
# Frame Result Types
# ============================================

@dataclass(frozen=True)
class FrameResultYield:
    """Frame yielded an Effect or Program."""
    item: Effect | Program


@dataclass(frozen=True)
class FrameResultReturn:
    """Frame completed with return value."""
    value: Any


@dataclass(frozen=True)
class FrameResultRaise:
    """Frame raised an exception."""
    exception: BaseException


FrameResult = Union[FrameResultYield, FrameResultReturn, FrameResultRaise]


# ============================================
# Continuation Frame
# ============================================

class InvalidFrameStateError(Exception):
    """Raised when attempting invalid operation on a frame."""


@dataclass
class ContinuationFrame:
    """
    A single frame in the continuation stack.

    Semantics:
    - Wraps a Python generator that yields Effects/Programs
    - Tracks handler scope for proper effect routing
    - Maintains error state for exception propagation
    """

    # The generator representing this computation
    generator: Generator[Effect | Program, Any, Any]

    # Source program info for debugging
    source_info: CallFrame | None

    # Original program (for reference)
    original_program: Program | None = None

    # Error state: exception being propagated through this frame
    pending_exception: BaseException | None = None

    # Frame state for lifecycle management
    state: FrameState = FrameState.ACTIVE

    def resume(self, value: Any) -> FrameResult:
        """
        Resume this frame with a value.

        Returns:
            FrameResultYield(item) - Frame yielded an Effect or Program
            FrameResultReturn(value) - Frame completed with return value
            FrameResultRaise(exc) - Frame raised an exception
        """
        if self.state != FrameState.ACTIVE:
            raise InvalidFrameStateError(f"Cannot resume frame in state {self.state}")

        try:
            next_item = self.generator.send(value)
            return FrameResultYield(next_item)
        except StopIteration as e:
            self.state = FrameState.COMPLETED
            return FrameResultReturn(e.value)
        except BaseException as exc:
            self.state = FrameState.FAILED
            return FrameResultRaise(exc)

    def throw(self, exc: BaseException) -> FrameResult:
        """
        Throw an exception into this frame.

        The generator may catch it and continue, or propagate it.
        """
        if self.state != FrameState.ACTIVE:
            raise InvalidFrameStateError(f"Cannot throw into frame in state {self.state}")

        try:
            next_item = self.generator.throw(exc)
            return FrameResultYield(next_item)
        except StopIteration as e:
            self.state = FrameState.COMPLETED
            return FrameResultReturn(e.value)
        except BaseException as propagated:
            self.state = FrameState.FAILED
            return FrameResultRaise(propagated)

    def close(self) -> None:
        """Clean up this frame (for cancellation)."""
        if self.state == FrameState.ACTIVE:
            try:
                self.generator.close()
            finally:
                self.state = FrameState.CANCELLED


# ============================================
# Step Action Types
# ============================================

@dataclass(frozen=True)
class StepActionContinue:
    """Continue processing - no async boundary crossed."""


@dataclass(frozen=True)
class StepActionYieldEffect:
    """Yield to effect handler - this is the async boundary."""
    effect: Effect


@dataclass(frozen=True)
class StepActionDone(Generic[T]):
    """Interpretation complete."""
    value: T


@dataclass(frozen=True)
class StepActionError:
    """Interpretation failed."""
    exception: BaseException
    stack_snapshot: tuple[CallFrame | None, ...]


StepAction = Union[StepActionContinue, StepActionYieldEffect, StepActionDone, StepActionError]


# ============================================
# Interpretation Phase
# ============================================

class InterpretationPhase(Enum):
    """Current phase of interpretation."""
    INITIALIZING = auto()
    STEPPING = auto()
    AWAITING_EFFECT = auto()
    PROPAGATING_ERROR = auto()
    UNWINDING = auto()  # Cancellation in progress
    COMPLETED = auto()
    FAILED = auto()


# ============================================
# Interpretation Stats
# ============================================

@dataclass
class InterpretationStats:
    """
    Statistics collected during interpretation.

    Collection Cost Model:
        - All operations are O(1) increments
        - No heap allocations during collection
        - No synchronization (single-threaded interpreter)
    """

    # Step counter: incremented once per iteration of main loop
    total_steps: int = 0

    # Effect counter: incremented when effect handler returns successfully
    total_effects_handled: int = 0

    # Frame counter: incremented in push_frame()
    total_frames_created: int = 0

    # High-water mark: max() comparison in push_frame()
    max_stack_depth: int = 0

    # Exception counter: incremented when frame catches exception
    total_exceptions_caught: int = 0

    # Timing
    start_time_ns: int | None = None
    end_time_ns: int | None = None

    @property
    def duration_ns(self) -> int | None:
        if self.start_time_ns and self.end_time_ns:
            return self.end_time_ns - self.start_time_ns
        return None

    def copy(self) -> InterpretationStats:
        return InterpretationStats(
            total_steps=self.total_steps,
            total_effects_handled=self.total_effects_handled,
            total_frames_created=self.total_frames_created,
            max_stack_depth=self.max_stack_depth,
            total_exceptions_caught=self.total_exceptions_caught,
            start_time_ns=self.start_time_ns,
            end_time_ns=self.end_time_ns,
        )


# ============================================
# Interpreter State Snapshot
# ============================================

@dataclass(frozen=True)
class InterpreterStateSnapshot:
    """Immutable snapshot for debugging/logging."""
    stack_depth: int
    frame_infos: tuple[CallFrame | None, ...]
    phase: InterpretationPhase
    stats: InterpretationStats


# ============================================
# Interpreter State
# ============================================

@dataclass
class InterpreterState:
    """
    Complete interpreter state - explicit, introspectable.

    Invariants:
    - continuation_stack is never empty during active interpretation
    - current_item is the item being processed (Effect, Program, or value)
    - At most one frame is in ACTIVE state with pending work
    """

    # The continuation stack - LIFO order (top = current frame)
    continuation_stack: list[ContinuationFrame]

    # Current item being processed
    current_item: Effect | Program | Any | None

    # Shared execution context (mutable, handlers can modify)
    context: ExecutionContext

    # Interpretation phase
    phase: InterpretationPhase

    # Statistics for monitoring
    stats: InterpretationStats

    # Exception being propagated (if in PROPAGATING_ERROR phase)
    propagating_exception: BaseException | None = None

    # Effect that caused the exception (for error reporting)
    failed_effect: Effect | None = None

    # Stack snapshot captured when error first occurred (before frames are popped)
    stack_at_error: tuple[CallFrame | None, ...] | None = None

    # Program call stack snapshot at error time (for EffectFailure)
    call_stack_at_error: tuple[CallFrame, ...] | None = None

    def push_frame(self, frame: ContinuationFrame) -> None:
        """Push a new frame onto the stack (entering sub-program)."""
        self.continuation_stack.append(frame)
        self.stats.max_stack_depth = max(
            self.stats.max_stack_depth,
            len(self.continuation_stack)
        )
        self.stats.total_frames_created += 1

    def pop_frame(self) -> ContinuationFrame | None:
        """Pop the top frame (exiting sub-program)."""
        if self.continuation_stack:
            return self.continuation_stack.pop()
        return None

    @property
    def current_frame(self) -> ContinuationFrame | None:
        """The frame that will receive the next value."""
        return self.continuation_stack[-1] if self.continuation_stack else None

    @property
    def stack_depth(self) -> int:
        """Current continuation stack depth."""
        return len(self.continuation_stack)

    def snapshot(self) -> InterpreterStateSnapshot:
        """Create immutable snapshot for debugging/logging."""
        return InterpreterStateSnapshot(
            stack_depth=self.stack_depth,
            frame_infos=tuple(f.source_info for f in self.continuation_stack),
            phase=self.phase,
            stats=self.stats.copy()
        )

    def start_error_propagation(
        self,
        exc: BaseException,
        failed_effect: Effect | None = None
    ) -> None:
        """
        Transition to PROPAGATING_ERROR phase, capturing stack state.

        Only captures the stack snapshot on the FIRST error (to preserve context
        when exception is re-raised or transformed during propagation).
        """
        self.propagating_exception = exc
        if failed_effect is not None:
            self.failed_effect = failed_effect
        self.phase = InterpretationPhase.PROPAGATING_ERROR

        # Only capture stack on first error
        if self.stack_at_error is None:
            self.stack_at_error = tuple(f.source_info for f in self.continuation_stack)
            self.call_stack_at_error = tuple(self.context.program_call_stack)

    def clear_error_state(self) -> None:
        """Clear error state when exception is caught and handled."""
        self.propagating_exception = None
        self.failed_effect = None
        self.stack_at_error = None
        self.call_stack_at_error = None


# ============================================
# Interpreter Exceptions
# ============================================

class InterpreterException(Exception):
    """Base class for all interpreter-originated exceptions."""


class ContinuationStackOverflowError(InterpreterException):
    """
    Raised when continuation stack exceeds configured limit.
    """
    def __init__(
        self,
        message: str,
        stack_snapshot: InterpreterStateSnapshot,
        max_depth: int,
        actual_depth: int
    ):
        super().__init__(message)
        self.stack_snapshot = stack_snapshot
        self.max_depth = max_depth
        self.actual_depth = actual_depth


class InterpreterInvariantError(InterpreterException):
    """
    Raised when an interpreter invariant is violated.
    """


class InterpreterReentrancyError(InterpreterException):
    """
    Raised when interpreter is called reentrantly.
    """


# ============================================
# Effect Stack Trace Types
# ============================================

class EffectStackFrameType(Enum):
    """Types of frames in an effect stack trace."""

    KLEISLI_CALL = auto()      # KleisliProgram invocation: fetch_user(id=123)
    EFFECT_YIELD = auto()      # Effect yielded: yield db.query(...)
    PROGRAM_FLAT_MAP = auto()  # flat_map chain: .flat_map(lambda x: ...)
    HANDLER_BOUNDARY = auto()  # Entered an effect handler: catch(...), recover(...)
    SPAWN_BOUNDARY = auto()    # Crossed spawn boundary: spawn(sub_program)


@dataclass(frozen=True)
class PythonLocation:
    """A location in Python source code."""

    filename: str
    line: int
    function: str
    code: str | None = None

    def format(self) -> str:
        loc = f"{self.filename}:{self.line} in {self.function}"
        if self.code:
            return f"{loc}\n    {self.code}"
        return loc


@dataclass(frozen=True)
class EffectStackFrame:
    """
    A single frame in the effect stack trace.

    Derived from ContinuationFrame.source_info (CallFrame).
    """

    # What kind of frame is this?
    frame_type: EffectStackFrameType

    # Name to display (function name, effect name, etc.)
    name: str

    # Source location
    location: PythonLocation | None

    # For KleisliProgram calls: the arguments (for debugging)
    call_args: tuple[Any, ...] | None = None
    call_kwargs: dict[str, Any] | None = None

    # The raw CallFrame for advanced introspection
    raw_frame: CallFrame | None = None


@dataclass(frozen=True)
class EffectStackTrace:
    """
    Complete effect call stack derived from InterpreterStateSnapshot.

    This replaces the scattered reconstruction in current display().
    """

    # The frames, ordered from outermost (entry point) to innermost (failure point)
    frames: tuple[EffectStackFrame, ...]

    # The effect that failed (if applicable)
    failed_effect: Effect | None

    # The original Python exception
    original_exception: BaseException

    # Python location where exception was raised
    python_raise_location: PythonLocation | None


# ============================================
# Effect Stack Trace Renderer
# ============================================

class EffectStackTraceRenderer:
    """Renders EffectStackTrace for human consumption."""

    def __init__(
        self,
        max_frames: int | None = None,  # None = unlimited
        head_frames: int = 10,           # Frames to keep from start
    ):
        self.max_frames = max_frames
        self.head_frames = head_frames

    def render(self, trace: EffectStackTrace) -> str:
        lines: list[str] = []

        # Header
        exc_type = type(trace.original_exception).__name__
        exc_msg = str(trace.original_exception)
        lines.append(f"EffectError: {exc_type}: {exc_msg}")
        lines.append("")

        # Effect Call Stack
        lines.append("Effect Call Stack (most recent call last):")
        lines.append("")

        frames_to_show, omitted = self._maybe_truncate(trace.frames)
        shown_count = 0

        for i, frame in enumerate(frames_to_show):
            if omitted > 0 and shown_count == self.head_frames:
                lines.append(f"  ... ({omitted} frames omitted) ...")
                lines.append("")

            indent = "  "

            # Format based on frame type
            if frame.frame_type == EffectStackFrameType.KLEISLI_CALL:
                # Show as function call with args
                args_str = self._format_args(frame.call_args, frame.call_kwargs)
                lines.append(f"{indent}→ {frame.name}({args_str})")

            elif frame.frame_type == EffectStackFrameType.EFFECT_YIELD:
                lines.append(f"{indent}⚡ yield {frame.name}")

            elif frame.frame_type == EffectStackFrameType.HANDLER_BOUNDARY:
                lines.append(f"{indent}↳ [handler: {frame.name}]")

            elif frame.frame_type == EffectStackFrameType.SPAWN_BOUNDARY:
                lines.append(f"{indent}⎇ [spawn: {frame.name}]")

            elif frame.frame_type == EffectStackFrameType.PROGRAM_FLAT_MAP:
                lines.append(f"{indent}  .flat_map → {frame.name}")

            # Add location if available
            if frame.location:
                lines.append(f"{indent}    at {frame.location.filename}:{frame.location.line}")
                if frame.location.code:
                    lines.append(f"{indent}    │ {frame.location.code}")

            shown_count += 1

        lines.append("")

        # Python Exception Location
        if trace.python_raise_location:
            loc = trace.python_raise_location
            lines.append("Exception raised at:")
            lines.append(f'  File "{loc.filename}", line {loc.line}, in {loc.function}')
            if loc.code:
                lines.append(f"    {loc.code}")

        return "\n".join(lines)

    def _format_args(
        self,
        args: tuple[Any, ...] | None,
        kwargs: dict[str, Any] | None
    ) -> str:
        parts: list[str] = []
        if args:
            for arg in args[:3]:  # Limit displayed args
                parts.append(self._format_value(arg))
            if len(args) > 3:
                parts.append("...")
        if kwargs:
            for key, value in list(kwargs.items())[:2]:  # Limit displayed kwargs
                parts.append(f"{key}={self._format_value(value)}")
            if len(kwargs) > 2:
                parts.append("...")
        return ", ".join(parts)

    def _format_value(self, value: Any, max_length: int = 30) -> str:
        text = repr(value)
        if len(text) > max_length:
            return text[:max_length - 3] + "..."
        return text

    def _maybe_truncate(
        self,
        frames: tuple[EffectStackFrame, ...]
    ) -> tuple[tuple[EffectStackFrame, ...], int]:
        if self.max_frames is None or len(frames) <= self.max_frames:
            return frames, 0

        # Clamp head_frames to at most max_frames
        effective_head = min(self.head_frames, self.max_frames)
        tail_frames = max(0, self.max_frames - effective_head)
        omitted = len(frames) - self.max_frames

        if tail_frames == 0:
            return (frames[:effective_head], omitted)

        return (
            frames[:effective_head] + frames[-tail_frames:],
            omitted
        )


# ============================================
# Trampolined Interpreter
# ============================================

class TrampolinedInterpreter:
    """
    Interpreter with explicit continuation management.

    Key properties:
    - NO recursive calls to run/run_async
    - Single async boundary per effect (not per sub-program)
    - Explicit state machine with defined transitions
    - Supports cancellation via UNWINDING phase
    """

    def __init__(
        self,
        custom_handlers: dict[str, Any] | None = None,
        *,
        max_log_entries: int | None = None,
        max_stack_depth: int = 10000,
        spawn_default_backend: SpawnBackend = "thread",
        spawn_thread_max_workers: int | None = None,
        spawn_process_max_workers: int | None = None,
        spawn_ray_address: str | None = None,
        spawn_ray_init_kwargs: dict[str, Any] | None = None,
        spawn_ray_runtime_env: dict[str, Any] | None = None,
        capture_stack_trace: bool = True,
    ):
        """Initialize effect handlers.

        Args:
            custom_handlers: Optional dict mapping effect categories to custom handlers.
            max_log_entries: Optional cap on the number of writer log entries retained.
            max_stack_depth: Maximum continuation stack depth before overflow error.
            spawn_default_backend: Default backend for Spawn effects.
            spawn_thread_max_workers: Max worker threads for Spawn thread backend.
            spawn_process_max_workers: Max worker processes for Spawn process backend.
            spawn_ray_address: Ray cluster address for Spawn Ray backend.
            spawn_ray_init_kwargs: Extra kwargs passed to ray.init().
            spawn_ray_runtime_env: Default runtime_env for Ray tasks.
            capture_stack_trace: If False, effect_stack_trace will be None on error.
        """
        if max_log_entries is not None and max_log_entries < 0:
            raise ValueError("max_log_entries must be >= 0 or None")

        self._max_log_entries = max_log_entries
        self._max_stack_depth = max_stack_depth
        self._capture_stack_trace = capture_stack_trace
        # Note: We intentionally don't have a reentrancy guard here.
        # Handlers can and do call interpreter.run_async() to run sub-programs.
        # This is safe because each call has its own InterpreterState.

        # Initialize default handlers
        handlers = {
            "reader": ReaderEffectHandler(),
            "state": StateEffectHandler(),
            "atomic": AtomicEffectHandler(),
            "writer": WriterEffectHandler(),
            "future": FutureEffectHandler(),
            "thread": ThreadEffectHandler(),
            "spawn": SpawnEffectHandler(
                default_backend=spawn_default_backend,
                thread_max_workers=spawn_thread_max_workers,
                process_max_workers=spawn_process_max_workers,
                ray_address=spawn_ray_address,
                ray_init_kwargs=spawn_ray_init_kwargs,
                ray_runtime_env=spawn_ray_runtime_env,
                max_log_entries=max_log_entries,
            ),
            "result": ResultEffectHandler(),
            "io": IOEffectHandler(),
            "graph": GraphEffectHandler(),
            "memo": MemoEffectHandler(),
            "cache": CacheEffectHandler(),
        }

        # Override with custom handlers if provided
        if custom_handlers:
            handlers.update(custom_handlers)

        # Set handlers as attributes
        self.reader_handler = handlers["reader"]
        self.state_handler = handlers["state"]
        self.atomic_handler = handlers["atomic"]
        self.writer_handler = handlers["writer"]
        self.future_handler = handlers["future"]
        self.thread_handler = handlers["thread"]
        self.spawn_handler = handlers["spawn"]
        self.result_handler = handlers["result"]
        self.io_handler = handlers["io"]
        self.graph_handler = handlers["graph"]
        self.memo_handler = handlers["memo"]
        self.cache_handler = handlers["cache"]

    def _new_log_buffer(self) -> BoundedLog:
        """Return a fresh log buffer respecting the configured limit."""
        return BoundedLog(max_entries=self._max_log_entries)

    def _ensure_log_buffer(self, ctx: ExecutionContext) -> None:
        """Ensure the execution context uses a bounded log with the configured limit."""
        log = ctx.log
        if isinstance(log, BoundedLog):
            log.set_max_entries(self._max_log_entries)
        else:
            ctx.log = BoundedLog(log, max_entries=self._max_log_entries)

    def run(
        self,
        program: Program[T],
        context: ExecutionContext | None = None
    ) -> RunResult[T]:
        """
        Run a program with full monad support (synchronous interface).

        Returns a RunResult[T] containing:
        - context: final execution context (state, log, graph)
        - result: Ok(value) or Err(error)
        """
        return asyncio.run(self.run_async(program, context))

    async def run_async(
        self,
        program: Program[T],
        context: ExecutionContext | None = None
    ) -> RunResult[T]:
        """
        Run a program with trampolined execution (async interface).

        This is the main entry point for async execution.
        Handlers can call this method to run sub-programs; each call has its own state.
        """
        ctx = context or ExecutionContext(
            env={},
            state={},
            log=self._new_log_buffer(),
            graph=WGraph(
                last=WStep(inputs=(), output=WNode("_root"), meta={}),
                steps=frozenset(),
            ),
            io_allowed=True,
            program_call_stack=[],
        )

        self._ensure_log_buffer(ctx)

        state = self._initialize(program, ctx)

        # Check if program immediately completed during initialization
        if state.phase == InterpretationPhase.COMPLETED:
            return RunResult(ctx, Ok(state.current_item))

        # Check if program failed during initialization
        if state.phase == InterpretationPhase.FAILED:
            exc = state.propagating_exception
            effect_failure = self._wrap_exception(exc, state, None)
            return RunResult(ctx, Err(effect_failure))

        # TRAMPOLINED LOOP - single level of async
        while state.phase in (
            InterpretationPhase.STEPPING,
            InterpretationPhase.AWAITING_EFFECT,
            InterpretationPhase.PROPAGATING_ERROR
        ):
            state.stats.total_steps += 1

            # Check stack depth limit
            if state.stack_depth > self._max_stack_depth:
                return self._fail_with_overflow(state)

            try:
                if state.phase == InterpretationPhase.STEPPING:
                    action = self._step_once(state)

                    if isinstance(action, StepActionContinue):
                        continue

                    if isinstance(action, StepActionYieldEffect):
                        state.phase = InterpretationPhase.AWAITING_EFFECT
                        # Handle effect - THIS IS THE ONLY ASYNC BOUNDARY
                        try:
                            value = await self._handle_effect(action.effect, state.context)
                            state.stats.total_effects_handled += 1
                            # Resume generator with value - DO NOT use _step_once
                            # to avoid auto-executing Programs returned from handlers
                            resume_action = self._resume_with_value(state, value)
                            if isinstance(resume_action, StepActionYieldEffect):
                                # Generator yielded another effect - stay in this state
                                action = resume_action
                                continue
                            if isinstance(resume_action, StepActionDone):
                                state.phase = InterpretationPhase.COMPLETED
                                state.stats.end_time_ns = time.perf_counter_ns()
                                return RunResult(state.context, Ok(resume_action.value))
                            if isinstance(resume_action, StepActionError):
                                state.phase = InterpretationPhase.FAILED
                                state.stats.end_time_ns = time.perf_counter_ns()
                                effect_failure = self._wrap_exception(
                                    resume_action.exception, state, state.failed_effect
                                )
                                return RunResult(state.context, Err(effect_failure))
                            # StepActionContinue - check if we're in error propagation
                            # (phase would be PROPAGATING_ERROR if _resume_with_value
                            # encountered an exception)
                            if state.phase != InterpretationPhase.PROPAGATING_ERROR:
                                state.phase = InterpretationPhase.STEPPING
                        except (asyncio.CancelledError, KeyboardInterrupt):
                            # Let cancellation/interrupt propagate without wrapping
                            raise
                        except BaseException as exc:
                            state.current_item = None
                            state.start_error_propagation(exc, action.effect)

                    elif isinstance(action, StepActionDone):
                        state.phase = InterpretationPhase.COMPLETED
                        state.stats.end_time_ns = time.perf_counter_ns()
                        return RunResult(state.context, Ok(action.value))

                    elif isinstance(action, StepActionError):
                        state.phase = InterpretationPhase.FAILED
                        state.stats.end_time_ns = time.perf_counter_ns()
                        effect_failure = self._wrap_exception(
                            action.exception,
                            state,
                            state.failed_effect  # Use tracked failed effect
                        )
                        return RunResult(state.context, Err(effect_failure))

                elif state.phase == InterpretationPhase.PROPAGATING_ERROR:
                    action = self._propagate_error_step(state)

                    if isinstance(action, StepActionContinue):
                        continue

                    if isinstance(action, StepActionDone):
                        state.phase = InterpretationPhase.COMPLETED
                        state.stats.end_time_ns = time.perf_counter_ns()
                        return RunResult(state.context, Ok(action.value))

                    if isinstance(action, StepActionError):
                        state.phase = InterpretationPhase.FAILED
                        state.stats.end_time_ns = time.perf_counter_ns()
                        effect_failure = self._wrap_exception(
                            action.exception,
                            state,
                            state.failed_effect  # Use tracked failed effect
                        )
                        return RunResult(state.context, Err(effect_failure))

                    if isinstance(action, StepActionYieldEffect):
                        # Exception was caught, now yielding an effect
                        state.phase = InterpretationPhase.AWAITING_EFFECT
                        state.clear_error_state()  # Clear error state since we recovered
                        try:
                            value = await self._handle_effect(action.effect, state.context)
                            state.stats.total_effects_handled += 1
                            # Resume generator with value - DO NOT use _step_once
                            # to avoid auto-executing Programs returned from handlers
                            resume_action = self._resume_with_value(state, value)
                            if isinstance(resume_action, StepActionYieldEffect):
                                # Generator yielded another effect - stay in this state
                                action = resume_action
                                continue
                            if isinstance(resume_action, StepActionDone):
                                state.phase = InterpretationPhase.COMPLETED
                                state.stats.end_time_ns = time.perf_counter_ns()
                                return RunResult(state.context, Ok(resume_action.value))
                            if isinstance(resume_action, StepActionError):
                                state.phase = InterpretationPhase.FAILED
                                state.stats.end_time_ns = time.perf_counter_ns()
                                effect_failure = self._wrap_exception(
                                    resume_action.exception, state, state.failed_effect
                                )
                                return RunResult(state.context, Err(effect_failure))
                            # StepActionContinue - check if we're in error propagation
                            # (phase would be PROPAGATING_ERROR if _resume_with_value
                            # encountered an exception)
                            if state.phase != InterpretationPhase.PROPAGATING_ERROR:
                                state.phase = InterpretationPhase.STEPPING
                        except (asyncio.CancelledError, KeyboardInterrupt):
                            # Let cancellation/interrupt propagate without wrapping
                            raise
                        except BaseException as exc:
                            state.current_item = None
                            state.start_error_propagation(exc, action.effect)

            except (asyncio.CancelledError, KeyboardInterrupt):
                # Cancellation/interrupt requested - unwind stack and re-raise
                state.phase = InterpretationPhase.UNWINDING
                self._unwind_stack(state)
                raise
            except BaseException as exc:
                # Interpreter error (e.g., invalid yield, invariant violation)
                # Wrap and return as error result
                state.phase = InterpretationPhase.FAILED
                state.stats.end_time_ns = time.perf_counter_ns()
                effect_failure = self._wrap_exception(exc, state, state.failed_effect)
                return RunResult(state.context, Err(effect_failure))

        # Should not reach here
        raise InterpreterInvariantError(f"Unexpected phase: {state.phase}")

    def _initialize(
        self,
        program: Program[T],
        ctx: ExecutionContext
    ) -> InterpreterState:
        """Initialize interpreter state from a program."""
        from doeff.types import EffectBase

        state = InterpreterState(
            continuation_stack=[],
            current_item=None,
            context=ctx,
            phase=InterpretationPhase.INITIALIZING,
            stats=InterpretationStats(start_time_ns=time.perf_counter_ns())
        )

        # Handle KleisliProgramCall - push call frame
        call_frame_pushed = False
        if isinstance(program, KleisliProgramCall):
            if program.kleisli_source is not None:
                frame = CallFrame(
                    kleisli=program.kleisli_source,
                    function_name=program.function_name,
                    args=program.args,
                    kwargs=program.kwargs,
                    depth=len(ctx.program_call_stack),
                    created_at=program.created_at,
                )
                ctx.program_call_stack.append(frame)
                call_frame_pushed = True

        # Handle effect as direct program
        if isinstance(program, EffectBase):
            state.current_item = program
            state.phase = InterpretationPhase.STEPPING
            return state

        # Create generator
        to_gen = getattr(program, "to_generator", None)
        if to_gen is None:
            raise TypeError(
                f"Program {program!r} does not implement to_generator(); cannot execute"
            )
        gen = to_gen()

        # Create initial frame
        source_info = None
        if isinstance(program, KleisliProgramCall) and call_frame_pushed:
            source_info = ctx.program_call_stack[-1] if ctx.program_call_stack else None

        frame = ContinuationFrame(
            generator=gen,
            source_info=source_info,
            original_program=program,
        )

        # Get first yielded item
        try:
            first_item = next(gen)
            # Validate first yielded item is Effect or Program
            if not isinstance(first_item, (EffectBase, Program)):
                raise TypeError(
                    f"Program yielded invalid type {type(first_item).__name__!r}; "
                    f"expected Effect or Program, got {first_item!r}"
                )
            state.current_item = first_item
            state.push_frame(frame)
            state.phase = InterpretationPhase.STEPPING
        except StopIteration as e:
            # Program immediately returned
            state.current_item = e.value
            state.phase = InterpretationPhase.COMPLETED
            if call_frame_pushed:
                ctx.program_call_stack.pop()
        except BaseException as exc:
            # Program failed on first step - capture call stack BEFORE popping
            state.stack_at_error = tuple(f.source_info for f in state.continuation_stack)
            state.call_stack_at_error = tuple(ctx.program_call_stack)
            state.propagating_exception = exc
            state.phase = InterpretationPhase.FAILED
            if call_frame_pushed:
                ctx.program_call_stack.pop()

        return state

    def _step_once(self, state: InterpreterState) -> StepAction:
        """
        Execute exactly one step. Returns control instruction.

        CRITICAL: This method NEVER recurses and NEVER awaits.
        All async work happens in the main loop via YieldEffect.

        Invariants enforced:
        - INV-S1: Only one frame is ACTIVE at a time
        - INV-S2: current_item is valid for the current phase
        """
        from doeff.program import ProgramBase
        from doeff.types import EffectBase

        # INV-S1: Check that we're in a valid stepping state
        assert state.phase == InterpretationPhase.STEPPING, (
            f"_step_once called in invalid phase: {state.phase}"
        )

        current = state.current_item

        # Case 1: Effect - yield to handler
        if isinstance(current, EffectBase):
            return StepActionYieldEffect(current)

        # Case 2: Program - push new frame (NO RECURSION)
        if isinstance(current, ProgramBase):
            return self._enter_subprogram(state, current)

        # Case 3: Value - resume current frame
        return self._resume_with_value(state, current)

    def _enter_subprogram(
        self,
        state: InterpreterState,
        program: Program
    ) -> StepAction:
        """Enter a sub-program by pushing a new frame. No recursion."""
        from doeff.types import EffectBase

        # Handle KleisliProgramCall - push call frame
        call_frame_pushed = False
        if isinstance(program, KleisliProgramCall):
            if program.kleisli_source is not None:
                frame = CallFrame(
                    kleisli=program.kleisli_source,
                    function_name=program.function_name,
                    args=program.args,
                    kwargs=program.kwargs,
                    depth=len(state.context.program_call_stack),
                    created_at=program.created_at,
                )
                state.context.program_call_stack.append(frame)
                call_frame_pushed = True

        # Handle effect as direct program
        if isinstance(program, EffectBase):
            state.current_item = program
            return StepActionContinue()

        # Get generator
        to_gen = getattr(program, "to_generator", None)
        if to_gen is None:
            if call_frame_pushed:
                state.context.program_call_stack.pop()
            raise TypeError(
                f"Program {program!r} does not implement to_generator()"
            )
        gen = to_gen()

        # Create source info
        source_info = None
        if isinstance(program, KleisliProgramCall) and call_frame_pushed:
            source_info = state.context.program_call_stack[-1] if state.context.program_call_stack else None

        frame = ContinuationFrame(
            generator=gen,
            source_info=source_info,
            original_program=program,
        )

        try:
            first_item = next(gen)
            # Validate first yielded item is Effect or Program
            from doeff.program import ProgramBase
            from doeff.types import EffectBase
            if not isinstance(first_item, (EffectBase, ProgramBase)):
                raise TypeError(
                    f"Program yielded invalid type {type(first_item).__name__!r}; "
                    f"expected Effect or Program, got {first_item!r}"
                )
            state.push_frame(frame)
            state.current_item = first_item
            return StepActionContinue()
        except StopIteration as e:
            # Sub-program immediately returned - resume parent with the value
            if call_frame_pushed:
                state.context.program_call_stack.pop()
            # Resume parent frame with returned value, avoiding auto-execution
            return self._resume_with_value(state, e.value)
        except BaseException as exc:
            # Sub-program failed on first step
            # Capture call stack BEFORE popping the call frame
            state.start_error_propagation(exc)
            if call_frame_pushed:
                state.context.program_call_stack.pop()
            return StepActionContinue()

    def _resume_with_value(
        self,
        state: InterpreterState,
        value: Any
    ) -> StepAction:
        """
        Resume current frame with a value. Handles frame completion.

        Uses a loop instead of recursion to maintain trampolined design.
        When a frame returns, we keep resuming parent frames in a loop
        until we hit an effect/program yield, Done, or Error.
        """
        from doeff.program import ProgramBase
        from doeff.types import EffectBase

        # Loop to handle chain of frame returns without recursion
        current_value = value

        while True:
            frame = state.current_frame

            if frame is None:
                # No more frames - interpretation complete
                return StepActionDone(current_value)

            result = frame.resume(current_value)

            if isinstance(result, FrameResultYield):
                item = result.item
                # Validate yielded item is Effect or Program
                if not isinstance(item, (EffectBase, ProgramBase)):
                    raise TypeError(
                        f"Program yielded invalid type {type(item).__name__!r}; "
                        f"expected Effect or Program, got {item!r}"
                    )
                state.current_item = item
                return StepActionContinue()

            if isinstance(result, FrameResultReturn):
                # Frame completed - pop and continue with parent
                popped_frame = state.pop_frame()

                # Pop call frame if this was a KleisliProgramCall
                if (popped_frame and
                    popped_frame.original_program and
                    isinstance(popped_frame.original_program, KleisliProgramCall) and
                    popped_frame.original_program.kleisli_source is not None and
                    state.context.program_call_stack):
                    state.context.program_call_stack.pop()

                # Continue loop with returned value to resume parent frame
                current_value = result.value
                continue

            if isinstance(result, FrameResultRaise):
                state.start_error_propagation(result.exception)
                return StepActionContinue()

            raise InterpreterInvariantError(f"Unknown frame result type: {type(result)}")

    def _propagate_error_step(self, state: InterpreterState) -> StepAction:
        """Propagate exception through frames. Returns when caught or stack empty.

        Invariants enforced:
        - INV-E1: Must have a propagating_exception
        - INV-E2: Phase must be PROPAGATING_ERROR
        """
        from doeff.types import EffectBase

        # INV-E2: Check phase
        assert state.phase == InterpretationPhase.PROPAGATING_ERROR, (
            f"_propagate_error_step called in invalid phase: {state.phase}"
        )

        exc = state.propagating_exception
        if exc is None:
            raise InterpreterInvariantError("No exception to propagate")

        frame = state.current_frame

        if frame is None:
            # Stack empty - unhandled exception
            state.propagating_exception = None
            return StepActionError(exc, state.snapshot().frame_infos)

        # Check if frame is already in a terminal state (e.g., exception raised after yield)
        # In this case, we can't throw into it - just pop and continue propagating
        if frame.state != FrameState.ACTIVE:
            popped_frame = state.pop_frame()

            # Pop call frame if this was a KleisliProgramCall
            if (popped_frame and
                popped_frame.original_program and
                isinstance(popped_frame.original_program, KleisliProgramCall) and
                popped_frame.original_program.kleisli_source is not None and
                state.context.program_call_stack):
                state.context.program_call_stack.pop()

            return StepActionContinue()

        result = frame.throw(exc)

        if isinstance(result, FrameResultYield):
            # Exception was caught, frame continues
            item = result.item
            # Validate yielded item is Effect or Program
            from doeff.program import ProgramBase
            if not isinstance(item, (EffectBase, ProgramBase)):
                raise TypeError(
                    f"Program yielded invalid type {type(item).__name__!r}; "
                    f"expected Effect or Program, got {item!r}"
                )
            state.stats.total_exceptions_caught += 1
            state.current_item = item
            state.clear_error_state()
            state.phase = InterpretationPhase.STEPPING

            # Check if the yielded item is an effect or program
            if isinstance(item, EffectBase):
                return StepActionYieldEffect(item)
            return StepActionContinue()

        if isinstance(result, FrameResultReturn):
            # Exception was caught and frame completed
            state.stats.total_exceptions_caught += 1
            popped_frame = state.pop_frame()

            # Pop call frame if this was a KleisliProgramCall
            if (popped_frame and
                popped_frame.original_program and
                isinstance(popped_frame.original_program, KleisliProgramCall) and
                popped_frame.original_program.kleisli_source is not None and
                state.context.program_call_stack):
                state.context.program_call_stack.pop()

            state.clear_error_state()
            state.phase = InterpretationPhase.STEPPING
            # Resume parent frame with returned value, avoiding auto-execution of Programs
            return self._resume_with_value(state, result.value)

        if isinstance(result, FrameResultRaise):
            # Exception not caught - pop frame and continue propagating
            popped_frame = state.pop_frame()

            # Pop call frame if this was a KleisliProgramCall
            if (popped_frame and
                popped_frame.original_program and
                isinstance(popped_frame.original_program, KleisliProgramCall) and
                popped_frame.original_program.kleisli_source is not None and
                state.context.program_call_stack):
                state.context.program_call_stack.pop()

            state.propagating_exception = result.exception  # May be transformed
            return StepActionContinue()

        raise InterpreterInvariantError(f"Unknown frame result type: {type(result)}")

    def _unwind_stack(self, state: InterpreterState) -> None:
        """Unwind all frames on cancellation."""
        while state.current_frame is not None:
            frame = state.pop_frame()
            if frame:
                frame.close()

                # Pop call frame if this was a KleisliProgramCall
                if (frame.original_program and
                    isinstance(frame.original_program, KleisliProgramCall) and
                    frame.original_program.kleisli_source is not None and
                    state.context.program_call_stack):
                    state.context.program_call_stack.pop()

    def _fail_with_overflow(self, state: InterpreterState) -> RunResult:
        """Handle stack overflow condition."""
        exc = ContinuationStackOverflowError(
            f"Continuation stack exceeded {self._max_stack_depth} frames",
            stack_snapshot=state.snapshot(),
            max_depth=self._max_stack_depth,
            actual_depth=state.stack_depth
        )
        self._unwind_stack(state)
        state.stats.end_time_ns = time.perf_counter_ns()
        return RunResult(state.context, Err(exc))

    def _wrap_exception(
        self,
        exc: BaseException,
        state: InterpreterState,
        effect: Effect | None
    ) -> EffectFailure:
        """Wrap exception in EffectFailure with context.

        If exc is already an EffectFailure, returns it as-is to avoid double-wrapping.
        Uses captured call_stack_at_error if available (before frames were popped).
        """
        from doeff._types_internal import NullEffect

        # Avoid double-wrapping EffectFailure
        if isinstance(exc, EffectFailure):
            return exc

        runtime_tb = capture_traceback(exc)
        creation_context = getattr(effect, "created_at", None) if effect else None

        # Use captured call stack if available, otherwise current (may be empty)
        call_stack = (
            state.call_stack_at_error
            if state.call_stack_at_error is not None
            else tuple(state.context.program_call_stack)
        )

        return EffectFailure(
            effect=effect if effect else NullEffect(),
            cause=exc,
            runtime_traceback=runtime_tb,
            creation_context=creation_context,
            call_stack_snapshot=call_stack,
        )

    def _build_effect_stack_trace(
        self,
        state: InterpreterState,
        failed_effect: Effect | None,
        exception: BaseException
    ) -> EffectStackTrace:
        """
        Build a complete effect stack trace from interpreter state.

        This is called when an error occurs and we need to report it.
        The continuation_stack gives us the EXACT call chain.
        """
        if not self._capture_stack_trace:
            return EffectStackTrace(
                frames=(),
                failed_effect=failed_effect,
                original_exception=exception,
                python_raise_location=None
            )

        frames: list[EffectStackFrame] = []

        # Walk the continuation stack from bottom (entry) to top (current)
        for cont_frame in state.continuation_stack:
            source = cont_frame.source_info

            if source is None:
                # Anonymous program (e.g., pure(), sequence())
                frames.append(EffectStackFrame(
                    frame_type=EffectStackFrameType.PROGRAM_FLAT_MAP,
                    name="<anonymous>",
                    location=None,
                ))
                continue

            # Determine frame type from source
            frame_type = self._classify_frame(source, cont_frame)

            # Extract location from CallFrame.created_at
            location = None
            if source.created_at:
                ctx = source.created_at
                location = PythonLocation(
                    filename=ctx.filename,
                    line=ctx.line,
                    function=ctx.function,
                    code=ctx.code,
                )

            frames.append(EffectStackFrame(
                frame_type=frame_type,
                name=source.function_name,
                location=location,
                call_args=source.args,
                call_kwargs=source.kwargs,
                raw_frame=source,
            ))

        # Add the failed effect as the innermost frame
        if failed_effect is not None:
            effect_location = None
            if hasattr(failed_effect, "created_at") and failed_effect.created_at:
                ctx = failed_effect.created_at
                effect_location = PythonLocation(
                    filename=ctx.filename,
                    line=ctx.line,
                    function=ctx.function,
                    code=ctx.code,
                )

            frames.append(EffectStackFrame(
                frame_type=EffectStackFrameType.EFFECT_YIELD,
                name=type(failed_effect).__name__,
                location=effect_location,
            ))

        # Extract Python raise location from exception
        python_location = self._extract_raise_location(exception)

        return EffectStackTrace(
            frames=tuple(frames),
            failed_effect=failed_effect,
            original_exception=exception,
            python_raise_location=python_location,
        )

    def _classify_frame(
        self,
        source: CallFrame,
        cont_frame: ContinuationFrame
    ) -> EffectStackFrameType:
        """Classify what type of frame this is based on function name and program type."""
        func_name = source.function_name.lower() if source.function_name else ""

        # Check for handler boundaries (catch, recover, finally, safe, etc.)
        handler_keywords = ("catch", "recover", "finally", "safe", "retry", "handle")
        if any(keyword in func_name for keyword in handler_keywords):
            return EffectStackFrameType.HANDLER_BOUNDARY

        # Check for spawn boundaries
        if "spawn" in func_name or "thread" in func_name:
            return EffectStackFrameType.SPAWN_BOUNDARY

        # Check for flat_map chains
        if "flat_map" in func_name or "flatmap" in func_name or "and_then" in func_name:
            return EffectStackFrameType.PROGRAM_FLAT_MAP

        # Default to Kleisli call
        return EffectStackFrameType.KLEISLI_CALL

    def _extract_raise_location(self, exc: BaseException) -> PythonLocation | None:
        """Extract the Python file:line where exception was raised."""
        tb = exc.__traceback__
        if tb is None:
            return None

        # Walk to innermost frame
        while tb.tb_next is not None:
            tb = tb.tb_next

        frame = tb.tb_frame
        return PythonLocation(
            filename=frame.f_code.co_filename,
            line=tb.tb_lineno,
            function=frame.f_code.co_name,
            code=None,  # Could extract with linecache if needed
        )

    def _record_effect_usage(
        self,
        effect: Effect,
        ctx: ExecutionContext
    ) -> None:
        """Record Dep/Ask effect usage for later inspection."""
        try:
            observations = ctx.effect_observations
        except AttributeError:
            return

        effect_type: str | None = None
        key: EnvKey | None = None

        if _effect_is(effect, DepInjectEffect):
            effect_type = "Dep"
            key = getattr(effect, "key", None)
        elif _effect_is(effect, AskEffect):
            effect_type = "Ask"
            key = getattr(effect, "key", None)

        if effect_type is None:
            return

        context_info = getattr(effect, "created_at", None)
        sanitized = context_info.without_frames() if context_info is not None else None

        snapshot = tuple(ctx.program_call_stack)

        observations.append(
            EffectObservation(
                effect_type=effect_type,
                key=key,
                context=sanitized,
                call_stack_snapshot=snapshot,
            )
        )

    async def _handle_effect(
        self,
        effect: Effect,
        ctx: ExecutionContext
    ) -> Any:
        """Dispatch effect to appropriate handler."""
        self._record_effect_usage(effect, ctx)

        result = await self._try_intercept_effect(effect, ctx)
        if result is not _NO_HANDLER:
            return result

        # Try each category of effects
        result = await self._try_reader_effects(effect, ctx)
        if result is not _NO_HANDLER:
            return result

        result = await self._try_state_effects(effect, ctx)
        if result is not _NO_HANDLER:
            return result

        result = await self._try_result_effects(effect, ctx)
        if result is not _NO_HANDLER:
            return result

        result = await self._try_other_effects(effect, ctx)
        if result is not _NO_HANDLER:
            return result

        raise ValueError(f"Unknown effect: {effect!r}")

    async def _try_intercept_effect(
        self,
        effect: Effect,
        ctx: ExecutionContext
    ) -> Any:
        """Handle program interception effects."""
        if _effect_is(effect, InterceptEffect):
            return await self._handle_intercept_effect(effect, ctx)
        return _NO_HANDLER

    async def _handle_intercept_effect(
        self,
        effect: InterceptEffect,
        ctx: ExecutionContext
    ) -> Any:
        """Run a program through the intercept pipeline."""
        from doeff.interpreter import _build_intercept_program

        intercept_program = _build_intercept_program(effect.program, effect.transforms)

        # Create a fresh interpreter for nested execution to avoid reentrancy
        nested_interpreter = TrampolinedInterpreter(
            max_log_entries=self._max_log_entries,
            max_stack_depth=self._max_stack_depth,
            capture_stack_trace=self._capture_stack_trace,
        )
        nested_interpreter.reader_handler = self.reader_handler
        nested_interpreter.state_handler = self.state_handler
        nested_interpreter.atomic_handler = self.atomic_handler
        nested_interpreter.writer_handler = self.writer_handler
        nested_interpreter.future_handler = self.future_handler
        nested_interpreter.thread_handler = self.thread_handler
        nested_interpreter.spawn_handler = self.spawn_handler
        nested_interpreter.result_handler = self.result_handler
        nested_interpreter.io_handler = self.io_handler
        nested_interpreter.graph_handler = self.graph_handler
        nested_interpreter.memo_handler = self.memo_handler
        nested_interpreter.cache_handler = self.cache_handler

        sub_result = await nested_interpreter.run_async(intercept_program, ctx)

        if isinstance(sub_result.result, Err):
            raise sub_result.result.error

        return sub_result.value

    async def _try_reader_effects(
        self,
        effect: Effect,
        ctx: ExecutionContext
    ) -> Any:
        """Handle Reader/Dep/Ask effects. Returns _NO_HANDLER if not matched."""
        if _effect_is(effect, AskEffect):
            return await self.reader_handler.handle_ask(effect, ctx, self)
        if _effect_is(effect, LocalEffect):
            return await self.reader_handler.handle_local(effect, ctx, self)
        if _effect_is(effect, DepInjectEffect):
            proxy_effect = AskEffect(key=effect.key, created_at=effect.created_at)
            self._record_effect_usage(proxy_effect, ctx)
            return await self.reader_handler.handle_ask(proxy_effect, ctx, self)
        return _NO_HANDLER

    async def _try_state_effects(
        self,
        effect: Effect,
        ctx: ExecutionContext
    ) -> Any:
        """Handle State/Atomic/Writer effects. Returns _NO_HANDLER if not matched."""
        if _effect_is(effect, StateGetEffect):
            return await self.state_handler.handle_get(effect, ctx)
        if _effect_is(effect, StatePutEffect):
            return await self.state_handler.handle_put(effect, ctx)
        if _effect_is(effect, StateModifyEffect):
            return await self.state_handler.handle_modify(effect, ctx)
        if _effect_is(effect, AtomicGetEffect):
            return await self.atomic_handler.handle_get(effect, ctx)
        if _effect_is(effect, AtomicUpdateEffect):
            return await self.atomic_handler.handle_update(effect, ctx)
        if _effect_is(effect, WriterTellEffect):
            return await self.writer_handler.handle_tell(effect, ctx)
        if _effect_is(effect, WriterListenEffect):
            return await self.writer_handler.handle_listen(effect, ctx, self)
        return _NO_HANDLER

    async def _try_result_effects(
        self,
        effect: Effect,
        ctx: ExecutionContext
    ) -> Any:
        """Handle Result monad effects. Returns _NO_HANDLER if not matched."""
        from doeff.effects.pure import PureEffect

        if _effect_is(effect, PureEffect):
            return await self.result_handler.handle_pure(effect)
        if _effect_is(effect, ResultFailEffect):
            return await self.result_handler.handle_fail(effect)
        if _effect_is(effect, ResultCatchEffect):
            return await self.result_handler.handle_catch(effect, ctx, self)
        if _effect_is(effect, ResultFinallyEffect):
            return await self.result_handler.handle_finally(effect, ctx, self)
        if _effect_is(effect, ResultRecoverEffect):
            return await self.result_handler.handle_recover(effect, ctx, self)
        if _effect_is(effect, ResultRetryEffect):
            return await self.result_handler.handle_retry(effect, ctx, self)
        if _effect_is(effect, ResultFirstSuccessEffect):
            return await self.result_handler.handle_first_success(effect, ctx, self)
        if _effect_is(effect, ResultSafeEffect):
            return await self.result_handler.handle_safe(effect, ctx, self)
        if _effect_is(effect, ResultUnwrapEffect):
            return await self.result_handler.handle_unwrap(effect, ctx, self)
        return _NO_HANDLER

    async def _try_other_effects(
        self,
        effect: Effect,
        ctx: ExecutionContext
    ) -> Any:
        """Handle Future/IO/Graph effects. Returns _NO_HANDLER if not matched."""
        result = await self._try_future_io_graph_effects(effect, ctx)
        if result is not _NO_HANDLER:
            return result
        result = await self._try_callstack_effects(effect, ctx)
        if result is not _NO_HANDLER:
            return result
        return await self._try_gather_memo_cache_effects(effect, ctx)

    async def _try_callstack_effects(
        self,
        effect: Effect,
        ctx: ExecutionContext
    ) -> Any:
        """Handle call-stack introspection effects."""
        if _effect_is(effect, ProgramCallStackEffect):
            return tuple(ctx.program_call_stack)

        if _effect_is(effect, ProgramCallFrameEffect):
            depth = getattr(effect, "depth", 0)
            stack = ctx.program_call_stack
            if depth >= len(stack):
                raise IndexError(
                    f"Program call stack depth {depth} out of range (size={len(stack)})"
                )
            return stack[-1 - depth]

        return _NO_HANDLER

    async def _try_future_io_graph_effects(
        self,
        effect: Effect,
        ctx: ExecutionContext
    ) -> Any:
        """Handle Future/IO/Graph effects. Returns _NO_HANDLER if not matched."""
        if _effect_is(effect, FutureAwaitEffect):
            return await self.future_handler.handle_await(effect)
        if _effect_is(effect, FutureParallelEffect):
            return await self.future_handler.handle_parallel(effect)
        if _effect_is(effect, SpawnEffect):
            return self.spawn_handler.handle_spawn(effect, ctx, self)
        if _effect_is(effect, TaskJoinEffect):
            return await self.spawn_handler.handle_join(effect, ctx)
        if _effect_is(effect, ThreadEffect):
            awaitable = self.thread_handler.handle_thread(effect, ctx, self)
            if effect.await_result:
                return await self.future_handler.handle_await(
                    FutureAwaitEffect(awaitable=awaitable)
                )
            return awaitable
        if _effect_is(effect, IOPerformEffect):
            return await self.io_handler.handle_run(effect, ctx)
        if _effect_is(effect, IOPrintEffect):
            return await self.io_handler.handle_print(effect, ctx)
        if _effect_is(effect, GraphStepEffect):
            return await self.graph_handler.handle_step(effect, ctx)
        if _effect_is(effect, GraphAnnotateEffect):
            return await self.graph_handler.handle_annotate(effect, ctx)
        if _effect_is(effect, GraphSnapshotEffect):
            return await self.graph_handler.handle_snapshot(effect, ctx)
        if _effect_is(effect, GraphCaptureEffect):
            return await self.graph_handler.handle_capture(effect, ctx, self)
        return _NO_HANDLER

    async def _try_gather_memo_cache_effects(
        self,
        effect: Effect,
        ctx: ExecutionContext
    ) -> Any:
        """Handle Gather/Memo/Cache effects. Returns _NO_HANDLER if not matched."""
        if _effect_is(effect, GatherEffect):
            return await self._handle_gather_effect(effect, ctx)
        if _effect_is(effect, GatherDictEffect):
            return await self._handle_gather_dict_effect(effect, ctx)
        if _effect_is(effect, MemoGetEffect):
            return await self.memo_handler.handle_get(effect, ctx)
        if _effect_is(effect, MemoPutEffect):
            return await self.memo_handler.handle_put(effect, ctx)
        if _effect_is(effect, CacheGetEffect):
            return await self.cache_handler.handle_get(effect, ctx)
        if _effect_is(effect, CachePutEffect):
            return await self.cache_handler.handle_put(effect, ctx)
        return _NO_HANDLER

    async def _handle_gather_effect(
        self,
        effect: GatherEffect,
        ctx: ExecutionContext
    ) -> Any:
        return await self._run_gather_sequence(list(effect.programs), ctx)

    async def _handle_gather_dict_effect(
        self,
        effect: GatherDictEffect,
        ctx: ExecutionContext
    ) -> Any:
        program_list = list(effect.programs.values())
        results = await self._run_gather_sequence(program_list, ctx)
        return dict(zip(effect.programs.keys(), results, strict=False))

    async def _run_gather_sequence(
        self,
        programs: list[Program],
        ctx: ExecutionContext
    ) -> list[Any]:
        from doeff.program import ProgramBase

        normalized_programs: list[Program] = []

        def _enqueue_program(prog_like: Any) -> None:
            if isinstance(prog_like, ProgramBase):
                normalized_programs.append(prog_like)
                return

            if isinstance(prog_like, (list, tuple)):
                for nested in prog_like:
                    _enqueue_program(nested)
                return

            raise TypeError(
                "gather expects Program or Effect instances, optionally nested in iterables"
            )

        for program in programs:
            _enqueue_program(program)

        tasks = []
        for prog in normalized_programs:
            ctx_copy = ExecutionContext(
                env=ctx.env.copy() if ctx.env else {},
                state=ctx.state.copy() if ctx.state else {},
                log=self._new_log_buffer(),
                graph=ctx.graph,
                io_allowed=ctx.io_allowed,
                cache=ctx.cache,
                effect_observations=ctx.effect_observations,
            )
            self._ensure_log_buffer(ctx_copy)

            # Create a fresh interpreter for each parallel task
            nested_interpreter = TrampolinedInterpreter(
                max_log_entries=self._max_log_entries,
                max_stack_depth=self._max_stack_depth,
                capture_stack_trace=self._capture_stack_trace,
            )
            nested_interpreter.reader_handler = self.reader_handler
            nested_interpreter.state_handler = self.state_handler
            nested_interpreter.atomic_handler = self.atomic_handler
            nested_interpreter.writer_handler = self.writer_handler
            nested_interpreter.future_handler = self.future_handler
            nested_interpreter.thread_handler = self.thread_handler
            nested_interpreter.spawn_handler = self.spawn_handler
            nested_interpreter.result_handler = self.result_handler
            nested_interpreter.io_handler = self.io_handler
            nested_interpreter.graph_handler = self.graph_handler
            nested_interpreter.memo_handler = self.memo_handler
            nested_interpreter.cache_handler = self.cache_handler

            tasks.append(asyncio.create_task(nested_interpreter.run_async(prog, ctx_copy)))

        sub_results = await asyncio.gather(*tasks)

        results: list[Any] = []
        sub_contexts = [sub_result.context for sub_result in sub_results]
        error_to_raise: BaseException | None = None

        for sub_result in sub_results:
            if isinstance(sub_result.result, Err):
                if error_to_raise is None:
                    error_to_raise = sub_result.result.error
            else:
                results.append(sub_result.value)

        combined_steps = set(ctx.graph.steps)
        gather_inputs: list[WNode] = []
        for sub_ctx in sub_contexts:
            ctx.state.update(sub_ctx.state)
            ctx.log.extend(sub_ctx.log)
            combined_steps.update(sub_ctx.graph.steps)
            gather_inputs.append(sub_ctx.graph.last.output)

        if gather_inputs:
            gather_node = WNode(tuple(results))
            gather_step = WStep(inputs=tuple(gather_inputs), output=gather_node)
            combined_steps.add(gather_step)
            ctx.graph = WGraph(last=gather_step, steps=frozenset(combined_steps))
        else:
            ctx.graph = WGraph(last=ctx.graph.last, steps=frozenset(combined_steps))

        if error_to_raise is not None:
            raise error_to_raise

        return results


# DEPRECATED - kept for API compatibility
def force_eval(prog: Program[T]) -> Program[T]:
    """
    DEPRECATED: Trampolined interpreter handles nesting automatically.
    This function now simply returns its input unchanged.
    """
    import warnings
    warnings.warn(
        "force_eval() is deprecated with TrampolinedInterpreter",
        DeprecationWarning
    )
    return prog


__all__ = [
    "ContinuationFrame",
    "ContinuationStackOverflowError",
    "EffectStackFrame",
    "EffectStackFrameType",
    "EffectStackTrace",
    "EffectStackTraceRenderer",
    "FrameResultRaise",
    "FrameResultReturn",
    "FrameResultYield",
    "FrameState",
    "InterpretationPhase",
    "InterpretationStats",
    "InterpreterInvariantError",
    "InterpreterReentrancyError",
    "InterpreterState",
    "InterpreterStateSnapshot",
    "InvalidFrameStateError",
    "PythonLocation",
    "StepActionContinue",
    "StepActionDone",
    "StepActionError",
    "StepActionYieldEffect",
    "TrampolinedInterpreter",
    "force_eval",
]
