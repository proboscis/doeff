"""Control flow handlers: Local, Safe, Gather, Intercept."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff._vendor import FrozenDict
from doeff.cesk.actions import Resume, RunProgram

if TYPE_CHECKING:
    from doeff.cesk.step import HandlerContext
    from doeff.effects import (
        GatherEffect,
        InterceptEffect,
        LocalEffect,
        ResultSafeEffect,
    )


def handle_local(effect: LocalEffect, ctx: HandlerContext) -> tuple[RunProgram, ...]:
    new_env: FrozenDict[Any, Any] = FrozenDict({**ctx.env, **effect.env_update})
    return (RunProgram(effect.sub_program, new_env),)


def handle_safe(effect: ResultSafeEffect, ctx: HandlerContext) -> tuple[RunProgram, ...]:
    return (RunProgram(effect.sub_program),)


def handle_gather(effect: GatherEffect, ctx: HandlerContext) -> tuple[RunProgram, ...] | tuple[Resume, ...]:
    programs = effect.programs
    if not programs:
        return (Resume([]),)
    return (RunProgram(programs[0]),)


def handle_intercept(effect: InterceptEffect, ctx: HandlerContext) -> tuple[RunProgram, ...]:
    return (RunProgram(effect.program),)


__all__ = [
    "handle_local",
    "handle_safe",
    "handle_gather",
    "handle_intercept",
]
