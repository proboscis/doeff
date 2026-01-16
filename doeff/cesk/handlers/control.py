"""Control flow effect handlers.

Handles effects that modify control flow:
- LocalEffect: Run program with modified environment
- InterceptEffect: Transform effects passing through
- WriterListenEffect: Capture log output
- GatherEffect: Run multiple programs and collect results
- ResultSafeEffect: Capture errors and return Result
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from doeff._vendor import FrozenDict
from doeff.cesk.actions import RunProgram
from doeff.cesk.handlers import HandlerContext, HandlerResult

if TYPE_CHECKING:
    from doeff.effects import (
        GatherEffect,
        InterceptEffect,
        LocalEffect,
        ResultSafeEffect,
        WriterListenEffect,
    )


def handle_local(effect: LocalEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle LocalEffect: run program with modified environment.

    Creates a new environment with effect.env_update merged in,
    then runs the sub-program in that environment.
    """
    new_env = ctx.env | FrozenDict(effect.env_update)
    return HandlerResult.run_program(effect.sub_program, new_env)


def handle_intercept(effect: InterceptEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle InterceptEffect: transform effects passing through.

    Runs the sub-program and transforms any effects it yields
    using the provided transform functions.
    """
    return HandlerResult.run_program(effect.program)


def handle_listen(effect: WriterListenEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle WriterListenEffect: capture log output.

    Runs the sub-program and captures any log entries written
    during its execution, returning them along with the result.
    """
    return HandlerResult.run_program(effect.sub_program)


def handle_gather(effect: GatherEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle GatherEffect: run multiple programs and collect results.

    Runs each program sequentially (in the current implementation)
    and collects all results into a list.
    """
    programs = list(effect.programs)
    if not programs:
        # Empty gather returns empty list immediately
        return HandlerResult.resume([])

    # Run first program, rest will be handled by GatherFrame
    first = programs[0]
    return HandlerResult.run_program(first)


def handle_safe(effect: ResultSafeEffect, ctx: HandlerContext) -> HandlerResult:
    """Handle ResultSafeEffect: capture errors and return Result.

    Runs the sub-program and converts any exception to Err,
    or wraps successful result in Ok.
    """
    return HandlerResult.run_program(effect.sub_program)


__all__ = [
    "handle_local",
    "handle_intercept",
    "handle_listen",
    "handle_gather",
    "handle_safe",
]
