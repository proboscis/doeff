"""Result/error handling effects."""

from __future__ import annotations

import doeff_vm

from ._program_types import ProgramLike
from ._validators import ensure_program_like

ResultSafeEffect = doeff_vm.ResultSafeEffect


def _try_program(sub_program: ProgramLike):
    ensure_program_like(sub_program, name="sub_program")
    return ResultSafeEffect(sub_program=sub_program)


def try_(sub_program: ProgramLike):
    return _try_program(sub_program)


def Try(sub_program: ProgramLike):
    return _try_program(sub_program)


__all__ = [
    "ResultSafeEffect",
    "Try",
    "try_",
]
