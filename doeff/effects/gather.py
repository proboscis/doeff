from __future__ import annotations

from typing import TYPE_CHECKING, Any

import doeff_vm

from .base import Effect, create_effect_with_trace
from .spawn import TaskCancelledError, Waitable, is_task_cancelled, normalize_waitable

if TYPE_CHECKING:
    from doeff.program import ProgramBase


GatherEffect = doeff_vm.GatherEffect


def _validate_gather_items(items: tuple[Any, ...]) -> tuple[Any, ...]:
    from doeff.program import ProgramBase
    from doeff.types import EffectBase

    normalized: list[Any] = []
    for i, item in enumerate(items):
        if isinstance(item, (ProgramBase, EffectBase)):
            normalized.append(item)
            continue
        try:
            normalized.append(normalize_waitable(item))
        except TypeError as exc:
            raise TypeError(
                f"Gather expects Waitable, Program, or Effect, got {type(item).__name__} at index {i}."
            ) from exc
    return tuple(normalized)


def gather(*items: "Waitable[Any] | ProgramBase[Any]") -> GatherEffect:
    validated = _validate_gather_items(tuple(items))
    return create_effect_with_trace(GatherEffect(items=validated))


def Gather(*items: "Waitable[Any] | ProgramBase[Any]") -> Any:
    validated = _validate_gather_items(tuple(items))

    from doeff import do

    @do
    def _program():
        for item in validated:
            if is_task_cancelled(item):
                raise TaskCancelledError("Task was cancelled")

        return (
            yield create_effect_with_trace(
                GatherEffect(items=validated),
                skip_frames=3,
            )
        )

    return _program()


__all__ = [
    "Gather",
    "GatherEffect",
    "gather",
]
