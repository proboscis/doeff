from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import Effect, EffectBase, create_effect_with_trace
from .spawn import Waitable


@dataclass(frozen=True)
class GatherEffect(EffectBase):
    futures: tuple[Waitable[Any], ...]
    _partial_results: tuple[Any, ...] | None = field(default=None, compare=False)


def _validate_waitables(items: tuple[Any, ...]) -> tuple[Waitable[Any], ...]:
    for i, item in enumerate(items):
        if not isinstance(item, Waitable):
            raise TypeError(
                f"Gather expects Waitable (Task or Future), got {type(item).__name__} at index {i}."
            )
    return items  # type: ignore[return-value]


def gather(*items: Waitable[Any]) -> GatherEffect:
    validated = _validate_waitables(tuple(items))
    return create_effect_with_trace(GatherEffect(futures=validated))


def Gather(*items: Waitable[Any]) -> Effect:
    validated = _validate_waitables(tuple(items))
    return create_effect_with_trace(
        GatherEffect(futures=validated), skip_frames=3
    )


__all__ = [
    "Gather",
    "GatherEffect",
    "gather",
]
