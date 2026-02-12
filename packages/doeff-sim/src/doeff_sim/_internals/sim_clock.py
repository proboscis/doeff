"""Simulation clock state."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SimClock:
    current_time: float = 0.0

    def advance_by(self, seconds: float) -> float:
        delay = float(seconds)
        if delay < 0.0:
            raise ValueError("delay seconds must be >= 0")
        self.current_time += delay
        return self.current_time

    def advance_to(self, target_time: float) -> float:
        target = float(target_time)
        if target > self.current_time:
            self.current_time = target
        return self.current_time

    def set_time(self, new_time: float) -> float:
        self.current_time = float(new_time)
        return self.current_time
