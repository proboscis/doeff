"""
Trampolined interpreter with explicit continuation stack.

This interpreter avoids recursive calls when running nested programs by managing
continuations explicitly and stepping a state machine.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Generator, Generic, TypeVar

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
    ThreadEffect,
    TaskJoinEffect,
    StateGetEffect,
    StateModifyEffect,
    StatePutEffect,
    WriterListenEffect,
    WriterTellEffect,
)
from doeff.effects.pure import PureEffect
from doeff.handlers import (
    AtomicEffectHandler,
    CacheEffectHandler,
    FutureEffectHandler,
    GraphEffectHandler,
    HandlerScope,
    IOEffectHandler,
    MemoEffectHandler,
    ReaderEffectHandler,
    ResultEffectHandler,
    SpawnEffectHandler,
    StateEffectHandler,
    ThreadEffectHandler,
    WriterEffectHandler,
)
from doeff.program import KleisliProgramCall, Program, ProgramBase
from doeff.types import (
    CallFrame,
    Effect,
    EffectBase,
    EffectFailure,
    EffectObservation,
    ExecutionContext,
    RunResult,
    capture_traceback,
)
from doeff.types import EffectStackFrame, EffectStackFrameType, EffectStackTrace, PythonLocation
from doeff.utils import BoundedLog


T = TypeVar("T")

logger = logging.getLogger(__name__)

# Sentinel value to distinguish "no handler found" from "handler returned None"
_NO_HANDLER = object()


def _effect_is(effect: Effect, cls: type[Any]) -> bool:
    """Return True if effect is instance of cls, tolerant to module reloads."""
    return isinstance(effect, cls) or effect.__class__.__name__ == cls.__name__


class InterpreterException(Exception):
    """Base class for interpreter-originated exceptions."""


class ContinuationStackOverflowError(InterpreterException):
    """Raised when continuation stack exceeds configured limit."""

    def __init__(
        self,
        message: str,
        stack_snapshot: "InterpreterStateSnapshot",
        max_depth: int,
        actual_depth: int,
    ) -> None:
        super().__init__(message)
        self.stack_snapshot = stack_snapshot
        self.max_depth = max_depth
        self.actual_depth = actual_depth


class InvalidFrameStateError(InterpreterException):
    """Raised when attempting invalid operation on a frame."""


class InterpreterInvariantError(InterpreterException):
    """Raised when an interpreter invariant is violated."""


class InterpreterReentrancyError(InterpreterException):
    """Raised when reentrant use of the interpreter is detected."""


class FrameState(Enum):
    """Lifecycle state of a continuation frame."""

    ACTIVE = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass(frozen=True)
class FrameResult:
    """Result of resuming or throwing into a frame."""

    @dataclass(frozen=True)
    class Yield:
        item: Effect | Program

    @dataclass(frozen=True)
    class Return:
        value: Any

    @dataclass(frozen=True)
    class Raise:
        exception: BaseException


@dataclass
class ContinuationFrame:
    """A single frame in the continuation stack."""

    generator: Generator[Effect | Program, Any, Any]
    context_snapshot: ExecutionContext
    handler_scope: HandlerScope
    source_info: CallFrame | None
    pending_exception: BaseException | None = None
    state: FrameState = FrameState.ACTIVE
    frame_type_override: EffectStackFrameType | None = None
    frame_name_override: str | None = None
    is_current: bool = True
    _in_flight: bool = field(default=False, init=False, repr=False)

    VALID_TRANSITIONS = {
        FrameState.ACTIVE: {FrameState.COMPLETED, FrameState.FAILED, FrameState.CANCELLED},
        FrameState.COMPLETED: set(),
        FrameState.FAILED: set(),
        FrameState.CANCELLED: set(),
    }

    def _transition(self, new_state: FrameState) -> None:
        if new_state not in self.VALID_TRANSITIONS.get(self.state, set()):
            raise InvalidFrameStateError(
                f"Invalid transition from {self.state} to {new_state}"
            )
        self.state = new_state

    def _enter(self) -> None:
        if self.state is not FrameState.ACTIVE:
            raise InvalidFrameStateError(
                f"Cannot resume frame in state {self.state}"
            )
        if self._in_flight:
            raise InvalidFrameStateError("Concurrent resume/throw detected")
        self._in_flight = True

    def _exit(self) -> None:
        self._in_flight = False

    def resume(self, value: Any) -> FrameResult:
        """Resume this frame with a value."""
        self._enter()
        try:
            next_item = self.generator.send(value)
            return FrameResult.Yield(next_item)
        except StopIteration as exc:
            self._transition(FrameState.COMPLETED)
            return FrameResult.Return(exc.value)
        except BaseException as exc:
            self._transition(FrameState.FAILED)
            return FrameResult.Raise(exc)
        finally:
            self._exit()

    def throw(self, exc: BaseException) -> FrameResult:
        """Throw an exception into this frame."""
        self._enter()
        try:
            next_item = self.generator.throw(exc)
            return FrameResult.Yield(next_item)
        except StopIteration as stop_exc:
            self._transition(FrameState.COMPLETED)
            return FrameResult.Return(stop_exc.value)
        except BaseException as propagated:
            self._transition(FrameState.FAILED)
            return FrameResult.Raise(propagated)
        finally:
            self._exit()

    def close(self) -> None:
        """Clean up this frame (for cancellation)."""
        if self.state is not FrameState.ACTIVE:
            return
        try:
            self.generator.close()
        finally:
            self._transition(FrameState.CANCELLED)


class InterpretationPhase(Enum):
    """Current phase of interpretation."""

    INITIALIZING = auto()
    STEPPING = auto()
    AWAITING_EFFECT = auto()
    PROPAGATING_ERROR = auto()
    UNWINDING = auto()
    COMPLETED = auto()
    FAILED = auto()


@dataclass
class InterpretationStats:
    """Statistics for monitoring and performance analysis."""

    total_steps: int = 0
    total_effects_handled: int = 0
    total_frames_created: int = 0
    max_stack_depth: int = 0
    total_exceptions_caught: int = 0

    def copy(self) -> "InterpretationStats":
        return InterpretationStats(
            total_steps=self.total_steps,
            total_effects_handled=self.total_effects_handled,
            total_frames_created=self.total_frames_created,
            max_stack_depth=self.max_stack_depth,
            total_exceptions_caught=self.total_exceptions_caught,
        )


@dataclass(frozen=True)
class InterpreterStateSnapshot:
    """Immutable snapshot of interpreter state for debugging."""

    stack_depth: int
    frame_infos: tuple[CallFrame | None, ...]
    phase: InterpretationPhase
    stats: InterpretationStats


@dataclass
class InterpreterState:
    """Complete interpreter state."""

    continuation_stack: list[ContinuationFrame]
    current_item: Effect | Program | Any | None
    context: ExecutionContext
    phase: InterpretationPhase
    stats: InterpretationStats
    pending_exception: BaseException | None = None
    pending_failure: EffectFailure | None = None
    pending_effect: EffectBase | None = None
    failure_stack_snapshot: tuple[ContinuationFrame, ...] | None = None
    last_stats_snapshot: InterpretationStats | None = None

    def push_frame(self, frame: ContinuationFrame) -> None:
        if self.continuation_stack:
            self.continuation_stack[-1].is_current = False
        frame.is_current = True
        self.continuation_stack.append(frame)
        self.stats.max_stack_depth = max(
            self.stats.max_stack_depth,
            len(self.continuation_stack),
        )

    def pop_frame(self) -> ContinuationFrame | None:
        if not self.continuation_stack:
            return None
        frame = self.continuation_stack.pop()
        frame.is_current = False
        if self.continuation_stack:
            self.continuation_stack[-1].is_current = True
        return frame

    @property
    def current_frame(self) -> ContinuationFrame | None:
        return self.continuation_stack[-1] if self.continuation_stack else None

    @property
    def stack_depth(self) -> int:
        return len(self.continuation_stack)

    def snapshot(self) -> InterpreterStateSnapshot:
        return InterpreterStateSnapshot(
            stack_depth=self.stack_depth,
            frame_infos=tuple(frame.source_info for frame in self.continuation_stack),
            phase=self.phase,
            stats=self.stats.copy(),
        )


@dataclass(frozen=True)
class StepAction(Generic[T]):
    """Result of a single interpretation step."""

    @dataclass(frozen=True)
    class Continue:
        pass

    @dataclass(frozen=True)
    class YieldEffect:
        effect: EffectBase

    @dataclass(frozen=True)
    class Done(Generic[T]):
        value: T

    @dataclass(frozen=True)
    class Error:
        exception: BaseException
        stack_snapshot: tuple[ContinuationFrame, ...]


class TrampolinedInterpreter:
    """Interpreter with explicit continuation management."""

    def __init__(
        self,
        custom_handlers: dict[str, Any] | None = None,
        *,
        max_log_entries: int | None = None,
        spawn_default_backend: SpawnBackend = "thread",
        spawn_thread_max_workers: int | None = None,
        spawn_process_max_workers: int | None = None,
        spawn_ray_address: str | None = None,
        spawn_ray_init_kwargs: dict[str, Any] | None = None,
        spawn_ray_runtime_env: dict[str, Any] | None = None,
        allow_reentrancy: bool = False,
        max_stack_depth: int = 10000,
    ) -> None:
        if max_log_entries is not None and max_log_entries < 0:
            raise ValueError("max_log_entries must be >= 0 or None")

        self._max_log_entries = max_log_entries
        self._allow_reentrancy = allow_reentrancy
        self._max_stack_depth = max_stack_depth
        self._running = 0
        self._last_state: InterpreterState | None = None

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

        if custom_handlers:
            handlers.update(custom_handlers)

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
        return BoundedLog(max_entries=self._max_log_entries)

    def _ensure_log_buffer(self, ctx: ExecutionContext) -> None:
        log = ctx.log
        if isinstance(log, BoundedLog):
            log.set_max_entries(self._max_log_entries)
        else:
            ctx.log = BoundedLog(log, max_entries=self._max_log_entries)

    def run(self, program: Program[T], context: ExecutionContext | None = None) -> RunResult[T]:
        return asyncio.run(self.run_async(program, context))

    async def run_async(
        self, program: Program[T], context: ExecutionContext | None = None
    ) -> RunResult[T]:
        if not self._allow_reentrancy and self._running:
            raise InterpreterReentrancyError(
                "Interpreter does not support reentrant calls."
            )
        self._running += 1
        state: InterpreterState | None = None
        ctx: ExecutionContext | None = None
        try:
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
            if state.phase == InterpretationPhase.COMPLETED:
                self._last_state = state
                return RunResult(ctx, Ok(state.current_item), stats=state.stats)

            while state.phase in (
                InterpretationPhase.STEPPING,
                InterpretationPhase.AWAITING_EFFECT,
                InterpretationPhase.PROPAGATING_ERROR,
            ):
                self._assert_invariants(state)
                state.stats.total_steps += 1

                if (
                    self._max_stack_depth
                    and state.stack_depth > self._max_stack_depth
                ):
                    return self._fail_with_overflow(state)

                if state.phase == InterpretationPhase.STEPPING:
                    action = self._step_once(state)

                    if isinstance(action, StepAction.Continue):
                        continue

                    if isinstance(action, StepAction.YieldEffect):
                        state.phase = InterpretationPhase.AWAITING_EFFECT
                        try:
                            value = await self._handle_effect(action.effect, state.context)
                            state.stats.total_effects_handled += 1
                            state.current_item = value
                            state.phase = InterpretationPhase.STEPPING
                        except asyncio.CancelledError:
                            raise
                        except BaseException as exc:
                            if isinstance(exc, (SystemExit, KeyboardInterrupt)):
                                raise
                            self._begin_error_propagation(
                                state,
                                exc,
                                failed_effect=action.effect,
                            )
                        continue

                    if isinstance(action, StepAction.Done):
                        state.phase = InterpretationPhase.COMPLETED
                        self._finalize_state(state)
                        self._last_state = state
                        return RunResult(ctx, Ok(action.value), stats=state.stats)

                    if isinstance(action, StepAction.Error):
                        state.phase = InterpretationPhase.FAILED
                        result = self._build_error_result(
                            state,
                            action.exception,
                            action.stack_snapshot,
                        )
                        self._last_state = state
                        return result

                if state.phase == InterpretationPhase.PROPAGATING_ERROR:
                    action = self._propagate_error_step(state)

                    if isinstance(action, StepAction.Continue):
                        continue

                    if isinstance(action, StepAction.Done):
                        state.phase = InterpretationPhase.COMPLETED
                        self._finalize_state(state)
                        self._last_state = state
                        return RunResult(ctx, Ok(action.value), stats=state.stats)

                    if isinstance(action, StepAction.Error):
                        state.phase = InterpretationPhase.FAILED
                        result = self._build_error_result(
                            state,
                            action.exception,
                            action.stack_snapshot,
                        )
                        self._last_state = state
                        return result

            raise InterpreterInvariantError(f"Unexpected phase: {state.phase}")
        except asyncio.CancelledError:
            if state is not None:
                state.phase = InterpretationPhase.UNWINDING
                self._unwind_stack(state)
            raise
        except BaseException as exc:
            if isinstance(exc, (SystemExit, KeyboardInterrupt, InterpreterException)):
                if state is not None:
                    self._unwind_stack(state)
                raise
            if ctx is None:
                ctx = ExecutionContext(env={}, state={}, log=self._new_log_buffer())
            if state is not None:
                self._unwind_stack(state)
            error = self._wrap_exception(ctx, exc, None)
            stack_snapshot = tuple(state.continuation_stack) if state is not None else tuple()
            trace = self._build_effect_stack_trace(
                stack_snapshot=stack_snapshot,
                failed_effect=None,
                exception=exc,
                effect_failure=error,
            )
            return RunResult(ctx, Err(error), stats=state.stats if state else None, effect_stack_trace=trace)
        finally:
            if state is not None:
                self._last_state = state
            self._running -= 1

    def _initialize(self, program: Program[T], ctx: ExecutionContext) -> InterpreterState:
        state = InterpreterState(
            continuation_stack=[],
            current_item=None,
            context=ctx,
            phase=InterpretationPhase.INITIALIZING,
            stats=InterpretationStats(),
        )

        gen = self._program_to_generator(program)
        call_frame = self._maybe_push_call_frame(program, ctx)

        frame = ContinuationFrame(
            generator=gen,
            context_snapshot=ctx.copy(),
            handler_scope=HandlerScope.SHARED,
            source_info=call_frame,
        )
        self._apply_frame_metadata(frame, program)
        state.stats.total_frames_created += 1

        try:
            first_item = next(gen)
        except StopIteration as exc:
            if call_frame is not None:
                ctx.program_call_stack.pop()
            state.current_item = exc.value
            state.phase = InterpretationPhase.COMPLETED
            return state

        state.current_item = first_item
        state.push_frame(frame)
        state.phase = InterpretationPhase.STEPPING
        return state

    def _step_once(self, state: InterpreterState) -> StepAction:
        current = state.current_item

        if isinstance(current, EffectBase):
            return StepAction.YieldEffect(current)

        if isinstance(current, ProgramBase):
            return self._enter_subprogram(state, current)

        return self._resume_with_value(state, current)

    def _enter_subprogram(self, state: InterpreterState, program: ProgramBase) -> StepAction:
        gen = self._program_to_generator(program)
        call_frame = self._maybe_push_call_frame(program, state.context)

        frame = ContinuationFrame(
            generator=gen,
            context_snapshot=state.context.copy(),
            handler_scope=HandlerScope.SHARED,
            source_info=call_frame,
        )
        self._apply_frame_metadata(frame, program)
        state.stats.total_frames_created += 1

        try:
            first_item = next(gen)
        except StopIteration as exc:
            if call_frame is not None:
                state.context.program_call_stack.pop()
            state.current_item = exc.value
            return StepAction.Continue()
        except BaseException as exc:
            if call_frame is not None:
                state.context.program_call_stack.pop()
            self._begin_error_propagation(state, exc)
            return StepAction.Continue()

        state.current_item = first_item
        state.push_frame(frame)
        return StepAction.Continue()

    def _resume_with_value(self, state: InterpreterState, value: Any) -> StepAction:
        frame = state.current_frame
        if frame is None:
            return StepAction.Done(value)

        result = frame.resume(value)
        if isinstance(result, FrameResult.Yield):
            state.current_item = result.item
            return StepAction.Continue()

        if isinstance(result, FrameResult.Return):
            popped = state.pop_frame()
            if popped and popped.source_info is not None:
                self._maybe_pop_call_frame(state.context, popped.source_info)
            if state.current_frame is None:
                return StepAction.Done(result.value)
            state.current_item = result.value
            return StepAction.Continue()

        if isinstance(result, FrameResult.Raise):
            self._begin_error_propagation(state, result.exception)
            return StepAction.Continue()

        raise InterpreterInvariantError("Unknown FrameResult")

    def _begin_error_propagation(
        self,
        state: InterpreterState,
        exc: BaseException,
        *,
        failed_effect: EffectBase | None = None,
    ) -> None:
        state.pending_exception = exc
        state.pending_effect = failed_effect
        if failed_effect is not None:
            state.pending_failure = self._wrap_exception(state.context, exc, failed_effect)
        elif isinstance(exc, EffectFailure):
            state.pending_failure = exc
        else:
            state.pending_failure = self._wrap_exception(state.context, exc, None)
        state.failure_stack_snapshot = tuple(state.continuation_stack)
        state.phase = InterpretationPhase.PROPAGATING_ERROR

    def _propagate_error_step(self, state: InterpreterState) -> StepAction:
        exc = state.pending_exception
        if exc is None:
            return StepAction.Error(RuntimeError("Missing pending exception"), tuple())

        while state.current_frame is not None:
            frame = state.current_frame
            result = frame.throw(exc)

            if isinstance(result, FrameResult.Yield):
                state.current_item = result.item
                state.stats.total_exceptions_caught += 1
                self._clear_pending_error(state)
                state.phase = InterpretationPhase.STEPPING
                return StepAction.Continue()

            if isinstance(result, FrameResult.Return):
                popped = state.pop_frame()
                if popped and popped.source_info is not None:
                    self._maybe_pop_call_frame(state.context, popped.source_info)
                state.stats.total_exceptions_caught += 1
                self._clear_pending_error(state)
                if state.current_frame is None:
                    return StepAction.Done(result.value)
                state.current_item = result.value
                state.phase = InterpretationPhase.STEPPING
                return StepAction.Continue()

            if isinstance(result, FrameResult.Raise):
                popped = state.pop_frame()
                if popped and popped.source_info is not None:
                    self._maybe_pop_call_frame(state.context, popped.source_info)
                new_exc = result.exception
                if new_exc is not exc:
                    state.pending_exception = new_exc
                    state.pending_effect = None
                    if isinstance(new_exc, EffectFailure):
                        state.pending_failure = new_exc
                    else:
                        state.pending_failure = self._wrap_exception(
                            state.context,
                            new_exc,
                            None,
                        )
                exc = new_exc
                continue

        stack_snapshot = state.failure_stack_snapshot or tuple()
        return StepAction.Error(exc, stack_snapshot)

    def _clear_pending_error(self, state: InterpreterState) -> None:
        state.pending_exception = None
        state.pending_failure = None
        state.pending_effect = None
        state.failure_stack_snapshot = None

    def _finalize_state(self, state: InterpreterState) -> None:
        if state.current_frame is not None:
            remaining = state.continuation_stack[:]
            for frame in reversed(remaining):
                frame.close()
            state.continuation_stack.clear()
        self._clear_pending_error(state)

    def _unwind_stack(self, state: InterpreterState) -> None:
        for frame in reversed(state.continuation_stack):
            frame.close()
        state.continuation_stack.clear()

    def _wrap_exception(
        self,
        ctx: ExecutionContext,
        exc: BaseException,
        failed_effect: EffectBase | None,
    ) -> EffectFailure:
        from doeff._types_internal import NullEffect

        effect = failed_effect or NullEffect()
        runtime_tb = capture_traceback(exc)
        creation_context = getattr(effect, "created_at", None)
        return EffectFailure(
            effect=effect,
            cause=exc,
            runtime_traceback=runtime_tb,
            creation_context=creation_context,
            call_stack_snapshot=tuple(ctx.program_call_stack),
        )

    def _build_error_result(
        self,
        state: InterpreterState,
        exc: BaseException,
        stack_snapshot: tuple[ContinuationFrame, ...],
    ) -> RunResult[Any]:
        error = state.pending_failure
        if error is None:
            if isinstance(exc, EffectFailure):
                error = exc
            else:
                error = self._wrap_exception(state.context, exc, state.pending_effect)
        trace = self._build_effect_stack_trace(
            stack_snapshot=stack_snapshot,
            failed_effect=state.pending_effect,
            exception=exc,
            effect_failure=error,
        )
        return RunResult(
            state.context,
            Err(error),
            stats=state.stats,
            effect_stack_trace=trace,
        )

    def _fail_with_overflow(self, state: InterpreterState) -> RunResult[Any]:
        stack_snapshot = tuple(state.continuation_stack)
        snapshot = state.snapshot()
        error = ContinuationStackOverflowError(
            "Continuation stack limit exceeded",
            snapshot,
            self._max_stack_depth,
            state.stack_depth,
        )
        self._unwind_stack(state)
        trace = self._build_effect_stack_trace(
            stack_snapshot=stack_snapshot,
            failed_effect=None,
            exception=error,
            effect_failure=None,
        )
        return RunResult(state.context, Err(error), stats=state.stats, effect_stack_trace=trace)

    def _maybe_push_call_frame(
        self,
        program: ProgramBase,
        ctx: ExecutionContext,
    ) -> CallFrame | None:
        if isinstance(program, KleisliProgramCall) and program.kleisli_source is not None:
            frame = CallFrame(
                kleisli=program.kleisli_source,
                function_name=program.function_name,
                args=program.args,
                kwargs=program.kwargs,
                depth=len(ctx.program_call_stack),
                created_at=program.created_at,
            )
            ctx.program_call_stack.append(frame)
            return frame
        return None

    def _maybe_pop_call_frame(self, ctx: ExecutionContext, frame: CallFrame) -> None:
        if ctx.program_call_stack and ctx.program_call_stack[-1] is frame:
            ctx.program_call_stack.pop()

    def _apply_frame_metadata(self, frame: ContinuationFrame, program: ProgramBase) -> None:
        def _safe_get_attr(obj: Any, name: str) -> Any | None:
            try:
                return object.__getattribute__(obj, name)
            except AttributeError:
                return None

        frame_type = _safe_get_attr(program, "_effect_stack_frame_type")
        if frame_type is not None:
            frame.frame_type_override = frame_type
            frame.frame_name_override = _safe_get_attr(program, "_effect_stack_name")

    def _assert_invariants(self, state: InterpreterState) -> None:
        if not __debug__:
            return

        if state.phase in (
            InterpretationPhase.STEPPING,
            InterpretationPhase.AWAITING_EFFECT,
            InterpretationPhase.PROPAGATING_ERROR,
        ):
            if not state.continuation_stack:
                raise InterpreterInvariantError("Active phase with empty continuation stack")

        frame_ids = [id(frame) for frame in state.continuation_stack]
        if len(set(frame_ids)) != len(frame_ids):
            raise InterpreterInvariantError("Continuation stack contains duplicate frames")

        if state.continuation_stack:
            active_frames = [frame for frame in state.continuation_stack if frame.is_current]
            if len(active_frames) != 1 or active_frames[0] is not state.current_frame:
                raise InterpreterInvariantError("Multiple active frames detected")

        if state.last_stats_snapshot is not None:
            last = state.last_stats_snapshot
            if state.stats.total_steps < last.total_steps:
                raise InterpreterInvariantError("Stats total_steps decreased")
            if state.stats.total_effects_handled < last.total_effects_handled:
                raise InterpreterInvariantError("Stats total_effects_handled decreased")
            if state.stats.total_frames_created < last.total_frames_created:
                raise InterpreterInvariantError("Stats total_frames_created decreased")
            if state.stats.max_stack_depth < last.max_stack_depth:
                raise InterpreterInvariantError("Stats max_stack_depth decreased")
            if state.stats.total_exceptions_caught < last.total_exceptions_caught:
                raise InterpreterInvariantError("Stats total_exceptions_caught decreased")

        if state.stats.max_stack_depth < state.stack_depth:
            raise InterpreterInvariantError("max_stack_depth less than current stack depth")

        state.last_stats_snapshot = state.stats.copy()

    def _record_effect_usage(self, effect: Effect, ctx: ExecutionContext) -> None:
        try:
            observations = ctx.effect_observations
        except AttributeError:
            return

        effect_type: str | None = None
        key: Any | None = None

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

    async def _handle_effect(self, effect: Effect, ctx: ExecutionContext) -> Any:
        self._record_effect_usage(effect, ctx)

        result = await self._try_intercept_effect(effect, ctx)
        if result is not _NO_HANDLER:
            return result

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

    async def _try_intercept_effect(self, effect: Effect, ctx: ExecutionContext) -> Any:
        if _effect_is(effect, InterceptEffect):
            return await self._handle_intercept_effect(effect, ctx)
        return _NO_HANDLER

    async def _handle_intercept_effect(self, effect: InterceptEffect, ctx: ExecutionContext) -> Any:
        intercept_program = _build_intercept_program(effect.program, effect.transforms)
        return intercept_program

    async def _try_reader_effects(self, effect: Effect, ctx: ExecutionContext) -> Any:
        if _effect_is(effect, AskEffect):
            return await self.reader_handler.handle_ask(effect, ctx, self)
        if _effect_is(effect, LocalEffect):
            return await self.reader_handler.handle_local(effect, ctx, self)
        if _effect_is(effect, DepInjectEffect):
            proxy_effect = AskEffect(key=effect.key, created_at=effect.created_at)
            self._record_effect_usage(proxy_effect, ctx)
            return await self.reader_handler.handle_ask(proxy_effect, ctx, self)
        return _NO_HANDLER

    async def _try_state_effects(self, effect: Effect, ctx: ExecutionContext) -> Any:
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

    async def _try_result_effects(self, effect: Effect, ctx: ExecutionContext) -> Any:
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

    async def _try_other_effects(self, effect: Effect, ctx: ExecutionContext) -> Any:
        result = await self._try_future_io_graph_effects(effect, ctx)
        if result is not _NO_HANDLER:
            return result
        result = await self._try_callstack_effects(effect, ctx)
        if result is not _NO_HANDLER:
            return result
        return await self._try_gather_memo_cache_effects(effect, ctx)

    async def _try_callstack_effects(self, effect: Effect, ctx: ExecutionContext) -> Any:
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

    async def _try_future_io_graph_effects(self, effect: Effect, ctx: ExecutionContext) -> Any:
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

    async def _try_gather_memo_cache_effects(self, effect: Effect, ctx: ExecutionContext) -> Any:
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

    async def _handle_gather_effect(self, effect: GatherEffect, ctx: ExecutionContext) -> Any:
        return await self._run_gather_sequence(list(effect.programs), ctx)

    async def _handle_gather_dict_effect(
        self, effect: GatherDictEffect, ctx: ExecutionContext
    ) -> Any:
        program_list = list(effect.programs.values())
        results = await self._run_gather_sequence(program_list, ctx)
        return dict(zip(effect.programs.keys(), results, strict=False))

    async def _run_gather_sequence(
        self, programs: list[Program], ctx: ExecutionContext
    ) -> list[Any]:
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
            tasks.append(asyncio.create_task(self.run_async(prog, ctx_copy)))

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

    def _build_effect_stack_trace(
        self,
        *,
        stack_snapshot: tuple[ContinuationFrame, ...],
        failed_effect: EffectBase | None,
        exception: BaseException,
        effect_failure: EffectFailure | None,
    ) -> EffectStackTrace:
        frames: list[EffectStackFrame] = []

        for cont_frame in stack_snapshot:
            source = cont_frame.source_info
            frame_type = cont_frame.frame_type_override
            name = cont_frame.frame_name_override
            if frame_type is None:
                if source is None:
                    frame_type = EffectStackFrameType.PROGRAM_FLAT_MAP
                else:
                    frame_type = EffectStackFrameType.KLEISLI_CALL
            if name is None:
                if source is None:
                    name = "<anonymous>"
                else:
                    name = source.function_name

            location = None
            call_args = None
            call_kwargs = None
            if source is not None:
                call_args = source.args
                call_kwargs = source.kwargs
                if source.created_at:
                    ctx = source.created_at
                    location = PythonLocation(
                        filename=ctx.filename,
                        line=ctx.line,
                        function=ctx.function,
                        code=ctx.code,
                    )

            frames.append(
                EffectStackFrame(
                    frame_type=frame_type,
                    name=name,
                    location=location,
                    call_args=call_args,
                    call_kwargs=call_kwargs,
                    raw_frame=source,
                )
            )

        if failed_effect is not None:
            if isinstance(failed_effect, TaskJoinEffect) and isinstance(
                exception, EffectFailure
            ):
                child_frames = self._frames_from_call_stack(exception.call_stack_snapshot)
                frames.append(
                    EffectStackFrame(
                        frame_type=EffectStackFrameType.SPAWN_BOUNDARY,
                        name="spawn",
                        location=None,
                    )
                )
                frames.extend(child_frames)

        if failed_effect is not None:
            effect_location = None
            if getattr(failed_effect, "created_at", None):
                ctx = failed_effect.created_at
                effect_location = PythonLocation(
                    filename=ctx.filename,
                    line=ctx.line,
                    function=ctx.function,
                    code=ctx.code,
                )
            frames.append(
                EffectStackFrame(
                    frame_type=EffectStackFrameType.EFFECT_YIELD,
                    name=type(failed_effect).__name__,
                    location=effect_location,
                )
            )

        python_location = self._extract_raise_location(exception)

        return EffectStackTrace(
            frames=tuple(frames),
            failed_effect=failed_effect,
            original_exception=exception,
            python_raise_location=python_location,
        )

    def _frames_from_call_stack(
        self, call_stack: tuple[CallFrame, ...]
    ) -> list[EffectStackFrame]:
        frames: list[EffectStackFrame] = []
        for call_frame in call_stack:
            location = None
            if call_frame.created_at:
                ctx = call_frame.created_at
                location = PythonLocation(
                    filename=ctx.filename,
                    line=ctx.line,
                    function=ctx.function,
                    code=ctx.code,
                )
            frames.append(
                EffectStackFrame(
                    frame_type=EffectStackFrameType.KLEISLI_CALL,
                    name=call_frame.function_name,
                    location=location,
                    call_args=call_frame.args,
                    call_kwargs=call_frame.kwargs,
                    raw_frame=call_frame,
                )
            )
        return frames

    @staticmethod
    def _extract_raise_location(exc: BaseException) -> PythonLocation | None:
        tb = exc.__traceback__
        if tb is None:
            return None

        while tb.tb_next is not None:
            tb = tb.tb_next

        frame = tb.tb_frame
        return PythonLocation(
            filename=frame.f_code.co_filename,
            line=tb.tb_lineno,
            function=frame.f_code.co_name,
            code=None,
        )

    @staticmethod
    def _program_to_generator(program: ProgramBase) -> Generator[Effect | Program, Any, Any]:
        if isinstance(program, KleisliProgramCall):
            return program.to_generator()
        to_gen = getattr(program, "to_generator", None)
        if callable(to_gen):
            return to_gen()
        raise TypeError(
            f"Program {program!r} does not implement to_generator(); cannot execute"
        )


# Intercept utilities

def _build_intercept_program(
    program: Program[T],
    transforms: tuple[Callable[[Effect], Effect | Program], ...],
) -> Program[T]:
    from doeff.program import GeneratorProgram

    return GeneratorProgram(lambda: _intercept_generator(program, transforms))


def _intercept_generator(
    base: Program[T],
    transforms: tuple[Callable[[Effect], Effect | Program], ...],
) -> Generator[Effect | Program, Any, T]:
    from doeff.program import ProgramBase, _InterceptedProgram
    from doeff.types import EffectBase

    gen = _program_to_generator(base)
    try:
        current = next(gen)
    except StopIteration as exc:
        return exc.value

    transform_chain = _compose_intercept_transforms(transforms)

    def _forward_exception(e: BaseException) -> bool | T:
        nonlocal current
        try:
            current = gen.throw(e)
            return True
        except StopIteration as stop_exc:
            return stop_exc.value

    while True:
        if isinstance(current, EffectBase):
            effect_program = transform_chain(current)
            try:
                final_effect = yield effect_program
            except GeneratorExit:
                gen.close()
                raise
            except BaseException as e:
                result = _forward_exception(e)
                if result is True:
                    continue
                return result

            if not isinstance(final_effect, EffectBase):
                raise TypeError(
                    "Intercept transform must resolve to an Effect, "
                    f"got {type(final_effect).__name__}"
                )

            nested_effect = final_effect.intercept(
                lambda eff: transform_chain(eff)
            )
            try:
                result = yield nested_effect
            except GeneratorExit:
                gen.close()
                raise
            except BaseException as e:
                fwd_result = _forward_exception(e)
                if fwd_result is True:
                    continue
                return fwd_result
            try:
                current = gen.send(result)
            except StopIteration as exc:
                return exc.value
            continue

        if isinstance(current, ProgramBase):
            wrapped = _InterceptedProgram.compose(current, transforms)
            try:
                yielded_value = yield wrapped
            except GeneratorExit:
                gen.close()
                raise
            except BaseException as e:
                result = _forward_exception(e)
                if result is True:
                    continue
                return result
            try:
                current = gen.send(yielded_value)
            except StopIteration as exc:
                return exc.value
            continue

        try:
            value = yield current
        except GeneratorExit:
            gen.close()
            raise
        except BaseException as e:
            result = _forward_exception(e)
            if result is True:
                continue
            return result
        try:
            current = gen.send(value)
        except StopIteration as exc:
            return exc.value


def _compose_intercept_transforms(
    transforms: tuple[Callable[[Effect], Effect | Program], ...]
) -> Callable[[Effect], Program]:
    from doeff.program import Program
    from doeff.types import EffectBase

    lifted = [_lift_intercept_transform(transform) for transform in transforms]

    def combined(effect: EffectBase) -> Program:
        program: Program = Program.pure(effect)
        for lift in lifted:
            program = program.flat_map(lift)
        return program

    return combined


def _lift_intercept_transform(
    transform: Callable[[Effect], Effect | Program]
) -> Callable[[Effect], Program]:
    from doeff.program import Program, ProgramBase
    from doeff.types import EffectBase

    def lifted(effect: EffectBase) -> Program:
        result = transform(effect)

        if isinstance(result, EffectBase):
            return Program.pure(result)

        if isinstance(result, ProgramBase):
            return result.flat_map(_ensure_effect_program)

        raise TypeError(
            "Intercept transform must return Effect or Program yielding Effect, "
            f"got {type(result).__name__}"
        )

    return lifted


def _ensure_effect_program(value: Any) -> Program:
    from doeff.program import Program
    from doeff.types import EffectBase

    if isinstance(value, EffectBase):
        return Program.pure(value)
    raise TypeError(
        "Intercept transform must resolve to an Effect, "
        f"got {type(value).__name__}"
    )


def _program_to_generator(base: Program[T]) -> Generator[Effect | Program, Any, T]:
    if isinstance(base, KleisliProgramCall):
        return base.to_generator()

    to_gen = getattr(base, "to_generator", None)
    if callable(to_gen):
        return to_gen()

    raise TypeError(
        "Cannot intercept value that does not expose to_generator(): "
        f"{type(base).__name__}"
    )


__all__ = ["TrampolinedInterpreter"]
