# report.py
from datetime import datetime, timedelta, timezone

from doeff import Cancel, Pass, Spawn, TaskCancelledError, Wait, do, handler, run
from doeff_core_effects.scheduler import scheduled
from doeff_time import (
    Delay,
    DelayEffect,
    GetTime,
    RaceWithin,
    WaitUntil,
    WaitUntilEffect,
    sim_time_handler,
)


# The program: every weekday at 09:00, build the report; give a build 10 minutes.
def next_weekday_at(now: datetime, hour: int) -> datetime:
    at = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if at <= now:
        at += timedelta(days=1)
    while at.weekday() >= 5:  # Saturday and Sunday
        at += timedelta(days=1)
    return at


@do
def build(minutes: int):  # a step that takes as long as we say
    yield Delay(minutes * 60)
    return f"built in {minutes} min"


@do
def within(seconds: float, program):  # the value of program, or "timed out"
    task = yield Spawn(program)
    outcome = yield RaceWithin((task,), seconds)
    if not outcome.timed_out:
        return outcome.value
    yield Cancel(task)
    try:
        return (yield Wait(task))  # raises once the cancelled task has unwound
    except TaskCancelledError:
        return "timed out"


@do
def report_day(minutes: int):  # wait for the next weekday 09:00, then build
    now = yield GetTime()
    yield WaitUntil(next_weekday_at(now, 9))
    started = yield GetTime()
    result = yield within(10 * 60, build(minutes))
    finished = yield GetTime()
    return f"{started:%a %d %H:%M}-{finished:%H:%M} {result}"


@do
def daily_report(build_minutes: list[int]):  # one line per day
    lines: tuple[str, ...] = ()
    for minutes in build_minutes:
        lines = (*lines, (yield report_day(minutes)))
    return lines


# Layers: a virtual clock that starts on a Friday evening, and a printout of every wait.
FRIDAY_EVENING = datetime(2026, 11, 27, 17, 0, tzinfo=timezone.utc)


def simulated(program):  # virtual time from Friday 17:00
    return scheduled(sim_time_handler(start_time=FRIDAY_EVENING)(program))


@handler
def timeline(effect: DelayEffect | WaitUntilEffect, k):  # print every wait as it starts
    now = yield GetTime()
    if isinstance(effect, DelayEffect):
        print(f"{now:%a %H:%M} wait {effect.seconds:g} s")
    else:
        print(f"{now:%a %H:%M} wait until {effect.target:%a %H:%M}")
    return (yield Pass(effect, k))


if __name__ == "__main__":
    print("-- the report:")
    for line in run(simulated(daily_report([3, 12, 5]))):
        print(line)
    print("-- the waits:")
    run(simulated(timeline(daily_report([3, 12]))))
