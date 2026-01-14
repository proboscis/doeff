"""Kontinuation frame types for the CESK machine."""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

from doeff.cesk.types import Environment

if TYPE_CHECKING:
    from doeff.program import KleisliProgramCall, Program
    from doeff.types import Effect


@dataclass
class ReturnFrame:
    """Resume generator with value.

    Note: This frame is intentionally NOT frozen because Python generators
    are mutable objects. The generator is owned exclusively by this frame.
    Each ReturnFrame owns one generator and advances it on each step.
    This is the unavoidable impurity in implementing coroutine-style effects.
    """

    generator: Generator[Any, Any, Any]
    saved_env: Environment
    program_call: KleisliProgramCall | None = None


@dataclass(frozen=True)
class CatchFrame:
    """Error boundary - catches exceptions and invokes handler.

    On success: passes value through, restores saved_env
    On error: runs handler(ex) with saved_env, result becomes the value
    """

    handler: Callable[[Exception], Program]
    saved_env: Environment


@dataclass(frozen=True)
class FinallyFrame:
    """Cleanup on exit - runs cleanup program on both success and error.

    On success: cleanup runs, then original value returned
    On error: cleanup runs, then original error re-raised
    If cleanup FAILS: cleanup error REPLACES original value/error
    """

    cleanup_program: Program
    saved_env: Environment


@dataclass(frozen=True)
class LocalFrame:
    """Restore environment after scoped execution.

    On BOTH success AND error: restores environment before continuing.
    """

    restore_env: Environment


@dataclass(frozen=True)
class InterceptFrame:
    """Transform effects passing through. Marks interception boundary.

    Non-control-flow effects get transformed as they bubble up.
    Control flow effects pass through unchanged.

    Chain semantics (inner -> outer):
    - All InterceptFrames in K are traversed in order
    - Each frame's transforms are applied to the (possibly transformed) effect
    - First transform returning Effect/Program within a frame wins
    - Outer interceptors see effects that may have been transformed by inner ones
    - If all transforms return None -> original effect unchanged

    This enables composable interception.
    """

    transforms: tuple[Callable[[Effect], Effect | Program | None], ...]


@dataclass(frozen=True)
class ListenFrame:
    """Capture log output from sub-computation.

    On success: returns (value, captured_logs)
    On error: propagates error (logs up to error point remain in S for debugging)
    """

    log_start_index: int


@dataclass(frozen=True)
class GatherFrame:
    """Collect results from sequential program execution.

    Sequential: each program runs with saved_env, sees S modifications from previous.
    On success: returns list of all results, restores saved_env.
    On error: propagates immediately (partial results discarded), restores saved_env.
    """

    remaining_programs: list[Program]
    collected_results: list[Any]
    saved_env: Environment


@dataclass(frozen=True)
class SafeFrame:
    """Safe boundary - captures K stack on error, returns Result.

    On success: wraps value in Ok and passes through
    On error: captures K stack snapshot, wraps in Err with traceback attached
    """

    saved_env: Environment


Frame: TypeAlias = (
    ReturnFrame
    | CatchFrame
    | FinallyFrame
    | LocalFrame
    | InterceptFrame
    | ListenFrame
    | GatherFrame
    | SafeFrame
)

Kontinuation: TypeAlias = list[Frame]


__all__ = [
    "ReturnFrame",
    "CatchFrame",
    "FinallyFrame",
    "LocalFrame",
    "InterceptFrame",
    "ListenFrame",
    "GatherFrame",
    "SafeFrame",
    "Frame",
    "Kontinuation",
]
