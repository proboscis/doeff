"""
Dependency injection effects compatible with pinjected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .base import Effect, EffectBase, create_effect_with_trace
from ._validators import ensure_str


@dataclass(frozen=True)
class DepInjectEffect(EffectBase):
    """Resolves the dependency identified by key and yields the bound object."""

    key: str

    def __post_init__(self) -> None:
        ensure_str(self.key, name="key")

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "DepInjectEffect":
        return self


def inject(key: str) -> DepInjectEffect:
    return create_effect_with_trace(DepInjectEffect(key=key))


def Dep(key: str) -> Effect:
    return create_effect_with_trace(DepInjectEffect(key=key), skip_frames=3)


__all__ = [
    "DepInjectEffect",
    "inject",
    "Dep",
]
