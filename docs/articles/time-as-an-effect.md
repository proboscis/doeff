# Time as an effect: days of waiting in milliseconds, the same code on the wall clock

*Code that waits asks a handler for time. The handler holds the rest of the code and decides when it runs, so a test runs days of a schedule in milliseconds and production runs the same code on the wall clock.*

This is part 3 of a series on algebraic effects in Python with doeff. Part 1, [Algebraic effects for Python](algebraic-effects-in-python.md), introduces effects and handlers. The previous part, *Yield and async: who answers when your Python code pauses*, compares `yield` with `await`, and the next one is about recording the answers handlers give and replaying them.

`await asyncio.sleep(1800)` waits half an hour, in production and in your test. Code that waits is hard to check, for three reasons that tend to come together:

- A wait takes as long in a test as it does in production.
- Two waits that run at once must end in the right order, so making each wait return at once is not enough.
- Code that reads the clock, such as "the next weekday at 09:00", gives a different answer depending on when the test runs.

In `asyncio`, the event loop owns the clock: `asyncio.sleep` puts a timer on the loop, and the loop decides when your coroutine goes on. This post moves that decision into a handler. The code asks for time with effects, and the handler that answers them receives the rest of the code, so the handler picks the clock: a virtual one that jumps ahead, or the wall clock through `asyncio`. The code in between does not change.

If you skipped part 1, three words are enough for this post:

- An **effect** is a plain value that a `@do` function yields to ask for something. The function pauses at the `yield`.
- A **handler** is a function of `(effect, k)` marked with `@handler`. `k` is the rest of the paused function, and the handler decides how it goes on: `yield Resume(k, value)` runs the rest with `value` as the result of the `yield`.
- The **caller** installs the handlers: `handler(program)` is `program` with `handler` around it.

## Asking for time

[doeff-time](https://github.com/proboscis/doeff/tree/main/packages/doeff-time) is a package of time effects, and of handlers that answer them, for doeff. `nap` asks for time twice and never says which clock:

~~~python
# clock.py (excerpt)
from datetime import datetime, timedelta, timezone

from doeff import Gather, Resume, Spawn, do, handler, run
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import scheduled
from doeff_time import (
    Delay,
    DelayEffect,
    GetTime,
    GetTimeEffect,
    async_time_handler,
    sim_time_handler,
)

START = datetime(2026, 11, 30, 9, 0, tzinfo=timezone.utc)


@do
def nap(seconds: float):  # asks for time twice, and never says which clock
    yield Delay(seconds)
    return (yield GetTime())
~~~

- `yield Delay(seconds)` asks to wait for `seconds`.
- `yield GetTime()` asks what time it is now. The answer is a timezone-aware `datetime`.
- `Delay(...)` builds a `DelayEffect` and `GetTime()` a `GetTimeEffect`. A handler names these classes in its annotation to say which effects it takes.

Run `nap(20)` with no handler, and it stops at its `Delay` with `UnhandledEffect`: the effect has no meaning until a handler gives it one.

## A clock is a handler that decides when `k` runs

Here is a clock of our own. It keeps the time in a variable. On `Delay`, it moves the time forward and resumes `k` at once. On `GetTime`, it resumes `k` with the time it keeps:

~~~python
# clock.py (excerpt)
def ticking(start: datetime):  # a clock of our own: resume k at once, move the hands
    now = start

    @handler
    def clock(effect: DelayEffect | GetTimeEffect, k):
        nonlocal now
        if isinstance(effect, DelayEffect):
            now += timedelta(seconds=effect.seconds)
            return (yield Resume(k, None))
        return (yield Resume(k, now))

    return clock
~~~

~~~python
print(run(ticking(START)(nap(20))))
~~~

~~~text
2026-11-30 09:00:20+00:00
~~~

Twenty seconds passed on this clock, and none on yours. For one task, that is all a virtual clock has to do. Now two tasks wait at once, one for 20 seconds and one for 10:

~~~python
# clock.py (excerpt)
@do
def two_naps(slow: float = 20, fast: float = 10):  # two tasks wait at once
    start = yield GetTime()
    slow_task = yield Spawn(nap(slow))
    fast_task = yield Spawn(nap(fast))
    woke = yield Gather(slow_task, fast_task)
    return [(at - start).total_seconds() for at in woke]
~~~

`Spawn` starts a program as a task, and `Gather` waits for the tasks and returns their results in the order of its arguments. Both are effects too, and `scheduled` is doeff's reference handler for them. Each result is how long after the start that task woke up:

~~~python
print(run(scheduled(ticking(START)(two_naps()))))
~~~

~~~text
[20.0, 30.0]
~~~

The 10-second nap woke 30 seconds after the start. `ticking` resumed the slow task's `k` at once, so the slow task's 20 seconds were on the clock before the fast task began its wait, and the fast task added 10 more. A clock for several tasks cannot answer each wait as it comes. It has to hold the `k` of every waiting task until no task can run, then move the time to the earliest wake-up and resume that task first.

doeff-time's `sim_time_handler` is a clock that does this:

~~~python
print(run(scheduled(sim_time_handler(start_time=START)(two_naps()))))
~~~

~~~text
[20.0, 10.0]
~~~

The slow task woke 20 seconds after the start and the fast one after 10, and the run took no time. Here is that run, one message at a time:

```mermaid
sequenceDiagram
    participant slow as nap(20)
    participant fast as nap(10)
    participant clock as sim_time_handler
    slow->>clock: yield Delay(20), paused as k1
    fast->>clock: yield Delay(10), paused as k2
    Note over clock: no task can run: move to 09:00:10
    clock->>fast: Resume(k2, None)
    fast->>clock: yield GetTime()
    clock->>fast: Resume(k2, 09:00:10)
    Note over clock: no task can run: move to 09:00:20
    clock->>slow: Resume(k1, None)
```

`sim_time_handler` keeps each waiting `k` until its wake-up time. The diagram writes each resume as `Resume`; in its code, `sim_time_handler` resumes with `Transfer(k, value)`, which part 1 introduced: the task goes on, and the handler does not get control back. It moves the clock from a task of idle priority, which the scheduler runs only when every other task is waiting, so the time moves only when no task can run. It reaches the scheduler only through effects that any program can yield, and the docs chapter [Why Effects?](https://github.com/proboscis/doeff/blob/main/docs/20-why-effects-over-di.md#virtual-time-hours-of-waiting-in-an-instant) builds a clock like it in about 30 lines.

That is what makes time an effect rather than a function you call. A handler decides what a `yield` returns, and because it holds `k`, it also decides when the rest of the program runs.

## Days of a schedule in milliseconds

Now a program with more time in it. Every weekday at 09:00 it builds a report, and a build gets 10 minutes:

~~~python
# report.py (excerpt)
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
~~~

- `next_weekday_at` is plain Python. It takes `now` as an argument, so it never reads a clock.
- `build` takes as long as we tell it to, so that one build can be too slow.
- `within` is a timeout. It starts `program` as a task and yields `RaceWithin((task,), seconds)`: wait for the task for at most `seconds`. If the deadline comes first, it cancels the task and waits for it to unwind, and `Wait` raises `TaskCancelledError` at that `yield`.
- `report_day` asks for the time with `GetTime` and waits with `WaitUntil`. It reads no clock of its own, so what it does depends on which clock answers, not on when it runs.
- `daily_report` runs one `report_day` per build. Yielding a program from a `@do` function runs it and gives back its result.

Start the virtual clock on a Friday evening:

~~~python
# report.py (excerpt)
FRIDAY_EVENING = datetime(2026, 11, 27, 17, 0, tzinfo=timezone.utc)


def simulated(program):  # virtual time from Friday 17:00
    return scheduled(sim_time_handler(start_time=FRIDAY_EVENING)(program))
~~~

~~~python
for line in run(simulated(daily_report([3, 12, 5]))):
    print(line)
~~~

~~~text
Mon 30 09:00-09:03 built in 3 min
Tue 01 09:00-09:10 timed out
Wed 02 09:00-09:05 built in 5 min
~~~

The weekend went by, the 12-minute build ran into its 10-minute deadline, and the clock read 09:10 when `within` gave up on it. The run goes from Friday evening to Wednesday morning in virtual time and takes a few milliseconds. It prints the same lines every time, whatever day it is.

### A test that reads the clock afterwards

~~~python
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
~~~

`SimClock` is the virtual clock itself. Hand one to `sim_time_handler`, and the test can read where the time stopped: Wednesday 09:05, when the last build finished. The test asserts exact times, because nothing in the run depends on how fast the machine is.

### A layer of your own around any clock

A handler can also sit between the program and the clock. `timeline` prints every wait at the time it starts:

~~~python
# report.py (excerpt)
@handler
def timeline(effect: DelayEffect | WaitUntilEffect, k):  # print every wait as it starts
    now = yield GetTime()
    if isinstance(effect, DelayEffect):
        print(f"{now:%a %H:%M} wait {effect.seconds:g} s")
    else:
        print(f"{now:%a %H:%M} wait until {effect.target:%a %H:%M}")
    return (yield Pass(effect, k))
~~~

~~~python
run(simulated(timeline(daily_report([3, 12]))))
~~~

~~~text
Fri 17:00 wait until Mon 09:00
Mon 09:00 wait 180 s
Mon 09:03 wait until Tue 09:00
Tue 09:00 wait 720 s
~~~

`timeline` yields an effect of its own: its `GetTime` travels out to the clock like any other. Then `Pass(effect, k)` hands the wait and `k` on to the clock, which decides when `k` runs. `timeline` takes only `Delay` and `WaitUntil`, so the program's `GetTime` and the deadline of `RaceWithin` go past it to the clock. With it, you see the whole schedule a run makes, not only its result, and the program has no logging for it. Installed around the wall clock below, the same layer prints the waits as they happen.

## The same code on the wall clock

~~~python
# clock.py (excerpt)
def wall_clock(program):  # the wall clock, waiting on asyncio
    return scheduled(await_handler()(async_time_handler()(program)))
~~~

~~~python
print(run(wall_clock(two_naps())))
~~~

This run takes 20 seconds and prints about `[20.05, 10.05]`, because a timer on the wall clock wakes a little late. `async_time_handler` answers `Delay` by awaiting `asyncio.sleep` and `GetTime` with the system clock, and `await_handler` runs those awaits on an `asyncio` event loop under the scheduler. In the same way, `run(wall_clock(daily_report([3, 12, 5])))` waits until the next weekday at 09:00, and the timeout in `within` cancels a build after 10 real minutes.

Neither `nap` nor `daily_report` is `async def`. The program does not choose `asyncio`; the handler does. For code that blocks instead, `sync_time_handler` waits with `time.sleep`.

| Run | The clock | What the same `daily_report` does |
|---|---|---|
| test | `sim_time_handler(start_time=FRIDAY_EVENING)` | Friday evening to Wednesday morning in a few milliseconds, the same lines every run |
| test that reads the clock | `sim_time_handler(clock=SimClock(FRIDAY_EVENING))` | the same, and the test reads where the time stopped |
| test of the schedule | `timeline` inside `simulated` | every wait is printed with its virtual time |
| production | `async_time_handler()` under `await_handler()` | waits on `asyncio`; the timeout cancels after 10 real minutes |

## How this compares

Python already has ways to make waits fast in tests. We ran each with two tasks that wait 20 and 10 seconds, on CPython 3.12 with trio 0.34.0, looptime 0.9, freezegun 1.5.5 and time-machine 3.6.0:

| | A wait in a test | Two waits end in time order | The date and time move with the waits | Your code |
|---|---|---|---|---|
| `asyncio.sleep` patched to return at once | returns at once | No: both report 0 seconds, in the order they started | No | unchanged |
| freezegun `freeze_time()` | never returns: the loop's clock is frozen too (with `real_asyncio=True`, it waits for real) | — | No: `datetime.now()` stays where it was frozen | unchanged |
| time-machine `travel(..., tick=False)` | waits for real | Yes, in real time | No | unchanged |
| trio's test clock with `autojump_threshold=0`, or looptime for `asyncio` | returns at once | Yes | Partly: `trio.current_time()` or `loop.time()` moves, `datetime.now()` does not | unchanged; trio's clock needs code written for trio |
| doeff-time `sim_time_handler` | returns at once | Yes | Yes: `GetTime` and the waits are answered by one handler | written with `yield` and doeff-time's effects |

If your code is already `async` and you only need its waits to be fast, trio's autojump clock and looptime do that without touching the code, and they are the simpler choice. What doeff adds is that the clock is a handler in your own stack rather than part of the event loop. The date comes from the same clock as the waits, so a schedule like "the next weekday at 09:00" runs on virtual time. You can write the clock yourself, as `ticking` above shows, and you can put layers such as `timeline` around it. The price is the one from part 1: `yield` and `@do` in your code.

## Limits

- Virtual time moves only when every task is waiting on an effect. A task that computes in a loop holds the clock still until it yields, and a blocking call such as `time.sleep` or a socket read stops every task, on any clock.
- Only time asked for through effects is virtual. `datetime.now()` and `time.time()`, in your code or in a library it calls, still read the system clock. Pass the time in, as `next_weekday_at` does, or ask for it with `GetTime`.
- `sim_time_handler` goes inside `scheduled(...)` and outside every `Spawn` whose tasks wait on it, so that the tasks share one clock.
- A program given to doeff-time's `ScheduleAt` runs under the handlers outside the time handler, so it cannot ask for time itself.
- A timer on the wall clock wakes a little late, as the run above shows. A test that asserts exact times belongs on the virtual clock.
- doeff is 0.x and doeff-time is 0.1, so names can still change between versions.

## Try it

~~~bash
pip install doeff doeff-time pytest   # CPython 3.11 to 3.14
~~~

Every example in this post runs as shown with doeff 0.8.0 and doeff-time 0.1.0. The complete files are in [examples/time_as_an_effect](https://github.com/proboscis/doeff/tree/main/examples/time_as_an_effect), and the [doeff-time README](https://github.com/proboscis/doeff/blob/main/packages/doeff-time/README.md) gives the contract each clock keeps.

Where does time make your tests slow or flaky today: a retry with backoff, a cron-like schedule, a timeout? Issues are open at https://github.com/proboscis/doeff/issues.

The next part is about record and replay: a handler keeps the answers that the other handlers give, and runs the same program again from them.
