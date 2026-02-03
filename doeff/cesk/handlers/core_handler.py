"""Core effect handler for fundamental and nesting effects.

Handles:
- Fundamental: Pure, Debug, Callstack
- Nesting (require clean continuation): Local, Listen, Safe, GraphCapture, Intercept, Gather

Simple effects (Ask, Tell, Get/Put, Cache, Graph, Atomic) are handled by
specialized handlers. Nesting effects stay here because they wrap sub-programs
and require access to the full continuation without forwarding pollution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.do import do
from doeff._types_internal import CallFrame, EffectBase
from doeff._vendor import FrozenDict
from doeff.cesk.frames import ReturnFrame
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.handlers.patterns import with_graph_capture, with_intercept, with_safe
from doeff.cesk.state import CESKState
from doeff.effects.callstack import ProgramCallFrameEffect, ProgramCallStackEffect
from doeff.effects.debug import GetDebugContextEffect
from doeff.effects.graph import GraphCaptureEffect
from doeff.effects.intercept import InterceptEffect
from doeff.effects.pure import PureEffect
from doeff.effects.reader import AskEffect, LocalEffect
from doeff.effects.result import ResultSafeEffect
from doeff.effects.writer import WriterListenEffect
from doeff.program import ProgramBase

if TYPE_CHECKING:
    pass


@do
def core_handler(effect: EffectBase, ctx: HandlerContext):
    store = dict(ctx.store)

    if isinstance(effect, PureEffect):
        return CESKState.with_value(effect.value, ctx.env, store, ctx.k)

    if isinstance(effect, AskEffect):
        from doeff.cesk.handlers.reader_handler import CircularAskError, _ASK_IN_PROGRESS

        key = effect.key

        if key in ctx.env:
            env_value = ctx.env[key]

            if isinstance(env_value, ProgramBase):
                cache = store.get("__ask_lazy_cache__", {})
                if key in cache:
                    cached_program, cached_value = cache[key]
                    if cached_value is _ASK_IN_PROGRESS:
                        return CESKState.with_error(
                            CircularAskError(key),
                            ctx.env, store, ctx.k
                        )
                    if cached_program is env_value:
                        return CESKState.with_value(cached_value, ctx.env, store, ctx.k)

                from doeff.cesk.frames import AskLazyFrame
                from doeff.cesk.result import DirectState

                new_cache = {**cache, key: (env_value, _ASK_IN_PROGRESS)}
                new_store = {**store, "__ask_lazy_cache__": new_cache}

                return DirectState(CESKState.with_program(
                    env_value, ctx.env, new_store,
                    [AskLazyFrame(ask_key=key, program=env_value)] + list(ctx.k)
                ))

            return CESKState.with_value(env_value, ctx.env, store, ctx.k)

        from doeff.cesk.errors import MissingEnvKeyError
        return CESKState.with_error(MissingEnvKeyError(key), ctx.env, store, ctx.k)

    if isinstance(effect, LocalEffect):
        from doeff.cesk.frames import LocalRestoreFrame
        from doeff.cesk.result import DirectState

        merged_env = FrozenDict({**ctx.env, **effect.env_update})
        local_frame = LocalRestoreFrame(saved_env=ctx.env)

        return DirectState(CESKState.with_program(
            effect.sub_program, merged_env, store,
            [local_frame] + list(ctx.k)
        ))

    if isinstance(effect, WriterListenEffect):
        from doeff.cesk.frames import ListenCaptureFrame
        from doeff.cesk.result import DirectState

        current_log = store.get("__log__", [])
        log_start_index = len(current_log)
        listen_frame = ListenCaptureFrame(log_start_index=log_start_index)

        return DirectState(CESKState.with_program(
            effect.sub_program, ctx.env, store,
            [listen_frame] + list(ctx.k)
        ))

    if isinstance(effect, ResultSafeEffect):
        result = yield with_safe(effect.sub_program)
        return result

    if isinstance(effect, InterceptEffect):
        result = yield with_intercept(effect.transforms, effect.program)
        return result

    if isinstance(effect, GraphCaptureEffect):
        result = yield with_graph_capture(effect.program)
        return result

    if isinstance(effect, ProgramCallStackEffect):
        frames = []
        for idx, frame in enumerate(ctx.delimited_k):
            if isinstance(frame, ReturnFrame) and frame.program_call is not None:
                pc = frame.program_call
                call_frame = CallFrame(
                    kleisli=pc.kleisli_source,
                    function_name=pc.function_name,
                    args=pc.args,
                    kwargs=pc.kwargs,
                    depth=idx,
                    created_at=pc.created_at,
                )
                frames.append(call_frame)
        return CESKState.with_value(tuple(frames), ctx.env, store, ctx.k)

    if isinstance(effect, ProgramCallFrameEffect):
        frames = []
        for idx, frame in enumerate(ctx.delimited_k):
            if isinstance(frame, ReturnFrame) and frame.program_call is not None:
                pc = frame.program_call
                call_frame = CallFrame(
                    kleisli=pc.kleisli_source,
                    function_name=pc.function_name,
                    args=pc.args,
                    kwargs=pc.kwargs,
                    depth=idx,
                    created_at=pc.created_at,
                )
                frames.append(call_frame)

        depth = effect.depth
        if depth >= len(frames):
            return CESKState.with_error(
                IndexError(f"Call stack depth {depth} exceeds available frames ({len(frames)})"),
                ctx.env, store, ctx.k
            )

        return CESKState.with_value(frames[depth], ctx.env, store, ctx.k)

    if isinstance(effect, GetDebugContextEffect):
        from doeff.cesk.debug import get_debug_context

        full_k = ctx.delimited_k + ctx.outer_k
        debug_ctx = get_debug_context(full_k, current_effect=type(effect).__name__)
        return CESKState.with_value(debug_ctx, ctx.env, store, ctx.k)

    from doeff.effects.gather import GatherEffect
    from doeff.effects.spawn import SpawnEffect

    if isinstance(effect, GatherEffect):
        items = effect.items
        if not items:
            return CESKState.with_value([], ctx.env, store, ctx.k)

        all_programs = all(
            isinstance(item, ProgramBase) and not isinstance(item, SpawnEffect)
            for item in items
        )
        if all_programs:
            from doeff.cesk.frames import GatherFrame
            from doeff.cesk.result import DirectState

            first_prog, *rest = items
            gather_frame = GatherFrame(
                remaining_programs=rest,
                collected_results=[],
                saved_env=ctx.env,
            )
            return DirectState(CESKState.with_program(
                first_prog, ctx.env, store,
                [gather_frame] + list(ctx.k)
            ))

        result = yield effect
        return result

    result = yield effect
    return result


__all__ = [
    "core_handler",
]
