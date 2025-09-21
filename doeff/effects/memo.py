"""Memoization effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import Effect, EffectBase, create_effect_with_trace


@dataclass(frozen=True)
class MemoGetEffect(EffectBase):
    """Fetches the memoized value for the key and yields it."""

    key: Any


@dataclass(frozen=True)
class MemoPutEffect(EffectBase):
    """Stores the value in the memo cache and completes without a result."""

    key: Any
    value: Any


def memo_get(key: Any) -> MemoGetEffect:
    return create_effect_with_trace(MemoGetEffect(key=key))


def memo_put(key: Any, value: Any) -> MemoPutEffect:
    return create_effect_with_trace(MemoPutEffect(key=key, value=value))


def MemoGet(key: Any) -> Effect:
    return create_effect_with_trace(MemoGetEffect(key=key), skip_frames=3)


def MemoPut(key: Any, value: Any) -> Effect:
    return create_effect_with_trace(MemoPutEffect(key=key, value=value), skip_frames=3)


__all__ = [
    "MemoGetEffect",
    "MemoPutEffect",
    "memo_get",
    "memo_put",
    "MemoGet",
    "MemoPut",
]
