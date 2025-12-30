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

NOTE: Recover/Retry/Fail/Safe are NOT handled in CESK core.
They can be implemented as library sugar over Catch in the Pure interpreter.

NOTE: For parallel execution, use asyncio.create_task + Await + Gather pattern.

Design Note: Result/Maybe as Values
-----------------------------------
Domain-level Result/Maybe types are treated as VALUES, not effects.
The interpreter's Error/exception handling is kept separate.
Therefore, ResultSafeEffect (which wraps in Ok/Err) is NOT supported
in the CESK core. Users should use Catch for error handling and construct
Result values explicitly if needed at domain boundaries.

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
import copy
from abc import ABC, abstractmethod
from collections.abc import Callable, Generator
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar

from doeff._vendor import Err, FrozenDict, Ok, Result

if TYPE_CHECKING:
    from doeff.cesk_observability import OnStepCallback
    from doeff.program import Program
    from doeff.storage import DurableStorage
    from doeff.types import Effect

from doeff._types_internal import EffectBase, EffectCreationContext, ListenResult
from doeff.utils import BoundedLog


T = TypeVar("T")

# ============================================================================
# CESK State Components
# ============================================================================

# E: Environment - immutable mapping (copy-on-write semantics)
Environment: TypeAlias = FrozenDict[Any, Any]

# S: Store - mutable state (dict with reserved keys __log__, __memo__)
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


@dataclass(frozen=True)
class EffectControl:
    """Control state: need to handle an effect."""

    effect: "EffectBase"


@dataclass(frozen=True)
class ProgramControl:
    """Control state: need to execute a program."""

    program: "Program"


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


@dataclass(frozen=True)
class CatchFrame:
    """Error boundary - catches exceptions and invokes handler.

    On success: passes value through, restores saved_env
    On error: runs handler(ex) with saved_env, result becomes the value
    """

    handler: Callable[[Exception], "Program"]
    saved_env: Environment


@dataclass(frozen=True)
class FinallyFrame:
    """Cleanup on exit - runs cleanup program on both success and error.

    On success: cleanup runs, then original value returned
    On error: cleanup runs, then original error re-raised
    If cleanup FAILS: cleanup error REPLACES original value/error
    """

    cleanup_program: "Program"
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

    transforms: tuple[Callable[["Effect"], "Effect | Program | None"], ...]


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

    remaining_programs: list["Program"]
    collected_results: list[Any]
    saved_env: Environment


Frame: TypeAlias = (
    ReturnFrame
    | CatchFrame
    | FinallyFrame
    | LocalFrame
    | InterceptFrame
    | ListenFrame
    | GatherFrame
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
        program: "Program",
        env: Environment | dict[Any, Any] | None = None,
        store: Store | None = None,
    ) -> "CESKState":
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


@dataclass(frozen=True)
class Suspended:
    """Suspend: need external handling to continue.

    Continuation-based suspension for async operations. The effect is handled
    externally, then the appropriate continuation is called with the result.

    Per spec: continuations take (value, new_store) to incorporate handler's
    store updates. On error, resume_error uses the original store (S) from
    before the effect - effectful handlers should NOT mutate S in-place.
    """

    effect: "EffectBase"
    # Continuation: (value, new_store) -> next state
    resume: Callable[[Any, Store], "CESKState"]
    # Error continuation: exception -> next state (uses original store)
    resume_error: Callable[[BaseException], "CESKState"]


Terminal: TypeAlias = Done | Failed
StepResult: TypeAlias = CESKState | Terminal | Suspended


# ============================================================================
# Effect Classification
# ============================================================================


def is_control_flow_effect(effect: "EffectBase") -> bool:
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
        WriterListenEffect,
    )

    # Note: Recover/Retry/Fail/Safe are NOT included - they are library sugar
    # handled by the Pure interpreter, not CESK core
    return isinstance(
        effect,
        (
            ResultCatchEffect,
            ResultFinallyEffect,
            LocalEffect,
            InterceptEffect,
            WriterListenEffect,
            GatherEffect,
        ),
    )


def is_pure_effect(effect: "EffectBase") -> bool:
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


def is_effectful(effect: "EffectBase") -> bool:
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

    pass


class InterpreterInvariantError(Exception):
    """Raised when the interpreter reaches an invalid state."""

    pass


def handle_pure(effect: "EffectBase", env: Environment, store: Store) -> tuple[Any, Store]:
    """
    Pure effect handler - deterministic, no external side effects.

    Contract:
    - MUST NOT perform I/O or spawn processes (except durable cache via __durable_storage__)
    - MUST NOT run sub-programs or call the interpreter recursively
    - MAY raise exceptions for invalid operations (e.g., missing key)
    - Returns (raw_value, new_store) - step function wraps in Value(raw_value)
    """
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

    if isinstance(effect, StateGetEffect):
        return (store.get(effect.key), store)

    if isinstance(effect, StatePutEffect):
        new_store = {**store, effect.key: effect.value}
        return (None, new_store)

    if isinstance(effect, StateModifyEffect):
        old_value = store.get(effect.key)
        new_value = effect.func(old_value)
        new_store = {**store, effect.key: new_value}
        return (new_value, new_store)

    if isinstance(effect, AskEffect):
        if effect.key not in env:
            raise KeyError(f"Missing environment key: {effect.key!r}")
        return (env[effect.key], store)

    if isinstance(effect, WriterTellEffect):
        # Note: doeff API stores raw messages, not LogEntry objects.
        # Each WriterTellEffect has a single `message` field.
        # For batch logging, use multiple yield calls or slog() for structured entries.
        log = store.get("__log__", [])
        new_log = log + [effect.message]
        new_store = {**store, "__log__": new_log}
        return (None, new_store)

    if isinstance(effect, MemoGetEffect):
        memo = store.get("__memo__", {})
        # Return None on miss (per spec), don't raise
        return (memo.get(effect.key), store)

    if isinstance(effect, MemoPutEffect):
        memo = {**store.get("__memo__", {}), effect.key: effect.value}
        new_store = {**store, "__memo__": memo}
        return (None, new_store)

    if isinstance(effect, PureEffect):
        return (effect.value, store)

    # Durable cache effects (use __durable_storage__ from store)
    if isinstance(effect, DurableCacheGet):
        storage = store.get("__durable_storage__")
        if storage is None:
            # No storage configured, return None (same as cache miss)
            return (None, store)
        return (storage.get(effect.key), store)

    if isinstance(effect, DurableCachePut):
        storage = store.get("__durable_storage__")
        if storage is not None:
            storage.put(effect.key, effect.value)
        return (None, store)

    if isinstance(effect, DurableCacheDelete):
        storage = store.get("__durable_storage__")
        if storage is None:
            return (False, store)
        return (storage.delete(effect.key), store)

    if isinstance(effect, DurableCacheExists):
        storage = store.get("__durable_storage__")
        if storage is None:
            return (False, store)
        return (storage.exists(effect.key), store)

    raise UnhandledEffectError(f"No pure handler for {type(effect).__name__}")


# ============================================================================
# Transform Application
# ============================================================================


def apply_transforms(
    transforms: tuple[Callable[["Effect"], "Effect | Program | None"], ...],
    effect: "Effect",
) -> "Effect | Program":
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


def apply_intercept_chain(K: Kontinuation, effect: "Effect") -> "Effect | Program":
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


# ============================================================================
# Helper Functions for Cleanup
# ============================================================================


def _wrap_callable_as_program(func: Callable[[], Any]) -> "Program":
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


def make_cleanup_then_return(cleanup: "Program", value: Any) -> "Program":
    """Create program that runs cleanup then returns value."""
    from doeff.do import do

    @do
    def cleanup_then_return_impl():
        yield cleanup
        return value

    return cleanup_then_return_impl()


def make_cleanup_then_raise(cleanup: "Program", ex: BaseException) -> "Program":
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


def to_generator(program: "Program") -> Generator[Any, Any, Any]:
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


def step(state: CESKState) -> StepResult:
    """
    Single step of the CESK machine.

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
        return Failed(C.ex, S)

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
            WriterListenEffect,
        )
        # Note: Recover/Retry/Fail/Safe are NOT handled in CESK core
        # They are library sugar handled by the Pure interpreter
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
            # GatherEffect runs programs sequentially per CESK spec
            # Each program sees S modifications from previous (state accumulates)
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

        # =====================================================================
        # EFFECT INTERCEPTION (before generic effect handling)
        # Chain semantics: apply transforms from ALL InterceptFrames (inner→outer)
        # =====================================================================

        if not is_control_flow_effect(effect) and has_intercept_frame(K):
            try:
                transformed = apply_intercept_chain(K, effect)
            except Exception as ex:
                return CESKState(C=Error(ex), E=E, S=S, K=K)

            from doeff.program import ProgramBase
            from doeff.types import EffectBase

            # IMPORTANT: Check EffectBase BEFORE ProgramBase because EffectBase inherits from ProgramBase
            # If we check ProgramBase first, Effects would match and be treated as Programs (wrong!)

            if isinstance(transformed, EffectBase):
                if is_control_flow_effect(transformed):
                    # Transform returned control-flow effect - handle it normally
                    return CESKState(C=EffectControl(transformed), E=E, S=S, K=K)

                if is_pure_effect(transformed):
                    # Transform returned pure Effect - handle inline
                    try:
                        v, S_new = handle_pure(transformed, E, S)
                        return CESKState(C=Value(v), E=E, S=S_new, K=K)
                    except Exception as ex:
                        return CESKState(C=Error(ex), E=E, S=S, K=K)

                if is_effectful(transformed):
                    # Transform returned effectful Effect - async boundary
                    return Suspended(
                        effect=transformed,
                        resume=lambda v, new_store, E=E, K=K: CESKState(
                            C=Value(v), E=E, S=new_store, K=K
                        ),
                        resume_error=lambda ex, E=E, S=S, K=K: CESKState(
                            C=Error(ex), E=E, S=S, K=K
                        ),
                    )

                # Effect not handled by any category - return as unhandled
                return CESKState(
                    C=Error(UnhandledEffectError(f"No handler for {type(transformed).__name__}")),
                    E=E,
                    S=S,
                    K=K,
                )

            if isinstance(transformed, ProgramBase):
                # Transform returned Program (not Effect) - run it INSIDE intercept scope
                return CESKState(C=ProgramControl(transformed), E=E, S=S, K=K)

            # Unknown effect type - error
            return CESKState(
                C=Error(UnhandledEffectError(f"No handler for {type(transformed).__name__}")),
                E=E,
                S=S,
                K=K,
            )

        # =====================================================================
        # PURE EFFECTS → handle and return value
        # =====================================================================

        if is_pure_effect(effect):
            try:
                v, S_new = handle_pure(effect, E, S)
                return CESKState(C=Value(v), E=E, S=S_new, K=K)
            except Exception as ex:
                return CESKState(C=Error(ex), E=E, S=S, K=K)

        # =====================================================================
        # EFFECTFUL EFFECTS → async boundary
        # =====================================================================

        if is_effectful(effect):
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

        return CESKState(
            C=Error(UnhandledEffectError(f"No handler for {type(effect).__name__}")),
            E=E,
            S=S,
            K=K,
        )

    # =========================================================================
    # PROGRAM → push ReturnFrame, step into generator
    # =========================================================================

    if isinstance(C, ProgramControl):
        program = C.program
        try:
            gen = to_generator(program)
            item = next(gen)

            from doeff.program import ProgramBase
            from doeff.types import EffectBase

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
                K=[ReturnFrame(gen, E)] + K,
            )
        except StopIteration as e:
            # Program immediately returned without yielding
            return CESKState(C=Value(e.value), E=E, S=S, K=K)
        except Exception as ex:
            # Generator raised on first step
            return CESKState(C=Error(ex), E=E, S=S, K=K)

    # =========================================================================
    # VALUE + Frames → propagate value through continuation
    # =========================================================================

    if isinstance(C, Value) and K:
        frame = K[0]
        K_rest = K[1:]

        if isinstance(frame, ReturnFrame):
            try:
                item = frame.generator.send(C.v)

                from doeff.program import ProgramBase
                from doeff.types import EffectBase

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
                    K=[ReturnFrame(frame.generator, frame.saved_env)] + K_rest,
                )
            except StopIteration as e:
                return CESKState(C=Value(e.value), E=frame.saved_env, S=S, K=K_rest)
            except Exception as ex:
                return CESKState(C=Error(ex), E=frame.saved_env, S=S, K=K_rest)

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
                # All programs complete - restore saved_env
                final_results = frame.collected_results + [C.v]
                return CESKState(C=Value(final_results), E=frame.saved_env, S=S, K=K_rest)

            # More programs to run (sequential: S accumulates)
            next_prog, *rest = frame.remaining_programs
            return CESKState(
                C=ProgramControl(next_prog),
                E=frame.saved_env,
                S=S,
                K=[GatherFrame(rest, frame.collected_results + [C.v], frame.saved_env)] + K_rest,
            )

    # =========================================================================
    # ERROR + Frames → propagate error through continuation
    # =========================================================================

    if isinstance(C, Error) and K:
        frame = K[0]
        K_rest = K[1:]

        if isinstance(frame, ReturnFrame):
            try:
                # Throw into generator - single-arg form preserves traceback
                # when passing exception instance (modern Python approach)
                item = frame.generator.throw(C.ex)

                from doeff.program import ProgramBase
                from doeff.types import EffectBase

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
                    K=[ReturnFrame(frame.generator, frame.saved_env)] + K_rest,
                )
            except StopIteration as e:
                # Generator caught exception and returned
                return CESKState(C=Value(e.value), E=frame.saved_env, S=S, K=K_rest)
            except Exception as propagated:
                # Generator didn't catch - continue propagating
                return CESKState(C=Error(propagated), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, CatchFrame):
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
                # Handler itself raised - propagate that error
                return CESKState(C=Error(handler_ex), E=frame.saved_env, S=S, K=K_rest)

        # Note: RecoverFrame removed - Result/Maybe are values, not effects

        if isinstance(frame, FinallyFrame):
            # Run cleanup, then re-raise
            cleanup_program = make_cleanup_then_raise(frame.cleanup_program, C.ex)
            return CESKState(C=ProgramControl(cleanup_program), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, LocalFrame):
            # Restore env, continue propagating
            return CESKState(C=Error(C.ex), E=frame.restore_env, S=S, K=K_rest)

        if isinstance(frame, InterceptFrame):
            # Intercept doesn't catch errors - propagate
            return CESKState(C=Error(C.ex), E=E, S=S, K=K_rest)

        if isinstance(frame, ListenFrame):
            # Propagate error (logs remain in S for debugging)
            return CESKState(C=Error(C.ex), E=E, S=S, K=K_rest)

        if isinstance(frame, GatherFrame):
            # Propagate (partial results discarded), restore env
            return CESKState(C=Error(C.ex), E=frame.saved_env, S=S, K=K_rest)

    # =========================================================================
    # CATCH-ALL (should never reach - indicates bug in rules)
    # =========================================================================

    head_desc = type(K[0]).__name__ if K else "empty"
    raise InterpreterInvariantError(f"Unhandled state: C={type(C).__name__}, K head={head_desc}")


# ============================================================================
# Thread Pool Management (for ThreadEffect strategy support)
# ============================================================================

_shared_executor: ThreadPoolExecutor | None = None
_shared_executor_lock = threading.Lock()


def _get_shared_executor() -> ThreadPoolExecutor:
    """Get or create the shared thread pool executor for 'pooled' strategy."""
    global _shared_executor
    if _shared_executor is None:
        with _shared_executor_lock:
            if _shared_executor is None:
                _shared_executor = ThreadPoolExecutor(
                    max_workers=4,  # Default pool size
                    thread_name_prefix="cesk-pooled",
                )
    return _shared_executor


def shutdown_shared_executor(wait: bool = True) -> None:
    """Shutdown the shared executor. Call this on application exit."""
    global _shared_executor
    if _shared_executor is not None:
        with _shared_executor_lock:
            if _shared_executor is not None:
                _shared_executor.shutdown(wait=wait)
                _shared_executor = None


# ============================================================================
# Effectful Effect Handlers
# ============================================================================


async def handle_effectful(
    effect: "EffectBase",
    env: Environment,
    store: Store,
) -> tuple[Any, Store]:
    """
    Effectful handler - may perform I/O, spawn processes, etc.

    Contract:
    - May perform external I/O
    - MUST NOT run sub-programs in the same machine (use Spawn for independent machines)
    - May raise exceptions (converted to Error by main loop)
    - Returns (raw_value, new_store) - main loop wraps in Value(raw_value)
    """
    from doeff.effects import (
        FutureAwaitEffect,
        IOPerformEffect,
        IOPrintEffect,
        SpawnEffect,
        TaskJoinEffect,
        ThreadEffect,
    )
    from doeff.effects.spawn import Task

    if isinstance(effect, IOPerformEffect):
        result = effect.action()
        return (result, store)

    if isinstance(effect, IOPrintEffect):
        print(effect.message)
        return (None, store)

    if isinstance(effect, FutureAwaitEffect):
        result = await effect.awaitable
        return (result, store)

    # NOTE: For parallel execution, use asyncio.create_task + Await + Gather pattern

    if isinstance(effect, SpawnEffect):
        # Spawn creates an independent CESK machine
        # Child gets deep copy of store and env; starts with fresh K (no InterceptFrame inheritance)
        child_store = copy.deepcopy(store)
        child_env = env  # Environment is immutable, shared is fine

        # Create a container to hold the child's final store for later merging
        final_store_holder: dict[str, Any] = {"store": None}

        async def run_and_capture_store():
            """Run child and capture final store for later merging at join time."""
            result, final_store = await _run_internal(effect.program, child_env, child_store)
            final_store_holder["store"] = final_store
            return result

        # Create asyncio task for the child machine
        async_task = asyncio.create_task(run_and_capture_store())

        # Return doeff Task handle - compatible with Task.join() effect
        # Note: preferred_backend/options are recorded but CESK always uses asyncio internally
        # _state_snapshot holds reference to final_store_holder for merge at join time
        task = Task(
            backend=effect.preferred_backend or "thread",
            _handle=async_task,
            _env_snapshot=dict(env),
            _state_snapshot=final_store_holder,  # Reference to holder for final store
        )
        return (task, store)

    if isinstance(effect, ThreadEffect):
        # Thread runs program in a separate machine in an actual thread
        # Child gets deep copy of store; starts with fresh K
        child_store = copy.deepcopy(store)
        child_env = env
        strategy = effect.strategy
        loop = asyncio.get_running_loop()

        def run_in_thread() -> tuple[Result, Store]:
            """Run async interpreter in a new event loop in this thread.

            Returns (result, final_store) for state merging.
            """
            thread_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(thread_loop)
            try:
                return thread_loop.run_until_complete(
                    _run_internal(effect.program, child_env, child_store)
                )
            finally:
                thread_loop.close()

        def merge_thread_state(parent_store: Store, child_store: Store) -> Store:
            """Merge thread state: child state replaces parent (except logs append).

            Unlike Spawn (where child adds new keys only), Thread synchronously
            blocks and its state should fully replace parent state.
            """
            merged = {}
            # User keys: child wins completely
            for key, value in child_store.items():
                if not key.startswith("__"):
                    merged[key] = value
            # Also include parent keys not in child
            for key, value in parent_store.items():
                if not key.startswith("__") and key not in merged:
                    merged[key] = value

            # Append logs (same as spawn)
            parent_log = parent_store.get("__log__", [])
            child_log = child_store.get("__log__", [])
            if child_log:
                merged["__log__"] = list(parent_log) + list(child_log)
            elif parent_log:
                merged["__log__"] = list(parent_log)

            # Merge memo
            parent_memo = parent_store.get("__memo__", {})
            child_memo = child_store.get("__memo__", {})
            if parent_memo or child_memo:
                merged["__memo__"] = {**parent_memo, **child_memo}

            return merged

        if strategy == "pooled":
            # Use shared pool (module-level singleton) with ThreadPoolExecutor
            executor = _get_shared_executor()

            if effect.await_result:
                result, child_final_store = await loop.run_in_executor(
                    executor, run_in_thread
                )
                if isinstance(result, Ok):
                    merged_store = merge_thread_state(store, child_final_store)
                    return (result.value, merged_store)
                elif isinstance(result, Err):
                    raise result.error
                else:
                    return (result, store)
            else:
                # Fire and forget for pooled - return unwrapping awaitable
                raw_future = loop.run_in_executor(executor, run_in_thread)

                async def unwrap_thread_result():
                    result, _ = await raw_future
                    if isinstance(result, Ok):
                        return result.value
                    elif isinstance(result, Err):
                        raise result.error
                    return result

                return (unwrap_thread_result(), store)

        else:
            # For dedicated/daemon: use threading.Thread directly to control daemon flag
            is_daemon = strategy == "daemon"
            future: asyncio.Future[tuple[Result, Store]] = loop.create_future()

            def thread_target() -> None:
                try:
                    result = run_in_thread()
                except BaseException as exc:
                    loop.call_soon_threadsafe(future.set_exception, exc)
                else:
                    loop.call_soon_threadsafe(future.set_result, result)

            thread = threading.Thread(
                target=thread_target,
                name=f"cesk-{'daemon' if is_daemon else 'dedicated'}",
                daemon=is_daemon,
            )
            thread.start()

            if effect.await_result:
                result, child_final_store = await future
                if isinstance(result, Ok):
                    merged_store = merge_thread_state(store, child_final_store)
                    return (result.value, merged_store)
                elif isinstance(result, Err):
                    raise result.error
                else:
                    return (result, store)
            else:
                # Return unwrapping awaitable for thread result
                async def unwrap_thread_result():
                    result, _ = await future
                    if isinstance(result, Ok):
                        return result.value
                    elif isinstance(result, Err):
                        raise result.error
                    return result

                return (unwrap_thread_result(), store)

    if isinstance(effect, TaskJoinEffect):
        # Wait for spawned task to complete and merge state
        task = effect.task
        if hasattr(task, "_handle") and isinstance(task._handle, asyncio.Task):
            result = await task._handle

            if isinstance(result, Err):
                # On error: NO state merge (error propagates, parent store unchanged)
                raise result.error

            # On success: merge child's final store into parent (ONCE)
            # The _state_snapshot holds reference to final_store_holder
            final_store_holder = task._state_snapshot
            if isinstance(final_store_holder, dict) and "store" in final_store_holder:
                child_final_store = final_store_holder.get("store")
                # Check if we've already merged (avoid double merge on multiple joins)
                already_merged = final_store_holder.get("_merged", False)
                if child_final_store is not None and not already_merged:
                    merged_store = merge_store(store, child_final_store)
                    # Mark as merged to prevent duplicate merge on second join
                    final_store_holder["_merged"] = True
                else:
                    merged_store = store
            else:
                merged_store = store

            if isinstance(result, Ok):
                return (result.value, merged_store)
            else:
                return (result, merged_store)
        else:
            raise ValueError(f"Cannot join task with handle type: {type(task._handle)}")

    raise UnhandledEffectError(f"No effectful handler for {type(effect).__name__}")


# ============================================================================
# Main Loop
# ============================================================================


async def _run_internal(
    program: "Program",
    env: Environment,
    store: Store,
    on_step: "OnStepCallback | None" = None,
    storage: "DurableStorage | None" = None,
) -> tuple[Result[T], Store]:
    """
    Internal main interpreter loop that returns both result and final store.

    Used for Spawn where we need the child's final store for merging.

    Args:
        program: The program to execute.
        env: Initial environment.
        store: Initial store.
        on_step: Optional callback invoked after each interpreter step.
        storage: Optional durable storage backend for cache effects.
    """
    from doeff.cesk_observability import ExecutionSnapshot, OnStepCallback

    state = CESKState.initial(program, env, store)
    step_count = 0

    while True:
        result = step(state)
        step_count += 1

        # Call on_step callback if provided
        if on_step is not None:
            try:
                if isinstance(result, Done):
                    snapshot = ExecutionSnapshot.from_state(
                        state, "completed", step_count, storage
                    )
                elif isinstance(result, Failed):
                    snapshot = ExecutionSnapshot.from_state(
                        state, "failed", step_count, storage
                    )
                elif isinstance(result, Suspended):
                    snapshot = ExecutionSnapshot.from_state(
                        state, "paused", step_count, storage
                    )
                else:
                    snapshot = ExecutionSnapshot.from_state(
                        result, "running", step_count, storage
                    )
                on_step(snapshot)
            except Exception:
                pass  # Don't let callback errors break execution

        if isinstance(result, Done):
            return Ok(result.value), result.store

        if isinstance(result, Failed):
            return Err(result.exception), result.store

        if isinstance(result, Suspended):
            # Async boundary - use continuation-based resumption
            effect = result.effect
            original_store = state.S  # Capture for error case

            # Handle effectful effects and resume with continuation
            try:
                v, new_store = await handle_effectful(effect, state.E, original_store)
                state = result.resume(v, new_store)
            except Exception as ex:
                state = result.resume_error(ex)
            continue

        if isinstance(result, CESKState):
            # Normal state transition
            state = result
            continue

        # Should never reach here
        raise InterpreterInvariantError(f"Unexpected step result: {type(result).__name__}")


async def run(
    program: "Program",
    env: Environment | dict[Any, Any] | None = None,
    store: Store | None = None,
    *,
    storage: "DurableStorage | None | object" = ...,
    on_step: "OnStepCallback | None" = None,
) -> Result[T]:
    """
    Main interpreter loop.

    Pure stepping is synchronous. Async boundaries occur for:
    - Effectful handlers (IO, Await, Thread, Spawn) via Suspended

    For parallel execution, use asyncio.create_task + Await + Gather pattern.

    Args:
        program: The program to execute.
        env: Initial environment (default: empty).
        store: Initial store (default: empty).
        storage: Durable storage backend for cache effects (default: SQLiteStorage(".doeff-cache.db")).
                 Pass None to disable storage.
        on_step: Optional callback invoked after each interpreter step.

    Returns:
        Ok(value) or Err(exception).
    """
    from doeff.cesk_observability import OnStepCallback
    from doeff.storage import DurableStorage, SQLiteStorage

    # Use default SQLiteStorage if not provided
    if storage is ...:
        storage = SQLiteStorage(".doeff-cache.db")

    # Coerce env to FrozenDict to ensure immutability
    if env is None:
        E = FrozenDict()
    elif isinstance(env, FrozenDict):
        E = env
    else:
        E = FrozenDict(env)

    # Initialize store with durable storage if provided
    S = store if store is not None else {}
    if storage is not None:
        S = {**S, "__durable_storage__": storage}

    result, _ = await _run_internal(program, E, S, on_step=on_step, storage=storage)
    return result


def run_sync(
    program: "Program",
    env: Environment | None = None,
    store: Store | None = None,
    *,
    storage: "DurableStorage | None | object" = ...,
    on_step: "OnStepCallback | None" = None,
) -> Result[T]:
    """
    Synchronous wrapper for the run function.

    Args:
        program: The program to execute.
        env: Initial environment (default: empty).
        store: Initial store (default: empty).
        storage: Durable storage backend for cache effects (default: SQLiteStorage(".doeff-cache.db")).
                 Pass None to disable storage.
        on_step: Optional callback invoked after each interpreter step.

    Returns:
        Ok(value) or Err(exception).
    """
    return asyncio.run(run(program, env, store, storage=storage, on_step=on_step))


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
    # Classification
    "is_control_flow_effect",
    "is_pure_effect",
    "is_effectful",
    "has_intercept_frame",
    "find_intercept_frame_index",
    # Handlers
    "handle_pure",
    "handle_effectful",
    "UnhandledEffectError",
    "InterpreterInvariantError",
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
