"""Control flow effect handlers: local, safe, listen, intercept, tell."""

from __future__ import annotations

from typing import TYPE_CHECKING

from doeff._vendor import FrozenDict
from doeff.cesk.frames import (
    ContinueProgram,
    ContinueValue,
    FrameResult,
    InterceptFrame,
    ListenFrame,
    LocalFrame,
    SafeFrame,
)

if TYPE_CHECKING:
    from doeff.cesk.runtime.context import HandlerContext

from doeff.effects.intercept import InterceptEffect
from doeff.effects.reader import LocalEffect
from doeff.effects.result import ResultSafeEffect
from doeff.effects.writer import WriterListenEffect, WriterTellEffect


def handle_local(
    effect: LocalEffect,
    ctx: HandlerContext,
) -> FrameResult:
    new_env = ctx.task_state.env | FrozenDict(effect.env_update)
    return ContinueProgram(
        program=effect.sub_program,
        env=new_env,
        store=ctx.store,
        k=[LocalFrame(ctx.task_state.env)] + ctx.task_state.kontinuation,
    )


def handle_safe(
    effect: ResultSafeEffect,
    ctx: HandlerContext,
) -> FrameResult:
    return ContinueProgram(
        program=effect.sub_program,
        env=ctx.task_state.env,
        store=ctx.store,
        k=[SafeFrame(ctx.task_state.env)] + ctx.task_state.kontinuation,
    )


def handle_listen(
    effect: WriterListenEffect,
    ctx: HandlerContext,
) -> FrameResult:
    log_start = len(ctx.store.get("__log__", []))
    return ContinueProgram(
        program=effect.sub_program,
        env=ctx.task_state.env,
        store=ctx.store,
        k=[ListenFrame(log_start)] + ctx.task_state.kontinuation,
    )


def handle_intercept(
    effect: InterceptEffect,
    ctx: HandlerContext,
) -> FrameResult:
    return ContinueProgram(
        program=effect.program,
        env=ctx.task_state.env,
        store=ctx.store,
        k=[InterceptFrame(effect.transforms)] + ctx.task_state.kontinuation,
    )


def handle_tell(
    effect: WriterTellEffect,
    ctx: HandlerContext,
) -> FrameResult:
    current_log = ctx.store.get("__log__", [])
    message = effect.message
    new_log = list(current_log) + [message]
    new_store = {**ctx.store, "__log__": new_log}
    return ContinueValue(
        value=None,
        env=ctx.task_state.env,
        store=new_store,
        k=ctx.task_state.kontinuation,
    )


__all__ = [
    "handle_intercept",
    "handle_listen",
    "handle_local",
    "handle_safe",
    "handle_tell",
]
