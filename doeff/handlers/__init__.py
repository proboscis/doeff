"""
Effect handlers for the doeff system.

This module contains handler classes for each effect category.
Each handler is responsible for interpreting specific effects.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

from doeff._vendor import Ok, Err, WNode, WStep, WGraph
from doeff.types import ExecutionContext, ListenResult

if TYPE_CHECKING:
    from doeff.interpreter import ProgramInterpreter

# Need Program at runtime for isinstance checks
from doeff.program import Program


class ReaderEffectHandler:
    """Handles Reader monad effects."""

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

    async def handle_fail(self, exc: Exception) -> None:
        """Handle result.fail effect."""
        raise exc

    async def handle_catch(
        self, payload: Dict, ctx: ExecutionContext, engine: "ProgramInterpreter"
    ) -> Any:
        """Handle result.catch effect."""
        # Import here to avoid circular import
        from doeff.program import Program
        from doeff._vendor import TraceError
        
        # Check if payload["program"] is already a Program or a callable
        sub_program = payload["program"]
        if callable(sub_program) and not isinstance(sub_program, Program):
            # It's a thunk, call it to get the Program
            sub_program = sub_program()

        try:
            pragmatic_result = await engine.run(sub_program, ctx)
            if isinstance(pragmatic_result.result, Err):
                # Extract the actual exception from TraceError if present
                error = pragmatic_result.result.error
                if isinstance(error, TraceError):
                    error = error.exc
                
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
            # Unwrap TraceError if present
            actual_error = e.exc if isinstance(e, TraceError) else e
            
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
        
        # Check if payload["program"] is already a Program or a callable
        sub_program = payload["program"]
        if callable(sub_program) and not isinstance(sub_program, Program):
            # It's a thunk, call it to get the Program
            sub_program = sub_program()
        
        # Try to run the program
        pragmatic_result = await engine.run(sub_program, ctx)
        
        if isinstance(pragmatic_result.result, Err):
            # Error occurred, use fallback
            fallback = payload["fallback"]
            
            # If fallback is a callable (thunk), call it
            if callable(fallback) and not isinstance(fallback, Program):
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
        from doeff._vendor import TraceError, Ok
        
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

    def __init__(self):
        """Initialize the cache handler with an in-memory cache."""
        import time
        import hashlib
        import json
        self._cache: Dict[str, Tuple[Any, Optional[float]]] = {}  # serialized_key -> (value, expiry_time)
        self._time = time.time
        self._hashlib = hashlib
        self._json = json

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
        
        if serialized_key not in self._cache:
            raise KeyError(f"Cache miss for key")
        
        value, expiry = self._cache[serialized_key]
        
        # Check if expired
        if expiry is not None and self._time() > expiry:
            del self._cache[serialized_key]
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
        
        self._cache[serialized_key] = (value, expiry)


__all__ = [
    "ReaderEffectHandler",
    "StateEffectHandler",
    "WriterEffectHandler",
    "FutureEffectHandler",
    "ResultEffectHandler",
    "IOEffectHandler",
    "GraphEffectHandler",
    "CacheEffectHandler",
]