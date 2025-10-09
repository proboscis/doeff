"""Effects for introspecting the program call stack."""

from __future__ import annotations

from dataclasses import dataclass

from typing import Tuple

from doeff.types import CallFrame

from .base import EffectBase, create_effect_with_trace
from ._validators import ensure_non_negative_int


@dataclass(frozen=True)
class ProgramCallFrameEffect(EffectBase):
    """Return a snapshot of the program call stack frame at the given depth."""

    depth: int = 0

    def __post_init__(self) -> None:
        ensure_non_negative_int(self.depth, name="depth")

    def intercept(self, transform):  # type: ignore[override]
        return self


def ProgramCallFrame(depth: int = 0) -> ProgramCallFrameEffect:
    """Create an effect that yields the ``CallFrame`` at the requested depth.

    Args:
        depth: ``0`` (default) yields the innermost frame (current program call).
            ``1`` yields the parent frame, and so on. ``IndexError`` is raised if
            the depth exceeds the available call stack.
    """

    return create_effect_with_trace(ProgramCallFrameEffect(depth=depth))


@dataclass(frozen=True)
class ProgramCallStackEffect(EffectBase):
    """Return a snapshot of the entire program call stack."""

    def intercept(self, transform):  # type: ignore[override]
        return self


def ProgramCallStack() -> ProgramCallStackEffect:
    """Create an effect that yields the current call stack as a tuple."""

    return create_effect_with_trace(ProgramCallStackEffect())


__all__ = [
    "ProgramCallFrame",
    "ProgramCallFrameEffect",
    "ProgramCallStack",
    "ProgramCallStackEffect",
]
