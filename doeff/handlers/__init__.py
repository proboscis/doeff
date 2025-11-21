"""
Effect handlers for the doeff system.

This module contains handler classes for each effect category.
Each handler is responsible for interpreting specific effects.
"""

from __future__ import annotations

import asyncio
import hashlib
import lzma
import os
import sqlite3
import tempfile
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cloudpickle

from doeff._vendor import Err, Ok, WGraph, WNode, WStep
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
    ThreadEffect,
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

# Need Program at runtime for isinstance checks

if TYPE_CHECKING:
    from doeff.interpreter import ProgramInterpreter


class HandlerScope(Enum):
    """Defines how handler state should be managed in parallel execution contexts."""
    ISOLATED = auto()  # Each parallel execution gets its own handler instance (e.g., State)
    SHARED = auto()    # All parallel executions share the same handler instance (e.g., Cache, Log)


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

            if isinstance(value, ProgramType):
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

        raise KeyError(f"Missing environment key: {key}")

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
            store[key] = current
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
                self._executor = ThreadPoolExecutor(
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

        def run_program() -> tuple[Any, ExecutionContext]:
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

    async def handle_snapshot(self, _effect: GraphSnapshotEffect, ctx: ExecutionContext):
        """Return the current computation graph."""
        return ctx.graph

    async def handle_capture(
        self,
        effect: GraphCaptureEffect,
        ctx: ExecutionContext,
        engine: ProgramInterpreter,
    ) -> tuple[Any, WGraph]:
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

        return result.value, result.context.graph



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
    """Persistent cache backed by SQLite/LZMA storage."""

    scope = HandlerScope.SHARED

    def __init__(self):
        import time

        self._time = time.time
        db_path = os.environ.get("DOEFF_CACHE_PATH")
        if db_path:
            self._db_path = Path(db_path)
        else:
            self._db_path = Path(tempfile.gettempdir()) / "doeff_cache.sqlite3"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
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
        self._lock = asyncio.Lock()

    def _serialize_key(self, key: Any) -> tuple[str, bytes]:
        key_bytes = _cloudpickle_dumps(key, "cache key")
        key_blob = lzma.compress(key_bytes)
        key_hash = hashlib.sha256(key_blob).hexdigest()
        return key_hash, key_blob

    async def handle_get(self, effect: CacheGetEffect, _ctx: ExecutionContext) -> Any:
        key_hash, _ = self._serialize_key(effect.key)
        # hmm, serializing a key is failing here, but i cannot tell what is passing a bad key...

        async with self._lock:
            row = self._conn.execute(
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

    async def handle_put(self, effect: CachePutEffect, _ctx: ExecutionContext) -> None:
        key = effect.key
        value = effect.value
        policy = effect.policy

        ttl = policy.ttl
        expiry = None
        if ttl is not None and ttl > 0:
            expiry = self._time() + ttl

        key_hash, key_blob = self._serialize_key(key)
        value_blob = lzma.compress(cloudpickle.dumps(value))

        async with self._lock:
            self._conn.execute(
                "REPLACE INTO cache_entries (key_hash, expiry, key_blob, value_blob) VALUES (?, ?, ?, ?)",
                (key_hash, expiry, key_blob, value_blob),
            )
            self._conn.commit()

    async def _delete_entry(self, key_hash: str) -> None:
        async with self._lock:
            self._conn.execute(
                "DELETE FROM cache_entries WHERE key_hash = ?",
                (key_hash,),
            )
            self._conn.commit()


__all__ = [
    "AtomicEffectHandler",
    "CacheEffectHandler",
    "FutureEffectHandler",
    "GraphEffectHandler",
    "HandlerScope",
    "IOEffectHandler",
    "MemoEffectHandler",
    "ReaderEffectHandler",
    "ResultEffectHandler",
    "StateEffectHandler",
    "ThreadEffectHandler",
    "WriterEffectHandler",
]
