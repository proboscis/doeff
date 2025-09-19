"""
Program interpreter for the doeff system.

This module contains the main ProgramInterpreter that executes programs
by handling effects through the registered handlers.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Any, TypeVar

from doeff._vendor import Err, Ok, WGraph, WNode, WStep
from doeff.effects import (
    AskEffect,
    CacheGetEffect,
    CachePutEffect,
    DepInjectEffect,
    FutureAwaitEffect,
    FutureParallelEffect,
    GatherDictEffect,
    GatherEffect,
    GraphAnnotateEffect,
    GraphSnapshotEffect,
    GraphStepEffect,
    IOPerformEffect,
    IOPrintEffect,
    LocalEffect,
    MemoGetEffect,
    MemoPutEffect,
    ResultCatchEffect,
    ResultFailEffect,
    ResultRecoverEffect,
    ResultRetryEffect,
    ResultUnwrapEffect,
    StateGetEffect,
    StateModifyEffect,
    StatePutEffect,
    WriterListenEffect,
    WriterTellEffect,
)
from doeff.handlers import (
    CacheEffectHandler,
    FutureEffectHandler,
    GraphEffectHandler,
    IOEffectHandler,
    MemoEffectHandler,
    ReaderEffectHandler,
    ResultEffectHandler,
    StateEffectHandler,
    WriterEffectHandler,
)
from doeff.program import Program
from doeff.types import Effect, EffectFailure, ExecutionContext, RunResult


def _effect_is(effect: Effect, cls) -> bool:
    """Return True if effect is instance of cls, tolerant to module reloads."""
    return isinstance(effect, cls) or effect.__class__.__name__ == cls.__name__


T = TypeVar("T")

logger = logging.getLogger(__name__)


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
    
    Effect handlers can be customized by passing custom handlers to __init__.
    """

    def __init__(self, custom_handlers: dict[str, Any] | None = None):
        """Initialize effect handlers.
        
        Args:
            custom_handlers: Optional dict mapping effect categories to custom handlers.
                           Keys can be: 'reader', 'state', 'writer', 'future', 'result',
                           'io', 'graph', 'memo', 'cache'.
                           Values should be handler instances with appropriate handle_* methods.
        """
        # Initialize default handlers
        handlers = {
            "reader": ReaderEffectHandler(),
            "state": StateEffectHandler(),
            "writer": WriterEffectHandler(),
            "future": FutureEffectHandler(),
            "result": ResultEffectHandler(),
            "io": IOEffectHandler(),
            "graph": GraphEffectHandler(),
            "memo": MemoEffectHandler(),
            "cache": CacheEffectHandler(),
        }

        # Override with custom handlers if provided
        if custom_handlers:
            handlers.update(custom_handlers)

        # Set handlers as attributes for backward compatibility
        self.reader_handler = handlers["reader"]
        self.state_handler = handlers["state"]
        self.writer_handler = handlers["writer"]
        self.future_handler = handlers["future"]
        self.result_handler = handlers["result"]
        self.io_handler = handlers["io"]
        self.graph_handler = handlers["graph"]
        self.memo_handler = handlers["memo"]
        self.cache_handler = handlers["cache"]


    async def run(
        self, program: Program[T], context: ExecutionContext | None = None
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
            #program = force_eval(program)

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
                logger.debug(f"effect: {current}")
                if isinstance(current, Effect):
                    # Handle the effect
                    try:
                        value = await self._handle_effect(current, ctx)
                    except Exception as exc:
                        # Create an EffectFailure with both runtime and creation context
                        # Capture the runtime traceback now while we have it
                        runtime_tb = "".join(
                            traceback.format_exception(
                                exc.__class__, exc, exc.__traceback__
                            )
                        ) if hasattr(exc, "__traceback__") else None

                        effect_failure = EffectFailure(
                            effect=current,
                            cause=exc,
                            runtime_traceback=runtime_tb,
                            creation_context=current.created_at,
                        )
                        return RunResult(ctx, Err(effect_failure))

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
                        Err(TypeError(f"Unknown yield type: {type(current)}"))
                    )

        except Exception as exc:
            return RunResult(ctx, Err(exc))

    async def _handle_effect(self, effect: Effect, ctx: ExecutionContext) -> Any:
        """Dispatch effect to appropriate handler."""

        if _effect_is(effect, AskEffect):
            return await self.reader_handler.handle_ask(effect, ctx)
        if _effect_is(effect, LocalEffect):
            return await self.reader_handler.handle_local(effect, ctx, self)

        if _effect_is(effect, StateGetEffect):
            return await self.state_handler.handle_get(effect, ctx)
        if _effect_is(effect, StatePutEffect):
            return await self.state_handler.handle_put(effect, ctx)
        if _effect_is(effect, StateModifyEffect):
            return await self.state_handler.handle_modify(effect, ctx)

        if _effect_is(effect, WriterTellEffect):
            return await self.writer_handler.handle_tell(effect, ctx)
        if _effect_is(effect, WriterListenEffect):
            return await self.writer_handler.handle_listen(effect, ctx, self)

        if _effect_is(effect, FutureAwaitEffect):
            return await self.future_handler.handle_await(effect)
        if _effect_is(effect, FutureParallelEffect):
            return await self.future_handler.handle_parallel(effect)

        if _effect_is(effect, ResultFailEffect):
            return await self.result_handler.handle_fail(effect)
        if _effect_is(effect, ResultCatchEffect):
            return await self.result_handler.handle_catch(effect, ctx, self)
        if _effect_is(effect, ResultRecoverEffect):
            return await self.result_handler.handle_recover(effect, ctx, self)
        if _effect_is(effect, ResultRetryEffect):
            return await self.result_handler.handle_retry(effect, ctx, self)
        if _effect_is(effect, ResultUnwrapEffect):
            return await self.result_handler.handle_unwrap(effect, ctx, self)

        if _effect_is(effect, IOPerformEffect):
            return await self.io_handler.handle_run(effect, ctx)
        if _effect_is(effect, IOPrintEffect):
            return await self.io_handler.handle_print(effect, ctx)

        if _effect_is(effect, GraphStepEffect):
            return await self.graph_handler.handle_step(effect, ctx)
        if _effect_is(effect, GraphAnnotateEffect):
            return await self.graph_handler.handle_annotate(effect, ctx)
        if _effect_is(effect, GraphSnapshotEffect):
            return await self.graph_handler.handle_snapshot(effect, ctx)

        if _effect_is(effect, DepInjectEffect):
            proxy_effect = AskEffect(key=effect.key, created_at=effect.created_at)
            return await self.reader_handler.handle_ask(proxy_effect, ctx)

        if _effect_is(effect, GatherEffect):
            return await self._handle_gather_effect(effect, ctx)
        if _effect_is(effect, GatherDictEffect):
            return await self._handle_gather_dict_effect(effect, ctx)

        if _effect_is(effect, MemoGetEffect):
            return await self.memo_handler.handle_get(effect, ctx)
        if _effect_is(effect, MemoPutEffect):
            return await self.memo_handler.handle_put(effect, ctx)

        if _effect_is(effect, CacheGetEffect):
            return await self.cache_handler.handle_get(effect, ctx)
        if _effect_is(effect, CachePutEffect):
            return await self.cache_handler.handle_put(effect, ctx)

        raise ValueError(f"Unknown effect: {effect!r}")

    async def _handle_gather_effect(self, effect: GatherEffect, ctx: ExecutionContext) -> Any:
        return await self._run_gather_sequence(list(effect.programs), ctx)

    async def _handle_gather_dict_effect(
        self, effect: GatherDictEffect, ctx: ExecutionContext
    ) -> Any:
        program_list = list(effect.programs.values())
        results = await self._run_gather_sequence(program_list, ctx)
        return {
            key: value
            for key, value in zip(effect.programs.keys(), results, strict=False)
        }

    async def _run_gather_sequence(
        self, programs: list[Program], ctx: ExecutionContext
    ) -> list[Any]:
        normalized_programs: list[Program] = []

        def _enqueue_program(prog_like: Any) -> None:
            if isinstance(prog_like, Program):
                normalized_programs.append(prog_like)
                return

            if callable(prog_like) and not isinstance(prog_like, Program):
                _enqueue_program(prog_like())
                return

            if isinstance(prog_like, (list, tuple)):
                for nested in prog_like:
                    _enqueue_program(nested)
                return

            raise TypeError(
                "gather expects Program instances, callables returning Programs, or iterables of them"
            )

        for program in programs:
            _enqueue_program(program)

        tasks = []
        for prog in normalized_programs:
            ctx_copy = ExecutionContext(
                env=ctx.env.copy() if ctx.env else {},
                state=ctx.state.copy() if ctx.state else {},
                log=[],
                graph=ctx.graph,
                io_allowed=ctx.io_allowed,
                cache=ctx.cache,
            )
            tasks.append(asyncio.create_task(self.run(prog, ctx_copy)))

        sub_results = await asyncio.gather(*tasks)

        results: list[Any] = []
        sub_contexts = [sub_result.context for sub_result in sub_results]
        error_to_raise: BaseException | None = None

        for sub_result in sub_results:
            if isinstance(sub_result.result, Err):
                if error_to_raise is None:
                    error_to_raise = sub_result.result.error
            else:
                results.append(sub_result.value)

        combined_steps = set(ctx.graph.steps)
        gather_inputs: list[WNode] = []
        for sub_ctx in sub_contexts:
            ctx.state.update(sub_ctx.state)
            ctx.log.extend(sub_ctx.log)
            combined_steps.update(sub_ctx.graph.steps)
            gather_inputs.append(sub_ctx.graph.last.output)

        if gather_inputs:
            gather_node = WNode(tuple(results))
            gather_step = WStep(inputs=tuple(gather_inputs), output=gather_node)
            combined_steps.add(gather_step)
            ctx.graph = WGraph(last=gather_step, steps=frozenset(combined_steps))
        else:
            ctx.graph = WGraph(last=ctx.graph.last, steps=frozenset(combined_steps))

        if error_to_raise is not None:
            raise error_to_raise

        return results


__all__ = ["ProgramInterpreter", "force_eval"]
