"""
CESK Machine abstraction for the doeff effect interpreter.

Specification: See ISSUE-CORE-422.md in doeff-vault repository for full design.
https://github.com/CyberAgentAILab/doeff-vault/blob/main/Issues/ISSUE-CORE-422.md

This module implements a CESK machine (Control, Environment, Store, Kontinuation)
as described in Felleisen & Friedman (1986) and Van Horn & Might (2010).

The CESK machine provides:
- Clean semantics based on well-established abstract machine model
- Total machine - every state has a defined transition
- Separated concerns - pure handlers vs effectful handlers vs control flow
- Explicit control flow - Catch/Intercept/Local are K frames, not magic
- Fully trampolined - no nested interpreters (except intentional parallelism)
- Continuation-based suspension via `Suspended` type

API Mapping Notes (doeff API vs ORCH_PROMPT spec):
-------------------------------------------------
The ORCH_PROMPT spec uses abstract effect names. This implementation maps them
to the actual doeff API effects:

| ORCH_PROMPT Spec     | doeff API Effect           | Implementation          |
|----------------------|----------------------------|-------------------------|
| Catch (handler)      | ResultCatchEffect          | CatchFrame              |
| Finally (cleanup)    | ResultFinallyEffect        | FinallyFrame            |
| Thread (callable)    | ThreadEffect               | ThreadPoolExecutor      |
| Spawn (program)      | SpawnEffect                | Async child machine     |
| Tell (message)       | WriterTellEffect           | Single message append   |

NOTE: Recover/Retry/Fail are NOT handled in CESK core.
They can be implemented as library sugar over Catch in the Pure interpreter.

NOTE: Safe IS supported in CESK - see SafeFrame and ResultSafeEffect handling.
Safe captures K stack traceback BEFORE unwinding on error (ISSUE-CORE-429).

NOTE: For parallel execution, use asyncio.create_task + Await + Gather pattern.

Suspension Model (Continuation-based):
-------------------------------------
When the step function encounters an effectful operation, it returns a
`Suspended` object containing:
- `effect`: The effect to be handled externally
- `resume(value, new_store)`: Continuation to call on success
- `resume_error(exception)`: Continuation to call on error

This unified model replaces ad-hoc NeedAsync with explicit continuations,
aligning with CPS (continuation-passing style) semantics.

State Merging Semantics:
-----------------------
- **Spawn (await_result=True)**: Child gets deep copy, state merges on join.

- **Spawn (await_result=False)**: Fire-and-forget, no state merge.

- **Thread**: Runs a callable (not a program) in thread pool. No CESK machine
  for child, no state merge. Store passes through unchanged.

- **Listen**: Captures logs from sub-computation. Child logs from Spawn
  merge BEFORE Listen captures (only when state merge occurs).

For parallel execution, use asyncio.create_task + Await + Gather pattern.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeAlias, TypeVar

from doeff._vendor import Err, FrozenDict, Ok, Result

if TYPE_CHECKING:
    from doeff.cesk_observability import OnStepCallback
    from doeff.cesk_traceback import CapturedTraceback
    from doeff.program import KleisliProgramCall, Program
    from doeff.storage import DurableStorage
    from doeff.types import Effect

from doeff._types_internal import EffectBase, ListenResult
from doeff.runtime import (
    Continuation,
    HandlerResult,
    Resume,
    Schedule,
    Scheduled,
    ScheduledEffectHandler,
    ScheduledHandlers,
    Scheduler,
    Suspend,
)
from doeff.scheduled_handlers import default_scheduled_handlers
from doeff.utils import BoundedLog

T = TypeVar("T")
E = TypeVar("E", bound=EffectBase)
R = TypeVar("R")


class HandlerRegistryError(Exception):
    """Raised when there's a conflict or invalid handler registration."""



# ============================================================================
# CESK State Components
# ============================================================================

# E: Environment - immutable mapping (copy-on-write semantics)
Environment: TypeAlias = FrozenDict[Any, Any]

# S: Store - mutable state (dict with reserved keys: __log__, __memo__, __durable_storage__, __dispatcher__)
Store: TypeAlias = dict[str, Any]


# ============================================================================
# Control (C) - What we're currently evaluating
# ============================================================================


@dataclass(frozen=True)
class Value:
    """Control state: computation has produced a value."""

    v: Any


@dataclass(frozen=True)
class Error:
    """Control state: computation has raised an exception."""

    ex: BaseException
    # Captured traceback data (captured when error first occurs)
    captured_traceback: CapturedTraceback | None = None


@dataclass(frozen=True)
class EffectControl:
    """Control state: need to handle an effect."""

    effect: EffectBase


@dataclass(frozen=True)
class ProgramControl:
    """Control state: need to execute a program."""

    program: Program


Control: TypeAlias = Value | Error | EffectControl | ProgramControl


# ============================================================================
# Kontinuation (K) - Frames representing what to do with results
# ============================================================================


@dataclass
class ReturnFrame:
    """Resume generator with value.

    Note: This frame is intentionally NOT frozen because Python generators
    are mutable objects. The generator is owned exclusively by this frame.
    Each ReturnFrame owns one generator and advances it on each step.
    This is the unavoidable impurity in implementing coroutine-style effects.
    """

    generator: Generator[Any, Any, Any]
    saved_env: Environment
    # The KleisliProgramCall that created this generator (for correct function name in tracebacks)
    program_call: KleisliProgramCall | None = None


@dataclass(frozen=True)
class CatchFrame:
    """Error boundary - catches exceptions and invokes handler.

    On success: passes value through, restores saved_env
    On error: runs handler(ex) with saved_env, result becomes the value
    """

    handler: Callable[[Exception], Program]
    saved_env: Environment


@dataclass(frozen=True)
class FinallyFrame:
    """Cleanup on exit - runs cleanup program on both success and error.

    On success: cleanup runs, then original value returned
    On error: cleanup runs, then original error re-raised
    If cleanup FAILS: cleanup error REPLACES original value/error
    """

    cleanup_program: Program
    saved_env: Environment


@dataclass(frozen=True)
class LocalFrame:
    """Restore environment after scoped execution.

    On BOTH success AND error: restores environment before continuing.
    """

    restore_env: Environment


@dataclass(frozen=True)
class InterceptFrame:
    """Transform effects passing through. Marks interception boundary.

    Non-control-flow effects get transformed as they bubble up.
    Control flow effects pass through unchanged.

    Chain semantics (inner → outer):
    - All InterceptFrames in K are traversed in order
    - Each frame's transforms are applied to the (possibly transformed) effect
    - First transform returning Effect/Program within a frame wins
    - Outer interceptors see effects that may have been transformed by inner ones
    - If all transforms return None → original effect unchanged

    This enables composable interception.
    """

    transforms: tuple[Callable[[Effect], Effect | Program | None], ...]


@dataclass(frozen=True)
class ListenFrame:
    """Capture log output from sub-computation.

    On success: returns (value, captured_logs)
    On error: propagates error (logs up to error point remain in S for debugging)
    """

    log_start_index: int


@dataclass(frozen=True)
class GatherFrame:
    """Collect results from sequential program execution.

    Sequential: each program runs with saved_env, sees S modifications from previous.
    On success: returns list of all results, restores saved_env.
    On error: propagates immediately (partial results discarded), restores saved_env.
    """

    remaining_programs: list[Program]
    collected_results: list[Any]
    saved_env: Environment


@dataclass(frozen=True)
class SafeFrame:
    """Safe boundary - captures K stack on error, returns Result.
    
    On success: wraps value in Ok and passes through
    On error: captures K stack snapshot, wraps in Err with traceback attached
    """

    saved_env: Environment


Frame: TypeAlias = (
    ReturnFrame
    | CatchFrame
    | FinallyFrame
    | LocalFrame
    | InterceptFrame
    | ListenFrame
    | GatherFrame
    | SafeFrame
)

# Kontinuation is a stack of frames
Kontinuation: TypeAlias = list[Frame]


# ============================================================================
# State Tuple
# ============================================================================


@dataclass
class CESKState:
    """Full CESK machine state."""

    C: Control
    E: Environment
    S: Store
    K: Kontinuation

    @classmethod
    def initial(
        cls,
        program: Program,
        env: Environment | dict[Any, Any] | None = None,
        store: Store | None = None,
    ) -> CESKState:
        """Create initial state for a program."""
        # Coerce env to FrozenDict to ensure immutability
        if env is None:
            env_frozen = FrozenDict()
        elif isinstance(env, FrozenDict):
            env_frozen = env
        else:
            env_frozen = FrozenDict(env)
        return cls(
            C=ProgramControl(program),
            E=env_frozen,
            S=store if store is not None else {},
            K=[],
        )


# ============================================================================
# Step Result - What step() returns
# ============================================================================


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
    # Complete traceback data for error reporting
    captured_traceback: CapturedTraceback | None = None


@dataclass(frozen=True)
class Suspended:
    """Suspend: need external handling to continue.

    Continuation-based suspension for async operations. The effect is handled
    externally, then the appropriate continuation is called with the result.

    Per spec: continuations take (value, new_store) to incorporate handler's
    store updates. On error, resume_error uses the original store (S) from
    before the effect - effectful handlers should NOT mutate S in-place.
    """

    effect: EffectBase
    # Continuation: (value, new_store) -> next state
    resume: Callable[[Any, Store], CESKState]
    # Error continuation: exception -> next state (uses original store)
    resume_error: Callable[[BaseException], CESKState]


Terminal: TypeAlias = Done | Failed
StepResult: TypeAlias = CESKState | Terminal | Suspended


# ============================================================================
# CESKResult - Public result type with traceback support
# ============================================================================


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

    @property
    def is_ok(self) -> bool:
        """Check if result is successful."""
        return isinstance(self.result, Ok)

    @property
    def is_err(self) -> bool:
        """Check if result is an error."""
        return isinstance(self.result, Err)

    @property
    def value(self) -> T:
        """Get success value. Raises if error."""
        return self.result.ok()

    @property
    def error(self) -> BaseException:
        """Get error. Raises if success."""
        return self.result.err()


# ============================================================================
# Effect Classification
# ============================================================================


def is_control_flow_effect(effect: EffectBase) -> bool:
    """Check if effect is a control flow effect that pushes frames.

    Control flow effects are NOT interceptable by InterceptFrame - they always
    push their frames directly.
    """
    from doeff.effects import (
        GatherEffect,
        InterceptEffect,
        LocalEffect,
        ResultCatchEffect,
        ResultFinallyEffect,
        ResultSafeEffect,
        WriterListenEffect,
    )

    return isinstance(
        effect,
        (
            ResultCatchEffect,
            ResultFinallyEffect,
            ResultSafeEffect,
            LocalEffect,
            InterceptEffect,
            WriterListenEffect,
            GatherEffect,
        ),
    )


def is_pure_effect(effect: EffectBase) -> bool:
    """Check if effect can be handled synchronously without I/O."""
    from doeff.effects import (
        AskEffect,
        MemoGetEffect,
        MemoPutEffect,
        StateGetEffect,
        StateModifyEffect,
        StatePutEffect,
        WriterTellEffect,
    )
    from doeff.effects.durable_cache import (
        DurableCacheDelete,
        DurableCacheExists,
        DurableCacheGet,
        DurableCachePut,
    )
    from doeff.effects.pure import PureEffect

    return isinstance(
        effect,
        (
            StateGetEffect,
            StatePutEffect,
            StateModifyEffect,
            AskEffect,
            WriterTellEffect,
            MemoGetEffect,
            MemoPutEffect,
            PureEffect,
            # Durable cache effects (handled via __durable_storage__ in store)
            DurableCacheGet,
            DurableCachePut,
            DurableCacheDelete,
            DurableCacheExists,
        ),
    )


def is_effectful(effect: EffectBase) -> bool:
    """Check if effect may perform I/O (async boundary)."""
    from doeff.effects import (
        FutureAwaitEffect,
        IOPerformEffect,
        IOPrintEffect,
        SpawnEffect,
        TaskJoinEffect,
        ThreadEffect,
    )

    return isinstance(
        effect,
        (
            IOPerformEffect,
            IOPrintEffect,
            FutureAwaitEffect,
            ThreadEffect,
            SpawnEffect,
            TaskJoinEffect,
        ),
    )


def has_intercept_frame(K: Kontinuation) -> bool:
    """Check if continuation stack contains an InterceptFrame."""
    return any(isinstance(f, InterceptFrame) for f in K)


def find_intercept_frame_index(K: Kontinuation) -> int:
    """Find index of first InterceptFrame in continuation stack."""
    for i, f in enumerate(K):
        if isinstance(f, InterceptFrame):
            return i
    raise ValueError("No InterceptFrame found")


# ============================================================================
# Pure Effect Handlers
# ============================================================================


class UnhandledEffectError(Exception):
    """Raised when no handler exists for an effect."""



class InterpreterInvariantError(Exception):
    """Raised when the interpreter reaches an invalid state."""









# ============================================================================
# Transform Application
# ============================================================================


def apply_transforms(
    transforms: tuple[Callable[[Effect], Effect | Program | None], ...],
    effect: Effect,
) -> Effect | Program:
    """
    Apply transform functions in order. First non-None result wins.

    Transform contract:
    - Takes an Effect
    - Returns Effect (rewrite effect), Program (substitute computation), or None (no match)
    - MUST be pure (no I/O, no side effects)
    - MUST NOT call the interpreter or run programs
    - MAY raise exceptions (converted to Error by step function)
    """
    for transform in transforms:
        result = transform(effect)
        if result is not None:
            return result
    return effect  # No transform matched, return original


def apply_intercept_chain(K: Kontinuation, effect: Effect) -> Effect | Program:
    """
    Apply intercept transforms from ALL InterceptFrames in the continuation stack.

    Chain semantics (inner → outer):
    - Traverse K looking for InterceptFrames
    - For each InterceptFrame, apply its transforms to current effect
    - First transform returning Effect/Program replaces current effect
    - Continue to next InterceptFrame with the (possibly transformed) effect
    - If all transforms return None → original effect unchanged

    This enables composable interception: outer interceptors see effects
    that may have been transformed by inner interceptors.
    """
    current = effect
    for frame in K:
        if isinstance(frame, InterceptFrame):
            for transform in frame.transforms:
                result = transform(current)
                if result is not None:
                    current = result
                    break  # This frame matched; move to next frame
    return current


# ============================================================================
# State Merging
# ============================================================================


def merge_store(parent_store: Store, child_store: Store, child_snapshot: Store | None = None) -> Store:
    """Merge child store into parent after child completion.

    Merge semantics:
    - User keys: child can ADD new keys; for existing keys, parent wins
      (parent may have updated after spawn, child's snapshot is stale)
    - __log__: child logs are APPENDED to parent log
    - __memo__: child entries are MERGED (child overwrites on conflict)

    Args:
        parent_store: Current parent store state
        child_store: Child's final store state
        child_snapshot: Optional - child's initial snapshot (for detecting new keys)
    """
    merged = {**parent_store}

    # Merge user keys - only ADD new keys from child, don't overwrite parent
    # This ensures parent's updates after spawn are preserved
    for key, value in child_store.items():
        if key.startswith("__"):
            continue
        # Only add if key doesn't exist in parent
        if key not in parent_store:
            merged[key] = value

    # Append logs
    parent_log = merged.get("__log__", [])
    child_log = child_store.get("__log__", [])
    merged["__log__"] = parent_log + child_log

    # Merge memo (child overwrites)
    parent_memo = merged.get("__memo__", {})
    child_memo = child_store.get("__memo__", {})
    merged["__memo__"] = {**parent_memo, **child_memo}

    return merged


def _merge_thread_state(parent_store: Store, child_store: Store) -> Store:
    """Merge thread state: child state replaces parent (except logs append).

    Used by ThreadEffect handlers when await_result=True.

    Merge semantics (different from merge_store):
    - User keys: child's values replace parent's
    - __log__: child logs are APPENDED to parent log
    - __memo__: child entries are MERGED (child overwrites on conflict)
    """
    merged = {}

    # Child user keys replace parent
    for key, value in child_store.items():
        if not key.startswith("__"):
            merged[key] = value
    for key, value in parent_store.items():
        if not key.startswith("__") and key not in merged:
            merged[key] = value

    # Append logs
    parent_log = parent_store.get("__log__", [])
    child_log = child_store.get("__log__", [])
    if child_log:
        merged["__log__"] = list(parent_log) + list(child_log)
    elif parent_log:
        merged["__log__"] = list(parent_log)

    # Merge memo (child overwrites)
    parent_memo = parent_store.get("__memo__", {})
    child_memo = child_store.get("__memo__", {})
    if parent_memo or child_memo:
        merged["__memo__"] = {**parent_memo, **child_memo}

    # Preserve durable storage from parent (child doesn't own it)
    if "__durable_storage__" in parent_store:
        merged["__durable_storage__"] = parent_store["__durable_storage__"]

    return merged


# ============================================================================
# Helper Functions for Cleanup
# ============================================================================


def _wrap_callable_as_program(func: Callable[[], Any]) -> Program:
    """Wrap a callable (thunk) in a program that calls it."""
    from doeff.do import do

    @do
    def call_thunk():
        result = func()
        # If result is a program, yield it
        from doeff.program import ProgramBase
        from doeff.types import EffectBase

        if isinstance(result, (ProgramBase, EffectBase)):
            return (yield result)
        return result

    return call_thunk()


def make_cleanup_then_return(cleanup: Program, value: Any) -> Program:
    """Create program that runs cleanup then returns value."""
    from doeff.do import do

    @do
    def cleanup_then_return_impl():
        yield cleanup
        return value

    return cleanup_then_return_impl()


def make_cleanup_then_raise(cleanup: Program, ex: BaseException) -> Program:
    """Create program that runs cleanup then re-raises exception."""
    from doeff.do import do

    @do
    def cleanup_then_raise_impl():
        yield cleanup
        raise ex.with_traceback(ex.__traceback__)

    return cleanup_then_raise_impl()


# ============================================================================
# Program to Generator Conversion
# ============================================================================


def to_generator(program: Program) -> Generator[Any, Any, Any]:
    """Convert a program to a generator."""
    from doeff.program import KleisliProgramCall, ProgramBase

    if isinstance(program, KleisliProgramCall):
        return program.to_generator()

    if isinstance(program, ProgramBase):
        to_gen = getattr(program, "to_generator", None)
        if callable(to_gen):
            return to_gen()

    raise TypeError(f"Cannot convert {type(program).__name__} to generator")


# ============================================================================
# Step Function - The Core of the CESK Machine
# ============================================================================


def step(state: CESKState, dispatcher: ScheduledEffectDispatcher | None = None) -> StepResult:
    """
    Single step of the CESK machine.

    Args:
        state: Current CESK machine state.
        dispatcher: Scheduled effect dispatcher for handler lookup. If None, uses default
            handlers with legacy isinstance-based dispatch for backward compatibility.

    Returns:
    - CESKState for continued execution
    - Terminal: Done(value) | Failed(exception) - computation complete
    - Suspend: Suspended(effect, resume, resume_error) - pause for async
    """
    C, E, S, K = state.C, state.E, state.S, state.K

    # =========================================================================
    # TERMINAL STATES (check first)
    # =========================================================================

    if isinstance(C, Value) and not K:
        return Done(C.v, S)

    if isinstance(C, Error) and not K:
        return Failed(C.ex, S, captured_traceback=C.captured_traceback)

    # =========================================================================
    # CONTROL FLOW EFFECTS (before generic effect handling)
    # These push frames onto K rather than being "handled"
    # =========================================================================

    if isinstance(C, EffectControl):
        effect = C.effect
        from doeff.effects import (
            GatherEffect,
            InterceptEffect,
            LocalEffect,
            ResultCatchEffect,
            ResultFinallyEffect,
            ResultSafeEffect,
            WriterListenEffect,
        )
        # Note: Recover/Retry/Fail are NOT handled in CESK core
        # They are library sugar handled by the Pure interpreter
        # Note: Safe IS handled - see SafeFrame and ResultSafeEffect handling below
        # Note: For parallel execution, use asyncio.create_task + Await + Gather pattern

        if isinstance(effect, ResultCatchEffect):
            return CESKState(
                C=ProgramControl(effect.sub_program),
                E=E,
                S=S,
                K=[CatchFrame(effect.handler, E)] + K,
            )

        if isinstance(effect, ResultFinallyEffect):
            cleanup = effect.finalizer
            # Normalize finalizer to a Program per spec
            from doeff.program import ProgramBase
            from doeff.types import EffectBase

            if not isinstance(cleanup, (ProgramBase, EffectBase)):
                if callable(cleanup):
                    # Wrap callable in a program that calls it
                    cleanup = _wrap_callable_as_program(cleanup)
                else:
                    # Non-program, non-callable - wrap in pure program
                    from doeff.program import Program
                    cleanup = Program.pure(cleanup)
            return CESKState(
                C=ProgramControl(effect.sub_program),
                E=E,
                S=S,
                K=[FinallyFrame(cleanup, E)] + K,
            )

        if isinstance(effect, LocalEffect):
            # env_update is a dict to merge: E' = E | env_update
            new_env = E | FrozenDict(effect.env_update)
            return CESKState(
                C=ProgramControl(effect.sub_program),
                E=new_env,
                S=S,
                K=[LocalFrame(E)] + K,
            )

        if isinstance(effect, InterceptEffect):
            return CESKState(
                C=ProgramControl(effect.program),
                E=E,
                S=S,
                K=[InterceptFrame(effect.transforms)] + K,
            )

        if isinstance(effect, WriterListenEffect):
            log_start = len(S.get("__log__", []))
            return CESKState(
                C=ProgramControl(effect.sub_program),
                E=E,
                S=S,
                K=[ListenFrame(log_start)] + K,
            )

        if isinstance(effect, GatherEffect):
            programs = list(effect.programs)
            if not programs:
                return CESKState(C=Value([]), E=E, S=S, K=K)
            first, *rest = programs
            return CESKState(
                C=ProgramControl(first),
                E=E,
                S=S,
                K=[GatherFrame(rest, [], E)] + K,
            )

        if isinstance(effect, ResultSafeEffect):
            return CESKState(
                C=ProgramControl(effect.sub_program),
                E=E,
                S=S,
                K=[SafeFrame(E)] + K,
            )

        # =====================================================================
        # EFFECT INTERCEPTION (before generic effect handling)
        # Chain semantics: apply transforms from ALL InterceptFrames (inner→outer)
        # =====================================================================

        if not is_control_flow_effect(effect) and has_intercept_frame(K):
            from doeff.cesk_traceback import capture_traceback_safe

            try:
                transformed = apply_intercept_chain(K, effect)
            except Exception as ex:
                # Intercept transform raised - capture traceback
                captured = capture_traceback_safe(K, ex)
                return CESKState(C=Error(ex, captured_traceback=captured), E=E, S=S, K=K)

            from doeff.program import ProgramBase
            from doeff.types import EffectBase

            # IMPORTANT: Check EffectBase BEFORE ProgramBase because EffectBase inherits from ProgramBase
            # If we check ProgramBase first, Effects would match and be treated as Programs (wrong!)

            if isinstance(transformed, EffectBase):
                if is_control_flow_effect(transformed):
                    # Transform returned control-flow effect - handle it normally
                    return CESKState(C=EffectControl(transformed), E=E, S=S, K=K)

                has_handler = dispatcher.has_handler(transformed) if dispatcher else (is_pure_effect(transformed) or is_effectful(transformed))

                if has_handler:
                    return Suspended(
                        effect=transformed,
                        resume=lambda v, new_store, E=E, K=K: CESKState(
                            C=Value(v), E=E, S=new_store, K=K
                        ),
                        resume_error=lambda ex, E=E, S=S, K=K: CESKState(
                            C=Error(ex), E=E, S=S, K=K
                        ),
                    )

                unhandled_ex = UnhandledEffectError(f"No handler for {type(transformed).__name__}")
                captured = capture_traceback_safe(K, unhandled_ex)
                return CESKState(
                    C=Error(unhandled_ex, captured_traceback=captured),
                    E=E,
                    S=S,
                    K=K,
                )

            if isinstance(transformed, ProgramBase):
                # Transform returned Program (not Effect) - run it INSIDE intercept scope
                return CESKState(C=ProgramControl(transformed), E=E, S=S, K=K)

            # Unknown effect type - error
            unknown_ex = UnhandledEffectError(f"No handler for {type(transformed).__name__}")
            captured = capture_traceback_safe(K, unknown_ex)
            return CESKState(
                C=Error(unknown_ex, captured_traceback=captured),
                E=E,
                S=S,
                K=K,
            )

        # =====================================================================
        # EFFECT HANDLING → check dispatcher for handler, return Suspended
        # =====================================================================

        has_handler = dispatcher.has_handler(effect) if dispatcher else (is_pure_effect(effect) or is_effectful(effect))

        if has_handler:
            return Suspended(
                effect=effect,
                resume=lambda v, new_store, E=E, K=K: CESKState(
                    C=Value(v), E=E, S=new_store, K=K
                ),
                resume_error=lambda ex, E=E, S=S, K=K: CESKState(
                    C=Error(ex), E=E, S=S, K=K
                ),
            )

        # =====================================================================
        # UNHANDLED EFFECTS → error
        # =====================================================================

        from doeff.cesk_traceback import capture_traceback_safe

        unhandled_ex = UnhandledEffectError(f"No handler for {type(effect).__name__}")
        captured = capture_traceback_safe(K, unhandled_ex)
        return CESKState(
            C=Error(unhandled_ex, captured_traceback=captured),
            E=E,
            S=S,
            K=K,
        )

    # =========================================================================
    # PROGRAM → push ReturnFrame, step into generator
    # =========================================================================

    if isinstance(C, ProgramControl):
        program = C.program
        from doeff.cesk_traceback import capture_traceback_safe, pre_capture_generator
        from doeff.program import KleisliProgramCall, ProgramBase
        from doeff.types import EffectBase

        try:
            gen = to_generator(program)

            # Get program_call for correct function name (if program is KleisliProgramCall)
            program_call = program if isinstance(program, KleisliProgramCall) else None

            # PRE-CAPTURE: Save generator info BEFORE execution
            # is_resumed=False: use co_firstlineno, frame_kind="kleisli_entry"
            # NO file I/O here (linecache deferred to error path)
            pre_captured = pre_capture_generator(gen, is_resumed=False, program_call=program_call)

            item = next(gen)

            if isinstance(item, EffectBase):
                control = EffectControl(item)
            elif isinstance(item, ProgramBase):
                control = ProgramControl(item)
            else:
                # Unexpected yield type - programs must yield Effect or Program only
                return CESKState(
                    C=Error(InterpreterInvariantError(f"Program yielded unexpected type: {type(item).__name__}. Programs must yield Effect or Program instances only.")),
                    E=E,
                    S=S,
                    K=K,
                )

            return CESKState(
                C=control,
                E=E,
                S=S,
                K=[ReturnFrame(gen, E, program_call=program_call)] + K,
            )
        except StopIteration as e:
            # Program immediately returned without yielding
            return CESKState(C=Value(e.value), E=E, S=S, K=K)
        except Exception as ex:
            # Generator raised on first step - capture traceback
            # K might be empty for top-level, but we have pre_captured
            captured = capture_traceback_safe(K, ex, pre_captured=pre_captured)
            return CESKState(C=Error(ex, captured_traceback=captured), E=E, S=S, K=K)

    # =========================================================================
    # VALUE + Frames → propagate value through continuation
    # =========================================================================

    if isinstance(C, Value) and K:
        frame = K[0]
        K_rest = K[1:]

        if isinstance(frame, ReturnFrame):
            from doeff.cesk_traceback import capture_traceback_safe, pre_capture_generator
            from doeff.program import ProgramBase
            from doeff.types import EffectBase

            # PRE-CAPTURE: Save current generator info BEFORE send()
            # is_resumed=True: use gi_frame.f_lineno, frame_kind="kleisli_yield"
            pre_captured = pre_capture_generator(
                frame.generator, is_resumed=True, program_call=frame.program_call
            )

            try:
                item = frame.generator.send(C.v)

                if isinstance(item, EffectBase):
                    control = EffectControl(item)
                elif isinstance(item, ProgramBase):
                    control = ProgramControl(item)
                else:
                    return CESKState(
                        C=Error(InterpreterInvariantError(f"Program yielded unexpected type: {type(item).__name__}. Programs must yield Effect or Program instances only.")),
                        E=frame.saved_env,
                        S=S,
                        K=K_rest,
                    )

                return CESKState(
                    C=control,
                    E=frame.saved_env,
                    S=S,
                    K=[ReturnFrame(frame.generator, frame.saved_env, program_call=frame.program_call)] + K_rest,
                )
            except StopIteration as e:
                return CESKState(C=Value(e.value), E=frame.saved_env, S=S, K=K_rest)
            except Exception as ex:
                # Capture traceback with pre_captured (generator may be dead)
                captured = capture_traceback_safe(K_rest, ex, pre_captured=pre_captured)
                return CESKState(C=Error(ex, captured_traceback=captured), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, CatchFrame):
            # Value passes through CatchFrame unchanged, restore env
            return CESKState(C=Value(C.v), E=frame.saved_env, S=S, K=K_rest)

        # Note: RecoverFrame removed - Result/Maybe are values, not effects

        if isinstance(frame, FinallyFrame):
            # Run cleanup, then return value
            cleanup_program = make_cleanup_then_return(frame.cleanup_program, C.v)
            return CESKState(C=ProgramControl(cleanup_program), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, LocalFrame):
            # Restore environment
            return CESKState(C=Value(C.v), E=frame.restore_env, S=S, K=K_rest)

        if isinstance(frame, InterceptFrame):
            # Interception scope ends, pass value through
            return CESKState(C=Value(C.v), E=E, S=S, K=K_rest)

        if isinstance(frame, ListenFrame):
            # Capture logs and return ListenResult per doeff API
            current_log = S.get("__log__", [])
            captured = current_log[frame.log_start_index :]
            # Wrap captured logs in BoundedLog for ListenResult compatibility
            listen_result = ListenResult(value=C.v, log=BoundedLog(captured))
            return CESKState(C=Value(listen_result), E=E, S=S, K=K_rest)

        if isinstance(frame, GatherFrame):
            if not frame.remaining_programs:
                final_results = frame.collected_results + [C.v]
                return CESKState(C=Value(final_results), E=frame.saved_env, S=S, K=K_rest)

            next_prog, *rest = frame.remaining_programs
            return CESKState(
                C=ProgramControl(next_prog),
                E=frame.saved_env,
                S=S,
                K=[GatherFrame(rest, frame.collected_results + [C.v], frame.saved_env)] + K_rest,
            )

        if isinstance(frame, SafeFrame):
            return CESKState(C=Value(Ok(C.v)), E=frame.saved_env, S=S, K=K_rest)

    # =========================================================================
    # ERROR + Frames → propagate error through continuation
    # =========================================================================

    if isinstance(C, Error) and K:
        frame = K[0]
        K_rest = K[1:]

        if isinstance(frame, ReturnFrame):
            from doeff.cesk_traceback import capture_traceback_safe, pre_capture_generator
            from doeff.program import ProgramBase
            from doeff.types import EffectBase

            # PRE-CAPTURE: Save current generator info BEFORE throw()
            # is_resumed=True: generator is paused at a yield
            pre_captured = pre_capture_generator(
                frame.generator, is_resumed=True, program_call=frame.program_call
            )

            try:
                # Throw into generator - single-arg form preserves traceback
                # when passing exception instance (modern Python approach)
                item = frame.generator.throw(C.ex)

                if isinstance(item, EffectBase):
                    control = EffectControl(item)
                elif isinstance(item, ProgramBase):
                    control = ProgramControl(item)
                else:
                    return CESKState(
                        C=Error(InterpreterInvariantError(f"Program yielded unexpected type: {type(item).__name__}. Programs must yield Effect or Program instances only.")),
                        E=frame.saved_env,
                        S=S,
                        K=K_rest,
                    )

                return CESKState(
                    C=control,
                    E=frame.saved_env,
                    S=S,
                    K=[ReturnFrame(frame.generator, frame.saved_env, program_call=frame.program_call)] + K_rest,
                )
            except StopIteration as e:
                # Generator caught exception and returned
                return CESKState(C=Value(e.value), E=frame.saved_env, S=S, K=K_rest)
            except Exception as propagated:
                # Error continues propagating
                # If this is the SAME exception (not caught and re-raised), preserve original traceback
                # If it's a NEW exception (from generator's except handler), capture new traceback
                if propagated is C.ex:
                    # Same exception, preserve original traceback
                    return CESKState(
                        C=Error(propagated, captured_traceback=C.captured_traceback),
                        E=frame.saved_env,
                        S=S,
                        K=K_rest,
                    )
                # New exception from generator's except handler
                captured = capture_traceback_safe(K_rest, propagated, pre_captured=pre_captured)
                return CESKState(
                    C=Error(propagated, captured_traceback=captured),
                    E=frame.saved_env,
                    S=S,
                    K=K_rest,
                )

        if isinstance(frame, CatchFrame):
            from doeff.cesk_traceback import capture_traceback_safe

            # Invoke handler
            try:
                recovery_result = frame.handler(C.ex)
                # Normalize handler return value to Program
                from doeff.program import Program, ProgramBase
                from doeff.types import EffectBase

                if isinstance(recovery_result, (ProgramBase, EffectBase)):
                    recovery_program = recovery_result
                else:
                    # Handler returned raw value - wrap in pure program
                    recovery_program = Program.pure(recovery_result)
                return CESKState(C=ProgramControl(recovery_program), E=frame.saved_env, S=S, K=K_rest)
            except Exception as handler_ex:
                # Handler itself raised - capture NEW traceback for handler's error
                captured = capture_traceback_safe(K_rest, handler_ex)
                return CESKState(C=Error(handler_ex, captured_traceback=captured), E=frame.saved_env, S=S, K=K_rest)

        # Note: RecoverFrame removed - Result/Maybe are values, not effects

        if isinstance(frame, FinallyFrame):
            # Run cleanup, then re-raise
            cleanup_program = make_cleanup_then_raise(frame.cleanup_program, C.ex)
            return CESKState(C=ProgramControl(cleanup_program), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, LocalFrame):
            # Restore env, continue propagating - preserve traceback
            return CESKState(C=Error(C.ex, captured_traceback=C.captured_traceback), E=frame.restore_env, S=S, K=K_rest)

        if isinstance(frame, InterceptFrame):
            # Intercept doesn't catch errors - propagate with traceback
            return CESKState(C=Error(C.ex, captured_traceback=C.captured_traceback), E=E, S=S, K=K_rest)

        if isinstance(frame, ListenFrame):
            # Propagate error (logs remain in S for debugging) - preserve traceback
            return CESKState(C=Error(C.ex, captured_traceback=C.captured_traceback), E=E, S=S, K=K_rest)

        if isinstance(frame, GatherFrame):
            return CESKState(C=Error(C.ex, captured_traceback=C.captured_traceback), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, SafeFrame):
            from doeff._vendor import NOTHING, Some
            from doeff.cesk_traceback import capture_traceback_safe

            if C.captured_traceback is not None:
                captured_maybe = Some(C.captured_traceback)
            else:
                captured = capture_traceback_safe(K_rest, C.ex)
                captured_maybe = Some(captured) if captured else NOTHING
            err_result = Err(C.ex, captured_traceback=captured_maybe)
            return CESKState(C=Value(err_result), E=frame.saved_env, S=S, K=K_rest)

    # =========================================================================
    # CATCH-ALL (should never reach - indicates bug in rules)
    # =========================================================================

    head_desc = type(K[0]).__name__ if K else "empty"
    raise InterpreterInvariantError(f"Unhandled state: C={type(C).__name__}, K head={head_desc}")


# ============================================================================
# Thread Pool Management (delegated to scheduled_handlers.concurrency)
# ============================================================================


def shutdown_shared_executor(wait: bool = True) -> None:
    """Shutdown the shared executor. Call this on application exit."""
    import doeff.scheduled_handlers.concurrency as concurrency_module
    if concurrency_module._shared_executor is not None:
        with concurrency_module._shared_executor_lock:
            if concurrency_module._shared_executor is not None:
                concurrency_module._shared_executor.shutdown(wait=wait)
                concurrency_module._shared_executor = None


class ScheduledEffectDispatcher:
    def __init__(
        self,
        user_handlers: ScheduledHandlers | None = None,
        builtin_handlers: ScheduledHandlers | None = None,
    ):
        self._user = user_handlers or {}
        self._builtin = builtin_handlers or {}
        self._cache: dict[type[EffectBase], ScheduledEffectHandler | None] = {}

    def _lookup(self, effect_type: type[EffectBase]) -> ScheduledEffectHandler | None:
        if effect_type in self._cache:
            return self._cache[effect_type]

        if effect_type in self._user:
            handler = self._user[effect_type]
            self._cache[effect_type] = handler
            return handler

        if effect_type in self._builtin:
            handler = self._builtin[effect_type]
            self._cache[effect_type] = handler
            return handler

        for base in effect_type.__mro__[1:]:
            if base in self._user:
                handler = self._user[base]
                self._cache[effect_type] = handler
                return handler
            if base in self._builtin:
                handler = self._builtin[base]
                self._cache[effect_type] = handler
                return handler

        self._cache[effect_type] = None
        return None

    def has_handler(self, effect: EffectBase) -> bool:
        return self._lookup(type(effect)) is not None

    def dispatch(
        self,
        effect: EffectBase,
        env: Environment,
        store: Store,
    ) -> HandlerResult:
        handler = self._lookup(type(effect))
        if handler is None:
            raise UnhandledEffectError(f"No handler for {type(effect).__name__}")
        return handler(effect, env, store)


# ============================================================================
# Main Loop
# ============================================================================


async def _run_internal(
    program: Program,
    env: Environment,
    store: Store,
    on_step: OnStepCallback | None = None,
    storage: DurableStorage | None = None,
    dispatcher: ScheduledEffectDispatcher | None = None,
    scheduler: Scheduler | None = None,
) -> tuple[Result[T], Store, CapturedTraceback | None]:
    from doeff.cesk_observability import ExecutionSnapshot
    from doeff.cesk_traceback import capture_traceback_safe
    from doeff.runtime import FIFOScheduler

    if dispatcher is None:
        dispatcher = ScheduledEffectDispatcher(builtin_handlers=default_scheduled_handlers())

    if scheduler is None:
        scheduler = FIFOScheduler()

    store = {**store, "__dispatcher__": dispatcher}

    state = CESKState.initial(program, env, store)
    step_count = 0

    while True:
        result = step(state, dispatcher)
        step_count += 1

        if on_step is not None:
            try:
                if isinstance(result, Done):
                    snapshot = ExecutionSnapshot.from_state(state, "completed", step_count, storage)
                elif isinstance(result, Failed):
                    snapshot = ExecutionSnapshot.from_state(state, "failed", step_count, storage)
                elif isinstance(result, Suspended):
                    snapshot = ExecutionSnapshot.from_state(state, "paused", step_count, storage)
                else:
                    snapshot = ExecutionSnapshot.from_state(result, "running", step_count, storage)
                on_step(snapshot)
            except Exception as e:
                import logging
                logging.warning(f"on_step callback error (ignored): {e}", exc_info=True)

        if isinstance(result, Done):
            return Ok(result.value), result.store, None

        if isinstance(result, Failed):
            return Err(result.exception), result.store, result.captured_traceback

        if isinstance(result, Suspended):
            effect = result.effect
            original_store = state.S

            try:
                handler_result = dispatcher.dispatch(effect, state.E, original_store)

                if isinstance(handler_result, Resume):
                    state = result.resume(handler_result.value, handler_result.store)
                elif isinstance(handler_result, Schedule):
                    payload = handler_result.payload
                    try:
                        from collections.abc import Awaitable
                        if isinstance(payload, Awaitable):
                            async_result = await payload
                            if isinstance(async_result, tuple) and len(async_result) == 2:
                                value, new_store = async_result
                            else:
                                value, new_store = async_result, handler_result.store
                            state = result.resume(value, new_store)
                        else:
                            k = Continuation(
                                _resume=result.resume,
                                _resume_error=result.resume_error,
                                env=state.E,
                                store=original_store,
                            )
                            await scheduler.submit(k, payload)
                            next_k = scheduler.next()
                            if next_k is not None:
                                state = next_k.resume(None, handler_result.store)
                            else:
                                raise InterpreterInvariantError("Schedule but no continuation in scheduler")
                    except Exception as ex:
                        captured = capture_traceback_safe(state.K, ex)
                        error_state = result.resume_error(ex)
                        if isinstance(error_state.C, Error) and error_state.C.captured_traceback is None:
                            error_state = CESKState(
                                C=Error(ex, captured_traceback=captured),
                                E=error_state.E,
                                S=error_state.S,
                                K=error_state.K,
                            )
                        state = error_state
                elif isinstance(handler_result, Suspend):
                    try:
                        async_result = await handler_result.awaitable
                        if isinstance(async_result, tuple) and len(async_result) == 2:
                            value, new_store = async_result
                        else:
                            value, new_store = async_result, handler_result.store
                        state = result.resume(value, new_store)
                    except Exception as ex:
                        captured = capture_traceback_safe(state.K, ex)
                        error_state = result.resume_error(ex)
                        if isinstance(error_state.C, Error) and error_state.C.captured_traceback is None:
                            error_state = CESKState(
                                C=Error(ex, captured_traceback=captured),
                                E=error_state.E,
                                S=error_state.S,
                                K=error_state.K,
                            )
                        state = error_state
                elif isinstance(handler_result, Scheduled):
                    next_k = scheduler.next()
                    if next_k is not None:
                        state = next_k.resume(None, handler_result.store)
                    else:
                        raise InterpreterInvariantError("Scheduled but no continuation in scheduler")
                else:
                    raise InterpreterInvariantError(f"Unknown handler result: {type(handler_result)}")
            except UnhandledEffectError:
                raise
            except Exception as ex:
                captured = capture_traceback_safe(state.K, ex)
                error_state = result.resume_error(ex)
                if isinstance(error_state.C, Error) and error_state.C.captured_traceback is None:
                    error_state = CESKState(
                        C=Error(ex, captured_traceback=captured),
                        E=error_state.E,
                        S=error_state.S,
                        K=error_state.K,
                    )
                state = error_state
            continue

        if isinstance(result, CESKState):
            state = result
            continue

        raise InterpreterInvariantError(f"Unexpected step result: {type(result).__name__}")


async def run(
    program: Program,
    env: Environment | dict[Any, Any] | None = None,
    store: Store | None = None,
    *,
    storage: DurableStorage | None = None,
    on_step: OnStepCallback | None = None,
    scheduled_handlers: ScheduledHandlers | None = None,
    scheduler: Scheduler | None = None,
) -> CESKResult[T]:
    import warnings
    warnings.warn(
        "run() is deprecated. Use EffectRuntime(scheduler).run() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    if env is None:
        E = FrozenDict()
    elif isinstance(env, FrozenDict):
        E = env
    else:
        E = FrozenDict(env)

    S = store if store is not None else {}
    if storage is not None:
        S = {**S, "__durable_storage__": storage}

    dispatcher = ScheduledEffectDispatcher(
        user_handlers=scheduled_handlers,
        builtin_handlers=default_scheduled_handlers(),
    )

    result, _, captured_traceback = await _run_internal(
        program, E, S, on_step=on_step, storage=storage, dispatcher=dispatcher, scheduler=scheduler
    )
    return CESKResult(result, captured_traceback)


def run_sync(
    program: Program,
    env: Environment | dict[Any, Any] | None = None,
    store: Store | None = None,
    *,
    storage: DurableStorage | None = None,
    on_step: OnStepCallback | None = None,
    scheduled_handlers: ScheduledHandlers | None = None,
    scheduler: Scheduler | None = None,
) -> CESKResult[T]:
    import warnings
    warnings.warn(
        "run_sync() is deprecated. Use EffectRuntime(scheduler).run_sync() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return asyncio.run(
        run(
            program,
            env,
            store,
            storage=storage,
            on_step=on_step,
            scheduled_handlers=scheduled_handlers,
            scheduler=scheduler,
        )
    )


__all__ = [
    # State components
    "Environment",
    "Store",
    "Control",
    "Value",
    "Error",
    "EffectControl",
    "ProgramControl",
    # Thread pool management
    "shutdown_shared_executor",
    # Frames
    "Frame",
    "ReturnFrame",
    "CatchFrame",
    "FinallyFrame",
    "LocalFrame",
    "InterceptFrame",
    "ListenFrame",
    "GatherFrame",
    "Kontinuation",
    # State
    "CESKState",
    # Step results
    "StepResult",
    "Done",
    "Failed",
    "Suspended",
    "Terminal",
    # Public result type
    "CESKResult",
    # Classification
    "is_control_flow_effect",
    "is_pure_effect",
    "is_effectful",
    "has_intercept_frame",
    "find_intercept_frame_index",
    # Errors
    "UnhandledEffectError",
    "InterpreterInvariantError",
    "HandlerRegistryError",
    # Effect Dispatcher (new protocol)
    "ScheduledEffectDispatcher",
    "default_scheduled_handlers",
    # Transform
    "apply_transforms",
    "apply_intercept_chain",
    # State merging
    "merge_store",
    # Step function
    "step",
    # Main loop
    "run",
    "run_sync",
]
