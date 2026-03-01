"""Virtual monotonic clock for simulation handlers."""


from dataclasses import dataclass
from datetime import datetime, timezone

from .validation import ensure_aware_datetime


@dataclass
class SimClock:
    current_time: datetime = datetime(1970, 1, 1, tzinfo=timezone.utc)

    def __post_init__(self) -> None:
        self.current_time = ensure_aware_datetime(self.current_time, name="current_time")

    def advance_to(self, target_time: datetime) -> datetime:
        target = ensure_aware_datetime(target_time, name="target_time")
        self.current_time = max(self.current_time, target)
        return self.current_time

    def set_time(self, new_time: datetime) -> datetime:
        self.current_time = ensure_aware_datetime(new_time, name="new_time")
        return self.current_time
