"""Core effect handler for basic effects in the new handler system."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.do import do
from doeff._types_internal import EffectBase, ListenResult
from doeff._vendor import FrozenDict
from doeff.cesk.frames import (
    ContinueError,
    ContinueProgram,
    ContinueValue,
    FrameResult,
    ListenFrame,
    LocalFrame,
    SafeFrame,
)
from doeff.cesk.handler_frame import HandlerContext, ResumeK
from doeff.effects.intercept import InterceptEffect
from doeff.effects.pure import PureEffect
from doeff.effects.reader import AskEffect, LocalEffect
from doeff.effects.result import ResultSafeEffect
from doeff.effects.state import StateGetEffect, StateModifyEffect, StatePutEffect
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
            value=old_value,
            env=ctx.env,
            store=new_store,
            k=new_store,
        )
    
    if isinstance(effect, LocalEffect):
        overrides = effect.env_override
        new_env = FrozenDict(dict(ctx.env) | dict(overrides))
        new_k = [LocalFrame(restore_env=ctx.env)] + ctx.delimited_k
        return ContinueProgram(
            program=effect.program,
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
        messages = effect.messages
        if isinstance(messages, (list, tuple)):
            log.extend(messages)
        else:
            log.append(messages)
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
            program=effect.program,
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
