"""Atomic shared-state effects for safe parallel updates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ._validators import ensure_callable, ensure_optional_callable, ensure_str
from .base import Effect, EffectBase, create_effect_with_trace


@dataclass(frozen=True)
class AtomicGetEffect(EffectBase):
    """Retrieve the shared value for ``key`` with optional default initialization."""

    key: str
    default_factory: Callable[[], Any] | None = None

    def __post_init__(self) -> None:
        ensure_str(self.key, name="key")
        ensure_optional_callable(self.default_factory, name="default_factory")


@dataclass(frozen=True)
class AtomicUpdateEffect(EffectBase):
    """Apply ``updater`` to the shared value for ``key`` atomically."""

    key: str
    updater: Callable[[Any], Any]
    default_factory: Callable[[], Any] | None = None

    def __post_init__(self) -> None:
        ensure_str(self.key, name="key")
        ensure_callable(self.updater, name="updater")
        ensure_optional_callable(self.default_factory, name="default_factory")


def atomic_get(key: str, *, default_factory: Callable[[], Any] | None = None) -> AtomicGetEffect:
    return create_effect_with_trace(
        AtomicGetEffect(key=key, default_factory=default_factory)
    )


def atomic_update(
    key: str,
    updater: Callable[[Any], Any],
    *,
    default_factory: Callable[[], Any] | None = None,
) -> AtomicUpdateEffect:
    return create_effect_with_trace(
        AtomicUpdateEffect(key=key, updater=updater, default_factory=default_factory)
    )


def AtomicGet(key: str, *, default_factory: Callable[[], Any] | None = None) -> Effect:
    return create_effect_with_trace(
        AtomicGetEffect(key=key, default_factory=default_factory), skip_frames=3
    )


def AtomicUpdate(
    key: str,
    updater: Callable[[Any], Any],
    *,
    default_factory: Callable[[], Any] | None = None,
) -> Effect:
    return create_effect_with_trace(
        AtomicUpdateEffect(key=key, updater=updater, default_factory=default_factory),
        skip_frames=3,
    )


__all__ = [
    "AtomicGet",
    "AtomicGetEffect",
    "AtomicUpdate",
    "AtomicUpdateEffect",
    "atomic_get",
    "atomic_update",
]
