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
        
        # Check if payload["program"] is already a Program or a callable
        sub_program = payload["program"]
        if callable(sub_program) and not isinstance(sub_program, Program):
            # It's a thunk, call it to get the Program
            sub_program = sub_program()

        try:
            pragmatic_result = await engine.run(sub_program, ctx)
            if isinstance(pragmatic_result.result, Err):
                # Run error handler - handler should return a Program
                handler_result = payload["handler"](pragmatic_result.result.error)
                
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
            # Run error handler
            handler_result = payload["handler"](e)
            
            # If handler returned a Program, run it
            if isinstance(handler_result, Program):
                handler_pragmatic_result = await engine.run(handler_result, ctx)
                return handler_pragmatic_result.value
            else:
                # Handler returned a direct value
                return handler_result


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


__all__ = [
    "ReaderEffectHandler",
    "StateEffectHandler",
    "WriterEffectHandler",
    "FutureEffectHandler",
    "ResultEffectHandler",
    "IOEffectHandler",
    "GraphEffectHandler",
]