"""
Effect handlers for the doeff system.

This module contains handler classes for each effect category.
Each handler is responsible for interpreting specific effects.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import logging
import lzma
import sqlite3
import threading
from dataclasses import replace
from collections.abc import Awaitable, Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import cloudpickle

from doeff._vendor import Err, Ok, WGraph, WNode, WStep
from doeff.cache import CACHE_PATH_ENV_KEY, persistent_cache_path
from doeff.effects import (
    AskEffect,
    AtomicGetEffect,
    AtomicUpdateEffect,
    CacheGetEffect,
    CachePutEffect,
    FutureAwaitEffect,
    FutureParallelEffect,
    GraphAnnotateEffect,
    GraphCaptureEffect,
    GraphSnapshotEffect,
    GraphStepEffect,
    IOPerformEffect,
    IOPrintEffect,
    LocalEffect,
    MemoGetEffect,
    MemoPutEffect,
    ResultCatchEffect,
    ResultFailEffect,
    ResultFinallyEffect,
    ResultRecoverEffect,
    ResultRetryEffect,
    ResultSafeEffect,
    SpawnEffect,
    ThreadEffect,
    Task,
    TaskJoinEffect,
    StateGetEffect,
    StateModifyEffect,
    StatePutEffect,
    WriterListenEffect,
    WriterTellEffect,
)
from doeff.effects.pure import PureEffect
from doeff.effects.result import ResultFirstSuccessEffect, ResultUnwrapEffect
from doeff.program import Program
from doeff.types import EffectBase, EffectFailure, ExecutionContext, ListenResult
from doeff.utils import BoundedLog

logger = logging.getLogger(__name__)

_MISSING = object()


def _safe_object_repr(value: Any) -> str:
    try:
        return repr(value)
    except Exception as repr_error:  # pragma: no cover - defensive guard
        return f"<repr failed: {repr_error!r}>"


def _cloudpickle_dumps(value: Any, context: str) -> bytes:
    try:
        return cloudpickle.dumps(value)
    except Exception as exc:
        value_repr = _safe_object_repr(value)
        raise TypeError(
            f"Failed to cloudpickle {context}; object type={type(value).__name__}; repr={value_repr}"
        ) from exc


def _cloudpickle_loads(payload: bytes, context: str) -> Any:
    try:
        return cloudpickle.loads(payload)
    except Exception as exc:
        raise TypeError(f"Failed to load {context} via cloudpickle: {exc}") from exc


def _sanitize_created_at(created_at: Any) -> Any:
    from doeff.types import EffectCreationContext

    if isinstance(created_at, EffectCreationContext):
        return created_at.without_frames()
    return created_at


def _sanitize_program(program: Any) -> Any:
    from doeff.types import EffectBase

    def _get_attr(obj: Any, name: str) -> Any:
        try:
            return object.__getattribute__(obj, name)
        except AttributeError:
            return _MISSING

    if isinstance(program, EffectBase):
        created_at = _sanitize_created_at(program.created_at)
        if created_at is not program.created_at:
            return program.with_created_at(created_at)
        return program

    base_program = _get_attr(program, "base_program")
    transforms = _get_attr(program, "transforms")
    if base_program is not _MISSING and transforms is not _MISSING:
        sanitized_base = _sanitize_program(base_program)
        if sanitized_base is not base_program:
            try:
                return replace(program, base_program=sanitized_base)
            except Exception:
                pass

    created_at = _get_attr(program, "created_at")
    if created_at is not _MISSING:
        sanitized = _sanitize_created_at(created_at)
        if sanitized is not created_at:
            try:
                return replace(program, created_at=sanitized)
            except Exception:
                pass
    return program


def _sanitize_call_stack(call_stack: list[CallFrame]) -> list[CallFrame]:
    sanitized: list[CallFrame] = []
    for frame in call_stack:  # noqa: DOEFF012
        created_at = _sanitize_created_at(frame.created_at)
        frame = replace(
            frame,
            kleisli=None,
            args=(),
            kwargs={},
            created_at=created_at,
        )
        sanitized.append(frame)
    return sanitized


def _pack_execution_context(ctx: ExecutionContext) -> dict[str, Any]:
    return {
        "env": ctx.env,
        "state": ctx.state,
        "log": list(ctx.log),
        "graph": ctx.graph,
        "io_allowed": ctx.io_allowed,
        "cache": ctx.cache,
    }


def _sanitize_exception(error: BaseException) -> BaseException:
    from doeff.types import EffectFailure

    if isinstance(error, EffectFailure):
        error.creation_context = _sanitize_created_at(error.creation_context)
        error.effect = _sanitize_program(error.effect)
    return error


def _serialize_call_stack_for_spawn(
    call_stack: tuple[Any, ...],
) -> list[dict[str, Any]]:  # noqa: DOEFF006
    """Serialize call stack frames for cross-process transfer."""
    result = []
    for frame in call_stack:
        created_at = getattr(frame, "created_at", None)
        serialized_created_at = None
        if created_at is not None:
            serialized_created_at = {
                "filename": getattr(created_at, "filename", ""),
                "line": getattr(created_at, "line", 0),
                "function": getattr(created_at, "function", ""),
                "code": getattr(created_at, "code", ""),
                "stack_trace": getattr(created_at, "stack_trace", []),
            }
        result.append({
            "function_name": getattr(frame, "function_name", ""),
            "depth": getattr(frame, "depth", 0),
            "created_at": serialized_created_at,
        })
    return result


def _pack_spawn_error(error: BaseException) -> tuple[str, dict[str, Any]]:  # noqa: DOEFF006
    """Pack an error for cross-process transfer, preserving EffectFailure info."""
    from doeff.types import EffectFailure

    if isinstance(error, EffectFailure):
        # Preserve the full EffectFailure info including call stack
        creation_ctx = error.creation_context
        serialized_creation_ctx = None
        if creation_ctx is not None:
            serialized_creation_ctx = {
                "filename": getattr(creation_ctx, "filename", ""),
                "line": getattr(creation_ctx, "line", 0),
                "function": getattr(creation_ctx, "function", ""),
                "code": getattr(creation_ctx, "code", ""),
                "stack_trace": getattr(creation_ctx, "stack_trace", []),
            }

        return (
            _SPAWN_ERR_TAG,
            {
                "is_effect_failure": True,
                "type": error.cause.__class__.__name__ if error.cause else "EffectFailure",
                "message": str(error.cause) if error.cause else str(error),
                "effect_type": error.effect.__class__.__name__,
                "call_stack_snapshot": _serialize_call_stack_for_spawn(
                    error.call_stack_snapshot
                ),
                "creation_context": serialized_creation_ctx,
            },
        )

    return (
        _SPAWN_ERR_TAG,
        {
            "type": error.__class__.__name__,
            "message": str(error),
        },
    )


def _reconstruct_spawn_effect_failure(error_dict: dict[str, Any]) -> BaseException:
    """Reconstruct an EffectFailure from serialized spawn error data."""
    from doeff._types_internal import (
        CallFrame,
        EffectCreationContext,
        EffectFailureError,
        NullEffect,
    )

    # Reconstruct the cause exception
    error_type = error_dict.get("type", "Error")
    message = error_dict.get("message", "")

    # Create a SpawnTaskError that preserves the original error info
    cause = SpawnTaskError(f"{error_type}: {message}")

    # Reconstruct creation context if available
    creation_context = None
    ctx_data = error_dict.get("creation_context")
    if ctx_data:
        creation_context = EffectCreationContext(
            filename=ctx_data.get("filename", ""),
            line=ctx_data.get("line", 0),
            function=ctx_data.get("function", ""),
            code=ctx_data.get("code", ""),
            stack_trace=ctx_data.get("stack_trace", []),
            frame_info=None,
        )

    # Reconstruct call stack snapshot
    call_stack: list[CallFrame] = []
    for frame_data in error_dict.get("call_stack_snapshot", []):
        created_at = None
        created_at_data = frame_data.get("created_at")
        if created_at_data:
            created_at = EffectCreationContext(
                filename=created_at_data.get("filename", ""),
                line=created_at_data.get("line", 0),
                function=created_at_data.get("function", ""),
                code=created_at_data.get("code", ""),
                stack_trace=created_at_data.get("stack_trace", []),
                frame_info=None,
            )
        call_stack.append(
            CallFrame(
                kleisli=None,  # Not needed for display
                function_name=frame_data.get("function_name", ""),
                args=(),
                kwargs={},
                depth=frame_data.get("depth", 0),
                created_at=created_at,
            )
        )

    # Determine effect type from the error dict
    effect_type = error_dict.get("effect_type", "NullEffect")
    effect = NullEffect()  # Use placeholder effect

    return EffectFailureError(
        effect=effect,
        cause=cause,
        runtime_traceback=None,
        creation_context=creation_context,
        call_stack_snapshot=tuple(call_stack),
    )


class SpawnTaskError(Exception):
    """Exception raised when a spawned task fails, preserving original error info."""

    pass


def _run_spawn_payload(payload: bytes, max_log_entries: int | None) -> bytes:
    """Execute a spawn payload and return cloudpickled result.

    Returns cloudpickled bytes to ensure the result can be transferred back
    through ProcessPoolExecutor's standard pickle queue. This handles cases
    where state/env contains objects that only cloudpickle can serialize
    (e.g., Programs with lambda closures from flat_map).
    """
    try:
        program, ctx = _cloudpickle_loads(payload, "spawn payload")
    except Exception as exc:  # pragma: no cover - defensive fallback
        return _cloudpickle_dumps(_pack_spawn_error(exc), "spawn error result")

    from doeff.interpreter import ProgramInterpreter

    engine = ProgramInterpreter(max_log_entries=max_log_entries)
    try:
        result = engine.run(program, ctx)
    except Exception as exc:  # pragma: no cover - defensive fallback
        return _cloudpickle_dumps(_pack_spawn_error(exc), "spawn error result")

    if isinstance(result.result, Err):
        error = _sanitize_exception(result.result.error)
        return _cloudpickle_dumps(_pack_spawn_error(error), "spawn error result")

    packed_ctx = _pack_execution_context(result.context)
    result_tuple = (_SPAWN_OK_TAG, result.value, packed_ctx)
    return _cloudpickle_dumps(result_tuple, "spawn result")


_VALID_SPAWN_BACKENDS = ("thread", "process", "ray")
_SPAWN_OK_TAG = "__doeff_spawn_ok__"
_SPAWN_ERR_TAG = "__doeff_spawn_err__"

# Need Program at runtime for isinstance checks

if TYPE_CHECKING:
    from doeff.interpreter import ProgramInterpreter


class HandlerScope(Enum):
    """Defines how handler state should be managed in parallel execution contexts."""
    ISOLATED = auto()  # Each parallel execution gets its own handler instance (e.g., State)
    SHARED = auto()    # All parallel executions share the same handler instance (e.g., Cache, Log)


class GraphCaptureResult(NamedTuple):
    """Result from capturing a computation graph."""
    value: Any
    graph: WGraph


class ReaderEffectHandler:
    """Handles Reader monad effects."""
    scope = HandlerScope.ISOLATED  # Each parallel execution gets its own environment

    _RESOLUTION_IN_PROGRESS = object()

    async def handle_ask(
        self,
        effect: AskEffect,
        ctx: ExecutionContext,
        engine: ProgramInterpreter,
    ) -> Any:
        """Handle reader.ask effect."""
        from doeff.program import ProgramBase
        from doeff.types import Program as ProgramType

        key = effect.key
        if key == "__interpreter__":
            # Allow programs to access the active interpreter instance for bridging
            # into non-doeff callbacks (e.g., external frameworks).
            return _InterpreterHandle(engine, ctx)

        if key in ctx.env:
            value = ctx.env[key]
            if value is self._RESOLUTION_IN_PROGRESS:
                raise RuntimeError(f"Cyclic Ask dependency for environment key: {key!r}")

            if isinstance(value, (ProgramType, ProgramBase)):
                ctx.env[key] = self._RESOLUTION_IN_PROGRESS
                try:
                    result = await engine.run_async(value, ctx)
                    resolved_value = result.value
                except Exception:
                    ctx.env[key] = value
                    raise

                ctx.env[key] = resolved_value
                return resolved_value

            return value

        resolver = ctx.env.get("__resolver__")
        if resolver is not None:
            return await resolver.provide(key)

        raise KeyError(f"Missing environment key: {key!r}")

    async def handle_local(
        self, effect: LocalEffect, ctx: ExecutionContext, engine: ProgramInterpreter
    ) -> Any:
        """Handle reader.local effect."""
        sub_ctx = ctx.copy()
        sub_ctx.env.update(effect.env_update)
        sub_ctx.log = ctx.log  # Share writer log with parent context
        pragmatic_result = await engine.run_async(effect.sub_program, sub_ctx)

        # Propagate sub-context graph/state/log updates back to parent context.
        # ``ExecutionContext.copy`` shares the cache and effect observations, but
        # graph/state mutations happen on ``sub_ctx``. Without copying them back,
        # any GraphStep/Step effects executed inside ``Local`` are lost.
        ctx.graph = pragmatic_result.context.graph
        ctx.state = pragmatic_result.context.state
        ctx.log = pragmatic_result.context.log

        # Return the value from the sub-program
        return pragmatic_result.value


class StateEffectHandler:
    """Handles State monad effects."""
    scope = HandlerScope.ISOLATED  # Each parallel execution gets its own state

    async def handle_get(self, effect: StateGetEffect, ctx: ExecutionContext) -> Any:
        """Handle state.get effect."""
        return ctx.state.get(effect.key)

    async def handle_put(self, effect: StatePutEffect, ctx: ExecutionContext) -> None:
        """Handle state.put effect."""
        ctx.state[effect.key] = effect.value

    async def handle_modify(self, effect: StateModifyEffect, ctx: ExecutionContext) -> Any:
        """Handle state.modify effect."""
        key = effect.key
        old_value = ctx.state.get(key)
        new_value = effect.func(old_value)
        ctx.state[key] = new_value
        return new_value


class AtomicEffectHandler:
    """Handles atomic shared-state effects with per-key locks."""

    scope = HandlerScope.SHARED

    def __init__(self) -> None:
        self._locks: dict[tuple[int, str], asyncio.Lock] = {}

    def _atomic_store(self, ctx: ExecutionContext) -> dict[str, Any]:
        store = ctx.cache.get("__atomic_state__")
        if store is None:
            store = {}
            ctx.cache["__atomic_state__"] = store
        return store

    def _lock_for(self, store: dict[str, Any], key: str) -> asyncio.Lock:
        lock_key = (id(store), key)
        lock = self._locks.get(lock_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[lock_key] = lock
        return lock

    def _ensure_current(
        self,
        store: dict[str, Any],
        key: str,
        default_factory: Callable[[], Any] | None,
        ctx: ExecutionContext,
    ) -> Any:
        sentinel = object()
        current = store.get(key, sentinel)
        if current is sentinel:
            if key in ctx.state:
                current = ctx.state[key]
            elif default_factory is not None:
                current = default_factory()
            else:
                current = None
            store[key] = current  # noqa: DOEFF007
        ctx.state[key] = current
        return current

    async def handle_get(self, effect: AtomicGetEffect, ctx: ExecutionContext) -> Any:
        store = self._atomic_store(ctx)
        lock = self._lock_for(store, effect.key)
        async with lock:
            return self._ensure_current(store, effect.key, effect.default_factory, ctx)

    async def handle_update(
        self, effect: AtomicUpdateEffect, ctx: ExecutionContext
    ) -> Any:
        store = self._atomic_store(ctx)
        lock = self._lock_for(store, effect.key)
        async with lock:
            current = self._ensure_current(store, effect.key, effect.default_factory, ctx)
            new_value = effect.updater(current)
            store[effect.key] = new_value
            ctx.state[effect.key] = new_value
            return new_value


class WriterEffectHandler:
    """Handles Writer monad effects."""
    scope = HandlerScope.ISOLATED  # Each parallel execution gets its own log

    async def handle_tell(self, effect: WriterTellEffect, ctx: ExecutionContext) -> None:
        """Handle writer.tell effect."""
        ctx.log.append(effect.message)

    async def handle_listen(
        self,
        effect: WriterListenEffect,
        ctx: ExecutionContext,
        engine: ProgramInterpreter,
    ) -> ListenResult:
        """Handle writer.listen effect."""
        sub_program = effect.sub_program
        sub_ctx = ctx.copy()
        if isinstance(sub_ctx.log, BoundedLog):
            sub_ctx.log = sub_ctx.log.spawn_empty()
        else:  # Defensive: contexts constructed without bounded logs
            sub_ctx.log = BoundedLog()
        pragmatic_result = await engine.run_async(sub_program, sub_ctx)
        return ListenResult(value=pragmatic_result.value, log=sub_ctx.log)


class FutureEffectHandler:
    """Handles Future monad effects."""
    scope = HandlerScope.SHARED  # Async operations are stateless

    async def handle_await(self, effect: FutureAwaitEffect) -> Any:
        """Handle future.await effect."""
        return await effect.awaitable

    async def handle_parallel(
        self, effect: FutureParallelEffect
    ) -> list[Any]:
        """Handle future.parallel effect."""
        results = await asyncio.gather(*effect.awaitables)
        return results


class ThreadEffectHandler:
    """Handles executing programs on worker threads."""

    scope = HandlerScope.SHARED

    def __init__(self, *, max_workers: int | None = None) -> None:
        self._max_workers = max_workers
        self._executor: ThreadPoolExecutor | None = None
        self._executor_lock = threading.Lock()

    def _ensure_executor(self) -> ThreadPoolExecutor:
        executor = self._executor
        if executor is not None:
            return executor
        with self._executor_lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(  # noqa: DOEFF002
                    max_workers=self._max_workers,
                    thread_name_prefix="doeff-thread-pool",
                )
            return self._executor

    def handle_thread(
        self,
        effect: ThreadEffect,
        ctx: ExecutionContext,
        engine: "ProgramInterpreter",
    ) -> Awaitable[Any]:
        """Return awaitable that executes the nested program on a thread."""

        return self._build_thread_awaitable(effect, ctx, engine)

    def _build_thread_awaitable(
        self,
        effect: ThreadEffect,
        ctx: ExecutionContext,
        engine: "ProgramInterpreter",
    ) -> Awaitable[Any]:
        loop = asyncio.get_running_loop()
        sub_ctx = ctx.copy()

        def run_program() -> tuple[Any, ExecutionContext]:  # noqa: DOEFF006
            pragmatic_result = engine.run(effect.program, sub_ctx)
            if isinstance(pragmatic_result.result, Err):
                raise pragmatic_result.result.error
            return pragmatic_result.value, pragmatic_result.context

        if effect.strategy == "pooled":
            run_future = loop.run_in_executor(self._ensure_executor(), run_program)
        else:
            is_daemon = effect.strategy == "daemon"
            run_future = self._run_in_thread(loop, run_program, daemon=is_daemon)

        async def wrapper() -> Any:
            value, result_ctx = await run_future
            self._merge_context(ctx, result_ctx)
            return value

        return wrapper()

    def _run_in_thread(
        self,
        loop: asyncio.AbstractEventLoop,
        runner: Callable[[], tuple[Any, ExecutionContext]],
        *,
        daemon: bool,
    ) -> asyncio.Future[tuple[Any, ExecutionContext]]:
        future: asyncio.Future[tuple[Any, ExecutionContext]] = loop.create_future()

        def target() -> None:
            try:
                result = runner()
            except BaseException as exc:  # pragma: no cover - defensive guard
                loop.call_soon_threadsafe(future.set_exception, exc)
            else:
                loop.call_soon_threadsafe(future.set_result, result)

        thread = threading.Thread(
            target=target,
            name="doeff-thread",
            daemon=daemon,
        )
        thread.start()
        return future

    def _merge_context(self, parent: ExecutionContext, child: ExecutionContext) -> None:
        parent.env.clear()
        parent.env.update(child.env)
        parent.state.clear()
        parent.state.update(child.state)
        parent.log.clear()
        parent.log.extend(child.log)
        parent.graph = child.graph


class SpawnEffectHandler:
    """Handles spawning programs in background tasks."""

    scope = HandlerScope.SHARED

    def __init__(
        self,
        *,
        default_backend: str = "thread",
        thread_max_workers: int | None = None,
        process_max_workers: int | None = None,
        ray_address: str | None = None,
        ray_init_kwargs: dict[str, Any] | None = None,
        ray_runtime_env: dict[str, Any] | None = None,
        max_log_entries: int | None = None,
    ) -> None:
        if default_backend not in _VALID_SPAWN_BACKENDS:
            raise ValueError(
                "default_backend must be one of 'thread', 'process', or 'ray', "
                f"got {default_backend!r}"
            )

        self._default_backend = default_backend
        self._thread_max_workers = thread_max_workers
        self._process_max_workers = process_max_workers
        self._ray_address = ray_address
        self._ray_init_kwargs = dict(ray_init_kwargs or {})
        self._ray_runtime_env = dict(ray_runtime_env or {}) if ray_runtime_env else None
        self._max_log_entries = max_log_entries

        self._thread_executor: ThreadPoolExecutor | None = None
        self._process_executor: ProcessPoolExecutor | None = None
        self._thread_lock = threading.Lock()
        self._process_lock = threading.Lock()

        self._ray = None
        self._ray_lock = threading.Lock()
        self._ray_remote_runner = None
        self._ray_unavailable_warned = False
        self._task_results: dict[int, tuple[Any, ExecutionContext]] = {}
        self._merged_tasks: set[tuple[int, int]] = set()
        self._join_lock: asyncio.Lock | None = None

        # Track spawned tasks for auto-join at interpreter shutdown
        self._pending_tasks: dict[int, Task[Any]] = {}
        self._joined_task_ids: set[int] = set()
        self._pending_tasks_lock = threading.Lock()

    def handle_spawn(
        self,
        effect: SpawnEffect,
        ctx: ExecutionContext,
        engine: "ProgramInterpreter",
    ) -> Task[Any]:
        backend = self._resolve_backend(effect)
        if backend == "thread":
            task = self._spawn_thread(effect, ctx, engine)
        elif backend == "process":
            task = self._spawn_process(effect, ctx)
        elif backend == "ray":
            task = self._spawn_ray(effect, ctx)
        else:
            raise RuntimeError(f"Unsupported spawn backend: {backend!r}")

        # Track spawned task for auto-join
        task_id = id(task._handle)
        with self._pending_tasks_lock:
            self._pending_tasks[task_id] = task
        return task

    async def handle_join(
        self,
        effect: TaskJoinEffect,
        ctx: ExecutionContext,
    ) -> Any:
        task = effect.task
        task_id = id(task._handle)

        # Mark task as joined
        with self._pending_tasks_lock:
            self._joined_task_ids.add(task_id)

        cached = self._task_results.get(task_id)

        if cached is None:
            backend = task.backend
            if backend == "ray":
                result_payload = await self._await_ray(task._handle)
            else:
                result_payload = await task._handle
            value, result_ctx = self._unpack_result(result_payload)
            if self._join_lock is None:
                self._join_lock = asyncio.Lock()
            async with self._join_lock:
                cached = self._task_results.setdefault(task_id, (value, result_ctx))

        value, result_ctx = cached
        if self._join_lock is None:
            self._join_lock = asyncio.Lock()
        async with self._join_lock:
            merge_key = (task_id, id(ctx))
            should_merge = merge_key not in self._merged_tasks
            if should_merge:
                self._merged_tasks.add(merge_key)

        if should_merge:
            self._merge_context(ctx, result_ctx, task)
        return value

    async def join_pending_tasks(self) -> list[BaseException]:
        """Join all spawned tasks that haven't been explicitly joined.

        Returns a list of exceptions from failed tasks. This ensures that:
        1. All spawned tasks are properly awaited (preventing Future warnings)
        2. Errors from unjoined tasks are collected and reported

        This method should be called at the end of program execution.
        """
        errors: list[BaseException] = []

        with self._pending_tasks_lock:
            unjoined_task_ids = set(self._pending_tasks.keys()) - self._joined_task_ids
            tasks_to_join = [
                (task_id, self._pending_tasks[task_id])
                for task_id in unjoined_task_ids
            ]

        for task_id, task in tasks_to_join:
            try:
                backend = task.backend
                if backend == "ray":
                    await self._await_ray(task._handle)
                else:
                    await task._handle
            except BaseException as exc:
                errors.append(exc)
            finally:
                # Mark as joined to prevent duplicate processing
                with self._pending_tasks_lock:
                    self._joined_task_ids.add(task_id)

        return errors

    def clear_pending_tasks(self) -> None:
        """Clear tracking state for a new program execution."""
        with self._pending_tasks_lock:
            self._pending_tasks.clear()
            self._joined_task_ids.clear()

    def _resolve_backend(self, effect: SpawnEffect) -> str:
        backend = effect.preferred_backend or self._default_backend
        if backend == "ray" and not self._ray_available():
            fallback = self._default_backend if self._default_backend != "ray" else "thread"
            self._warn_ray_unavailable(fallback)
            return fallback
        return backend

    def _spawn_thread(
        self,
        effect: SpawnEffect,
        ctx: ExecutionContext,
        engine: "ProgramInterpreter",
    ) -> Task[Any]:
        loop = asyncio.get_running_loop()
        sub_ctx = self._prepare_context(ctx, share_cache=True)
        env_snapshot = sub_ctx.env.copy()
        state_snapshot = sub_ctx.state.copy()

        def run_program() -> tuple[Any, ExecutionContext]:  # noqa: DOEFF006
            pragmatic_result = engine.run(effect.program, sub_ctx)
            if isinstance(pragmatic_result.result, Err):
                raise pragmatic_result.result.error
            return pragmatic_result.value, pragmatic_result.context

        future = loop.run_in_executor(self._ensure_thread_executor(), run_program)
        return Task(
            backend="thread",
            _handle=future,
            _env_snapshot=env_snapshot,
            _state_snapshot=state_snapshot,
        )

    def _spawn_process(self, effect: SpawnEffect, ctx: ExecutionContext) -> Task[Any]:
        loop = asyncio.get_running_loop()
        sub_ctx = self._prepare_context(ctx, share_cache=False, sanitize_call_stack=True)
        env_snapshot = sub_ctx.env.copy()
        state_snapshot = sub_ctx.state.copy()
        program = _sanitize_program(effect.program)
        payload = _cloudpickle_dumps((program, sub_ctx), "spawn payload")
        future = loop.run_in_executor(
            self._ensure_process_executor(),
            _run_spawn_payload,
            payload,
            self._max_log_entries,
        )
        return Task(
            backend="process",
            _handle=future,
            _env_snapshot=env_snapshot,
            _state_snapshot=state_snapshot,
        )

    def _spawn_ray(self, effect: SpawnEffect, ctx: ExecutionContext) -> Task[Any]:
        self._ensure_ray_initialized()
        sub_ctx = self._prepare_context(ctx, share_cache=False, sanitize_call_stack=True)
        env_snapshot = sub_ctx.env.copy()
        state_snapshot = sub_ctx.state.copy()
        program = _sanitize_program(effect.program)
        payload = _cloudpickle_dumps((program, sub_ctx), "spawn payload")
        options = self._build_ray_options(effect.options)
        runner = self._ray_remote_runner
        if options:
            runner = runner.options(**options)
        object_ref = runner.remote(payload, max_log_entries=self._max_log_entries)
        return Task(
            backend="ray",
            _handle=object_ref,
            _env_snapshot=env_snapshot,
            _state_snapshot=state_snapshot,
        )

    def _prepare_context(
        self,
        ctx: ExecutionContext,
        *,
        share_cache: bool,
        sanitize_call_stack: bool = False,
    ) -> ExecutionContext:
        if isinstance(ctx.log, BoundedLog):
            log = ctx.log.spawn_empty()
        else:
            log = BoundedLog()
        if self._max_log_entries is not None:
            log.set_max_entries(self._max_log_entries)

        cache = ctx.cache if share_cache else {}
        observations = ctx.effect_observations if share_cache else []
        call_stack = ctx.program_call_stack.copy()
        if sanitize_call_stack:
            call_stack = _sanitize_call_stack(call_stack)

        return ExecutionContext(
            env=ctx.env.copy() if ctx.env else {},
            state=ctx.state.copy() if ctx.state else {},
            log=log,
            graph=ctx.graph,
            io_allowed=ctx.io_allowed,
            cache=cache,
            effect_observations=observations,
            program_call_stack=call_stack,
        )

    def _merge_context(
        self,
        parent: ExecutionContext,
        child: ExecutionContext,
        task: Task[Any],
    ) -> None:
        self._merge_mapping(parent.env, child.env, task._env_snapshot)
        self._merge_mapping(parent.state, child.state, task._state_snapshot)
        parent.log.extend(child.log)

        combined_steps = set(parent.graph.steps)
        combined_steps.update(child.graph.steps)
        parent.graph = WGraph(last=child.graph.last, steps=frozenset(combined_steps))

    @staticmethod
    def _merge_mapping(
        target: dict[Any, Any],
        source: dict[Any, Any],
        snapshot: dict[Any, Any],
    ) -> None:
        for key, value in source.items():
            if key not in snapshot or not SpawnEffectHandler._values_equal(
                snapshot.get(key), value
            ):
                target[key] = value  # noqa: DOEFF007

    @staticmethod
    def _values_equal(left: Any, right: Any) -> bool:
        try:
            result = left == right
            if isinstance(result, bool):
                return result
            # Non-bool __eq__ result (numpy arrays, DataFrames, etc.)
            return False
        except Exception:
            return False

    def _ensure_thread_executor(self) -> ThreadPoolExecutor:
        executor = self._thread_executor
        if executor is not None:
            return executor
        with self._thread_lock:
            if self._thread_executor is None:
                self._thread_executor = ThreadPoolExecutor(  # noqa: DOEFF002
                    max_workers=self._thread_max_workers,
                    thread_name_prefix="doeff-spawn-thread",
                )
            return self._thread_executor

    def _ensure_process_executor(self) -> ProcessPoolExecutor:
        executor = self._process_executor
        if executor is not None:
            return executor
        with self._process_lock:
            if self._process_executor is None:
                self._process_executor = ProcessPoolExecutor(  # noqa: DOEFF002
                    max_workers=self._process_max_workers
                )
            return self._process_executor

    def _ray_available(self) -> bool:
        if self._ray is not None:
            return True
        return importlib.util.find_spec("ray") is not None

    def _warn_ray_unavailable(self, fallback: str) -> None:
        if self._ray_unavailable_warned:
            return
        self._ray_unavailable_warned = True  # noqa: DOEFF002
        logger.warning(
            "Ray backend requested but 'ray' is not installed; falling back to %s. "
            "Install via `uv add 'doeff[ray]'` or `pip install ray`.",
            fallback,
        )

    def _load_ray(self) -> Any:
        if self._ray is not None:
            return self._ray
        try:
            import ray
        except ImportError as exc:
            raise RuntimeError(
                "Ray backend requested but 'ray' is not installed. "
                "Install via `uv add 'doeff[ray]'` or `pip install ray`."
            ) from exc
        self._ray = ray  # noqa: DOEFF002
        return ray

    def _ensure_ray_initialized(self) -> Any:
        ray = self._load_ray()
        if self._ray_remote_runner is not None:
            return ray
        with self._ray_lock:
            if self._ray_remote_runner is not None:
                return ray
            if not ray.is_initialized():
                init_kwargs = dict(self._ray_init_kwargs)
                if self._ray_runtime_env and "runtime_env" not in init_kwargs:
                    init_kwargs["runtime_env"] = self._ray_runtime_env
                if self._ray_address is not None:
                    init_kwargs = {"address": self._ray_address, **init_kwargs}
                ray.init(**init_kwargs)
            self._ray_remote_runner = ray.remote(_run_spawn_payload)  # noqa: DOEFF002
        return ray

    def _build_ray_options(self, options: dict[str, Any]) -> dict[str, Any]:
        ray_options = dict(options)
        runtime_env = ray_options.pop("runtime_env", None)
        merged_env = self._merge_runtime_env(runtime_env)
        if merged_env:
            ray_options["runtime_env"] = merged_env
        return ray_options

    def _merge_runtime_env(
        self, override: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if self._ray_runtime_env is None and override is None:
            return None

        merged: dict[str, Any] = {}
        if self._ray_runtime_env:
            merged.update(self._ray_runtime_env)

        if override:
            if (
                "env_vars" in merged
                and "env_vars" in override
                and isinstance(merged["env_vars"], dict)
                and isinstance(override["env_vars"], dict)
            ):
                merged["env_vars"] = {
                    **merged["env_vars"],
                    **override["env_vars"],
                }
                override = {key: value for key, value in override.items() if key != "env_vars"}
            merged.update(override)
        return merged

    async def _await_ray(self, object_ref: Any) -> Any:
        ray = self._load_ray()
        try:
            return await asyncio.to_thread(ray.get, object_ref)
        except Exception as exc:
            cause = getattr(exc, "cause", None)
            if isinstance(cause, BaseException):
                raise cause from exc
            raise

    def _unpack_result(self, payload: Any) -> tuple[Any, ExecutionContext]:  # noqa: DOEFF006
        decoded = payload
        if isinstance(payload, bytes):
            decoded = _cloudpickle_loads(payload, "spawn result")

        value, result_ctx = self._decode_spawn_payload(decoded)
        if isinstance(result_ctx, dict):
            result_ctx = ExecutionContext(
                env=result_ctx.get("env", {}),
                state=result_ctx.get("state", {}),
                log=result_ctx.get("log", []),
                graph=result_ctx.get("graph", WGraph.single(None)),
                io_allowed=result_ctx.get("io_allowed", True),
                cache=result_ctx.get("cache", {}),
                effect_observations=[],
                program_call_stack=[],
            )
        if not isinstance(result_ctx, ExecutionContext):
            raise TypeError(
                "spawn task result context must be ExecutionContext, "
                f"got {type(result_ctx).__name__}"
            )
        return value, result_ctx

    def _decode_spawn_payload(self, decoded: Any) -> tuple[Any, Any]:  # noqa: DOEFF006
        # Handle cloudpickled bytes from process backend
        if isinstance(decoded, bytes):
            decoded = _cloudpickle_loads(decoded, "spawn result")

        if isinstance(decoded, tuple) and decoded:
            tag = decoded[0]
            if tag == _SPAWN_ERR_TAG:
                error = decoded[1] if len(decoded) > 1 else RuntimeError("Spawn task failed")
                if isinstance(error, dict):
                    if error.get("is_effect_failure"):
                        # Reconstruct EffectFailure with preserved call stack
                        raise _reconstruct_spawn_effect_failure(error)
                    error_type = error.get("type", "Error")
                    message = error.get("message", "")
                    raise RuntimeError(f"Spawn task failed ({error_type}): {message}")
                if isinstance(error, BaseException):
                    raise error
                raise RuntimeError(f"Spawn task failed: {error!r}")
            if tag == _SPAWN_OK_TAG:
                value = decoded[1] if len(decoded) > 1 else None
                result_ctx = decoded[2] if len(decoded) > 2 else {}
                return value, result_ctx

        value, result_ctx = decoded
        return value, result_ctx


class _InterpreterHandle:
    """Proxy that reuses the current context when re-running programs via Ask(\"__interpreter__\")."""

    def __init__(self, engine: "ProgramInterpreter", ctx: ExecutionContext) -> None:
        self.engine = engine
        self._ctx = ctx

    def run(self, program: Program, context: ExecutionContext | None = None) -> RunResult:
        ctx = self._clone_context(context)
        return self.engine.run(program, ctx)

    async def run_async(self, program: Program, context: ExecutionContext | None = None) -> RunResult:
        ctx = self._clone_context(context)
        return await self.engine.run_async(program, ctx)

    def _clone_context(self, context: ExecutionContext | None) -> ExecutionContext:
        if context is not None:
            return context

        max_entries = getattr(self.engine, "_max_log_entries", None)
        log_copy = BoundedLog(self._ctx.log, max_entries=max_entries)

        return ExecutionContext(
            env=dict(self._ctx.env) if self._ctx.env else {},
            state=dict(self._ctx.state) if self._ctx.state else {},
            log=log_copy,
            graph=self._ctx.graph,
            io_allowed=self._ctx.io_allowed,
            cache=self._ctx.cache,
            effect_observations=self._ctx.effect_observations,
            program_call_stack=list(self._ctx.program_call_stack)
            if getattr(self._ctx, "program_call_stack", None) is not None
            else [],
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self.engine, name)


class ResultEffectHandler:
    """Handles Result monad effects."""
    scope = HandlerScope.SHARED  # Error handling is stateless

    async def handle_pure(self, effect: PureEffect) -> Any:
        """Handle pure effect - immediately return wrapped value."""
        return effect.value

    async def handle_fail(self, effect: ResultFailEffect) -> None:
        """Handle result.fail effect."""
        raise effect.exception

    async def handle_catch(
        self, effect: ResultCatchEffect, ctx: ExecutionContext, engine: ProgramInterpreter
    ) -> Any:
        """Handle result.catch effect."""
        # Import here to avoid circular import
        from doeff.types import EffectFailure

        sub_program = effect.sub_program

        def _unwrap_error(error: Any) -> Any:
            if isinstance(error, EffectFailure):
                return error.cause
            return error

        async def _run_handler(handler_error: Any) -> Any:
            from doeff.types import Program as ProgramType

            handler_result = effect.handler(handler_error)

            if isinstance(handler_result, ProgramType):
                handler_run = await engine.run_async(handler_result, ctx)
                if isinstance(handler_run.result, Err):
                    raise handler_run.result.error
                return handler_run.value

            return handler_result

        try:
            pragmatic_result = await engine.run_async(sub_program, ctx)
        except BaseException as exc:  # Handle direct exceptions from the sub-program
            if isinstance(exc, SystemExit):
                raise
            actual_error = _unwrap_error(exc)
            return await _run_handler(actual_error)

        if isinstance(pragmatic_result.result, Err):
            error = _unwrap_error(pragmatic_result.result.error)
            return await _run_handler(error)

        return pragmatic_result.value

    async def handle_finally(
        self,
        effect: ResultFinallyEffect,
        ctx: ExecutionContext,
        engine: ProgramInterpreter,
    ) -> Any:
        """Handle result.finally effect ensuring finalizer runs on all outcomes."""

        async def _run_finalizer() -> None:
            finalizer_value = effect.finalizer

            if callable(finalizer_value) and not isinstance(finalizer_value, Program):
                finalizer_value = finalizer_value()

            if finalizer_value is None:
                return

            if not isinstance(finalizer_value, (Program, EffectBase)):
                return

            finalizer_result = await engine.run_async(finalizer_value, ctx)
            if isinstance(finalizer_result.result, Err):
                raise finalizer_result.result.error

        sub_program = effect.sub_program

        try:
            pragmatic_result = await engine.run_async(sub_program, ctx)
        except BaseException:
            await _run_finalizer()
            raise

        await _run_finalizer()

        if isinstance(pragmatic_result.result, Err):
            raise pragmatic_result.result.error

        return pragmatic_result.value

    async def handle_safe(
        self, effect: ResultSafeEffect, ctx: ExecutionContext, engine: ProgramInterpreter
    ) -> Any:
        """Handle result.safe effect by capturing the program outcome."""
        pragmatic_result = await engine.run_async(effect.sub_program, ctx)
        result = pragmatic_result.result

        if isinstance(result, Err):
            error = result.error
            if isinstance(error, EffectFailure):
                unwrapped = error.cause
                while isinstance(unwrapped, EffectFailure):
                    unwrapped = unwrapped.cause

                if isinstance(unwrapped, Exception):
                    return Err(unwrapped)

            return result

        return result

    async def handle_first_success(
        self,
        effect: ResultFirstSuccessEffect,
        ctx: ExecutionContext,
        engine: ProgramInterpreter,
    ) -> Any:
        """Handle sequential attempts, returning the first successful value."""

        base_snapshot = ctx.copy()
        base_snapshot.effect_observations = list(ctx.effect_observations)

        last_error: Exception | None = None

        for candidate in effect.programs:

            attempt_ctx = base_snapshot.copy()
            attempt_ctx.effect_observations = list(base_snapshot.effect_observations)

            pragmatic_result = await engine.run_async(candidate, attempt_ctx)

            if isinstance(pragmatic_result.result, Ok):
                ctx.env = attempt_ctx.env
                ctx.state = attempt_ctx.state
                ctx.log = attempt_ctx.log
                ctx.graph = attempt_ctx.graph
                ctx.effect_observations = attempt_ctx.effect_observations
                return pragmatic_result.value

            last_error = pragmatic_result.result.error

        if last_error is not None:
            raise last_error

        raise RuntimeError("All programs failed")

    async def handle_unwrap(
        self,
        effect: ResultUnwrapEffect,
        _ctx: ExecutionContext,
        _engine: ProgramInterpreter,
    ) -> Any:
        """Handle result.unwrap effect by raising or returning the Result."""

        result = effect.result
        if isinstance(result, Ok):
            return result.value

        if isinstance(result, Err):
            raise result.error

        raise TypeError(
            f"ResultUnwrapEffect expected Result, got {type(result)!r}"
        )

    async def handle_recover(
        self, effect: ResultRecoverEffect, ctx: ExecutionContext, engine: ProgramInterpreter
    ) -> Any:
        """Handle result.recover effect - try program, use fallback on error."""
        pragmatic_result = await engine.run_async(effect.sub_program, ctx)

        if isinstance(pragmatic_result.result, Err):
            error = self._unwrap_effect_failure(pragmatic_result.result.error)
            fallback = await self._resolve_fallback(effect.fallback, error, ctx, engine)
            return await self._execute_fallback(fallback, ctx, engine)

        return pragmatic_result.value

    def _unwrap_effect_failure(self, error: Any) -> Any:
        """Unwrap EffectFailure to get the underlying cause."""
        from doeff.types import EffectFailure
        return error.cause if isinstance(error, EffectFailure) else error

    async def _resolve_fallback(
        self, fallback: Any, error: Any, ctx: ExecutionContext, engine: ProgramInterpreter
    ) -> Any:
        """Resolve fallback based on its type (error handler, thunk, or value)."""
        if not callable(fallback) or isinstance(fallback, Program):
            return fallback

        from doeff.kleisli import KleisliProgram
        if isinstance(fallback, KleisliProgram):
            return await self._handle_kleisli_fallback(fallback, error, ctx, engine)

        return await self._handle_regular_callable(fallback, error, ctx, engine)

    async def _handle_kleisli_fallback(
        self, fallback: Any, error: Any, ctx: ExecutionContext, engine: ProgramInterpreter
    ) -> Any:
        """Handle KleisliProgram fallback - check signature to determine if error handler or thunk."""
        import inspect

        # Check the signature of the underlying function to determine if it accepts arguments
        try:
            sig = inspect.signature(fallback)
            params = [p for p in sig.parameters.values()
                     if p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)]

            # If it has required parameters, it's an error handler
            if params and params[0].default is inspect.Parameter.empty:
                handler_result = fallback(error)
            else:
                # No required params, it's a thunk
                handler_result = fallback()
        except (ValueError, TypeError):
            # Can't inspect, try with error first, fallback to thunk on error
            try:
                handler_result = fallback(error)
            except TypeError as e:
                if "positional argument" in str(e):
                    handler_result = fallback()
                else:
                    raise

        from doeff.types import Program as ProgramType
        if not isinstance(handler_result, ProgramType):
            return handler_result

        try_result = await engine.run_async(handler_result, ctx)

        if isinstance(try_result.result, Err):
            raise self._unwrap_effect_failure(try_result.result.error)

        return try_result.value

    async def _handle_regular_callable(
        self, fallback: Any, error: Any, ctx: ExecutionContext, engine: ProgramInterpreter
    ) -> Any:
        """Handle regular callable fallback based on signature."""
        import inspect
        try:
            sig = inspect.signature(fallback)
            if len(sig.parameters) > 0:
                # Error handler - call with exception
                return await self._execute_error_handler(fallback(error), ctx, engine)
            # Thunk - call with no args
            return fallback()
        except (ValueError, TypeError):
            # Can't inspect signature, treat as thunk
            return fallback()

    async def _execute_error_handler(
        self, handler_result: Any, ctx: ExecutionContext, engine: ProgramInterpreter
    ) -> Any:
        """Execute error handler result if it's a Program."""
        from doeff.program import KleisliProgramCall

        if isinstance(handler_result, (Program, KleisliProgramCall, EffectBase)):
            result = await engine.run_async(handler_result, ctx)
            return result.value
        return handler_result

    async def _execute_fallback(
        self, fallback: Any, ctx: ExecutionContext, engine: ProgramInterpreter
    ) -> Any:
        """Execute the resolved fallback value."""
        from doeff.program import KleisliProgramCall

        if isinstance(fallback, (Program, KleisliProgramCall, EffectBase)):
            result = await engine.run_async(fallback, ctx)
            return result.value
        return fallback

    async def handle_retry(
        self, effect: ResultRetryEffect, ctx: ExecutionContext, engine: ProgramInterpreter
    ) -> Any:
        """Handle result.retry effect - retry program on failure."""
        import asyncio

        from doeff._vendor import Ok
        from doeff.program import Program

        max_attempts = effect.max_attempts
        delay_ms = effect.delay_ms
        delay_strategy = effect.delay_strategy

        sub_program = effect.sub_program

        last_error = None
        for attempt in range(max_attempts):
            pragmatic_result = await engine.run_async(sub_program, ctx)

            if isinstance(pragmatic_result.result, Ok):
                return pragmatic_result.value

            last_error = pragmatic_result.result.error

            if attempt < max_attempts - 1:
                delay_seconds: float | None = None
                if delay_strategy is not None:
                    try:
                        delay_value = delay_strategy(attempt + 1, last_error)  # type: ignore[arg-type]
                    except Exception as exc:
                        raise RuntimeError(
                            "Retry delay_strategy raised an exception"
                        ) from exc
                    if delay_value is None:
                        delay_seconds = None
                    else:
                        delay_seconds = float(delay_value)
                        if delay_seconds < 0:
                            raise ValueError("Retry delay_strategy must not return a negative delay")
                elif delay_ms > 0:
                    delay_seconds = delay_ms / 1000.0

                if delay_seconds is not None and delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)

        if last_error:
            raise last_error
        raise RuntimeError(f"All {max_attempts} attempts failed")


class IOEffectHandler:
    """Handles IO monad effects."""
    scope = HandlerScope.SHARED  # IO operations share permissions

    async def handle_run(self, effect: IOPerformEffect, ctx: ExecutionContext) -> Any:
        """Handle io.run effect."""
        if not ctx.io_allowed:
            raise PermissionError("IO not allowed in this context")
        return effect.action()

    async def handle_print(self, effect: IOPrintEffect, ctx: ExecutionContext) -> None:
        """Handle io.print effect."""
        if not ctx.io_allowed:
            raise PermissionError("IO not allowed in this context")
        print(effect.message)


class GraphEffectHandler:
    """Handles Graph effects for SGFR compatibility."""
    scope = HandlerScope.SHARED  # Share graph across parallel execution contexts

    async def handle_step(self, effect: GraphStepEffect, ctx: ExecutionContext) -> Any:
        """Handle graph.step effect."""
        value = effect.value
        meta = effect.meta

        new_node = WNode(value)
        new_step = WStep(inputs=(ctx.graph.last.output,), output=new_node, meta=meta)
        ctx.graph = WGraph(last=new_step, steps=ctx.graph.steps | {new_step})
        return value

    async def handle_annotate(
        self, effect: GraphAnnotateEffect, ctx: ExecutionContext
    ) -> None:
        """Handle graph.annotate effect."""
        ctx.graph = ctx.graph.with_last_meta(effect.meta)

    async def handle_snapshot(self, _effect: GraphSnapshotEffect, ctx: ExecutionContext) -> WGraph:
        """Return the current computation graph."""
        return ctx.graph

    async def handle_capture(
        self,
        effect: GraphCaptureEffect,
        ctx: ExecutionContext,
        engine: ProgramInterpreter,
    ) -> GraphCaptureResult:
        sub_program = effect.program

        sub_ctx = ExecutionContext(
            env=ctx.env.copy() if ctx.env else {},
            state=ctx.state.copy() if ctx.state else {},
            log=ctx.log.spawn_empty() if isinstance(ctx.log, BoundedLog) else BoundedLog(),
            graph=WGraph(
                last=WStep(inputs=(), output=WNode("_root"), meta={}),
                steps=frozenset(),
            ),
            io_allowed=ctx.io_allowed,
            cache=ctx.cache,
        )

        result = await engine.run_async(sub_program, sub_ctx)

        if isinstance(result.result, Err):
            raise result.result.error

        ctx.state.update(result.context.state)
        ctx.log.extend(result.context.log)

        return GraphCaptureResult(value=result.value, graph=result.context.graph)



class MemoEffectHandler:
    """In-memory memoization handler."""

    scope = HandlerScope.SHARED

    def _serialize_key(self, key: Any) -> str:
        key_bytes = _cloudpickle_dumps(key, "memo key")
        return hashlib.sha256(key_bytes).hexdigest()

    async def handle_get(self, effect: MemoGetEffect, ctx: ExecutionContext) -> Any:
        serialized_key = self._serialize_key(effect.key)
        if serialized_key not in ctx.cache:
            raise KeyError("Memo miss for key")
        return ctx.cache[serialized_key]

    async def handle_put(self, effect: MemoPutEffect, ctx: ExecutionContext) -> None:
        serialized_key = self._serialize_key(effect.key)
        ctx.cache[serialized_key] = effect.value


class CacheEffectHandler:
    """Persistent cache backed by SQLite/LZMA storage.

    The cache path is determined lazily on first use. The handler looks for
    ``CACHE_PATH_ENV_KEY`` (``"doeff.cache_path"``) in the execution context's
    environment. If not present, it falls back to :func:`persistent_cache_path`.
    """

    scope = HandlerScope.SHARED

    def __init__(self) -> None:
        import time

        self._time = time.time
        self._db_path: Path | None = None
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    def _ensure_connection(self, ctx: ExecutionContext) -> sqlite3.Connection:
        """Lazily initialize the SQLite connection using the context's environment."""
        if self._conn is not None:
            return self._conn

        # Look up cache path from context environment, fall back to default
        env_path = ctx.env.get(CACHE_PATH_ENV_KEY)
        if env_path is not None:
            self._db_path = Path(env_path) if not isinstance(env_path, Path) else env_path  # noqa: DOEFF002
        else:
            self._db_path = persistent_cache_path()

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(  # noqa: DOEFF002
            self._db_path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache_entries (
                key_hash TEXT PRIMARY KEY,
                expiry REAL,
                key_blob BLOB NOT NULL,
                value_blob BLOB NOT NULL
            )
            """
        )
        self._conn.commit()
        return self._conn

    def _serialize_key(self, key: Any) -> tuple[str, bytes]:  # noqa: DOEFF006
        key_bytes = _cloudpickle_dumps(key, "cache key")
        key_blob = lzma.compress(key_bytes)
        key_hash = hashlib.sha256(key_blob).hexdigest()
        return key_hash, key_blob

    async def handle_get(self, effect: CacheGetEffect, ctx: ExecutionContext) -> Any:
        key_hash, _ = self._serialize_key(effect.key)
        conn = self._ensure_connection(ctx)

        async with self._lock:
            row = conn.execute(
                "SELECT value_blob, expiry FROM cache_entries WHERE key_hash = ?",
                (key_hash,),
            ).fetchone()
        if row is None:
            raise KeyError("Cache miss for key")

        value_blob, expiry = row
        if expiry is not None and self._time() > expiry:
            await self._delete_entry(key_hash)
            raise KeyError("Cache expired for key")

        return cloudpickle.loads(lzma.decompress(value_blob))

    async def handle_put(self, effect: CachePutEffect, ctx: ExecutionContext) -> None:
        key = effect.key
        value = effect.value
        policy = effect.policy

        ttl = policy.ttl
        expiry = None
        if ttl is not None and ttl > 0:
            expiry = self._time() + ttl

        key_hash, key_blob = self._serialize_key(key)
        value_blob = lzma.compress(cloudpickle.dumps(value))
        conn = self._ensure_connection(ctx)

        async with self._lock:
            conn.execute(
                "REPLACE INTO cache_entries (key_hash, expiry, key_blob, value_blob) VALUES (?, ?, ?, ?)",
                (key_hash, expiry, key_blob, value_blob),
            )
            conn.commit()

    async def _delete_entry(self, key_hash: str) -> None:
        if self._conn is None:
            return
        async with self._lock:
            self._conn.execute(
                "DELETE FROM cache_entries WHERE key_hash = ?",
                (key_hash,),
            )
            self._conn.commit()


__all__ = [  # noqa: DOEFF021
    "AtomicEffectHandler",
    "CacheEffectHandler",
    "FutureEffectHandler",
    "GraphEffectHandler",
    "HandlerScope",
    "IOEffectHandler",
    "MemoEffectHandler",
    "ReaderEffectHandler",
    "ResultEffectHandler",
    "SpawnEffectHandler",
    "StateEffectHandler",
    "ThreadEffectHandler",
    "WriterEffectHandler",
]
