"""State monad effects."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ._validators import ensure_callable, ensure_str
from .base import Effect, EffectBase, create_effect_with_trace


@dataclass(frozen=True)
class StateGetEffect(EffectBase):
    """Retrieves the state value for key and yields it."""

    key: str

    def __post_init__(self) -> None:
        ensure_str(self.key, name="key")

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> StateGetEffect:
        return self


@dataclass(frozen=True)
class StatePutEffect(EffectBase):
    """Updates the stored state for key and completes with no value."""

    key: str
    value: Any

    def __post_init__(self) -> None:
        ensure_str(self.key, name="key")

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> StatePutEffect:
        return self


@dataclass(frozen=True)
class StateModifyEffect(EffectBase):
    """Applies func to the current state value and yields the updated value."""

    key: str
    func: Callable[[Any], Any]

    def __post_init__(self) -> None:
        ensure_str(self.key, name="key")
        ensure_callable(self.func, name="func")

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> StateModifyEffect:
        return self


def get(key: str) -> StateGetEffect:
    return create_effect_with_trace(StateGetEffect(key=key))


def put(key: str, value: Any) -> StatePutEffect:
    return create_effect_with_trace(StatePutEffect(key=key, value=value))


def modify(key: str, f: Callable[[Any], Any]) -> StateModifyEffect:
    return create_effect_with_trace(StateModifyEffect(key=key, func=f))


def Get(key: str) -> Effect:
    return create_effect_with_trace(StateGetEffect(key=key), skip_frames=3)


def Put(key: str, value: Any) -> Effect:
    return create_effect_with_trace(StatePutEffect(key=key, value=value), skip_frames=3)


def Modify(key: str, f: Callable[[Any], Any]) -> Effect:
    return create_effect_with_trace(StateModifyEffect(key=key, func=f), skip_frames=3)


__all__ = [
    "Get",
    "Modify",
    "Put",
    "StateGetEffect",
    "StateModifyEffect",
    "StatePutEffect",
    "get",
    "modify",
    "put",
]
