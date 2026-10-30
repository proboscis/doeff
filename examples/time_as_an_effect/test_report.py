# test_report.py
from datetime import datetime, timezone

from doeff import do, run

from clock import GetTime
from report import daily_report, simulated


@do
def report_and_the_time_after(build_minutes: list[int]):  # the test asks the same clock
    lines = yield daily_report(build_minutes)
    return lines, (yield GetTime())


def test_a_slow_build_times_out_and_the_next_day_still_runs():
    lines, stopped_at = run(simulated(report_and_the_time_after([3, 12, 5])))
    assert lines[1] == "Tue 01 09:00-09:10 timed out"
    assert lines[2] == "Wed 02 09:00-09:05 built in 5 min"
    assert stopped_at == datetime(2026, 12, 2, 9, 5, tzinfo=timezone.utc)
