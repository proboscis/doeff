"""Atomic shared-state effects for safe parallel updates."""


from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ._validators import ensure_callable, ensure_optional_callable, ensure_str
from .base import Effect, EffectBase


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
    return AtomicGetEffect(key=key, default_factory=default_factory)


def atomic_update(
    key: str,
    updater: Callable[[Any], Any],
    *,
    default_factory: Callable[[], Any] | None = None,
) -> AtomicUpdateEffect:
    return AtomicUpdateEffect(key=key, updater=updater, default_factory=default_factory)


def AtomicGet(key: str, *, default_factory: Callable[[], Any] | None = None) -> Effect:
    return AtomicGetEffect(key=key, default_factory=default_factory)


def AtomicUpdate(
    key: str,
    updater: Callable[[Any], Any],
    *,
    default_factory: Callable[[], Any] | None = None,
) -> Effect:
    return AtomicUpdateEffect(key=key, updater=updater, default_factory=default_factory)


__all__ = [
    "AtomicGet",
    "AtomicGetEffect",
    "AtomicUpdate",
    "AtomicUpdateEffect",
    "atomic_get",
    "atomic_update",
]
