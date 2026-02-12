"""Provider-agnostic time effects."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from doeff.effects.base import Effect, EffectBase, create_effect_with_trace


def _coerce_finite_float(value: float, *, name: str) -> float:
    if not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be float, got {type(value).__name__}")
    coerced = float(value)
    if math.isnan(coerced) or math.isinf(coerced):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return coerced


@dataclass(frozen=True)
class DelayEffect(EffectBase):
    """Sleep for a duration in seconds."""

    seconds: float

    def __post_init__(self) -> None:
        seconds = _coerce_finite_float(self.seconds, name="seconds")
        if seconds < 0.0:
            raise ValueError("seconds must be >= 0.0")
        object.__setattr__(self, "seconds", seconds)


@dataclass(frozen=True)
class WaitUntilEffect(EffectBase):
    """Wait until a specific epoch timestamp."""

    target: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "target", _coerce_finite_float(self.target, name="target"))


@dataclass(frozen=True)
class GetTimeEffect(EffectBase):
    """Read current epoch time."""


@dataclass(frozen=True)
class ScheduleAtEffect(EffectBase):
    """Schedule a program for execution at a specific epoch timestamp."""

    time: float
    program: Any

    def __post_init__(self) -> None:
        object.__setattr__(self, "time", _coerce_finite_float(self.time, name="time"))


def delay(seconds: float) -> DelayEffect:
    return create_effect_with_trace(DelayEffect(seconds=seconds))


def wait_until(target: float) -> WaitUntilEffect:
    return create_effect_with_trace(WaitUntilEffect(target=target))


def get_time() -> GetTimeEffect:
    return create_effect_with_trace(GetTimeEffect())


def schedule_at(time: float, program: Any) -> ScheduleAtEffect:
    return create_effect_with_trace(ScheduleAtEffect(time=time, program=program))


def Delay(seconds: float) -> Effect:  # noqa: N802
    return create_effect_with_trace(DelayEffect(seconds=seconds), skip_frames=3)


def WaitUntil(target: float) -> Effect:  # noqa: N802
    return create_effect_with_trace(WaitUntilEffect(target=target), skip_frames=3)


def GetTime() -> Effect:  # noqa: N802
    return create_effect_with_trace(GetTimeEffect(), skip_frames=3)


def ScheduleAt(time: float, program: Any) -> Effect:  # noqa: N802
    return create_effect_with_trace(ScheduleAtEffect(time=time, program=program), skip_frames=3)


__all__ = [
    "Delay",
    "DelayEffect",
    "GetTime",
    "GetTimeEffect",
    "ScheduleAt",
    "ScheduleAtEffect",
    "WaitUntil",
    "WaitUntilEffect",
    "delay",
    "get_time",
    "schedule_at",
    "wait_until",
]
