"""
CESK Machine abstraction for the doeff effect interpreter.

This module implements a CESK machine (Control, Environment, Store, Kontinuation)
as described in Felleisen & Friedman (1986) and Van Horn & Might (2010).

The CESK machine provides:
- Clean semantics based on well-established abstract machine model
- Total machine - every state has a defined transition
- Separated concerns - pure handlers vs effectful handlers vs control flow
- Explicit control flow - Catch/Intercept/Local are K frames, not magic
- Fully trampolined - no nested interpreters (except intentional parallelism)
"""

from __future__ import annotations

import asyncio
import copy
from abc import ABC, abstractmethod
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar

from doeff._vendor import Err, FrozenDict, Ok, Result

if TYPE_CHECKING:
    from doeff.program import Program
    from doeff.types import Effect, EffectBase


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

    Note: Generator is mutable - this is the unavoidable impurity in Python.
    The generator is owned exclusively by this frame.
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
class RecoverFrame:
    """Error recovery - always produces a Result type.

    On success: returns Ok(value)
    On error: returns Err(ex) (no handler - use Catch for error handling)
    """

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

    First-intercept-wins: innermost InterceptFrame always handles effects;
    outer intercepts NEVER see them (even if no transform matches).
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
    | RecoverFrame
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
class CEKSState:
    """Full CESK machine state."""

    C: Control
    E: Environment
    S: Store
    K: Kontinuation

    @classmethod
    def initial(
        cls,
        program: "Program",
        env: Environment | None = None,
        store: Store | None = None,
    ) -> "CEKSState":
        """Create initial state for a program."""
        return cls(
            C=ProgramControl(program),
            E=env if env is not None else FrozenDict(),
            S=store if store is not None else {},
            K=[],
        )


# ============================================================================
# Step Result - What step() returns
# ============================================================================


@dataclass(frozen=True)
class Done:
    """Terminal: computation completed successfully."""

    value: Any


@dataclass(frozen=True)
class Failed:
    """Terminal: computation failed with exception."""

    exception: BaseException


@dataclass(frozen=True)
class NeedAsync:
    """Suspend: need to handle effectful effect asynchronously."""

    effect: "EffectBase"
    E: Environment
    S: Store
    K: Kontinuation


@dataclass(frozen=True)
class NeedParallel:
    """Suspend: need to run parallel programs."""

    programs: list["Program"]
    E: Environment
    S: Store
    K: Kontinuation


Terminal: TypeAlias = Done | Failed
Suspend: TypeAlias = NeedAsync | NeedParallel
StepResult: TypeAlias = CEKSState | Terminal | Suspend


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
        ResultRecoverEffect,
        ResultSafeEffect,
        WriterListenEffect,
    )
    from doeff.effects.future import FutureParallelEffect

    return isinstance(
        effect,
        (
            ResultCatchEffect,
            ResultRecoverEffect,
            ResultFinallyEffect,
            LocalEffect,
            InterceptEffect,
            WriterListenEffect,
            GatherEffect,
            FutureParallelEffect,
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
    - MUST NOT perform I/O or spawn processes
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
        log = store.get("__log__", [])
        new_log = log + [effect.message]
        new_store = {**store, "__log__": new_log}
        return (None, new_store)

    if isinstance(effect, MemoGetEffect):
        memo = store.get("__memo__", {})
        if effect.key not in memo:
            raise KeyError("Memo miss for key")
        return (memo[effect.key], store)

    if isinstance(effect, MemoPutEffect):
        memo = {**store.get("__memo__", {}), effect.key: effect.value}
        new_store = {**store, "__memo__": memo}
        return (None, new_store)

    if isinstance(effect, PureEffect):
        return (effect.value, store)

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


# ============================================================================
# Helper Functions for Cleanup
# ============================================================================


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


def step(state: CEKSState) -> StepResult:
    """
    Single step of the CESK machine.

    Returns:
    - CEKSState for continued execution
    - Terminal: Done(value) | Failed(exception) - computation complete
    - Suspend: NeedAsync(effect, E, S, K) | NeedParallel(programs, E, S, K) - pause for async
    """
    C, E, S, K = state.C, state.E, state.S, state.K

    # =========================================================================
    # TERMINAL STATES (check first)
    # =========================================================================

    if isinstance(C, Value) and not K:
        return Done(C.v)

    if isinstance(C, Error) and not K:
        return Failed(C.ex)

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
            ResultRecoverEffect,
            ResultSafeEffect,
            WriterListenEffect,
        )
        from doeff.effects.future import FutureParallelEffect

        if isinstance(effect, ResultCatchEffect):
            return CEKSState(
                C=ProgramControl(effect.sub_program),
                E=E,
                S=S,
                K=[CatchFrame(effect.handler, E)] + K,
            )

        if isinstance(effect, ResultRecoverEffect):
            # Recover wraps result in Ok/Err - purely structural transformation
            return CEKSState(
                C=ProgramControl(effect.sub_program),
                E=E,
                S=S,
                K=[RecoverFrame(saved_env=E)] + K,
            )

        if isinstance(effect, ResultSafeEffect):
            # Safe also uses RecoverFrame as it wraps in Ok/Err
            return CEKSState(
                C=ProgramControl(effect.sub_program),
                E=E,
                S=S,
                K=[RecoverFrame(saved_env=E)] + K,
            )

        if isinstance(effect, ResultFinallyEffect):
            cleanup = effect.finalizer
            # If finalizer is callable, we need to call it to get the program
            if callable(cleanup) and not isinstance(cleanup, type):
                from doeff.program import ProgramBase
                from doeff.types import EffectBase

                if not isinstance(cleanup, (ProgramBase, EffectBase)):
                    try:
                        cleanup = cleanup()
                    except Exception:
                        pass
            return CEKSState(
                C=ProgramControl(effect.sub_program),
                E=E,
                S=S,
                K=[FinallyFrame(cleanup, E)] + K,
            )

        if isinstance(effect, LocalEffect):
            # env_update is a dict to merge: E' = E | env_update
            new_env = E | FrozenDict(effect.env_update)
            return CEKSState(
                C=ProgramControl(effect.sub_program),
                E=new_env,
                S=S,
                K=[LocalFrame(E)] + K,
            )

        if isinstance(effect, InterceptEffect):
            return CEKSState(
                C=ProgramControl(effect.program),
                E=E,
                S=S,
                K=[InterceptFrame(effect.transforms)] + K,
            )

        if isinstance(effect, WriterListenEffect):
            log_start = len(S.get("__log__", []))
            return CEKSState(
                C=ProgramControl(effect.sub_program),
                E=E,
                S=S,
                K=[ListenFrame(log_start)] + K,
            )

        if isinstance(effect, GatherEffect):
            programs = list(effect.programs)
            if not programs:
                return CEKSState(C=Value([]), E=E, S=S, K=K)
            first, *rest = programs
            return CEKSState(
                C=ProgramControl(first),
                E=E,
                S=S,
                K=[GatherFrame(rest, [], E)] + K,
            )

        if isinstance(effect, FutureParallelEffect):
            # For simplicity, parallel execution uses NeedParallel
            # Note: FutureParallelEffect has awaitables, not programs
            # This would need special handling
            return NeedAsync(effect, E, S, K)

        # =====================================================================
        # EFFECT INTERCEPTION (before generic effect handling)
        # =====================================================================

        if not is_control_flow_effect(effect) and has_intercept_frame(K):
            idx = find_intercept_frame_index(K)
            intercept_frame = K[idx]
            assert isinstance(intercept_frame, InterceptFrame)

            try:
                transformed = apply_transforms(intercept_frame.transforms, effect)
            except Exception as ex:
                return CEKSState(C=Error(ex), E=E, S=S, K=K)

            from doeff.program import ProgramBase
            from doeff.types import EffectBase

            if isinstance(transformed, ProgramBase):
                # Transform returned Program - run it INSIDE intercept scope
                return CEKSState(C=ProgramControl(transformed), E=E, S=S, K=K)

            if isinstance(transformed, EffectBase):
                if is_control_flow_effect(transformed):
                    # Transform returned control-flow effect - handle it normally
                    return CEKSState(C=EffectControl(transformed), E=E, S=S, K=K)

                if is_pure_effect(transformed):
                    # Transform returned pure Effect - handle inline
                    try:
                        v, S_new = handle_pure(transformed, E, S)
                        return CEKSState(C=Value(v), E=E, S=S_new, K=K)
                    except Exception as ex:
                        return CEKSState(C=Error(ex), E=E, S=S, K=K)

                if is_effectful(transformed):
                    # Transform returned effectful Effect - async boundary
                    return NeedAsync(transformed, E, S, K)

            # Unknown effect type - error
            return CEKSState(
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
                return CEKSState(C=Value(v), E=E, S=S_new, K=K)
            except Exception as ex:
                return CEKSState(C=Error(ex), E=E, S=S, K=K)

        # =====================================================================
        # EFFECTFUL EFFECTS → async boundary
        # =====================================================================

        if is_effectful(effect):
            return NeedAsync(effect, E, S, K)

        # =====================================================================
        # UNHANDLED EFFECTS → error
        # =====================================================================

        return CEKSState(
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
                # Unexpected yield type
                return CEKSState(
                    C=Error(TypeError(f"Program yielded unexpected type: {type(item).__name__}")),
                    E=E,
                    S=S,
                    K=K,
                )

            return CEKSState(
                C=control,
                E=E,
                S=S,
                K=[ReturnFrame(gen, E)] + K,
            )
        except StopIteration as e:
            # Program immediately returned without yielding
            return CEKSState(C=Value(e.value), E=E, S=S, K=K)
        except Exception as ex:
            # Generator raised on first step
            return CEKSState(C=Error(ex), E=E, S=S, K=K)

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
                    return CEKSState(
                        C=Error(TypeError(f"Program yielded unexpected type: {type(item).__name__}")),
                        E=frame.saved_env,
                        S=S,
                        K=K_rest,
                    )

                return CEKSState(
                    C=control,
                    E=frame.saved_env,
                    S=S,
                    K=[ReturnFrame(frame.generator, frame.saved_env)] + K_rest,
                )
            except StopIteration as e:
                return CEKSState(C=Value(e.value), E=frame.saved_env, S=S, K=K_rest)
            except Exception as ex:
                return CEKSState(C=Error(ex), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, CatchFrame):
            # Value passes through CatchFrame unchanged, restore env
            return CEKSState(C=Value(C.v), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, RecoverFrame):
            # Wrap value in Ok
            return CEKSState(C=Value(Ok(C.v)), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, FinallyFrame):
            # Run cleanup, then return value
            cleanup_program = make_cleanup_then_return(frame.cleanup_program, C.v)
            return CEKSState(C=ProgramControl(cleanup_program), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, LocalFrame):
            # Restore environment
            return CEKSState(C=Value(C.v), E=frame.restore_env, S=S, K=K_rest)

        if isinstance(frame, InterceptFrame):
            # Interception scope ends, pass value through
            return CEKSState(C=Value(C.v), E=E, S=S, K=K_rest)

        if isinstance(frame, ListenFrame):
            # Capture logs and return (value, logs)
            current_log = S.get("__log__", [])
            captured = current_log[frame.log_start_index :]
            from doeff.types import ListenResult

            return CEKSState(C=Value(ListenResult(value=C.v, log=captured)), E=E, S=S, K=K_rest)

        if isinstance(frame, GatherFrame):
            if not frame.remaining_programs:
                # All programs complete - restore saved_env
                final_results = frame.collected_results + [C.v]
                return CEKSState(C=Value(final_results), E=frame.saved_env, S=S, K=K_rest)

            # More programs to run (sequential: S accumulates)
            next_prog, *rest = frame.remaining_programs
            return CEKSState(
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
                # Throw into generator
                item = frame.generator.throw(C.ex)

                from doeff.program import ProgramBase
                from doeff.types import EffectBase

                if isinstance(item, EffectBase):
                    control = EffectControl(item)
                elif isinstance(item, ProgramBase):
                    control = ProgramControl(item)
                else:
                    return CEKSState(
                        C=Error(TypeError(f"Program yielded unexpected type: {type(item).__name__}")),
                        E=frame.saved_env,
                        S=S,
                        K=K_rest,
                    )

                return CEKSState(
                    C=control,
                    E=frame.saved_env,
                    S=S,
                    K=[ReturnFrame(frame.generator, frame.saved_env)] + K_rest,
                )
            except StopIteration as e:
                # Generator caught exception and returned
                return CEKSState(C=Value(e.value), E=frame.saved_env, S=S, K=K_rest)
            except Exception as propagated:
                # Generator didn't catch - continue propagating
                return CEKSState(C=Error(propagated), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, CatchFrame):
            # Invoke handler
            try:
                recovery_program = frame.handler(C.ex)
                return CEKSState(C=ProgramControl(recovery_program), E=frame.saved_env, S=S, K=K_rest)
            except Exception as handler_ex:
                # Handler itself raised - propagate that error
                return CEKSState(C=Error(handler_ex), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, RecoverFrame):
            # Wrap error in Err
            return CEKSState(C=Value(Err(C.ex)), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, FinallyFrame):
            # Run cleanup, then re-raise
            cleanup_program = make_cleanup_then_raise(frame.cleanup_program, C.ex)
            return CEKSState(C=ProgramControl(cleanup_program), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, LocalFrame):
            # Restore env, continue propagating
            return CEKSState(C=Error(C.ex), E=frame.restore_env, S=S, K=K_rest)

        if isinstance(frame, InterceptFrame):
            # Intercept doesn't catch errors - propagate
            return CEKSState(C=Error(C.ex), E=E, S=S, K=K_rest)

        if isinstance(frame, ListenFrame):
            # Propagate error (logs remain in S for debugging)
            return CEKSState(C=Error(C.ex), E=E, S=S, K=K_rest)

        if isinstance(frame, GatherFrame):
            # Propagate (partial results discarded), restore env
            return CEKSState(C=Error(C.ex), E=frame.saved_env, S=S, K=K_rest)

    # =========================================================================
    # CATCH-ALL (should never reach - indicates bug in rules)
    # =========================================================================

    head_desc = type(K[0]).__name__ if K else "empty"
    raise InterpreterInvariantError(f"Unhandled state: C={type(C).__name__}, K head={head_desc}")


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

    if isinstance(effect, IOPerformEffect):
        result = effect.action()
        return (result, store)

    if isinstance(effect, IOPrintEffect):
        print(effect.message)
        return (None, store)

    if isinstance(effect, FutureAwaitEffect):
        result = await effect.awaitable
        return (result, store)

    if isinstance(effect, SpawnEffect):
        # Spawn creates an independent machine - for now, just return a placeholder
        # Full spawn implementation would use the SpawnEffectHandler
        raise NotImplementedError("Spawn effect requires SpawnEffectHandler")

    if isinstance(effect, ThreadEffect):
        # Thread execution requires engine reference
        raise NotImplementedError("Thread effect requires full engine context")

    if isinstance(effect, TaskJoinEffect):
        # Task join requires the spawn handler's task tracking
        raise NotImplementedError("TaskJoin effect requires SpawnEffectHandler")

    raise UnhandledEffectError(f"No effectful handler for {type(effect).__name__}")


# ============================================================================
# Main Loop
# ============================================================================


async def run(
    program: "Program",
    env: Environment | None = None,
    store: Store | None = None,
) -> Result[T]:
    """
    Main interpreter loop.

    Pure stepping is synchronous. Async boundaries occur for:
    - Effectful handlers (IO, Await, Thread, Spawn) via NeedAsync
    - Parallel execution via NeedParallel

    Returns Ok(value) or Err(exception).
    """
    E = env if env is not None else FrozenDict()
    S = store if store is not None else {}
    state = CEKSState.initial(program, E, S)

    while True:
        result = step(state)

        if isinstance(result, Done):
            return Ok(result.value)

        if isinstance(result, Failed):
            return Err(result.exception)

        if isinstance(result, NeedAsync):
            # Async boundary - effectful handler
            try:
                v, S_new = await handle_effectful(result.effect, result.E, result.S)
                state = CEKSState(C=Value(v), E=result.E, S=S_new, K=result.K)
            except Exception as ex:
                state = CEKSState(C=Error(ex), E=result.E, S=result.S, K=result.K)
            continue

        if isinstance(result, NeedParallel):
            # Parallel execution - spawn independent machines
            # Each child gets DEEP COPY of store
            programs = result.programs
            if not programs:
                state = CEKSState(C=Value([]), E=result.E, S=result.S, K=result.K)
                continue

            tasks = [run(p, result.E, copy.deepcopy(result.S)) for p in programs]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Find first error
            first_error = None
            values = []
            for r in results:
                if isinstance(r, Exception):
                    first_error = first_error or r
                elif isinstance(r, Err):
                    first_error = first_error or r.error
                elif isinstance(r, Ok):
                    values.append(r.value)
                else:
                    values.append(r)

            if first_error is not None:
                state = CEKSState(C=Error(first_error), E=result.E, S=result.S, K=result.K)
            else:
                state = CEKSState(C=Value(values), E=result.E, S=result.S, K=result.K)
            continue

        if isinstance(result, CEKSState):
            # Normal state transition
            state = result
            continue

        # Should never reach here
        raise InterpreterInvariantError(f"Unexpected step result: {type(result).__name__}")


def run_sync(
    program: "Program",
    env: Environment | None = None,
    store: Store | None = None,
) -> Result[T]:
    """Synchronous wrapper for the run function."""
    return asyncio.run(run(program, env, store))


__all__ = [
    # State components
    "Environment",
    "Store",
    "Control",
    "Value",
    "Error",
    "EffectControl",
    "ProgramControl",
    # Frames
    "Frame",
    "ReturnFrame",
    "CatchFrame",
    "RecoverFrame",
    "FinallyFrame",
    "LocalFrame",
    "InterceptFrame",
    "ListenFrame",
    "GatherFrame",
    "Kontinuation",
    # State
    "CEKSState",
    # Step results
    "StepResult",
    "Done",
    "Failed",
    "NeedAsync",
    "NeedParallel",
    "Terminal",
    "Suspend",
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
    # Step function
    "step",
    # Main loop
    "run",
    "run_sync",
]
