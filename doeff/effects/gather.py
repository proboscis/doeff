from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import Effect, EffectBase, create_effect_with_trace
from .spawn import Future


@dataclass(frozen=True)
class GatherEffect(EffectBase):
    futures: tuple[Future[Any], ...]


def _validate_futures(items: tuple[Any, ...]) -> tuple[Future[Any], ...]:
    for i, item in enumerate(items):
        if not isinstance(item, Future):
            raise TypeError(
                f"Gather expects Futures, got {type(item).__name__} at index {i}. "
                f"Use Spawn to create Futures from Programs."
            )
    return items  # type: ignore[return-value]


def gather(*items: Future[Any]) -> GatherEffect:
    validated = _validate_futures(tuple(items))
    return create_effect_with_trace(GatherEffect(futures=validated))


def Gather(*items: Future[Any]) -> Effect:
    validated = _validate_futures(tuple(items))
    return create_effect_with_trace(
        GatherEffect(futures=validated), skip_frames=3
    )


__all__ = [
    "Gather",
    "GatherEffect",
    "gather",
]
