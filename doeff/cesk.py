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
import threading
from collections.abc import Awaitable, Callable, Generator
from concurrent.futures import ThreadPoolExecutor
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
from doeff.utils import BoundedLog

T = TypeVar("T")
E = TypeVar("E", bound=EffectBase)
R = TypeVar("R")


# ============================================================================
# Handler Protocols and Registry Types
# ============================================================================


class SyncEffectHandler(Protocol[E, R]):
    """Pure effect handler - executed within a single CESK step.

    Contract:
    - MUST NOT perform I/O or spawn processes
    - MUST NOT run sub-programs or call the interpreter recursively
    - MAY raise exceptions for invalid operations (e.g., missing key)
    - Returns (raw_value, new_store) - step function wraps in Value(raw_value)

    Args:
        effect: The effect instance to handle
        env: Read-only environment (FrozenDict)
        store: Current store (copy-on-write semantics)

    Returns:
        Tuple of (result_value, new_store)
    """

    def __call__(self, effect: E, env: Environment, store: Store) -> tuple[R, Store]: ...


class AsyncEffectHandler(Protocol[E, R]):
    """Effectful handler - causes suspension, resumed later.

    Contract:
    - May perform external I/O
    - MUST NOT run sub-programs in the same machine (use Spawn for independent machines)
    - May raise exceptions (converted to Error by main loop)
    - Returns (raw_value, new_store) - main loop wraps in Value(raw_value)

    Args:
        effect: The effect instance to handle
        env: Read-only environment (FrozenDict)
        store: Current store

    Returns:
        Awaitable of tuple (result_value, new_store)
    """

    def __call__(
        self, effect: E, env: Environment, store: Store
    ) -> Awaitable[tuple[R, Store]]: ...


# Type aliases for handler registries
PureHandlers: TypeAlias = dict[type[EffectBase], SyncEffectHandler[Any, Any]]
EffectfulHandlers: TypeAlias = dict[type[EffectBase], AsyncEffectHandler[Any, Any]]


class HandlerRegistryError(Exception):
    """Raised when there's a conflict or invalid handler registration."""



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
# Individual Pure Effect Handlers
# ============================================================================


def _handle_state_get(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle StateGetEffect."""
    return (store.get(effect.key), store)


def _handle_state_put(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle StatePutEffect."""
    new_store = {**store, effect.key: effect.value}
    return (None, new_store)


def _handle_state_modify(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle StateModifyEffect."""
    old_value = store.get(effect.key)
    new_value = effect.func(old_value)
    new_store = {**store, effect.key: new_value}
    return (new_value, new_store)


def _handle_ask(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle AskEffect."""
    if effect.key not in env:
        raise KeyError(f"Missing environment key: {effect.key!r}")
    return (env[effect.key], store)


def _handle_writer_tell(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle WriterTellEffect."""
    log = store.get("__log__", [])
    new_log = log + [effect.message]
    new_store = {**store, "__log__": new_log}
    return (None, new_store)


def _handle_memo_get(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle MemoGetEffect."""
    memo = store.get("__memo__", {})
    return (memo.get(effect.key), store)


def _handle_memo_put(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle MemoPutEffect."""
    memo = {**store.get("__memo__", {}), effect.key: effect.value}
    new_store = {**store, "__memo__": memo}
    return (None, new_store)


def _handle_pure_effect(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle PureEffect."""
    return (effect.value, store)


def _handle_durable_cache_get(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle DurableCacheGet."""
    storage = store.get("__durable_storage__")
    if storage is None:
        return (None, store)
    return (storage.get(effect.key), store)


def _handle_durable_cache_put(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle DurableCachePut."""
    storage = store.get("__durable_storage__")
    if storage is not None:
        storage.put(effect.key, effect.value)
    return (None, store)


def _handle_durable_cache_delete(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle DurableCacheDelete."""
    storage = store.get("__durable_storage__")
    if storage is None:
        return (False, store)
    return (storage.delete(effect.key), store)


def _handle_durable_cache_exists(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle DurableCacheExists."""
    storage = store.get("__durable_storage__")
    if storage is None:
        return (False, store)
    return (storage.exists(effect.key), store)


def default_pure_handlers() -> PureHandlers:
    """Create the default registry of pure effect handlers.

    Returns:
        A dict mapping effect types to their sync handlers.
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

    return {
        StateGetEffect: _handle_state_get,
        StatePutEffect: _handle_state_put,
        StateModifyEffect: _handle_state_modify,
        AskEffect: _handle_ask,
        WriterTellEffect: _handle_writer_tell,
        MemoGetEffect: _handle_memo_get,
        MemoPutEffect: _handle_memo_put,
        PureEffect: _handle_pure_effect,
        DurableCacheGet: _handle_durable_cache_get,
        DurableCachePut: _handle_durable_cache_put,
        DurableCacheDelete: _handle_durable_cache_delete,
        DurableCacheExists: _handle_durable_cache_exists,
    }


# ============================================================================
# Effect Dispatcher with MRO-based Lookup
# ============================================================================


class EffectDispatcher:
    """Dispatch effects to handlers with MRO-based fallback and caching.

    Dispatch strategy (per specification):
    1. Exact type match: user registry → built-in registry
    2. MRO fallback: traverse T.__mro__[1:], user registry → built-in for each
    3. Cache resolved handler for O(1) subsequent dispatch
    4. Raise UnhandledEffectError if no handler found

    Thread safety: Each dispatcher instance should be used by a single interpreter run.
    The caches are not thread-safe but each run() gets its own dispatcher.
    """

    def __init__(
        self,
        user_pure: PureHandlers | None = None,
        builtin_pure: PureHandlers | None = None,
        user_effectful: EffectfulHandlers | None = None,
        builtin_effectful: EffectfulHandlers | None = None,
    ):
        self._user_pure = user_pure or {}
        self._builtin_pure = builtin_pure or {}
        self._user_effectful = user_effectful or {}
        self._builtin_effectful = builtin_effectful or {}

        # Dispatch caches: effect_type -> (handler, is_pure)
        self._pure_cache: dict[type[EffectBase], SyncEffectHandler[Any, Any]] = {}
        self._effectful_cache: dict[type[EffectBase], AsyncEffectHandler[Any, Any]] = {}

    def _lookup_pure(self, effect_type: type[EffectBase]) -> SyncEffectHandler[Any, Any] | None:
        """Lookup pure handler using MRO fallback."""
        # Check cache first
        if effect_type in self._pure_cache:
            return self._pure_cache[effect_type]

        # Exact match in user registry
        if effect_type in self._user_pure:
            handler = self._user_pure[effect_type]
            self._pure_cache[effect_type] = handler
            return handler

        # Exact match in builtin registry
        if effect_type in self._builtin_pure:
            handler = self._builtin_pure[effect_type]
            self._pure_cache[effect_type] = handler
            return handler

        # MRO fallback (skip the type itself)
        for base in effect_type.__mro__[1:]:
            if base in self._user_pure:
                handler = self._user_pure[base]
                self._pure_cache[effect_type] = handler
                return handler
            if base in self._builtin_pure:
                handler = self._builtin_pure[base]
                self._pure_cache[effect_type] = handler
                return handler

        return None

    def _lookup_effectful(self, effect_type: type[EffectBase]) -> AsyncEffectHandler[Any, Any] | None:
        """Lookup effectful handler using MRO fallback."""
        # Check cache first
        if effect_type in self._effectful_cache:
            return self._effectful_cache[effect_type]

        # Exact match in user registry
        if effect_type in self._user_effectful:
            handler = self._user_effectful[effect_type]
            self._effectful_cache[effect_type] = handler
            return handler

        # Exact match in builtin registry
        if effect_type in self._builtin_effectful:
            handler = self._builtin_effectful[effect_type]
            self._effectful_cache[effect_type] = handler
            return handler

        # MRO fallback (skip the type itself)
        for base in effect_type.__mro__[1:]:
            if base in self._user_effectful:
                handler = self._user_effectful[base]
                self._effectful_cache[effect_type] = handler
                return handler
            if base in self._builtin_effectful:
                handler = self._builtin_effectful[base]
                self._effectful_cache[effect_type] = handler
                return handler

        return None

    def is_pure(self, effect: EffectBase) -> bool:
        """Check if effect has a pure handler."""
        return self._lookup_pure(type(effect)) is not None

    def is_effectful(self, effect: EffectBase) -> bool:
        """Check if effect has an effectful handler."""
        return self._lookup_effectful(type(effect)) is not None

    def dispatch_pure(
        self, effect: EffectBase, env: Environment, store: Store
    ) -> tuple[Any, Store]:
        """Dispatch a pure effect to its handler.

        Raises:
            UnhandledEffectError: If no pure handler found for the effect.
        """
        handler = self._lookup_pure(type(effect))
        if handler is None:
            raise UnhandledEffectError(f"No pure handler for {type(effect).__name__}")
        return handler(effect, env, store)

    async def dispatch_effectful(
        self, effect: EffectBase, env: Environment, store: Store
    ) -> tuple[Any, Store]:
        """Dispatch an effectful effect to its handler.

        Raises:
            UnhandledEffectError: If no effectful handler found for the effect.
        """
        handler = self._lookup_effectful(type(effect))
        if handler is None:
            raise UnhandledEffectError(f"No effectful handler for {type(effect).__name__}")
        return await handler(effect, env, store)


# ============================================================================
# Handler Wrapping Utilities
# ============================================================================


def wrap_sync_handler(
    handler: SyncEffectHandler[E, R],
    wrapper: Callable[[E, Environment, Store, SyncEffectHandler[E, R]], tuple[R, Store]],
) -> SyncEffectHandler[E, R]:
    """Wrap a sync handler with aspect-style behavior.

    The wrapper receives:
    - effect: The effect being handled
    - env: The environment
    - store: The store
    - next_handler: The wrapped handler to call

    Example:
        ```python
        def audit_wrapper(effect, env, store, next_handler):
            print(f"Handling {type(effect).__name__}")
            result, new_store = next_handler(effect, env, store)
            print(f"Completed with {result}")
            return result, new_store

        wrapped = wrap_sync_handler(original_handler, audit_wrapper)
        ```

    Returns:
        A new handler that applies the wrapper around the original.
    """

    def wrapped(effect: E, env: Environment, store: Store) -> tuple[R, Store]:
        return wrapper(effect, env, store, handler)

    return wrapped


def wrap_async_handler(
    handler: AsyncEffectHandler[E, R],
    wrapper: Callable[
        [E, Environment, Store, AsyncEffectHandler[E, R]], Awaitable[tuple[R, Store]]
    ],
) -> AsyncEffectHandler[E, R]:
    """Wrap an async handler with aspect-style behavior.

    The wrapper receives:
    - effect: The effect being handled
    - env: The environment
    - store: The store
    - next_handler: The wrapped handler to call

    Example:
        ```python
        async def timing_wrapper(effect, env, store, next_handler):
            start = time.time()
            result, new_store = await next_handler(effect, env, store)
            print(f"Took {time.time() - start:.3f}s")
            return result, new_store

        wrapped = wrap_async_handler(original_handler, timing_wrapper)
        ```

    Returns:
        A new handler that applies the wrapper around the original.
    """

    async def wrapped(effect: E, env: Environment, store: Store) -> tuple[R, Store]:
        return await wrapper(effect, env, store, handler)

    return wrapped


# ============================================================================
# Registry Merging and Validation
# ============================================================================


def merge_handler_registries(
    user_pure: PureHandlers | None,
    user_effectful: EffectfulHandlers | None,
    builtin_pure: PureHandlers,
    builtin_effectful: EffectfulHandlers,
    override_builtins: bool = False,
) -> tuple[PureHandlers, EffectfulHandlers]:
    """Merge user registries with built-in registries.

    Rules:
    - User handlers take precedence over built-ins for the same type
    - An effect type cannot be registered in both pure and effectful
    - Overriding built-ins requires override_builtins=True

    Args:
        user_pure: User-provided pure handlers
        user_effectful: User-provided effectful handlers
        builtin_pure: Built-in pure handlers
        builtin_effectful: Built-in effectful handlers
        override_builtins: If True, allow user handlers to override built-ins

    Returns:
        Tuple of (merged_pure, merged_effectful) registries

    Raises:
        HandlerRegistryError: If there are conflicts
    """
    user_pure = user_pure or {}
    user_effectful = user_effectful or {}

    # Check for pure/effectful conflicts within user registries
    user_conflicts = set(user_pure.keys()) & set(user_effectful.keys())
    if user_conflicts:
        raise HandlerRegistryError(
            f"Effect types cannot be in both pure and effectful registries: "
            f"{[t.__name__ for t in user_conflicts]}"
        )

    # Check for override conflicts without override_builtins
    if not override_builtins:
        builtin_pure_overrides = set(user_pure.keys()) & set(builtin_pure.keys())
        builtin_effectful_overrides = set(user_effectful.keys()) & set(builtin_effectful.keys())

        if builtin_pure_overrides:
            raise HandlerRegistryError(
                f"Cannot override built-in pure handlers without override_builtins=True: "
                f"{[t.__name__ for t in builtin_pure_overrides]}"
            )
        if builtin_effectful_overrides:
            raise HandlerRegistryError(
                f"Cannot override built-in effectful handlers without override_builtins=True: "
                f"{[t.__name__ for t in builtin_effectful_overrides]}"
            )

    # Check for category conflicts (user trying to make a built-in pure effect effectful or vice versa)
    pure_to_effectful = set(user_effectful.keys()) & set(builtin_pure.keys())
    effectful_to_pure = set(user_pure.keys()) & set(builtin_effectful.keys())

    if pure_to_effectful and not override_builtins:
        raise HandlerRegistryError(
            f"Cannot register built-in pure effects as effectful without override_builtins=True: "
            f"{[t.__name__ for t in pure_to_effectful]}"
        )
    if effectful_to_pure and not override_builtins:
        raise HandlerRegistryError(
            f"Cannot register built-in effectful effects as pure without override_builtins=True: "
            f"{[t.__name__ for t in effectful_to_pure]}"
        )

    # Merge registries (user handlers override built-ins)
    merged_pure = {**builtin_pure, **user_pure}
    merged_effectful = {**builtin_effectful, **user_effectful}

    # Remove any types that changed category
    if override_builtins:
        for t in pure_to_effectful:
            merged_pure.pop(t, None)
        for t in effectful_to_pure:
            merged_effectful.pop(t, None)

    return merged_pure, merged_effectful


def handle_pure(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
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


def step(state: CESKState, dispatcher: EffectDispatcher | None = None) -> StepResult:
    """
    Single step of the CESK machine.

    Args:
        state: Current CESK machine state.
        dispatcher: Effect dispatcher for handler lookup. If None, uses legacy
            isinstance-based dispatch for backward compatibility.

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

                # Use dispatcher if available for transformed effect
                trans_is_pure = dispatcher.is_pure(transformed) if dispatcher else is_pure_effect(transformed)
                trans_is_effectful = dispatcher.is_effectful(transformed) if dispatcher else is_effectful(transformed)

                if trans_is_pure:
                    # Transform returned pure Effect - handle inline
                    try:
                        if dispatcher:
                            v, S_new = dispatcher.dispatch_pure(transformed, E, S)
                        else:
                            v, S_new = handle_pure(transformed, E, S)
                        return CESKState(C=Value(v), E=E, S=S_new, K=K)
                    except Exception as ex:
                        # Pure effect handler raised after transform - capture traceback
                        captured = capture_traceback_safe(K, ex)
                        return CESKState(C=Error(ex, captured_traceback=captured), E=E, S=S, K=K)

                if trans_is_effectful:
                    # Transform returned effectful Effect - async boundary
                    # Note: resume_error traceback capture happens in _run_internal
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
        # PURE EFFECTS → handle and return value
        # =====================================================================

        # Use dispatcher if available, otherwise fall back to legacy checks
        effect_is_pure = dispatcher.is_pure(effect) if dispatcher else is_pure_effect(effect)
        effect_is_effectful = dispatcher.is_effectful(effect) if dispatcher else is_effectful(effect)

        if effect_is_pure:
            from doeff.cesk_traceback import capture_traceback_safe

            try:
                if dispatcher:
                    v, S_new = dispatcher.dispatch_pure(effect, E, S)
                else:
                    v, S_new = handle_pure(effect, E, S)
                return CESKState(C=Value(v), E=E, S=S_new, K=K)
            except Exception as ex:
                # Pure effect handler raised - capture traceback
                captured = capture_traceback_safe(K, ex)
                return CESKState(C=Error(ex, captured_traceback=captured), E=E, S=S, K=K)

        # =====================================================================
        # EFFECTFUL EFFECTS → async boundary
        # =====================================================================

        if effect_is_effectful:
            # Note: resume_error traceback capture happens in _run_internal
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
            # Propagate (partial results discarded), restore env - preserve traceback
            return CESKState(C=Error(C.ex, captured_traceback=C.captured_traceback), E=frame.saved_env, S=S, K=K_rest)

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
# Individual Effectful Effect Handlers
# ============================================================================


async def _handle_io_perform(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle IOPerformEffect."""
    result = effect.action()
    return (result, store)


async def _handle_io_print(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle IOPrintEffect."""
    print(effect.message)
    return (None, store)


async def _handle_future_await(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle FutureAwaitEffect."""
    result = await effect.awaitable
    return (result, store)


async def _handle_spawn(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle SpawnEffect - creates an independent CESK machine."""
    from doeff.effects.spawn import Task

    # Child gets deep copy of store and env; starts with fresh K (no InterceptFrame inheritance)
    child_store = copy.deepcopy(store)
    child_env = env  # Environment is immutable, shared is fine

    # Create a container to hold the child's final store for later merging
    final_store_holder: dict[str, Any] = {"store": None}

    async def run_and_capture_store():
        """Run child and capture final store for later merging at join time."""
        result, final_store, _ = await _run_internal(effect.program, child_env, child_store)
        final_store_holder["store"] = final_store
        return result

    # Create asyncio task for the child machine
    async_task = asyncio.create_task(run_and_capture_store())

    # Return doeff Task handle - compatible with Task.join() effect
    task = Task(
        backend=effect.preferred_backend or "thread",
        _handle=async_task,
        _env_snapshot=dict(env),
        _state_snapshot=final_store_holder,
    )
    return (task, store)


async def _handle_thread(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle ThreadEffect - runs program in a separate thread."""
    child_store = copy.deepcopy(store)
    child_env = env
    strategy = effect.strategy
    loop = asyncio.get_running_loop()

    def run_in_thread() -> tuple[Result, Store]:
        """Run async interpreter in a new event loop in this thread."""
        thread_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(thread_loop)
        try:
            result, final_store, _ = thread_loop.run_until_complete(
                _run_internal(effect.program, child_env, child_store)
            )
            return result, final_store
        finally:
            thread_loop.close()

    def merge_thread_state(parent_store: Store, child_store_result: Store) -> Store:
        """Merge thread state: child state replaces parent (except logs append)."""
        merged = {}
        for key, value in child_store_result.items():
            if not key.startswith("__"):
                merged[key] = value
        for key, value in parent_store.items():
            if not key.startswith("__") and key not in merged:
                merged[key] = value

        parent_log = parent_store.get("__log__", [])
        child_log = child_store_result.get("__log__", [])
        if child_log:
            merged["__log__"] = list(parent_log) + list(child_log)
        elif parent_log:
            merged["__log__"] = list(parent_log)

        parent_memo = parent_store.get("__memo__", {})
        child_memo = child_store_result.get("__memo__", {})
        if parent_memo or child_memo:
            merged["__memo__"] = {**parent_memo, **child_memo}

        return merged

    if strategy == "pooled":
        executor = _get_shared_executor()

        if effect.await_result:
            result, child_final_store = await loop.run_in_executor(executor, run_in_thread)
            if isinstance(result, Ok):
                merged_store = merge_thread_state(store, child_final_store)
                return (result.value, merged_store)
            if isinstance(result, Err):
                raise result.error
            return (result, store)
        raw_future = loop.run_in_executor(executor, run_in_thread)

        async def unwrap_thread_result():
            result, _ = await raw_future
            if isinstance(result, Ok):
                return result.value
            if isinstance(result, Err):
                raise result.error
            return result

        return (unwrap_thread_result(), store)

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
        if isinstance(result, Err):
            raise result.error
        return (result, store)
    async def unwrap_thread_result():
        result, _ = await future
        if isinstance(result, Ok):
            return result.value
        if isinstance(result, Err):
            raise result.error
        return result

    return (unwrap_thread_result(), store)


async def _handle_task_join(effect: EffectBase, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handle TaskJoinEffect - waits for spawned task to complete and merges state."""
    task = effect.task
    if hasattr(task, "_handle") and isinstance(task._handle, asyncio.Task):
        result = await task._handle

        if isinstance(result, Err):
            raise result.error

        final_store_holder = task._state_snapshot
        if isinstance(final_store_holder, dict) and "store" in final_store_holder:
            child_final_store = final_store_holder.get("store")
            already_merged = final_store_holder.get("_merged", False)
            if child_final_store is not None and not already_merged:
                merged_store = merge_store(store, child_final_store)
                final_store_holder["_merged"] = True
            else:
                merged_store = store
        else:
            merged_store = store

        if isinstance(result, Ok):
            return (result.value, merged_store)
        return (result, merged_store)
    raise ValueError(f"Cannot join task with handle type: {type(task._handle)}")


def default_effectful_handlers() -> EffectfulHandlers:
    """Create the default registry of effectful effect handlers.

    Returns:
        A dict mapping effect types to their async handlers.
    """
    from doeff.effects import (
        FutureAwaitEffect,
        IOPerformEffect,
        IOPrintEffect,
        SpawnEffect,
        TaskJoinEffect,
        ThreadEffect,
    )

    return {
        IOPerformEffect: _handle_io_perform,
        IOPrintEffect: _handle_io_print,
        FutureAwaitEffect: _handle_future_await,
        SpawnEffect: _handle_spawn,
        ThreadEffect: _handle_thread,
        TaskJoinEffect: _handle_task_join,
    }


# ============================================================================
# Effectful Effect Handlers (Legacy Dispatch)
# ============================================================================


async def handle_effectful(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> tuple[Any, Store]:
    """
    Effectful handler - may perform I/O, spawn processes, etc.

    This is the legacy handler that uses isinstance checks.
    New code should use EffectDispatcher with default_effectful_handlers().

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
            result, final_store, _ = await _run_internal(effect.program, child_env, child_store)
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
                result, final_store, _ = thread_loop.run_until_complete(
                    _run_internal(effect.program, child_env, child_store)
                )
                return result, final_store
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
                if isinstance(result, Err):
                    raise result.error
                return (result, store)
            # Fire and forget for pooled - return unwrapping awaitable
            raw_future = loop.run_in_executor(executor, run_in_thread)

            async def unwrap_thread_result():
                result, _ = await raw_future
                if isinstance(result, Ok):
                    return result.value
                if isinstance(result, Err):
                    raise result.error
                return result

            return (unwrap_thread_result(), store)

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
            if isinstance(result, Err):
                raise result.error
            return (result, store)
        # Return unwrapping awaitable for thread result
        async def unwrap_thread_result():
            result, _ = await future
            if isinstance(result, Ok):
                return result.value
            if isinstance(result, Err):
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
            return (result, merged_store)
        raise ValueError(f"Cannot join task with handle type: {type(task._handle)}")

    raise UnhandledEffectError(f"No effectful handler for {type(effect).__name__}")


# ============================================================================
# Main Loop
# ============================================================================


async def _run_internal(
    program: Program,
    env: Environment,
    store: Store,
    on_step: OnStepCallback | None = None,
    storage: DurableStorage | None = None,
    dispatcher: EffectDispatcher | None = None,
) -> tuple[Result[T], Store, CapturedTraceback | None]:
    """
    Internal main interpreter loop that returns result, final store, and traceback.

    Used for Spawn where we need the child's final store for merging.

    Args:
        program: The program to execute.
        env: Initial environment.
        store: Initial store.
        on_step: Optional callback invoked after each interpreter step.
        storage: Optional durable storage backend for cache effects.
        dispatcher: Effect dispatcher for handler lookup. If None, uses default handlers.

    Returns:
        Tuple of (Result[T], Store, CapturedTraceback | None)
        - Result is Ok on success, Err on error
        - Store is the final state
        - CapturedTraceback is provided on error, None on success
    """
    from doeff.cesk_observability import ExecutionSnapshot
    from doeff.cesk_traceback import capture_traceback_safe

    # Create default dispatcher if not provided
    if dispatcher is None:
        dispatcher = EffectDispatcher(
            builtin_pure=default_pure_handlers(),
            builtin_effectful=default_effectful_handlers(),
        )

    state = CESKState.initial(program, env, store)
    step_count = 0

    while True:
        result = step(state, dispatcher)
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
            except Exception as e:
                import logging

                logging.debug(f"on_step callback error: {e}")

        if isinstance(result, Done):
            return Ok(result.value), result.store, None

        if isinstance(result, Failed):
            return Err(result.exception), result.store, result.captured_traceback

        if isinstance(result, Suspended):
            # Async boundary - use continuation-based resumption
            effect = result.effect
            original_store = state.S  # Capture for error case

            # Handle effectful effects via dispatcher and resume with continuation
            try:
                v, new_store = await dispatcher.dispatch_effectful(effect, state.E, original_store)
                state = result.resume(v, new_store)
            except Exception as ex:
                # Capture traceback for effectful handler exception
                captured = capture_traceback_safe(state.K, ex)
                # Create error state with captured traceback
                error_state = result.resume_error(ex)
                # If the resumed state has Error control, attach our traceback
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
            # Normal state transition
            state = result
            continue

        # Should never reach here
        raise InterpreterInvariantError(f"Unexpected step result: {type(result).__name__}")


async def run(
    program: Program,
    env: Environment | dict[Any, Any] | None = None,
    store: Store | None = None,
    *,
    storage: DurableStorage | None = None,
    on_step: OnStepCallback | None = None,
    pure_handlers: PureHandlers | None = None,
    effectful_handlers: EffectfulHandlers | None = None,
    override_builtins: bool = False,
) -> CESKResult[T]:
    """
    Main interpreter loop.

    Pure stepping is synchronous. Async boundaries occur for:
    - Effectful handlers (IO, Await, Thread, Spawn) via Suspended

    For parallel execution, use asyncio.create_task + Await + Gather pattern.

    Args:
        program: The program to execute.
        env: Initial environment (default: empty).
        store: Initial store (default: empty).
        storage: Optional durable storage backend for cache effects.
        on_step: Optional callback invoked after each interpreter step.
        pure_handlers: Optional user-provided pure effect handlers.
        effectful_handlers: Optional user-provided effectful effect handlers.
        override_builtins: If True, allow user handlers to override built-in handlers.

    Returns:
        CESKResult containing Ok(value) or Err(exception) with captured traceback.

    Raises:
        HandlerRegistryError: If there are handler registration conflicts.
    """

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

    # Create effect dispatcher with merged registries
    builtin_pure = default_pure_handlers()
    builtin_effectful = default_effectful_handlers()

    # Validate and merge registries
    merged_pure, merged_effectful = merge_handler_registries(
        pure_handlers,
        effectful_handlers,
        builtin_pure,
        builtin_effectful,
        override_builtins,
    )

    dispatcher = EffectDispatcher(
        user_pure=pure_handlers,
        builtin_pure=builtin_pure,
        user_effectful=effectful_handlers,
        builtin_effectful=builtin_effectful,
    )

    result, _, captured_traceback = await _run_internal(
        program, E, S, on_step=on_step, storage=storage, dispatcher=dispatcher
    )
    return CESKResult(result, captured_traceback)


def run_sync(
    program: Program,
    env: Environment | None = None,
    store: Store | None = None,
    *,
    storage: DurableStorage | None = None,
    on_step: OnStepCallback | None = None,
    pure_handlers: PureHandlers | None = None,
    effectful_handlers: EffectfulHandlers | None = None,
    override_builtins: bool = False,
) -> CESKResult[T]:
    """
    Synchronous wrapper for the run function.

    Args:
        program: The program to execute.
        env: Initial environment (default: empty).
        store: Initial store (default: empty).
        storage: Optional durable storage backend for cache effects (default: None).
        on_step: Optional callback invoked after each interpreter step.
        pure_handlers: Optional user-provided pure effect handlers.
        effectful_handlers: Optional user-provided effectful effect handlers.
        override_builtins: If True, allow user handlers to override built-in handlers.

    Returns:
        CESKResult containing Ok(value) or Err(exception) with captured traceback.

    Raises:
        HandlerRegistryError: If there are handler registration conflicts.
    """
    return asyncio.run(
        run(
            program,
            env,
            store,
            storage=storage,
            on_step=on_step,
            pure_handlers=pure_handlers,
            effectful_handlers=effectful_handlers,
            override_builtins=override_builtins,
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
    # Classification (legacy)
    "is_control_flow_effect",
    "is_pure_effect",
    "is_effectful",
    "has_intercept_frame",
    "find_intercept_frame_index",
    # Handlers (legacy)
    "handle_pure",
    "handle_effectful",
    "UnhandledEffectError",
    "InterpreterInvariantError",
    # Handler Protocols and Registry Types
    "SyncEffectHandler",
    "AsyncEffectHandler",
    "PureHandlers",
    "EffectfulHandlers",
    "HandlerRegistryError",
    # Effect Dispatcher
    "EffectDispatcher",
    # Default Handler Registries
    "default_pure_handlers",
    "default_effectful_handlers",
    # Handler Wrapping
    "wrap_sync_handler",
    "wrap_async_handler",
    # Registry Merging
    "merge_handler_registries",
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
