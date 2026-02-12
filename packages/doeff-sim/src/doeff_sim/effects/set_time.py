"""Set simulation time explicitly."""

from __future__ import annotations

from dataclasses import dataclass

from doeff.effects.base import Effect, EffectBase, create_effect_with_trace


@dataclass(frozen=True)
class SetTimeEffect(EffectBase):
    """Set the current simulated clock time (epoch seconds)."""

    time: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "time", float(self.time))


def set_time(time: float) -> SetTimeEffect:
    return create_effect_with_trace(SetTimeEffect(time=time))


def SetTime(time: float) -> Effect:
    return create_effect_with_trace(SetTimeEffect(time=time), skip_frames=3)


__all__ = [
    "SetTime",
    "SetTimeEffect",
    "set_time",
]
