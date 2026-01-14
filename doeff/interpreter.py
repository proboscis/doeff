"""
Program interpreter for the doeff system.

This module contains the main ProgramInterpreter that executes programs
by handling effects through the registered handlers.
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from typing import Any, Callable, Generator, TypeVar

from doeff._vendor import Err, Ok, WGraph, WNode, WStep
from doeff.effects import (
    AskEffect,
    AtomicGetEffect,
    AtomicUpdateEffect,
    CacheGetEffect,
    CachePutEffect,
    DepInjectEffect,
    FutureAwaitEffect,
    GatherEffect,
    GraphAnnotateEffect,
    GraphCaptureEffect,
    GraphSnapshotEffect,
    GraphStepEffect,
    InterceptEffect,
    IOPerformEffect,
    IOPrintEffect,
    LocalEffect,
    MemoGetEffect,
    MemoPutEffect,
    ProgramCallFrameEffect,
    ProgramCallStackEffect,
    ResultCatchEffect,
    ResultFailEffect,
    ResultFinallyEffect,
    ResultFirstSuccessEffect,
    ResultRecoverEffect,
    ResultRetryEffect,
    ResultSafeEffect,
    ResultUnwrapEffect,
    SpawnBackend,
    SpawnEffect,
    ThreadEffect,
    TaskJoinEffect,
    StateGetEffect,
    StateModifyEffect,
    StatePutEffect,
    WriterListenEffect,
    WriterTellEffect,
)
from doeff.handlers import (
    AtomicEffectHandler,
    CacheEffectHandler,
    FutureEffectHandler,
    GraphEffectHandler,
    IOEffectHandler,
    MemoEffectHandler,
    ReaderEffectHandler,
    ResultEffectHandler,
    SpawnEffectHandler,
    StateEffectHandler,
    ThreadEffectHandler,
    WriterEffectHandler,
)
from doeff.program import Program
from doeff.types import CallFrame
from doeff.types import (
    EnvKey,
    Effect,
    EffectFailure,
    EffectObservation,
    ExecutionContext,
    RunResult,
    capture_traceback,
)
from doeff.utils import BoundedLog


def _effect_is(effect: Effect, cls) -> bool:
    """Return True if effect is instance of cls, tolerant to module reloads."""
    return isinstance(effect, cls) or effect.__class__.__name__ == cls.__name__


T = TypeVar("T")

logger = logging.getLogger(__name__)

# Sentinel value to distinguish "no handler found" from "handler returned None"
_NO_HANDLER = object()


def force_eval(prog: Program[T]) -> Program[T]:
    """
    Force evaluation of nested Programs to prevent stack overflow.

    This is critical for stack safety with deep monadic computations.
    Python's recursion limit (~1000 frames) requires trampolining.
    """
    def forced_generator():
        to_gen = getattr(prog, "to_generator", None)
        if to_gen is None:
            raise TypeError(
                f"Program {prog!r} does not implement to_generator(); cannot force evaluation"
            )
        gen = to_gen()
        try:
            current = next(gen)
        except StopIteration as e:
            return e.value

        while True:
            # If current is a Program, force evaluate it
            from doeff.types import Program as ProgramType
            if isinstance(current, ProgramType):
                current = force_eval(current)
            try:
                value = yield current
            except GeneratorExit:
                gen.close()
                raise
            except BaseException as e:
                try:
                    current = gen.throw(e)
                except StopIteration as stop_exc:
                    return stop_exc.value
                continue
            try:
                current = gen.send(value)
            except StopIteration as e:
                return e.value

    from doeff.program import GeneratorProgram
    return GeneratorProgram(forced_generator)


class ProgramInterpreter:
    """
    Engine that handles all monad types according to our pragmatic contract.

    Uses separate handler classes for each effect category to maintain
    single responsibility and reduce complexity.

    Effect handlers can be customized by passing custom handlers to __init__.
    """

    def __init__(
        self,
        custom_handlers: dict[str, Any] | None = None,
        *,
        max_log_entries: int | None = None,
        spawn_default_backend: SpawnBackend = "thread",
        spawn_thread_max_workers: int | None = None,
        spawn_process_max_workers: int | None = None,
        spawn_ray_address: str | None = None,
        spawn_ray_init_kwargs: dict[str, Any] | None = None,
        spawn_ray_runtime_env: dict[str, Any] | None = None,
    ):
        """Initialize effect handlers.

        Args:
            custom_handlers: Optional dict mapping effect categories to custom handlers.
                           Keys can be: 'reader', 'state', 'writer', 'future', 'thread', 'spawn',
                           'result', 'io', 'graph', 'memo', 'cache'.
                           Values should be handler instances with appropriate handle_* methods.
            max_log_entries: Optional cap on the number of writer log entries retained.
            spawn_default_backend: Default backend for Spawn effects.
            spawn_thread_max_workers: Max worker threads for Spawn thread backend.
            spawn_process_max_workers: Max worker processes for Spawn process backend.
            spawn_ray_address: Ray cluster address for Spawn Ray backend.
            spawn_ray_init_kwargs: Extra kwargs passed to ray.init().
            spawn_ray_runtime_env: Default runtime_env for Ray tasks.

        .. deprecated::
            ProgramInterpreter is deprecated. Use EffectRuntime instead.
            Example: ``runtime = create_runtime(); await runtime.run(program)``
        """
        warnings.warn(
            "ProgramInterpreter is deprecated. Use EffectRuntime instead. "
            "Example: runtime = create_runtime(); await runtime.run(program)",
            DeprecationWarning,
            stacklevel=2,
        )
        if max_log_entries is not None and max_log_entries < 0:
            raise ValueError("max_log_entries must be >= 0 or None")

        self._max_log_entries = max_log_entries

        # Initialize default handlers
        handlers = {
            "reader": ReaderEffectHandler(),
            "state": StateEffectHandler(),
            "atomic": AtomicEffectHandler(),
            "writer": WriterEffectHandler(),
            "future": FutureEffectHandler(),
            "thread": ThreadEffectHandler(),
            "spawn": SpawnEffectHandler(
                default_backend=spawn_default_backend,
                thread_max_workers=spawn_thread_max_workers,
                process_max_workers=spawn_process_max_workers,
                ray_address=spawn_ray_address,
                ray_init_kwargs=spawn_ray_init_kwargs,
                ray_runtime_env=spawn_ray_runtime_env,
                max_log_entries=max_log_entries,
            ),
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
        self.atomic_handler = handlers["atomic"]
        self.writer_handler = handlers["writer"]
        self.future_handler = handlers["future"]
        self.thread_handler = handlers["thread"]
        self.spawn_handler = handlers["spawn"]
        self.result_handler = handlers["result"]
        self.io_handler = handlers["io"]
        self.graph_handler = handlers["graph"]
        self.memo_handler = handlers["memo"]
        self.cache_handler = handlers["cache"]


    def _new_log_buffer(self) -> BoundedLog:
        """Return a fresh log buffer respecting the configured limit."""

        return BoundedLog(max_entries=self._max_log_entries)

    def _ensure_log_buffer(self, ctx: ExecutionContext) -> None:
        """Ensure the execution context uses a bounded log with the configured limit."""

        log = ctx.log
        if isinstance(log, BoundedLog):
            log.set_max_entries(self._max_log_entries)
        else:
            ctx.log = BoundedLog(log, max_entries=self._max_log_entries)


    def run(
        self, program: Program[T], context: ExecutionContext | None = None
    ) -> RunResult[T]:
        """
        Run a program with full monad support (synchronous interface).

        Returns a RunResult[T] containing:
        - context: final execution context (state, log, graph)
        - result: Ok(value) or Err(error)

        Note: This method is synchronous but internally uses asyncio.run()
        to handle async execution. This design ensures that async is treated
        as an implementation detail rather than a special effect.

        For async contexts (e.g., pytest async tests), use run_async() instead.
        """
        return asyncio.run(self.run_async(program, context))

    async def run_async(
        self, program: Program[T], context: ExecutionContext | None = None
    ) -> RunResult[T]:
        """
        Run a program with full monad support (async interface).

        This is the async version of run(), useful for:
        - Tests that are already in async context
        - Integration with async frameworks
        - Internal recursive calls

        Returns a RunResult[T] containing:
        - context: final execution context (state, log, graph)
        - result: Ok(value) or Err(error)
        """
        ctx = context or ExecutionContext(
            env={},
            state={},
            log=self._new_log_buffer(),
            graph=WGraph(
                last=WStep(inputs=(), output=WNode("_root"), meta={}),
                steps=frozenset(),
            ),
            io_allowed=True,
            program_call_stack=[],  # Initialize call stack
        )

        self._ensure_log_buffer(ctx)

        try:
            return await self._execute_program_loop(program, ctx)
        except Exception as exc:
            runtime_tb = capture_traceback(exc)
            # Wrap in EffectFailure to capture call stack for display
            from doeff._types_internal import NullEffect
            effect_failure = EffectFailure(
                effect=NullEffect(),  # No specific effect, exception raised directly
                cause=exc,
                runtime_traceback=runtime_tb,
                creation_context=None,
                call_stack_snapshot=tuple(ctx.program_call_stack),
            )
            return RunResult(ctx, Err(effect_failure))

    async def _execute_program_loop(
        self, program: Program[T], ctx: ExecutionContext
    ) -> RunResult[T]:
        """Execute the program's generator loop."""
        from doeff.program import KleisliProgramCall
        from doeff.types import Effect, EffectBase

        call_frame_pushed = False

        if isinstance(program, KleisliProgramCall):
            if program.kleisli_source is not None:
                frame = CallFrame(
                    kleisli=program.kleisli_source,
                    function_name=program.function_name,
                    args=program.args,
                    kwargs=program.kwargs,
                    depth=len(ctx.program_call_stack),
                    created_at=program.created_at,
                )
                ctx.program_call_stack.append(frame)
                call_frame_pushed = True
            gen = program.to_generator()
        elif isinstance(program, EffectBase):
            result = await self._handle_effect(program, ctx)
            return RunResult(ctx, Ok(result))
        else:
            to_gen = getattr(program, "to_generator", None)
            if to_gen is None:
                raise TypeError(
                    f"Program {program!r} does not implement to_generator(); cannot execute"
                )
            gen = to_gen()

        try:
            current = next(gen)
        except StopIteration as e:
            return RunResult(ctx, Ok(e.value))
        except Exception as exc:
            # Exception raised during generator initialization (e.g., raise in first line)
            # If already wrapped in EffectFailure, don't wrap again
            if isinstance(exc, EffectFailure):
                return RunResult(ctx, Err(exc))
            runtime_tb = capture_traceback(exc)
            from doeff._types_internal import NullEffect
            effect_failure = EffectFailure(
                effect=NullEffect(),
                cause=exc,
                runtime_traceback=runtime_tb,
                creation_context=None,
                call_stack_snapshot=tuple(ctx.program_call_stack),
            )
            return RunResult(ctx, Err(effect_failure))

        try:
            while True:
                logger.debug(f"effect: {current}")
                from doeff.types import Program as ProgramType

                if isinstance(current, EffectBase):
                    try:
                        value = await self._handle_effect(current, ctx)
                    except Exception as exc:
                        runtime_tb = capture_traceback(exc)
                        effect_failure = EffectFailure(
                            effect=current,
                            cause=exc,
                            runtime_traceback=runtime_tb,
                            creation_context=current.created_at,
                            call_stack_snapshot=tuple(ctx.program_call_stack),
                        )
                        # Throw into generator to enable native try-except
                        try:
                            current = gen.throw(exc)
                            continue
                        except StopIteration as e:
                            return RunResult(ctx, Ok(e.value))
                        except Exception as uncaught:
                            if uncaught is exc:
                                # Exception was not caught by generator
                                return RunResult(ctx, Err(effect_failure))
                            # New exception from catch block
                            new_tb = capture_traceback(uncaught)
                            new_failure = EffectFailure(
                                effect=current,
                                cause=uncaught,
                                runtime_traceback=new_tb,
                                creation_context=current.created_at,
                                call_stack_snapshot=tuple(ctx.program_call_stack),
                            )
                            return RunResult(ctx, Err(new_failure))

                    try:
                        current = gen.send(value)
                    except StopIteration as e:
                        return RunResult(ctx, Ok(e.value))
                    except Exception as exc:
                        # Exception raised after effect handling (e.g., raise after yield)
                        # If already wrapped in EffectFailure, don't wrap again
                        if isinstance(exc, EffectFailure):
                            return RunResult(ctx, Err(exc))
                        runtime_tb = capture_traceback(exc)
                        from doeff._types_internal import NullEffect
                        effect_failure = EffectFailure(
                            effect=NullEffect(),
                            cause=exc,
                            runtime_traceback=runtime_tb,
                            creation_context=None,
                            call_stack_snapshot=tuple(ctx.program_call_stack),
                        )
                        return RunResult(ctx, Err(effect_failure))

                elif isinstance(current, ProgramType):
                    sub_result = await self.run_async(current, ctx)
                    if isinstance(sub_result.result, Err):
                        # Extract exception from EffectFailure if present
                        error = sub_result.result.error
                        exc = error.cause if isinstance(error, EffectFailure) else error
                        if not isinstance(exc, BaseException):
                            exc = Exception(str(exc))
                        # Throw into generator to enable native try-except
                        try:
                            current = gen.throw(exc)
                            ctx = sub_result.context
                            continue
                        except StopIteration as e:
                            return RunResult(ctx, Ok(e.value))
                        except Exception as uncaught:
                            if uncaught is exc:
                                # Exception was not caught by generator
                                return sub_result
                            # New exception from catch block
                            return RunResult(ctx, Err(uncaught))

                    ctx = sub_result.context

                    try:
                        current = gen.send(sub_result.value)
                    except StopIteration as e:
                        return RunResult(ctx, Ok(e.value))
                    except Exception as exc:
                        # Exception raised directly in user code (not via yield Fail)
                        # If already wrapped in EffectFailure, don't wrap again
                        if isinstance(exc, EffectFailure):
                            return RunResult(ctx, Err(exc))
                        runtime_tb = capture_traceback(exc)
                        from doeff._types_internal import NullEffect

                        effect_failure = EffectFailure(
                            effect=NullEffect(),
                            cause=exc,
                            runtime_traceback=runtime_tb,
                            creation_context=None,
                            call_stack_snapshot=tuple(ctx.program_call_stack),
                        )
                        return RunResult(ctx, Err(effect_failure))

                else:
                    return RunResult(ctx, Err(TypeError(f"Unknown yield type: {type(current)}")))
        finally:
            if call_frame_pushed:
                ctx.program_call_stack.pop()

    def _record_effect_usage(self, effect: Effect, ctx: ExecutionContext) -> None:
        """Record Dep/Ask effect usage for later inspection."""

        try:
            observations = ctx.effect_observations
        except AttributeError:  # Defensive: context without observation tracking
            return

        effect_type: str | None = None
        key: EnvKey | None = None

        if _effect_is(effect, DepInjectEffect):
            effect_type = "Dep"
            key = getattr(effect, "key", None)
        elif _effect_is(effect, AskEffect):
            effect_type = "Ask"
            key = getattr(effect, "key", None)

        if effect_type is None:
            return

        context_info = getattr(effect, "created_at", None)
        sanitized = context_info.without_frames() if context_info is not None else None

        snapshot = tuple(ctx.program_call_stack)

        observations.append(
            EffectObservation(
                effect_type=effect_type,
                key=key,
                context=sanitized,
                call_stack_snapshot=snapshot,
            )
        )

    async def _handle_effect(self, effect: Effect, ctx: ExecutionContext) -> Any:
        """Dispatch effect to appropriate handler."""
        self._record_effect_usage(effect, ctx)

        result = await self._try_intercept_effect(effect, ctx)
        if result is not _NO_HANDLER:
            return result

        # Try each category of effects
        result = await self._try_reader_effects(effect, ctx)
        if result is not _NO_HANDLER:
            return result

        result = await self._try_state_effects(effect, ctx)
        if result is not _NO_HANDLER:
            return result

        result = await self._try_result_effects(effect, ctx)
        if result is not _NO_HANDLER:
            return result

        result = await self._try_other_effects(effect, ctx)
        if result is not _NO_HANDLER:
            return result

        raise ValueError(f"Unknown effect: {effect!r}")

    async def _try_intercept_effect(self, effect: Effect, ctx: ExecutionContext) -> Any:
        """Handle program interception effects."""

        if _effect_is(effect, InterceptEffect):
            return await self._handle_intercept_effect(effect, ctx)
        return _NO_HANDLER

    async def _handle_intercept_effect(
        self, effect: InterceptEffect, ctx: ExecutionContext
    ) -> Any:
        """Run a program through the intercept pipeline."""

        intercept_program = _build_intercept_program(effect.program, effect.transforms)
        sub_result = await self.run_async(intercept_program, ctx)

        if isinstance(sub_result.result, Err):
            raise sub_result.result.error

        return sub_result.value

    async def _try_reader_effects(self, effect: Effect, ctx: ExecutionContext) -> Any:
        """Handle Reader/Dep/Ask effects. Returns _NO_HANDLER if not matched."""
        if _effect_is(effect, AskEffect):
            return await self.reader_handler.handle_ask(effect, ctx, self)
        if _effect_is(effect, LocalEffect):
            return await self.reader_handler.handle_local(effect, ctx, self)
        if _effect_is(effect, DepInjectEffect):
            proxy_effect = AskEffect(key=effect.key, created_at=effect.created_at)
            self._record_effect_usage(proxy_effect, ctx)
            return await self.reader_handler.handle_ask(proxy_effect, ctx, self)
        return _NO_HANDLER

    async def _try_state_effects(self, effect: Effect, ctx: ExecutionContext) -> Any:  # noqa: PLR0911
        """Handle State/Atomic/Writer effects. Returns _NO_HANDLER if not matched."""
        if _effect_is(effect, StateGetEffect):
            return await self.state_handler.handle_get(effect, ctx)
        if _effect_is(effect, StatePutEffect):
            return await self.state_handler.handle_put(effect, ctx)
        if _effect_is(effect, StateModifyEffect):
            return await self.state_handler.handle_modify(effect, ctx)
        if _effect_is(effect, AtomicGetEffect):
            return await self.atomic_handler.handle_get(effect, ctx)
        if _effect_is(effect, AtomicUpdateEffect):
            return await self.atomic_handler.handle_update(effect, ctx)
        if _effect_is(effect, WriterTellEffect):
            return await self.writer_handler.handle_tell(effect, ctx)
        if _effect_is(effect, WriterListenEffect):
            return await self.writer_handler.handle_listen(effect, ctx, self)
        return _NO_HANDLER

    async def _try_result_effects(self, effect: Effect, ctx: ExecutionContext) -> Any:  # noqa: PLR0911
        """Handle Result monad effects. Returns _NO_HANDLER if not matched."""
        from doeff.effects.pure import PureEffect

        if _effect_is(effect, PureEffect):
            return await self.result_handler.handle_pure(effect)
        if _effect_is(effect, ResultFailEffect):
            return await self.result_handler.handle_fail(effect)
        if _effect_is(effect, ResultCatchEffect):
            return await self.result_handler.handle_catch(effect, ctx, self)
        if _effect_is(effect, ResultFinallyEffect):
            return await self.result_handler.handle_finally(effect, ctx, self)
        if _effect_is(effect, ResultRecoverEffect):
            return await self.result_handler.handle_recover(effect, ctx, self)
        if _effect_is(effect, ResultRetryEffect):
            return await self.result_handler.handle_retry(effect, ctx, self)
        if _effect_is(effect, ResultFirstSuccessEffect):
            return await self.result_handler.handle_first_success(effect, ctx, self)
        if _effect_is(effect, ResultSafeEffect):
            return await self.result_handler.handle_safe(effect, ctx, self)
        if _effect_is(effect, ResultUnwrapEffect):
            return await self.result_handler.handle_unwrap(effect, ctx, self)
        return _NO_HANDLER

    async def _try_other_effects(self, effect: Effect, ctx: ExecutionContext) -> Any:
        """Handle Future/IO/Graph effects. Returns _NO_HANDLER if not matched."""
        result = await self._try_future_io_graph_effects(effect, ctx)
        if result is not _NO_HANDLER:
            return result
        result = await self._try_callstack_effects(effect, ctx)
        if result is not _NO_HANDLER:
            return result
        return await self._try_gather_memo_cache_effects(effect, ctx)

    async def _try_callstack_effects(self, effect: Effect, ctx: ExecutionContext) -> Any:
        """Handle call-stack introspection effects."""
        if _effect_is(effect, ProgramCallStackEffect):
            return tuple(ctx.program_call_stack)

        if _effect_is(effect, ProgramCallFrameEffect):
            depth = getattr(effect, "depth", 0)
            stack = ctx.program_call_stack
            if depth >= len(stack):
                raise IndexError(
                    f"Program call stack depth {depth} out of range (size={len(stack)})"
                )
            # Return the frame without mutating the call stack.
            return stack[-1 - depth]

        return _NO_HANDLER

    async def _try_future_io_graph_effects(self, effect: Effect, ctx: ExecutionContext) -> Any:  # noqa: PLR0911
        """Handle Future/IO/Graph effects. Returns _NO_HANDLER if not matched."""
        if _effect_is(effect, FutureAwaitEffect):
            return await self.future_handler.handle_await(effect)
        # NOTE: For parallel execution, use asyncio.create_task + Await + Gather pattern
        if _effect_is(effect, SpawnEffect):
            return self.spawn_handler.handle_spawn(effect, ctx, self)
        if _effect_is(effect, TaskJoinEffect):
            return await self.spawn_handler.handle_join(effect, ctx)
        if _effect_is(effect, ThreadEffect):
            awaitable = self.thread_handler.handle_thread(effect, ctx, self)
            if effect.await_result:
                return await self.future_handler.handle_await(
                    FutureAwaitEffect(awaitable=awaitable)
                )
            return awaitable
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
        if _effect_is(effect, GraphCaptureEffect):
            return await self.graph_handler.handle_capture(effect, ctx, self)
        return _NO_HANDLER

    async def _try_gather_memo_cache_effects(self, effect: Effect, ctx: ExecutionContext) -> Any:  # noqa: PLR0911
        """Handle Gather/Memo/Cache effects. Returns _NO_HANDLER if not matched."""
        if _effect_is(effect, GatherEffect):
            return await self._handle_gather_effect(effect, ctx)
        if _effect_is(effect, MemoGetEffect):
            return await self.memo_handler.handle_get(effect, ctx)
        if _effect_is(effect, MemoPutEffect):
            return await self.memo_handler.handle_put(effect, ctx)
        if _effect_is(effect, CacheGetEffect):
            return await self.cache_handler.handle_get(effect, ctx)
        if _effect_is(effect, CachePutEffect):
            return await self.cache_handler.handle_put(effect, ctx)
        return _NO_HANDLER

    async def _handle_gather_effect(self, effect: GatherEffect, ctx: ExecutionContext) -> Any:
        return await self._run_gather_sequence(list(effect.programs), ctx)

    async def _run_gather_sequence(
        self, programs: list[Program], ctx: ExecutionContext
    ) -> list[Any]:
        from doeff.program import KleisliProgramCall

        normalized_programs: list[Program] = []

        def _enqueue_program(prog_like: Any) -> None:
            from doeff.types import Program as ProgramType
            if isinstance(prog_like, ProgramType):
                normalized_programs.append(prog_like)
                return

            if isinstance(prog_like, (list, tuple)):
                for nested in prog_like:
                    _enqueue_program(nested)
                return

            raise TypeError(
                "gather expects Program or Effect instances, optionally nested in iterables"
            )

        for program in programs:
            _enqueue_program(program)

        tasks = []
        for prog in normalized_programs:
            ctx_copy = ExecutionContext(
                env=ctx.env.copy() if ctx.env else {},
                state=ctx.state.copy() if ctx.state else {},
                log=self._new_log_buffer(),
                graph=ctx.graph,
                io_allowed=ctx.io_allowed,
                cache=ctx.cache,
                effect_observations=ctx.effect_observations,
            )
            self._ensure_log_buffer(ctx_copy)
            tasks.append(asyncio.create_task(self.run_async(prog, ctx_copy)))

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


def _build_intercept_program(
    program: Program[T],
    transforms: tuple[Callable[[Effect], Effect | Program], ...],
) -> Program[T]:
    """Return a program that applies ``transforms`` to every effect in ``program``."""

    from doeff.program import GeneratorProgram

    return GeneratorProgram(
        lambda: _intercept_generator(program, transforms)
    )


def _intercept_generator(
    base: Program[T],
    transforms: tuple[Callable[[Effect], Effect | Program], ...],
) -> Generator[Effect | Program, Any, T]:
    """Intercept all effects yielded by ``base``."""

    from doeff.program import ProgramBase, _InterceptedProgram
    from doeff.types import EffectBase

    gen = _program_to_generator(base)
    try:
        current = next(gen)
    except StopIteration as exc:
        return exc.value

    transform_chain = _compose_intercept_transforms(transforms)

    def _forward_exception(e: BaseException) -> bool | T:  # noqa: DOEFF014
        """Forward exception to inner generator, return new current or re-raise."""
        nonlocal current
        try:
            current = gen.throw(e)
            return True  # Continue with new current
        except StopIteration as stop_exc:
            return stop_exc.value  # Return value

    while True:
        if isinstance(current, EffectBase):
            effect_program = transform_chain(current)
            try:
                final_effect = yield effect_program
            except GeneratorExit:
                gen.close()
                raise
            except BaseException as e:
                result = _forward_exception(e)
                if result is True:
                    continue
                return result

            if not isinstance(final_effect, EffectBase):
                raise TypeError(
                    "Intercept transform must resolve to an Effect, "
                    f"got {type(final_effect).__name__}"
                )

            nested_effect = final_effect.intercept(
                lambda eff: transform_chain(eff)
            )
            try:
                result = yield nested_effect
            except GeneratorExit:
                gen.close()
                raise
            except BaseException as e:
                fwd_result = _forward_exception(e)
                if fwd_result is True:
                    continue
                return fwd_result
            try:
                current = gen.send(result)
            except StopIteration as exc:
                return exc.value
            continue

        if isinstance(current, ProgramBase):
            wrapped = _InterceptedProgram.compose(current, transforms)
            try:
                yielded_value = yield wrapped
            except GeneratorExit:
                gen.close()
                raise
            except BaseException as e:
                result = _forward_exception(e)
                if result is True:
                    continue
                return result
            try:
                current = gen.send(yielded_value)
            except StopIteration as exc:
                return exc.value
            continue

        try:
            value = yield current
        except GeneratorExit:
            gen.close()
            raise
        except BaseException as e:
            result = _forward_exception(e)
            if result is True:
                continue
            return result
        try:
            current = gen.send(value)
        except StopIteration as exc:
            return exc.value


def _compose_intercept_transforms(
    transforms: tuple[Callable[[Effect], Effect | Program], ...]
) -> Callable[[Effect], Program]:
    from doeff.program import Program
    from doeff.types import EffectBase

    lifted = [_lift_intercept_transform(transform) for transform in transforms]

    def combined(effect: EffectBase) -> Program[EffectBase]:
        program: Program[EffectBase] = Program.pure(effect)
        for lift in lifted:
            program = program.flat_map(lift)
        return program

    return combined


def _lift_intercept_transform(
    transform: Callable[[Effect], Effect | Program]
) -> Callable[[Effect], Program]:
    from doeff.program import Program, ProgramBase
    from doeff.types import EffectBase

    def lifted(effect: EffectBase) -> Program:
        result = transform(effect)

        if isinstance(result, EffectBase):
            return Program.pure(result)

        if isinstance(result, ProgramBase):
            return result.flat_map(_ensure_effect_program)

        raise TypeError(
            "Intercept transform must return Effect or Program yielding Effect, "
            f"got {type(result).__name__}"
        )

    return lifted


def _ensure_effect_program(value: Any) -> Program:
    from doeff.program import Program
    from doeff.types import EffectBase

    if isinstance(value, EffectBase):
        return Program.pure(value)
    raise TypeError(
        "Intercept transform must resolve to an Effect, "
        f"got {type(value).__name__}"
    )


def _program_to_generator(
    base: Program[T],
) -> Generator[Effect | Program, Any, T]:
    """Return a generator for the provided program instance."""

    from doeff.program import KleisliProgramCall

    if isinstance(base, KleisliProgramCall):
        return base.to_generator()

    to_gen = getattr(base, "to_generator", None)
    if callable(to_gen):
        return to_gen()

    raise TypeError(
        "Cannot intercept value that does not expose to_generator(): "
        f"{type(base).__name__}"
    )


__all__ = ["ProgramInterpreter", "force_eval"]  # noqa: DOEFF021
