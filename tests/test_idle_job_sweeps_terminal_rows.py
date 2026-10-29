"""A job with no work keeps the same objects however many clock ticks it runs.

The scheduler keeps a terminal task/promise row until the terminal-entry sweep
(#502) finds every handle dead, and that sweep ran only once per
``HANDLE_SWEEP_INTERVAL`` (1024) id allocations. A job with no work allocates a
few ids per tick of its clock, so the rows of every tick — the awaited-value
box (``_AwaitedValue``) and clock wake (``_ClockWake``) of each Delay, the
cancelled deadline timer of each timed wait, the finished request task — piled
up for hours: production jobs with no work grew 155 tracked objects every 10
minutes (agora card ki-f9aa5fce26e3). The sweep now also runs when the
scheduler is about to block on external completions (``IDLE_SWEEP_ROWS_PER_ID``).
"""

from __future__ import annotations

import asyncio
import gc
from collections import Counter

import pytest
from doeff_core_effects import await_handler
from doeff_core_effects import try_handler
from doeff_core_effects import RaiseCase, on_raise
from doeff_core_effects import SchedulerImplementation, SchedulerIntrospection, Spawn, scheduled
from doeff_time import Delay, async_time_handler, wait_within

from doeff import do, run, with_handlers
from doeff.result import Some

# The subject is the scheduler blocking on the await bridge's real asyncio loop
# thread: each simulated sleep takes SIMULATED_SLEEP_SECONDS of real time so the
# scheduler has nothing runnable and blocks (tests/realtime_tests.toml).
pytestmark = pytest.mark.realtime

TICKS = 60
TICK_SECONDS = 600.0
WATCHED = ("RaiseCase", "_AwaitedValue", "_ClockWake", "HandlerSpec", "TaskCancelledError")
# The most objects of one watched type a tick leaves in its rows (3 clock wakes:
# the tick's sleep, the request's sleep and the deadline timer's sleep).
TICK_ROWS = 3
# How many of the latest ticks may still be unswept at a count (see the test).
SETTLING_TICKS = 2
IMPLEMENTATIONS: tuple[SchedulerImplementation, ...] = ("rust", "python")


DEADLINE_SECONDS = 30.0


# How long every simulated sleep takes on the wall clock: long enough that the
# scheduler has nothing runnable and blocks, as a job with no work does.
SIMULATED_SLEEP_SECONDS = 0.002


async def _simulated_sleep(seconds: float) -> None:
    """The simulated clock: the tick's sleep ends at once and a request's deadline never comes
    (the request answers first, so its deadline timer is cancelled while it sleeps)."""
    if seconds >= DEADLINE_SECONDS and seconds != TICK_SECONDS:
        await asyncio.Event().wait()
    await asyncio.sleep(SIMULATED_SLEEP_SECONDS)


class _Refused:
    """A Raise reason the request's on-raise case names (never raised)."""


@do
def _request(tick: int):
    yield Delay(0.0)
    return tick


@do
def _idle_tick(tick: int):
    """One tick of a job with no work: sleep to the next tick, then one timed request."""
    yield Delay(TICK_SECONDS)
    request = on_raise(
        with_handlers([try_handler], _request(tick)),
        RaiseCase((_Refused,), lambda _reason: Some(-1)),
    )
    task = yield Spawn(request)
    return (yield wait_within(task, DEADLINE_SECONDS))


def _counts() -> dict[str, int]:
    gc.collect()
    counts = Counter(type(obj).__name__ for obj in gc.get_objects())
    return {name: counts[name] for name in WATCHED}


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_an_idle_job_keeps_the_same_objects_over_its_ticks(
    implementation: SchedulerImplementation,
) -> None:
    # Failure case: before the idle sweep every tick left its rows, so after
    # TICKS ticks _ClockWake, _AwaitedValue and TaskCancelledError were about
    # 177, 118 and 59 above their count after the first tick (TICK_ROWS each
    # tick), and the scheduler's tables grew the same way. Whether the bridge
    # thread has delivered the last cancelled timer's end when the scheduler
    # blocks varies from run to run, so the latest ticks' rows may still be
    # unswept at a count: the counts may differ by up to SETTLING_TICKS ticks'
    # rows, never by a number that grows with TICKS.
    @do
    def job():
        yield _idle_tick(0)
        first = (_counts(), (yield SchedulerIntrospection()))
        for tick in range(1, TICKS):
            yield _idle_tick(tick)
        return first, (_counts(), (yield SchedulerIntrospection()))

    program = with_handlers([await_handler(), async_time_handler(sleep=_simulated_sleep)], job())
    (first_counts, first_tables), (last_counts, last_tables) = run(
        scheduled(program, implementation=implementation)
    )

    slack = SETTLING_TICKS * TICK_ROWS
    grown = {name: last_counts[name] - first_counts[name] for name in WATCHED}
    assert all(growth <= slack for growth in grown.values()), (first_counts, last_counts)
    grown_tables = {
        table: last_tables[table] - first_tables[table]
        for table in ("tasks", "promises", "handle_refs")
    }
    assert all(growth <= 2 * slack for growth in grown_tables.values()), (first_tables, last_tables)
