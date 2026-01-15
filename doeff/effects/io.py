"""IO effects."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from .base import Effect, EffectBase, create_effect_with_trace
from ._validators import ensure_callable


@dataclass(frozen=True)
class IOPerformEffect(EffectBase):
    """Runs the supplied callable and yields whatever value it returns."""

    action: Callable[[], Any]

    def __post_init__(self) -> None:
        ensure_callable(self.action, name="action")

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "IOPerformEffect":
        return self


def perform(action: Callable[[], Any]) -> IOPerformEffect:
    return create_effect_with_trace(IOPerformEffect(action=action))


def run(action: Callable[[], Any]) -> IOPerformEffect:
    return perform(action)


def IO(action: Callable[[], Any]) -> Effect:
    return create_effect_with_trace(IOPerformEffect(action=action), skip_frames=3)


__all__ = [
    "IOPerformEffect",
    "IO",
    "perform",
    "run",
]
