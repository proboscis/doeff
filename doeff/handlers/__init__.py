"""
Effect handlers for the doeff system.

This module contains handler classes for each effect category.
Each handler is responsible for interpreting specific effects.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict

import hashlib
import lzma
import os
import sqlite3
import tempfile

import cloudpickle

from doeff._vendor import Err, Ok, WGraph, WNode, WStep
from doeff.cache_policy import CachePolicy, CacheStorage
from doeff.types import EffectBase, EffectFailure, ExecutionContext, ListenResult
from doeff.effects import (
    AskEffect,
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
    ResultSafeEffect,
    ResultRecoverEffect,
    ResultRetryEffect,
    StateGetEffect,
    StateModifyEffect,
    StatePutEffect,
    WriterListenEffect,
    WriterTellEffect,
)
from doeff.effects.result import ResultFirstSuccessEffect, ResultUnwrapEffect

if TYPE_CHECKING:
    from doeff.interpreter import ProgramInterpreter

# Need Program at runtime for isinstance checks
from doeff.program import Program


class HandlerScope(Enum):
    """Defines how handler state should be managed in parallel execution contexts."""
    ISOLATED = auto()  # Each parallel execution gets its own handler instance (e.g., State)
    SHARED = auto()    # All parallel executions share the same handler instance (e.g., Cache, Log)


class ReaderEffectHandler:
    """Handles Reader monad effects."""
    scope = HandlerScope.ISOLATED  # Each parallel execution gets its own environment

    async def handle_ask(self, effect: AskEffect, ctx: ExecutionContext) -> Any:
        """Handle reader.ask effect."""
        key = effect.key
        if key in ctx.env:
            return ctx.env[key]

        resolver = ctx.env.get("__resolver__")
        if resolver is not None:
            return await resolver.provide(key)

        raise KeyError(f"Missing environment key: {key}")

    async def handle_local(
        self, effect: LocalEffect, ctx: ExecutionContext, engine: "ProgramInterpreter"
    ) -> Any:
        """Handle reader.local effect."""
        sub_ctx = ctx.copy()
        sub_ctx.env.update(effect.env_update)
        sub_ctx.log = ctx.log  # Share writer log with parent context
        sub_program = Program.from_program_like(effect.sub_program)
        pragmatic_result = await engine.run(sub_program, sub_ctx)
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
        engine: "ProgramInterpreter",
    ) -> ListenResult:
        """Handle writer.listen effect."""
        from doeff.program import Program
        
        sub_program = Program.from_program_like(effect.sub_program)
        sub_ctx = ctx.copy()
        sub_ctx.log = []  # Fresh log for sub-program
        pragmatic_result = await engine.run(sub_program, sub_ctx)
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


class ResultEffectHandler:
    """Handles Result monad effects."""
    scope = HandlerScope.SHARED  # Error handling is stateless

    async def handle_fail(self, effect: ResultFailEffect) -> None:
        """Handle result.fail effect."""
        raise effect.exception

    async def handle_catch(
        self, effect: ResultCatchEffect, ctx: ExecutionContext, engine: "ProgramInterpreter"
    ) -> Any:
        """Handle result.catch effect."""
        # Import here to avoid circular import
        from doeff.program import Program
        from doeff.types import EffectFailure

        sub_program = Program.from_program_like(effect.sub_program)

        def _unwrap_error(error: Any) -> Any:
            if isinstance(error, EffectFailure):
                return error.cause
            return error

        async def _run_handler(handler_error: Any) -> Any:
            handler_result = effect.handler(handler_error)

            if isinstance(handler_result, (Program, EffectBase)):
                handler_program = Program.from_program_like(handler_result)
                handler_run = await engine.run(handler_program, ctx)
                if isinstance(handler_run.result, Err):
                    raise handler_run.result.error
                return handler_run.value

            return handler_result

        try:
            pragmatic_result = await engine.run(sub_program, ctx)
        except BaseException as exc:  # Handle direct exceptions from the sub-program
            if isinstance(exc, SystemExit):
                raise
            actual_error = _unwrap_error(exc)
            return await _run_handler(actual_error)

        if isinstance(pragmatic_result.result, Err):
            error = _unwrap_error(pragmatic_result.result.error)
            return await _run_handler(error)

        return pragmatic_result.value

    async def handle_safe(
        self, effect: ResultSafeEffect, ctx: ExecutionContext, engine: "ProgramInterpreter"
    ) -> Any:
        """Handle result.safe effect by capturing the program outcome."""
        from doeff.program import Program

        sub_program = Program.from_program_like(effect.sub_program)

        pragmatic_result = await engine.run(sub_program, ctx)
        return pragmatic_result.result

    async def handle_first_success(
        self,
        effect: ResultFirstSuccessEffect,
        ctx: ExecutionContext,
        engine: "ProgramInterpreter",
    ) -> Any:
        """Handle sequential attempts, returning the first successful value."""

        from doeff.program import Program

        base_snapshot = ctx.copy()
        base_snapshot.effect_observations = list(ctx.effect_observations)

        last_error: Exception | None = None

        for program_like in effect.programs:
            candidate = Program.from_program_like(program_like)

            attempt_ctx = base_snapshot.copy()
            attempt_ctx.effect_observations = list(base_snapshot.effect_observations)

            pragmatic_result = await engine.run(candidate, attempt_ctx)

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
        ctx: ExecutionContext,
        engine: "ProgramInterpreter",
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
        self, effect: ResultRecoverEffect, ctx: ExecutionContext, engine: "ProgramInterpreter"
    ) -> Any:
        """Handle result.recover effect - try program, use fallback on error."""
        from doeff.program import Program
        import inspect
        
        sub_program = Program.from_program_like(effect.sub_program)

        pragmatic_result = await engine.run(sub_program, ctx)
        
        if isinstance(pragmatic_result.result, Err):
            error = pragmatic_result.result.error
            
            from doeff.types import EffectFailure
            if isinstance(error, EffectFailure):
                error = error.cause
            
            fallback = effect.fallback
            
            # Check if fallback is an error handler function
            # We need to distinguish between:
            # 1. Error handlers: callables that take an exception parameter
            # 2. Thunks: zero-argument callables that return Programs
            # 3. Programs: which are also callable
            # 4. KleisliPrograms: @do decorated functions (can be either 1 or 2)
            if callable(fallback) and not isinstance(fallback, Program):
                from doeff.kleisli import KleisliProgram
                
                # KleisliProgram needs special handling since inspect.signature
                # doesn't give us the real signature
                if isinstance(fallback, KleisliProgram):
                    # Try calling it as an error handler first
                    handler_result = fallback(error)
                    if isinstance(handler_result, (Program, EffectBase)):
                        handler_program = Program.from_program_like(handler_result)
                        try_result = await engine.run(handler_program, ctx)
                        if isinstance(try_result.result, Err):
                            # Check if the error is a TypeError about arguments
                            inner_error = try_result.result.error
                            from doeff.types import EffectFailure
                            if isinstance(inner_error, EffectFailure):
                                inner_error = inner_error.cause
                            if isinstance(inner_error, TypeError) and "positional argument" in str(inner_error):
                                # It failed because it doesn't accept an error arg
                                # Try as thunk instead
                                fallback = fallback()
                            else:
                                # Some other error - re-raise it
                                raise inner_error
                        else:
                            # Succeeded as error handler
                            return try_result.value
                    else:
                        # Somehow got a non-Program result
                        return handler_result
                else:
                    # Regular callable - check signature
                    try:
                        sig = inspect.signature(fallback)
                        # If it accepts at least one parameter, treat it as an error handler
                        if len(sig.parameters) > 0:
                            # It's an error handler - call it with the exception
                            handler_result = fallback(error)

                            if isinstance(handler_result, (Program, EffectBase)):
                                handler_program = Program.from_program_like(handler_result)
                                handler_pragmatic_result = await engine.run(handler_program, ctx)
                                return handler_pragmatic_result.value

                            return handler_result
                        else:
                            # It's a thunk (zero-argument callable) - call it
                            fallback = fallback()
                    except (ValueError, TypeError):
                        # Can't inspect signature, treat as thunk
                        fallback = fallback()
            
            if isinstance(fallback, (Program, EffectBase)):
                fallback_program = Program.from_program_like(fallback)
                fallback_result = await engine.run(fallback_program, ctx)
                return fallback_result.value

            return fallback
        else:
            # Success - return the value
            return pragmatic_result.value
    
    async def handle_retry(
        self, effect: ResultRetryEffect, ctx: ExecutionContext, engine: "ProgramInterpreter"
    ) -> Any:
        """Handle result.retry effect - retry program on failure."""
        import asyncio
        from doeff.program import Program
        from doeff._vendor import Ok
        
        max_attempts = effect.max_attempts
        delay_ms = effect.delay_ms
        
        sub_program = Program.from_program_like(effect.sub_program)
        
        last_error = None
        for attempt in range(max_attempts):
            pragmatic_result = await engine.run(sub_program, ctx)
            
            if isinstance(pragmatic_result.result, Ok):
                return pragmatic_result.value
            
            last_error = pragmatic_result.result.error
            
            if attempt < max_attempts - 1 and delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000.0)
        
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

    async def handle_snapshot(self, effect: GraphSnapshotEffect, ctx: ExecutionContext):
        """Return the current computation graph."""
        return ctx.graph

    async def handle_capture(
        self,
        effect: GraphCaptureEffect,
        ctx: ExecutionContext,
        engine: "ProgramInterpreter",
    ) -> tuple[Any, WGraph]:
        from doeff.program import Program

        sub_program = Program.from_program_like(effect.program)

        sub_ctx = ExecutionContext(
            env=ctx.env.copy() if ctx.env else {},
            state=ctx.state.copy() if ctx.state else {},
            log=[],
            graph=WGraph(
                last=WStep(inputs=tuple(), output=WNode("_root"), meta={}),
                steps=frozenset(),
            ),
            io_allowed=ctx.io_allowed,
            cache=ctx.cache,
        )

        result = await engine.run(sub_program, sub_ctx)

        if isinstance(result.result, Err):
            raise result.result.error

        ctx.state.update(result.context.state)
        ctx.log.extend(result.context.log)

        return result.value, result.context.graph



class MemoEffectHandler:
    """In-memory memoization handler."""

    scope = HandlerScope.SHARED

    def _serialize_key(self, key: Any) -> str:
        key_bytes = cloudpickle.dumps(key)
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
        key_bytes = cloudpickle.dumps(key)
        key_blob = lzma.compress(key_bytes)
        key_hash = hashlib.sha256(key_blob).hexdigest()
        return key_hash, key_blob

    async def handle_get(self, effect: CacheGetEffect, ctx: ExecutionContext) -> Any:
        key_hash, _ = self._serialize_key(effect.key)
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
    "HandlerScope",
    "ReaderEffectHandler",
    "StateEffectHandler",
    "WriterEffectHandler",
    "FutureEffectHandler",
    "ResultEffectHandler",
    "IOEffectHandler",
    "GraphEffectHandler",
    "MemoEffectHandler",
    "CacheEffectHandler",
]
