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
    """Resume generator with value."""

    generator: Generator[Any, Any, Any]
    saved_env: Environment
    program_call: KleisliProgramCall | None = None


@dataclass(frozen=True)
class LocalFrame:
    """Restore environment after scoped execution."""

    restore_env: Environment


@dataclass(frozen=True)
class InterceptFrame:
    """Transform effects passing through."""

    transforms: tuple[Callable[[Effect], Effect | Program | None], ...]


@dataclass(frozen=True)
class ListenFrame:
    """Capture log output from sub-computation."""

    log_start_index: int


@dataclass(frozen=True)
class GatherFrame:
    """Collect results from sequential program execution."""

    remaining_programs: list[Program]
    collected_results: list[Any]
    saved_env: Environment


@dataclass(frozen=True)
class SafeFrame:
    """Safe boundary - captures K stack on error, returns Result."""

    saved_env: Environment


Frame: TypeAlias = (
    ReturnFrame
    | LocalFrame
    | InterceptFrame
    | ListenFrame
    | GatherFrame
    | SafeFrame
)

Kontinuation: TypeAlias = list[Frame]


__all__ = [
    "ReturnFrame",
    "LocalFrame",
    "InterceptFrame",
    "ListenFrame",
    "GatherFrame",
    "SafeFrame",
    "Frame",
    "Kontinuation",
]
