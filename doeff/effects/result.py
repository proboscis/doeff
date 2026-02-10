"""Result/error handling effects."""

from __future__ import annotations

from dataclasses import dataclass

from ._program_types import ProgramLike
from ._validators import ensure_program_like
from .base import EffectBase


@dataclass(frozen=True)
class ResultSafeEffect(EffectBase):
    """Runs the sub-program and yields a Result for success/failure."""

    sub_program: ProgramLike

    def __post_init__(self) -> None:
        ensure_program_like(self.sub_program, name="sub_program")


def _safe_program(sub_program: ProgramLike):
    ensure_program_like(sub_program, name="sub_program")
    return ResultSafeEffect(sub_program=sub_program)


def safe(sub_program: ProgramLike):
    return _safe_program(sub_program)


def Safe(sub_program: ProgramLike):
    return _safe_program(sub_program)


__all__ = [
    "ResultSafeEffect",
    "Safe",
    "safe",
]
