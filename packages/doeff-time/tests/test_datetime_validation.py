from datetime import datetime, timezone

import pytest
from doeff_time.effects import ScheduleAt, ScheduleAtEffect, SetTime, SetTimeEffect


def test_set_time_factory_creates_effect() -> None:
    target = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    effect = SetTime(target)
    assert isinstance(effect, SetTimeEffect)
    assert effect.time == target


def test_schedule_at_factory_creates_effect() -> None:
    target = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    effect = ScheduleAt(target, "program")
    assert isinstance(effect, ScheduleAtEffect)
    assert effect.time == target


def test_set_time_rejects_naive_datetime() -> None:
    naive = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc).replace(tzinfo=None)
    with pytest.raises(ValueError, match="time must be timezone-aware datetime"):
        SetTime(naive)


def test_schedule_at_rejects_naive_datetime() -> None:
    naive = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc).replace(tzinfo=None)
    with pytest.raises(ValueError, match="time must be timezone-aware datetime"):
        ScheduleAt(naive, "program")
