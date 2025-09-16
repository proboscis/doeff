"""
Program interpreter for the doeff system.

This module contains the main ProgramInterpreter that executes programs
by handling effects through the registered handlers.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, TypeVar

from doeff._vendor import Ok, Err, Result, trace_err, WGraph, WNode, WStep
from doeff.types import Effect, ExecutionContext, RunResult
from doeff.program import Program
from doeff.handlers import (
    ReaderEffectHandler,
    StateEffectHandler,
    WriterEffectHandler,
    FutureEffectHandler,
    ResultEffectHandler,
    IOEffectHandler,
    GraphEffectHandler,
)

T = TypeVar("T")


def force_eval(prog: Program[T]) -> Program[T]:
    """
    Force evaluation of nested Programs to prevent stack overflow.
    
    This is critical for stack safety with deep monadic computations.
    Python's recursion limit (~1000 frames) requires trampolining.
    """
    def forced_generator():
        gen = prog.generator_func()
        try:
            current = next(gen)
            while True:
                # If current is a Program, force evaluate it
                if isinstance(current, Program):
                    current = force_eval(current)
                value = yield current
                current = gen.send(value)
        except StopIteration as e:
            return e.value
    
    return Program(forced_generator)


class ProgramInterpreter:
    """
    Engine that handles all monad types according to our pragmatic contract.

    Uses separate handler classes for each effect category to maintain
    single responsibility and reduce complexity.
    """

    def __init__(self):
        """Initialize effect handlers."""
        self.reader_handler = ReaderEffectHandler()
        self.state_handler = StateEffectHandler()
        self.writer_handler = WriterEffectHandler()
        self.future_handler = FutureEffectHandler()
        self.result_handler = ResultEffectHandler()
        self.io_handler = IOEffectHandler()
        self.graph_handler = GraphEffectHandler()

        # Dispatch table
        self._dispatchers = {
            "reader.ask": self._dispatch_reader_ask,
            "reader.local": self._dispatch_reader_local,
            "state.get": self._dispatch_state_get,
            "state.put": self._dispatch_state_put,
            "state.modify": self._dispatch_state_modify,
            "writer.tell": self._dispatch_writer_tell,
            "writer.listen": self._dispatch_writer_listen,
            "future.await": self._dispatch_future_await,
            "future.parallel": self._dispatch_future_parallel,
            "result.fail": self._dispatch_result_fail,
            "result.catch": self._dispatch_result_catch,
            "result.recover": self._dispatch_result_recover,
            "result.retry": self._dispatch_result_retry,
            "io.run": self._dispatch_io_run,
            "io.perform": self._dispatch_io_run,  # Alias
            "io.print": self._dispatch_io_print,
            "graph.step": self._dispatch_graph_step,
            "graph.annotate": self._dispatch_graph_annotate,
            "program.gather": self._dispatch_program_gather,
            "gather.gather": self._dispatch_program_gather,  # Alias
            "program.gather_dict": self._dispatch_program_gather_dict,
            "gather.gather_dict": self._dispatch_program_gather_dict,  # Alias
            "dep.inject": self._dispatch_dep_inject,
        }

    async def run(
        self, program: Program[T], context: Optional[ExecutionContext] = None
    ) -> RunResult[T]:
        """
        Run a program with full monad support.

        Returns a RunResult[T] containing:
        - context: final execution context (state, log, graph)
        - result: Ok(value) or Err(error)
        """
        ctx = context or ExecutionContext(
            env={},
            state={},
            log=[],
            graph=WGraph(
                last=WStep(inputs=tuple(), output=WNode("_root"), meta={}),
                steps=frozenset(),
            ),
            io_allowed=True,
        )

        try:
            # Force evaluate the program for stack safety
            program = force_eval(program)
            
            # Create generator
            gen = program.generator_func()
            
            # Start the generator
            try:
                current = next(gen)
            except StopIteration as e:
                # Immediate return
                return RunResult(ctx, Ok(e.value))
            
            # Process effects
            while True:
                if isinstance(current, Effect):
                    # Handle the effect
                    try:
                        value = await self._handle_effect(current, ctx)
                    except Exception as exc:
                        return RunResult(ctx, Err(trace_err(exc)))
                    
                    # Send value back
                    try:
                        current = gen.send(value)
                    except StopIteration as e:
                        return RunResult(ctx, Ok(e.value))
                    
                elif isinstance(current, Program):
                    # Sub-program - run it recursively
                    sub_result = await self.run(current, ctx)
                    if isinstance(sub_result.result, Err):
                        return sub_result
                    
                    # Update context with sub-program changes
                    ctx = sub_result.context
                    
                    # Send sub-program result back
                    try:
                        current = gen.send(sub_result.value)
                    except StopIteration as e:
                        return RunResult(ctx, Ok(e.value))
                else:
                    # Unknown yield type
                    return RunResult(
                        ctx,
                        Err(trace_err(TypeError(f"Unknown yield type: {type(current)}")))
                    )
                    
        except Exception as exc:
            return RunResult(ctx, Err(trace_err(exc)))

    async def _handle_effect(self, effect: Effect, ctx: ExecutionContext) -> Any:
        """Dispatch effect to appropriate handler."""
        dispatcher = self._dispatchers.get(effect.tag)
        if dispatcher:
            return await dispatcher(effect.payload, ctx)
        else:
            raise ValueError(f"Unknown effect tag: {effect.tag}")

    # Reader dispatchers
    async def _dispatch_reader_ask(self, payload: Any, ctx: ExecutionContext) -> Any:
        return await self.reader_handler.handle_ask(payload, ctx)

    async def _dispatch_reader_local(self, payload: Any, ctx: ExecutionContext) -> Any:
        return await self.reader_handler.handle_local(payload, ctx, self)

    # State dispatchers
    async def _dispatch_state_get(self, payload: Any, ctx: ExecutionContext) -> Any:
        return await self.state_handler.handle_get(payload, ctx)

    async def _dispatch_state_put(self, payload: Any, ctx: ExecutionContext) -> Any:
        return await self.state_handler.handle_put(payload, ctx)

    async def _dispatch_state_modify(self, payload: Any, ctx: ExecutionContext) -> Any:
        return await self.state_handler.handle_modify(payload, ctx)

    # Writer dispatchers
    async def _dispatch_writer_tell(self, payload: Any, ctx: ExecutionContext) -> Any:
        return await self.writer_handler.handle_tell(payload, ctx)

    async def _dispatch_writer_listen(self, payload: Any, ctx: ExecutionContext) -> Any:
        return await self.writer_handler.handle_listen(payload, ctx, self)

    # Future dispatchers
    async def _dispatch_future_await(self, payload: Any, ctx: ExecutionContext) -> Any:
        return await self.future_handler.handle_await(payload)

    async def _dispatch_future_parallel(self, payload: Any, ctx: ExecutionContext) -> Any:
        return await self.future_handler.handle_parallel(payload)

    # Result dispatchers
    async def _dispatch_result_fail(self, payload: Any, ctx: ExecutionContext) -> Any:
        return await self.result_handler.handle_fail(payload)

    async def _dispatch_result_catch(self, payload: Any, ctx: ExecutionContext) -> Any:
        return await self.result_handler.handle_catch(payload, ctx, self)
    
    async def _dispatch_result_recover(self, payload: Any, ctx: ExecutionContext) -> Any:
        return await self.result_handler.handle_recover(payload, ctx, self)
    
    async def _dispatch_result_retry(self, payload: Any, ctx: ExecutionContext) -> Any:
        return await self.result_handler.handle_retry(payload, ctx, self)

    # IO dispatchers
    async def _dispatch_io_run(self, payload: Any, ctx: ExecutionContext) -> Any:
        return await self.io_handler.handle_run(payload, ctx)

    async def _dispatch_io_print(self, payload: Any, ctx: ExecutionContext) -> Any:
        return await self.io_handler.handle_print(payload, ctx)

    # Graph dispatchers
    async def _dispatch_graph_step(self, payload: Any, ctx: ExecutionContext) -> Any:
        return await self.graph_handler.handle_step(payload, ctx)

    async def _dispatch_graph_annotate(self, payload: Any, ctx: ExecutionContext) -> Any:
        return await self.graph_handler.handle_annotate(payload, ctx)

    # Program dispatchers for Gather effects
    async def _dispatch_program_gather(self, payload: Any, ctx: ExecutionContext) -> Any:
        """Handle program.gather and gather.gather effects.
        
        Each program runs with an isolated copy of the context to simulate
        parallel execution. State changes are merged at the end with 
        last-write-wins semantics.
        """
        programs = payload
        results = []
        sub_contexts = []
        
        # Run all programs with isolated contexts
        error_to_raise = None
        for prog in programs:
            # Check if it's a thunk
            if callable(prog) and not isinstance(prog, Program):
                prog = prog()
            # Run each program with a copy of the current context for isolation
            # This simulates parallel execution where each program starts with
            # the same initial state
            ctx_copy = ExecutionContext(
                env=ctx.env.copy() if ctx.env else {},
                state=ctx.state.copy() if ctx.state else {},
                log=[],  # Each parallel program starts with empty log
                graph=ctx.graph,  # Graph is immutable
                io_allowed=ctx.io_allowed,
            )
            sub_result = await self.run(prog, ctx_copy)
            sub_contexts.append(sub_result.context)
            if isinstance(sub_result.result, Err):
                # Store error to raise after merging logs
                if error_to_raise is None:
                    error_to_raise = sub_result.result.error
                # Still collect the context even on error
            else:
                results.append(sub_result.value)
        
        # Merge all state changes and logs at the end
        for sub_ctx in sub_contexts:
            # Merge state changes with last-write-wins semantics
            ctx.state.update(sub_ctx.state)
            # Append sub-program logs
            ctx.log.extend(sub_ctx.log)
        
        # Raise error after merging if one occurred
        if error_to_raise is not None:
            raise error_to_raise
            
        return results

    async def _dispatch_program_gather_dict(self, payload: Any, ctx: ExecutionContext) -> Any:
        """Handle program.gather_dict and gather.gather_dict effects.
        
        Each program runs with an isolated copy of the context to simulate
        parallel execution. State changes are merged at the end with 
        last-write-wins semantics.
        """
        programs_dict = payload
        results = {}
        sub_contexts = []
        
        # Run all programs with isolated contexts
        error_to_raise = None
        for key, prog in programs_dict.items():
            # Check if it's a thunk
            if callable(prog) and not isinstance(prog, Program):
                prog = prog()
            # Run each program with a copy of the current context for isolation
            ctx_copy = ExecutionContext(
                env=ctx.env.copy() if ctx.env else {},
                state=ctx.state.copy() if ctx.state else {},
                log=[],  # Each parallel program starts with empty log
                graph=ctx.graph,  # Graph is immutable
                io_allowed=ctx.io_allowed,
            )
            sub_result = await self.run(prog, ctx_copy)
            sub_contexts.append(sub_result.context)
            if isinstance(sub_result.result, Err):
                # Store error to raise after merging logs
                if error_to_raise is None:
                    error_to_raise = sub_result.result.error
                # Still collect the context even on error
            else:
                results[key] = sub_result.value
        
        # Merge all state changes and logs at the end
        for sub_ctx in sub_contexts:
            # Merge state changes with last-write-wins semantics  
            ctx.state.update(sub_ctx.state)
            # Append sub-program logs
            ctx.log.extend(sub_ctx.log)
        
        # Raise error after merging if one occurred
        if error_to_raise is not None:
            raise error_to_raise
            
        return results

    # Dependency injection dispatcher
    async def _dispatch_dep_inject(self, payload: Any, ctx: ExecutionContext) -> Any:
        """Handle dep.inject effect - same as reader.ask."""
        return await self.reader_handler.handle_ask(payload, ctx)


__all__ = ["ProgramInterpreter", "force_eval"]