"""Virtual clock state for simulation time handlers."""

from __future__ import annotations

import math
from dataclasses import dataclass


def _coerce_finite_float(value: float, *, name: str) -> float:
    if not isinstance(value, int | float):
        raise TypeError(f"{name} must be float, got {type(value).__name__}")
    coerced = float(value)
    if math.isnan(coerced) or math.isinf(coerced):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return coerced


@dataclass
class SimClock:
    _mut_current_time: float = 0.0

    def __post_init__(self) -> None:
        self._mut_current_time = _coerce_finite_float(
            self._mut_current_time,
            name="current_time",
        )

    @property
    def current_time(self) -> float:
        return self._mut_current_time

    def advance_to(self, target_time: float) -> float:
        target = _coerce_finite_float(target_time, name="target_time")
        if target > self._mut_current_time:
            self._mut_current_time = target
        return self.current_time

    def jump_to(self, new_time: float) -> float:
        self._mut_current_time = _coerce_finite_float(new_time, name="new_time")
        return self.current_time
