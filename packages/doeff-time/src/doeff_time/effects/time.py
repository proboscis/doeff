"""Provider-agnostic time effects."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from doeff.effects.base import Effect, EffectBase


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


@dataclass(frozen=True)
class SetTimeEffect(EffectBase):
    """Set current epoch time (simulation handlers may support this effect)."""

    time: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "time", _coerce_finite_float(self.time, name="time"))


def delay(seconds: float) -> DelayEffect:
    return DelayEffect(seconds=seconds)


def wait_until(target: float) -> WaitUntilEffect:
    return WaitUntilEffect(target=target)


def get_time() -> GetTimeEffect:
    return GetTimeEffect()


def schedule_at(time: float, program: Any) -> ScheduleAtEffect:
    return ScheduleAtEffect(time=time, program=program)


def set_time(time: float) -> SetTimeEffect:
    return SetTimeEffect(time=time)


def Delay(seconds: float) -> Effect:  # noqa: N802
    return DelayEffect(seconds=seconds)


def WaitUntil(target: float) -> Effect:  # noqa: N802
    return WaitUntilEffect(target=target)


def GetTime() -> Effect:  # noqa: N802
    return GetTimeEffect()


def ScheduleAt(time: float, program: Any) -> Effect:  # noqa: N802
    return ScheduleAtEffect(time=time, program=program)


def SetTime(time: float) -> Effect:  # noqa: N802
    return SetTimeEffect(time=time)


__all__ = [
    "Delay",
    "DelayEffect",
    "GetTime",
    "GetTimeEffect",
    "ScheduleAt",
    "ScheduleAtEffect",
    "SetTime",
    "SetTimeEffect",
    "WaitUntil",
    "WaitUntilEffect",
    "delay",
    "get_time",
    "schedule_at",
    "set_time",
    "wait_until",
]
