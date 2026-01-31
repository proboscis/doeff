"""Core effect handler for basic effects in the new handler system."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from doeff.do import do
from doeff._types_internal import CallFrame, EffectBase, ListenResult
from doeff._vendor import FrozenDict
from doeff.cesk.frames import (
    ContinueError,
    ContinueProgram,
    ContinueValue,
    FrameResult,
    GraphCaptureFrame,
    ListenFrame,
    LocalFrame,
    ReturnFrame,
    SafeFrame,
)
from doeff.cesk.handler_frame import HandlerContext, ResumeK
from doeff.effects.atomic import AtomicGetEffect, AtomicUpdateEffect
from doeff.effects.cache import (
    CacheDeleteEffect,
    CacheExistsEffect,
    CacheGetEffect,
    CachePutEffect,
)
from doeff.effects.callstack import ProgramCallFrameEffect, ProgramCallStackEffect
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
from doeff.utils import BoundedLog

if TYPE_CHECKING:
    pass


_ASK_IN_PROGRESS = object()


@do
def core_handler(effect: EffectBase, ctx: HandlerContext):
    """Forwards unhandled effects to outer handlers by yielding them."""
    store = dict(ctx.store)
    
    if isinstance(effect, PureEffect):
        return ContinueValue(
            value=effect.value,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, AskEffect):
        key = effect.key
        
        if key in ctx.env:
            env_value = ctx.env[key]
            
            if isinstance(env_value, ProgramBase):
                cache = store.get("__ask_lazy_cache__", {})
                if key in cache:
                    cached_program, cached_value = cache[key]
                    if cached_program is env_value:
                        return ContinueValue(
                            value=cached_value,
                            env=ctx.env,
                            store=store,
                            k=ctx.delimited_k,
                        )
                    if cached_value is _ASK_IN_PROGRESS:
                        return ContinueError(
                            error=RecursionError(f"Circular dependency detected for Ask({key!r})"),
                            env=ctx.env,
                            store=store,
                            k=ctx.delimited_k,
                        )
                
                new_cache = {**cache, key: (env_value, _ASK_IN_PROGRESS)}
                new_store = {**store, "__ask_lazy_cache__": new_cache}
                
                from doeff.cesk.frames import AskLazyFrame
                return ContinueProgram(
                    program=env_value,
                    env=ctx.env,
                    store=new_store,
                    k=[AskLazyFrame(ask_key=key, program=env_value)] + ctx.delimited_k,
                )
            
            return ContinueValue(
                value=env_value,
                env=ctx.env,
                store=store,
                k=ctx.delimited_k,
            )
        
        return ContinueError(
            error=KeyError(key),
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, StateGetEffect):
        key = effect.key
        if key in store:
            return ContinueValue(
                value=store[key],
                env=ctx.env,
                store=store,
                k=ctx.delimited_k,
            )
        return ContinueError(
            error=KeyError(key),
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, StatePutEffect):
        new_store = {**store, effect.key: effect.value}
        return ContinueValue(
            value=None,
            env=ctx.env,
            store=new_store,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, StateModifyEffect):
        key = effect.key
        if key not in store:
            return ContinueError(
                error=KeyError(key),
                env=ctx.env,
                store=store,
                k=ctx.delimited_k,
            )
        old_value = store[key]
        try:
            new_value = effect.func(old_value)
        except Exception as ex:
            return ContinueError(
                error=ex,
                env=ctx.env,
                store=store,
                k=ctx.delimited_k,
            )
        new_store = {**store, key: new_value}
        return ContinueValue(
            value=new_value,
            env=ctx.env,
            store=new_store,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, LocalEffect):
        overrides = effect.env_update
        new_env = FrozenDict(dict(ctx.env) | dict(overrides))
        new_k = [LocalFrame(restore_env=ctx.env)] + ctx.delimited_k
        return ContinueProgram(
            program=effect.sub_program,
            env=new_env,
            store=store,
            k=new_k,
        )
    
    if isinstance(effect, ResultSafeEffect):
        new_k = [SafeFrame(saved_env=ctx.env)] + ctx.delimited_k
        return ContinueProgram(
            program=effect.sub_program,
            env=ctx.env,
            store=store,
            k=new_k,
        )
    
    if isinstance(effect, WriterTellEffect):
        log = list(store.get("__log__", []))
        message = effect.message
        if isinstance(message, (list, tuple)):
            log.extend(message)
        else:
            log.append(message)
        new_store = {**store, "__log__": log}
        return ContinueValue(
            value=None,
            env=ctx.env,
            store=new_store,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, WriterListenEffect):
        current_log = store.get("__log__", [])
        log_start_index = len(current_log)
        new_k = [ListenFrame(log_start_index=log_start_index)] + ctx.delimited_k
        return ContinueProgram(
            program=effect.sub_program,
            env=ctx.env,
            store=store,
            k=new_k,
        )
    
    if isinstance(effect, InterceptEffect):
        from doeff.cesk.frames import InterceptFrame
        new_k = [InterceptFrame(transforms=effect.transforms)] + ctx.delimited_k
        return ContinueProgram(
            program=effect.program,
            env=ctx.env,
            store=store,
            k=new_k,
        )
    
    if isinstance(effect, GetTimeEffect):
        current_time = store.get("__current_time__")
        if current_time is None:
            current_time = datetime.now()
        return ContinueValue(
            value=current_time,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, IOPerformEffect):
        try:
            result = effect.action()
            return ContinueValue(
                value=result,
                env=ctx.env,
                store=store,
                k=ctx.delimited_k,
            )
        except Exception as ex:
            return ContinueError(
                error=ex,
                env=ctx.env,
                store=store,
                k=ctx.delimited_k,
            )
    
    if isinstance(effect, CacheGetEffect):
        cache = store.get("__cache_storage__", {})
        key = effect.key
        if key not in cache:
            return ContinueError(
                error=KeyError(f"Cache key not found: {key!r}"),
                env=ctx.env,
                store=store,
                k=ctx.delimited_k,
            )
        return ContinueValue(
            value=cache[key],
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, CachePutEffect):
        cache = store.get("__cache_storage__", {})
        new_cache = {**cache, effect.key: effect.value}
        new_store = {**store, "__cache_storage__": new_cache}
        return ContinueValue(
            value=None,
            env=ctx.env,
            store=new_store,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, CacheExistsEffect):
        cache = store.get("__cache_storage__", {})
        exists = effect.key in cache
        return ContinueValue(
            value=exists,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, CacheDeleteEffect):
        cache = store.get("__cache_storage__", {})
        new_cache = {k: v for k, v in cache.items() if k != effect.key}
        new_store = {**store, "__cache_storage__": new_cache}
        return ContinueValue(
            value=None,
            env=ctx.env,
            store=new_store,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, AtomicGetEffect):
        key = effect.key
        if key in store:
            value = store[key]
        elif effect.default_factory is not None:
            value = effect.default_factory()
            store = {**store, key: value}
        else:
            value = None
        return ContinueValue(
            value=value,
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        )
    
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
        return ContinueValue(
            value=new_value,
            env=ctx.env,
            store=new_store,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, GraphStepEffect):
        graph = store.get("__graph__", [])
        node = {"value": effect.value, "meta": effect.meta}
        new_graph = graph + [node]
        new_store = {**store, "__graph__": new_graph}
        return ContinueValue(
            value=effect.value,
            env=ctx.env,
            store=new_store,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, GraphAnnotateEffect):
        graph = store.get("__graph__", [])
        if graph:
            last_node = graph[-1]
            updated_node = {**last_node, "meta": {**last_node.get("meta", {}), **effect.meta}}
            new_graph = graph[:-1] + [updated_node]
            new_store = {**store, "__graph__": new_graph}
        else:
            new_store = store
        return ContinueValue(
            value=None,
            env=ctx.env,
            store=new_store,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, GraphSnapshotEffect):
        graph = store.get("__graph__", [])
        return ContinueValue(
            value=list(graph),
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        )
    
    if isinstance(effect, GraphCaptureEffect):
        graph = store.get("__graph__", [])
        graph_start = len(graph)
        new_k = [GraphCaptureFrame(graph_start)] + ctx.delimited_k
        return ContinueProgram(
            program=effect.program,
            env=ctx.env,
            store=store,
            k=new_k,
        )
    
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
        return ContinueValue(
            value=tuple(frames),
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        )
    
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
            return ContinueError(
                error=IndexError(f"Call stack depth {depth} exceeds available frames ({len(frames)})"),
                env=ctx.env,
                store=store,
                k=ctx.delimited_k,
            )
        
        return ContinueValue(
            value=frames[depth],
            env=ctx.env,
            store=store,
            k=ctx.delimited_k,
        )
    
    result = yield effect
    return ContinueValue(
        value=result,
        env=ctx.env,
        store=None,
        k=ctx.delimited_k,
    )


__all__ = [
    "core_handler",
]
