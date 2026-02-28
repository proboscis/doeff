"""Virtual monotonic clock for simulation handlers."""


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
    current_time: float = 0.0

    def __post_init__(self) -> None:
        self.current_time = _coerce_finite_float(self.current_time, name="current_time")

    def advance_to(self, target_time: float) -> float:
        target = _coerce_finite_float(target_time, name="target_time")
        self.current_time = max(self.current_time, target)
        return self.current_time

    def set_time(self, new_time: float) -> float:
        self.current_time = _coerce_finite_float(new_time, name="new_time")
        return self.current_time
