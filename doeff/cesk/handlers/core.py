"""Core effect handlers for the unified CESK architecture."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.errors import MissingEnvKeyError
from doeff.cesk.frames import AskLazyFrame, ContinueProgram, ContinueValue, FrameResult

if TYPE_CHECKING:
    from doeff.cesk.runtime.context import HandlerContext

from doeff.effects.pure import PureEffect
from doeff.effects.reader import AskEffect
from doeff.effects.state import StateGetEffect, StateModifyEffect, StatePutEffect

_ASK_IN_PROGRESS: Any = object()


class CircularAskError(Exception):
    def __init__(self, key: object) -> None:
        self.key = key
        super().__init__(f"Circular dependency detected: Ask({key!r}) is already being evaluated")


def handle_pure(
    effect: PureEffect,
    ctx: HandlerContext,
) -> FrameResult:
    return ContinueValue(
        value=effect.value,
        env=ctx.task_state.env,
        store=ctx.store,
        k=ctx.task_state.kontinuation,
    )


def handle_ask(
    effect: AskEffect,
    ctx: HandlerContext,
) -> FrameResult:
    from doeff.program import ProgramBase

    task_state = ctx.task_state
    store = ctx.store
    key = effect.key
    if key not in task_state.env:
        raise MissingEnvKeyError(key)
    value = task_state.env[key]

    if isinstance(value, ProgramBase):
        cache = store.get("__ask_lazy_cache__", {})

        if key in cache:
            cached_program, cached_value = cache[key]

            if cached_value is _ASK_IN_PROGRESS:
                raise CircularAskError(key)

            if cached_program is value:
                return ContinueValue(
                    value=cached_value,
                    env=task_state.env,
                    store=store,
                    k=task_state.kontinuation,
                )

        new_cache = {**cache, key: (value, _ASK_IN_PROGRESS)}
        new_store = {**store, "__ask_lazy_cache__": new_cache}

        return ContinueProgram(
            program=value,
            env=task_state.env,
            store=new_store,
            k=[AskLazyFrame(ask_key=key, program=value)] + task_state.kontinuation,
        )

    return ContinueValue(
        value=value,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


def handle_state_get(
    effect: StateGetEffect,
    ctx: HandlerContext,
) -> FrameResult:
    key = effect.key
    if key not in ctx.store:
        raise KeyError(f"Missing state key: {key!r}")
    value = ctx.store[key]
    return ContinueValue(
        value=value,
        env=ctx.task_state.env,
        store=ctx.store,
        k=ctx.task_state.kontinuation,
    )


def handle_state_put(
    effect: StatePutEffect,
    ctx: HandlerContext,
) -> FrameResult:
    new_store = {**ctx.store, effect.key: effect.value}
    return ContinueValue(
        value=None,
        env=ctx.task_state.env,
        store=new_store,
        k=ctx.task_state.kontinuation,
    )


def handle_state_modify(
    effect: StateModifyEffect,
    ctx: HandlerContext,
) -> FrameResult:
    old_value = ctx.store.get(effect.key)
    new_value = effect.func(old_value)
    new_store = {**ctx.store, effect.key: new_value}
    return ContinueValue(
        value=new_value,
        env=ctx.task_state.env,
        store=new_store,
        k=ctx.task_state.kontinuation,
    )


__all__ = [
    "CircularAskError",
    "handle_ask",
    "handle_pure",
    "handle_state_get",
    "handle_state_modify",
    "handle_state_put",
]
