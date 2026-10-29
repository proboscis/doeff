# test_report.py
from datetime import datetime, timezone

from doeff import run
from doeff_core_effects.scheduler import scheduled
from doeff_time import SimClock, sim_time_handler

from report import FRIDAY_EVENING, daily_report


def test_a_slow_build_times_out_and_the_next_day_still_runs():
    clock = SimClock(FRIDAY_EVENING)
    lines = run(scheduled(sim_time_handler(clock=clock)(daily_report([3, 12, 5]))))
    assert lines[1] == "Tue 01 09:00-09:10 timed out"
    assert lines[2] == "Wed 02 09:00-09:05 built in 5 min"
    assert clock.current_time == datetime(2026, 12, 2, 9, 5, tzinfo=timezone.utc)
