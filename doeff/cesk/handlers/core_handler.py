"""Core effect handler for basic effects in the new handler system.

Per SPEC-CESK-003: This handler uses user-space patterns (with_local, with_safe,
with_listen, with_intercept, with_graph_capture) instead of specialized Frames.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from doeff.do import do
from doeff._types_internal import CallFrame, EffectBase
from doeff._vendor import FrozenDict
from doeff.cesk.frames import ReturnFrame
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.handlers.patterns import (
    with_graph_capture,
    with_intercept,
    with_listen,
    with_local,
    with_safe,
)
from doeff.cesk.state import CESKState
from doeff.effects.atomic import AtomicGetEffect, AtomicUpdateEffect
from doeff.effects.cache import (
    CacheDeleteEffect,
    CacheExistsEffect,
    CacheGetEffect,
    CachePutEffect,
)
from doeff.effects.callstack import ProgramCallFrameEffect, ProgramCallStackEffect
from doeff.effects.debug import GetDebugContextEffect
from doeff.effects.graph import (
    GraphAnnotateEffect,
    GraphCaptureEffect,
    GraphSnapshotEffect,
    GraphStepEffect,
)
from doeff.effects.intercept import InterceptEffect
from doeff.effects.io import IOPerformEffect
from doeff.effects.pure import PureEffect
from doeff.effects.reader import AskEffect, LocalEffect
from doeff.effects.result import ResultSafeEffect
from doeff.effects.state import StateGetEffect, StateModifyEffect, StatePutEffect
from doeff.effects.time import GetTimeEffect
from doeff.effects.writer import WriterListenEffect, WriterTellEffect
from doeff.program import Program, ProgramBase

if TYPE_CHECKING:
    pass


class CircularAskError(Exception):
    """Raised when a circular dependency is detected in lazy Ask evaluation.

    This occurs when evaluating a Program value for Ask("key") requires
    asking for the same key (directly or indirectly), creating a cycle.

    Attributes:
        key: The Ask key where the cycle was detected.
    """

    def __init__(self, key: Any, message: str | None = None):
        self.key = key
        if message is None:
            message = f"Circular dependency detected for Ask({key!r})"
        super().__init__(message)


_ASK_IN_PROGRESS = object()


@do
def core_handler(effect: EffectBase, ctx: HandlerContext):
    """Forwards unhandled effects to outer handlers by yielding them."""
    store = dict(ctx.store)

    if isinstance(effect, PureEffect):
        return CESKState.with_value(effect.value, ctx.env, store, ctx.k)

    if isinstance(effect, AskEffect):
        key = effect.key

        if key in ctx.env:
            env_value = ctx.env[key]

            if isinstance(env_value, ProgramBase):
                cache = store.get("__ask_lazy_cache__", {})
                if key in cache:
                    cached_program, cached_value = cache[key]
                    # Check for cycle FIRST - if in progress, we have a circular dependency
                    if cached_value is _ASK_IN_PROGRESS:
                        return CESKState.with_error(
                            CircularAskError(key),
                            ctx.env, store, ctx.k
                        )
                    # Same program with completed value - return cached result
                    if cached_program is env_value:
                        return CESKState.with_value(cached_value, ctx.env, store, ctx.k)

                from doeff.cesk.frames import AskLazyFrame
                from doeff.cesk.result import DirectState

                new_cache = {**cache, key: (env_value, _ASK_IN_PROGRESS)}
                new_store = {**store, "__ask_lazy_cache__": new_cache}

                # Use DirectState to preserve the custom K with AskLazyFrame
                return DirectState(CESKState.with_program(
                    env_value, ctx.env, new_store,
                    [AskLazyFrame(ask_key=key, program=env_value)] + list(ctx.k)
                ))

            return CESKState.with_value(env_value, ctx.env, store, ctx.k)

        from doeff.cesk.errors import MissingEnvKeyError
        return CESKState.with_error(MissingEnvKeyError(key), ctx.env, store, ctx.k)

    if isinstance(effect, StateGetEffect):
        key = effect.key
        if key in store:
            return CESKState.with_value(store[key], ctx.env, store, ctx.k)
        return CESKState.with_error(KeyError(key), ctx.env, store, ctx.k)

    if isinstance(effect, StatePutEffect):
        new_store = {**store, effect.key: effect.value}
        return CESKState.with_value(None, ctx.env, new_store, ctx.k)

    if isinstance(effect, StateModifyEffect):
        key = effect.key
        # For missing keys, pass None to the function
        old_value = store.get(key, None)
        try:
            new_value = effect.func(old_value)
        except Exception as ex:
            return CESKState.with_error(ex, ctx.env, store, ctx.k)
        new_store = {**store, key: new_value}
        return CESKState.with_value(new_value, ctx.env, new_store, ctx.k)

    if isinstance(effect, LocalEffect):
        # Merge current environment with the update (Local overrides take precedence)
        from doeff.cesk.frames import LocalRestoreFrame
        from doeff.cesk.result import DirectState

        merged_env = FrozenDict({**ctx.env, **effect.env_update})

        # Push LocalRestoreFrame to restore original env after sub_program completes
        local_frame = LocalRestoreFrame(saved_env=ctx.env)

        # Return DirectState to preserve our custom K with LocalRestoreFrame
        return DirectState(CESKState.with_program(
            effect.sub_program, merged_env, store,
            [local_frame] + list(ctx.k)
        ))

    if isinstance(effect, ResultSafeEffect):
        # Use user-space pattern instead of SafeFrame
        result = yield with_safe(effect.sub_program)
        return result  # Plain value (Ok or Err) - HandlerResultFrame constructs CESKState

    if isinstance(effect, WriterTellEffect):
        log = list(store.get("__log__", []))
        message = effect.message
        if isinstance(message, (list, tuple)):
            log.extend(message)
        else:
            log.append(message)
        new_store = {**store, "__log__": log}
        return CESKState.with_value(None, ctx.env, new_store, ctx.k)

    if isinstance(effect, WriterListenEffect):
        # Use ListenCaptureFrame to track log entries during sub_program execution
        from doeff.cesk.frames import ListenCaptureFrame
        from doeff.cesk.result import DirectState

        # Record current log position
        current_log = store.get("__log__", [])
        log_start_index = len(current_log)

        # Push ListenCaptureFrame to extract captured logs when sub_program completes
        listen_frame = ListenCaptureFrame(log_start_index=log_start_index)

        # Return DirectState to preserve our custom K with ListenCaptureFrame
        return DirectState(CESKState.with_program(
            effect.sub_program, ctx.env, store,
            [listen_frame] + list(ctx.k)
        ))

    if isinstance(effect, InterceptEffect):
        # Use user-space pattern instead of InterceptFrame
        result = yield with_intercept(effect.transforms, effect.program)
        return result  # Plain value - HandlerResultFrame constructs CESKState

    if isinstance(effect, GetTimeEffect):
        current_time = store.get("__current_time__")
        if current_time is None:
            current_time = datetime.now()
        return CESKState.with_value(current_time, ctx.env, store, ctx.k)

    if isinstance(effect, IOPerformEffect):
        try:
            result = effect.action()
            return CESKState.with_value(result, ctx.env, store, ctx.k)
        except Exception as ex:
            return CESKState.with_error(ex, ctx.env, store, ctx.k)

    if isinstance(effect, CacheGetEffect):
        cache = store.get("__cache_storage__", {})
        key = effect.key
        if key not in cache:
            return CESKState.with_error(
                KeyError(f"Cache key not found: {key!r}"),
                ctx.env, store, ctx.k
            )
        return CESKState.with_value(cache[key], ctx.env, store, ctx.k)

    if isinstance(effect, CachePutEffect):
        cache = store.get("__cache_storage__", {})
        new_cache = {**cache, effect.key: effect.value}
        new_store = {**store, "__cache_storage__": new_cache}
        return CESKState.with_value(None, ctx.env, new_store, ctx.k)

    if isinstance(effect, CacheExistsEffect):
        cache = store.get("__cache_storage__", {})
        exists = effect.key in cache
        return CESKState.with_value(exists, ctx.env, store, ctx.k)

    if isinstance(effect, CacheDeleteEffect):
        cache = store.get("__cache_storage__", {})
        new_cache = {k: v for k, v in cache.items() if k != effect.key}
        new_store = {**store, "__cache_storage__": new_cache}
        return CESKState.with_value(None, ctx.env, new_store, ctx.k)

    if isinstance(effect, AtomicGetEffect):
        key = effect.key
        if key in store:
            value = store[key]
        elif effect.default_factory is not None:
            value = effect.default_factory()
            store = {**store, key: value}
        else:
            value = None
        return CESKState.with_value(value, ctx.env, store, ctx.k)

    if isinstance(effect, AtomicUpdateEffect):
        key = effect.key
        if key in store:
            old_value = store[key]
        elif effect.default_factory is not None:
            old_value = effect.default_factory()
        else:
            old_value = None
        new_value = effect.updater(old_value)
        new_store = {**store, key: new_value}
        return CESKState.with_value(new_value, ctx.env, new_store, ctx.k)

    if isinstance(effect, GraphStepEffect):
        graph = store.get("__graph__", [])
        node = {"value": effect.value, "meta": effect.meta}
        new_graph = graph + [node]
        new_store = {**store, "__graph__": new_graph}
        return CESKState.with_value(effect.value, ctx.env, new_store, ctx.k)

    if isinstance(effect, GraphAnnotateEffect):
        graph = store.get("__graph__", [])
        if graph:
            last_node = graph[-1]
            updated_node = {**last_node, "meta": {**last_node.get("meta", {}), **effect.meta}}
            new_graph = graph[:-1] + [updated_node]
            new_store = {**store, "__graph__": new_graph}
        else:
            new_store = store
        return CESKState.with_value(None, ctx.env, new_store, ctx.k)

    if isinstance(effect, GraphSnapshotEffect):
        graph = store.get("__graph__", [])
        return CESKState.with_value(list(graph), ctx.env, store, ctx.k)

    if isinstance(effect, GraphCaptureEffect):
        # Use user-space pattern instead of GraphCaptureFrame
        result = yield with_graph_capture(effect.program)
        return result  # Plain value (value, graph) - HandlerResultFrame constructs CESKState

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

    # Handle GatherEffect when items are Programs (sequential execution)
    from doeff.effects.gather import GatherEffect
    from doeff.effects.spawn import SpawnEffect, Waitable

    if isinstance(effect, GatherEffect):
        items = effect.items
        if not items:
            return CESKState.with_value([], ctx.env, store, ctx.k)

        # Check if all items are Programs (sequential execution case)
        # Note: SpawnEffect is a ProgramBase but should NOT be handled here -
        # it needs concurrent spawning via task_scheduler_handler
        all_programs = all(
            isinstance(item, ProgramBase) and not isinstance(item, SpawnEffect)
            for item in items
        )
        if all_programs:
            # Use GatherFrame for sequential execution
            from doeff.cesk.frames import GatherFrame
            from doeff.cesk.result import DirectState

            first_prog, *rest = items
            gather_frame = GatherFrame(
                remaining_programs=rest,
                collected_results=[],
                saved_env=ctx.env,
            )
            # Return DirectState to preserve our custom K with GatherFrame
            return DirectState(CESKState.with_program(
                first_prog, ctx.env, store,
                [gather_frame] + list(ctx.k)
            ))

        # Mixed or all-Waitable items → forward to outer handler (task_scheduler)
        result = yield effect
        return result

    # Forward unhandled effects to outer handlers
    result = yield effect
    # Return plain value - HandlerResultFrame constructs CESKState with current store
    return result


__all__ = [
    "CircularAskError",
    "core_handler",
]
