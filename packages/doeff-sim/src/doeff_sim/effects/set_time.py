"""Set simulation time explicitly."""

from __future__ import annotations

from dataclasses import dataclass

from doeff.effects.base import Effect, EffectBase


@dataclass(frozen=True)
class SetTimeEffect(EffectBase):
    """Set the current simulated clock time (epoch seconds)."""

    time: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "time", float(self.time))


def set_time(time: float) -> SetTimeEffect:
    return SetTimeEffect(time=time)


def SetTime(time: float) -> Effect:
    return SetTimeEffect(time=time)


__all__ = [
    "SetTime",
    "SetTimeEffect",
    "set_time",
]
