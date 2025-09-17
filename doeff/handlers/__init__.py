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
from typing import TYPE_CHECKING, Any, Optional, Dict

import hashlib
import json
import lzma
import os
import pickle
import sqlite3
import tempfile
from pathlib import Path

import cloudpickle

from doeff._vendor import Err, Ok, WGraph, WNode, WStep
from doeff.types import ExecutionContext, ListenResult
from doeff.cache_policy import CachePolicy, CacheStorage, ensure_cache_policy

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

    async def handle_ask(self, key: str, ctx: ExecutionContext) -> Any:
        """Handle reader.ask effect."""
        if key not in ctx.env:
            raise KeyError(f"Missing environment key: {key}")
        return ctx.env[key]

    async def handle_local(
        self, payload: dict, ctx: ExecutionContext, engine: "ProgramInterpreter"
    ) -> Any:
        """Handle reader.local effect."""
        sub_ctx = ctx.copy()
        sub_ctx.env.update(payload["env"])
        # Check if payload["program"] is already a Program or a callable
        sub_program = payload["program"]
        if callable(sub_program) and not isinstance(sub_program, Program):
            # It's a thunk, call it to get the Program
            sub_program = sub_program()
        pragmatic_result = await engine.run(sub_program, sub_ctx)
        # Return the value from the sub-program
        return pragmatic_result.value


class StateEffectHandler:
    """Handles State monad effects."""
    scope = HandlerScope.ISOLATED  # Each parallel execution gets its own state

    async def handle_get(self, key: str, ctx: ExecutionContext) -> Any:
        """Handle state.get effect."""
        return ctx.state.get(key)

    async def handle_put(self, payload: dict, ctx: ExecutionContext) -> None:
        """Handle state.put effect."""
        ctx.state[payload["key"]] = payload["value"]

    async def handle_modify(self, payload: dict, ctx: ExecutionContext) -> Any:
        """Handle state.modify effect."""
        key = payload["key"]
        old_value = ctx.state.get(key)
        new_value = payload["func"](old_value)
        ctx.state[key] = new_value
        return new_value


class WriterEffectHandler:
    """Handles Writer monad effects."""
    scope = HandlerScope.ISOLATED  # Each parallel execution gets its own log

    async def handle_tell(self, message: Any, ctx: ExecutionContext) -> None:
        """Handle writer.tell effect."""
        ctx.log.append(message)

    async def handle_listen(
        self,
        sub_program_func: Callable,
        ctx: ExecutionContext,
        engine: "ProgramInterpreter",
    ) -> ListenResult:
        """Handle writer.listen effect."""
        # Import here to avoid circular import
        from doeff.program import Program
        
        # Check if it's already a Program or a callable
        sub_program = sub_program_func
        if callable(sub_program) and not isinstance(sub_program, Program):
            # It's a thunk, call it to get the Program
            sub_program = sub_program()
        sub_ctx = ctx.copy()
        sub_ctx.log = []  # Fresh log for sub-program
        pragmatic_result = await engine.run(sub_program, sub_ctx)
        # Return both the value and the sub-program's log
        return ListenResult(value=pragmatic_result.value, log=sub_ctx.log)


class FutureEffectHandler:
    """Handles Future monad effects."""
    scope = HandlerScope.SHARED  # Async operations are stateless

    async def handle_await(self, awaitable: Awaitable[Any]) -> Any:
        """Handle future.await effect."""
        return await awaitable

    async def handle_parallel(
        self, awaitables: tuple[Awaitable[Any], ...]
    ) -> list[Any]:
        """Handle future.parallel effect."""
        results = await asyncio.gather(*awaitables)
        return results


class ResultEffectHandler:
    """Handles Result monad effects."""
    scope = HandlerScope.SHARED  # Error handling is stateless

    async def handle_fail(self, exc: Exception) -> None:
        """Handle result.fail effect."""
        raise exc

    async def handle_catch(
        self, payload: dict, ctx: ExecutionContext, engine: "ProgramInterpreter"
    ) -> Any:
        """Handle result.catch effect."""
        # Import here to avoid circular import
        from doeff.program import Program
        
        # Check if payload["program"] is already a Program or a callable
        sub_program = payload["program"]
        if callable(sub_program) and not isinstance(sub_program, Program):
            # It's a thunk, call it to get the Program
            sub_program = sub_program()

        try:
            pragmatic_result = await engine.run(sub_program, ctx)
            if isinstance(pragmatic_result.result, Err):
                # Extract the actual exception
                error = pragmatic_result.result.error

                # Unwrap EffectFailure to get the original cause
                from doeff.types import EffectFailure
                if isinstance(error, EffectFailure):
                    error = error.cause

                # Run error handler with the unwrapped exception
                handler_result = payload["handler"](error)

                # If handler returned a Program, run it
                if isinstance(handler_result, Program):
                    handler_pragmatic_result = await engine.run(handler_result, ctx)
                    return handler_pragmatic_result.value
                else:
                    # Handler returned a direct value
                    return handler_result
            else:
                # Success - return the value
                return pragmatic_result.value
        except BaseException as e:
            if isinstance(e, SystemExit):
                raise
            # Unwrap EffectFailure to get the original cause
            from doeff.types import EffectFailure
            actual_error = e
            if isinstance(actual_error, EffectFailure):
                actual_error = actual_error.cause

            # Run error handler with unwrapped exception
            handler_result = payload["handler"](actual_error)

            # If handler returned a Program, run it
            if isinstance(handler_result, Program):
                handler_pragmatic_result = await engine.run(handler_result, ctx)
                return handler_pragmatic_result.value
            else:
                # Handler returned a direct value
                return handler_result
    
    async def handle_recover(
        self, payload: Dict, ctx: ExecutionContext, engine: "ProgramInterpreter"
    ) -> Any:
        """Handle result.recover effect - try program, use fallback on error."""
        # Import here to avoid circular import
        from doeff.program import Program
        import inspect
        
        # Check if payload["program"] is already a Program or a callable
        sub_program = payload["program"]
        if callable(sub_program) and not isinstance(sub_program, Program):
            # It's a thunk, call it to get the Program
            sub_program = sub_program()
        
        # Try to run the program
        pragmatic_result = await engine.run(sub_program, ctx)
        
        if isinstance(pragmatic_result.result, Err):
            # Error occurred, extract the actual exception
            error = pragmatic_result.result.error
            
            # Unwrap EffectFailure to get the original cause
            from doeff.types import EffectFailure
            if isinstance(error, EffectFailure):
                error = error.cause
            
            # Get the fallback
            fallback = payload["fallback"]
            
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
                    # This will return a Program, try to run it
                    if isinstance(handler_result, Program):
                        # Run it and see if it fails with TypeError
                        try_result = await engine.run(handler_result, ctx)
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
                            
                            # If handler returned a Program, run it
                            if isinstance(handler_result, Program):
                                handler_pragmatic_result = await engine.run(handler_result, ctx)
                                return handler_pragmatic_result.value
                            else:
                                # Handler returned a direct value
                                return handler_result
                        else:
                            # It's a thunk (zero-argument callable) - call it
                            fallback = fallback()
                    except (ValueError, TypeError):
                        # Can't inspect signature, treat as thunk
                        fallback = fallback()
            
            # If fallback is a Program, run it
            if isinstance(fallback, Program):
                fallback_result = await engine.run(fallback, ctx)
                return fallback_result.value
            else:
                # Fallback is a direct value
                return fallback
        else:
            # Success - return the value
            return pragmatic_result.value
    
    async def handle_retry(
        self, payload: Dict, ctx: ExecutionContext, engine: "ProgramInterpreter"
    ) -> Any:
        """Handle result.retry effect - retry program on failure."""
        import asyncio
        from doeff.program import Program
        from doeff._vendor import Ok
        
        max_attempts = payload.get("max_attempts", 3)
        delay_ms = payload.get("delay_ms", 0)
        
        # Check if payload["program"] is already a Program or a callable
        sub_program = payload["program"]
        if callable(sub_program) and not isinstance(sub_program, Program):
            # It's a thunk, call it to get the Program
            sub_program = sub_program()
        
        last_error = None
        for attempt in range(max_attempts):
            # Try to run the program
            pragmatic_result = await engine.run(sub_program, ctx)
            
            if isinstance(pragmatic_result.result, Ok):
                # Success - return the value
                return pragmatic_result.value
            
            # Store the last error
            last_error = pragmatic_result.result.error
            
            # If not the last attempt, wait before retrying
            if attempt < max_attempts - 1 and delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000.0)
        
        # All attempts failed, raise the last error
        if last_error:
            # Re-raise the original error (it's already wrapped in TraceError)
            raise last_error
        else:
            raise RuntimeError(f"All {max_attempts} attempts failed")


class IOEffectHandler:
    """Handles IO monad effects."""
    scope = HandlerScope.SHARED  # IO operations share permissions

    async def handle_run(self, action: Callable[[], Any], ctx: ExecutionContext) -> Any:
        """Handle io.run effect."""
        if not ctx.io_allowed:
            raise PermissionError("IO not allowed in this context")
        return action()

    async def handle_print(self, message: str, ctx: ExecutionContext) -> None:
        """Handle io.print effect."""
        if not ctx.io_allowed:
            raise PermissionError("IO not allowed in this context")
        print(message)


class GraphEffectHandler:
    """Handles Graph effects for SGFR compatibility."""
    scope = HandlerScope.ISOLATED  # Each parallel execution tracks its own graph

    async def handle_step(self, payload: Dict, ctx: ExecutionContext) -> Any:
        """Handle graph.step effect."""
        value = payload["value"]
        meta = payload.get("meta", {})

        # Update graph
        new_node = WNode(value)
        new_step = WStep(inputs=(ctx.graph.last.output,), output=new_node, meta=meta)
        ctx.graph = WGraph(last=new_step, steps=ctx.graph.steps | {new_step})
        return value

    async def handle_annotate(
        self, meta: Dict[str, Any], ctx: ExecutionContext
    ) -> None:
        """Handle graph.annotate effect."""
        ctx.graph = ctx.graph.with_last_meta(meta)

    async def handle_snapshot(self, payload: Dict | None, ctx: ExecutionContext):
        """Return the current computation graph."""
        return ctx.graph



class MemoEffectHandler:
    """In-memory memoization handler."""

    scope = HandlerScope.SHARED

    def _serialize_key(self, key: Any) -> str:
        def make_serializable(obj: Any):
            if isinstance(obj, (str, int, float, bool, type(None))):
                return obj
            if isinstance(obj, (tuple, list)):
                return [
                    "__tuple__" if isinstance(obj, tuple) else "__list__",
                    [make_serializable(item) for item in obj],
                ]
            if hasattr(obj, "items"):
                return {str(k): make_serializable(v) for k, v in obj.items()}
            return str(obj)

        serializable = make_serializable(key)
        key_str = json.dumps(serializable, sort_keys=True)
        return hashlib.md5(key_str.encode()).hexdigest()

    async def handle_get(self, key: Any, ctx: ExecutionContext) -> Any:
        serialized_key = self._serialize_key(key)
        if serialized_key not in ctx.cache:
            raise KeyError("Memo miss for key")
        return ctx.cache[serialized_key]

    async def handle_put(self, payload: Dict, ctx: ExecutionContext) -> None:
        serialized_key = self._serialize_key(payload["key"])
        ctx.cache[serialized_key] = payload["value"]


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

    async def handle_get(self, key: Any, ctx: ExecutionContext) -> Any:
        key_hash, _ = self._serialize_key(key)
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

    async def handle_put(self, payload: Dict, ctx: ExecutionContext) -> None:
        key = payload["key"]
        value = payload["value"]
        policy = payload.get("policy")
        if not isinstance(policy, CachePolicy):
            policy = ensure_cache_policy(policy=policy)

        ttl = payload.get("ttl", policy.ttl)
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
