"""
Effect handlers for the doeff system.

This module contains handler classes for each effect category.
Each handler is responsible for interpreting specific effects.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

from doeff._vendor import Ok, Err, WNode, WStep, WGraph
from doeff.types import ExecutionContext, ListenResult

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
        self, payload: Dict, ctx: ExecutionContext, engine: "ProgramInterpreter"
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

    async def handle_put(self, payload: Dict, ctx: ExecutionContext) -> None:
        """Handle state.put effect."""
        ctx.state[payload["key"]] = payload["value"]

    async def handle_modify(self, payload: Dict, ctx: ExecutionContext) -> Any:
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
        self, awaitables: Tuple[Awaitable[Any], ...]
    ) -> List[Any]:
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
        self, payload: Dict, ctx: ExecutionContext, engine: "ProgramInterpreter"
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
        except Exception as e:
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


class CacheEffectHandler:
    """Handles Cache effects for memoization."""
    scope = HandlerScope.SHARED  # Cache MUST be shared across parallel executions

    def __init__(self):
        """Initialize the cache handler."""
        import time
        self._time = time.time

    def _serialize_key(self, key: Any) -> str:
        """Serialize any key to a string for internal cache storage.
        
        Handles tuples, FrozenDicts, and other serializable objects.
        """
        import json
        import hashlib
        
        # Convert key to a JSON-serializable format
        def make_serializable(obj):
            if isinstance(obj, (str, int, float, bool, type(None))):
                return obj
            elif isinstance(obj, (tuple, list)):
                return ["__tuple__" if isinstance(obj, tuple) else "__list__", 
                        [make_serializable(item) for item in obj]]
            elif hasattr(obj, '__dict__'):  # FrozenDict and similar
                return {"__type__": type(obj).__name__, 
                        "data": {k: make_serializable(v) for k, v in obj.items()}}
            elif isinstance(obj, dict):
                return {k: make_serializable(v) for k, v in obj.items()}
            else:
                # Fallback to string representation
                return str(obj)
        
        try:
            serializable = make_serializable(key)
            key_str = json.dumps(serializable, sort_keys=True)
        except (TypeError, ValueError):
            # Fallback to string representation
            key_str = str(key)
        
        # Create a hash for consistent, shorter keys
        hash_obj = hashlib.md5(key_str.encode())
        return f"cache:{hash_obj.hexdigest()}"

    async def handle_get(self, key: Any, ctx: ExecutionContext) -> Any:
        """Handle cache.get effect.
        
        Raises KeyError if the key is not in cache or has expired.
        """
        serialized_key = self._serialize_key(key)
        
        if serialized_key not in ctx.cache:
            raise KeyError(f"Cache miss for key")
        
        value, expiry = ctx.cache[serialized_key]
        
        # Check if expired
        if expiry is not None and self._time() > expiry:
            del ctx.cache[serialized_key]
            raise KeyError(f"Cache expired for key")
        
        return value

    async def handle_put(self, payload: Dict, ctx: ExecutionContext) -> None:
        """Handle cache.put effect."""
        key = payload["key"]
        value = payload["value"]
        ttl = payload.get("ttl")
        
        serialized_key = self._serialize_key(key)
        
        # Calculate expiry time if TTL is provided
        expiry = None
        if ttl is not None and ttl > 0:
            expiry = self._time() + ttl
        
        ctx.cache[serialized_key] = (value, expiry)


__all__ = [
    "HandlerScope",
    "ReaderEffectHandler",
    "StateEffectHandler",
    "WriterEffectHandler",
    "FutureEffectHandler",
    "ResultEffectHandler",
    "IOEffectHandler",
    "GraphEffectHandler",
    "CacheEffectHandler",
]