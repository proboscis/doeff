"""Properties a many-service simulation needs from ``sim_time_handler``.

Downstream (agora-controllers' simulated agora world) runs dozens of spawned
service loops that each sleep on the virtual clock. It relies on:

1. The clock advances only after every runnable task has parked — a task
   woken at ``t`` may take many scheduler steps (Spawn, Wait, Gather) and
   still reads ``t``.
2. Tasks woken at the same instant resume in the order they went to sleep
   (FIFO), so a run is reproducible.
3. The same program produces the same trace on the Python and the Rust
   scheduler.
4. The run closes out when the root returns, even while service loops are
   still parked on the clock (they are daemons), and with no close-out
   warning.
"""

import warnings
from datetime import datetime, timedelta, timezone

import pytest
from doeff_core_effects.scheduler import Gather, Spawn, Wait, scheduled
from doeff_time import (
    Delay,
    GetMonotonic,
    GetTime,
    SimClock,
    async_time_handler,
    sim_time_handler,
    sync_time_handler,
)

from doeff import do, run

START = datetime(2026, 9, 25, tzinfo=timezone.utc)
IMPLEMENTATIONS = ("python", "rust")


def _secs(value: datetime) -> float:
    return (value - START).total_seconds()


def _run(program, implementation: str):
    return run(scheduled(sim_time_handler(start_time=START)(program), implementation=implementation))


@do
def _noop():
    return None


@do
def _busy_then_read(depth: int):
    """Many scheduler round trips before reading the clock."""
    for _ in range(depth):
        child = yield Spawn(_noop())
        yield Wait(child)
    return (yield GetTime())


def _service_world(trace: list, *, services: int, rounds: int):
    @do
    def service(index: int):
        period = 1 + index % 4
        for round_no in range(rounds):
            yield Delay(period)
            woke = yield GetTime()
            # Burn scheduler steps; the clock must not move meanwhile.
            after = yield _busy_then_read(3)
            trace.append((_secs(woke), _secs(after), index, round_no))

    @do
    def root():
        tasks = []
        for index in range(services):
            tasks.append((yield Spawn(service(index))))
        yield Gather(*tasks)
        return (yield GetTime())

    return root()


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_clock_waits_until_every_task_is_parked(implementation: str) -> None:
    trace: list = []
    end = _run(_service_world(trace, services=40, rounds=5), implementation)
    assert _secs(end) == 20.0
    assert len(trace) == 40 * 5
    for woke, after, index, round_no in trace:
        period = 1 + index % 4
        assert woke == after == period * (round_no + 1)
    # The trace is ordered by virtual time (nobody saw a later tick first).
    assert [row[0] for row in trace] == sorted(row[0] for row in trace)


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_same_instant_wakes_in_sleep_order(implementation: str) -> None:
    order: list = []

    @do
    def sleeper(label: int):
        yield Delay(5)
        order.append(label)

    @do
    def root():
        # Spawn in a scrambled order; each task sleeps in spawn order.
        labels = [7, 3, 9, 1, 5, 0, 8, 2, 6, 4]
        tasks = []
        for label in labels:
            tasks.append((yield Spawn(sleeper(label))))
        yield Gather(*tasks)
        return labels

    labels = _run(root(), implementation)
    assert order == labels


def test_trace_is_identical_across_runs_and_schedulers() -> None:
    traces = []
    for implementation in IMPLEMENTATIONS * 2:
        trace: list = []
        _run(_service_world(trace, services=25, rounds=4), implementation)
        traces.append(trace)
    assert all(trace == traces[0] for trace in traces)


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_root_return_closes_out_with_services_still_sleeping(implementation: str) -> None:
    @do
    def forever(period: float):
        while True:
            yield Delay(period)

    @do
    def root():
        for period in (1.0, 2.5, 7.0):
            yield Spawn(forever(period), daemon=True)
        yield Delay(30)
        return (yield GetTime())

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        end = _run(root(), implementation)
    assert end == START + timedelta(seconds=30)


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_fractional_seconds_keep_microsecond_precision(implementation: str) -> None:
    @do
    def root():
        yield Delay(0.0015)
        yield Delay(0.25)
        return (yield GetTime())

    assert _run(root(), implementation) == START + timedelta(microseconds=251500)


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_caller_owned_clock_is_observable_and_shared(implementation: str) -> None:
    clock = SimClock(START)
    seen: list = []

    @do
    def service(period: float):
        yield Delay(period)
        seen.append(_secs(clock.current_time))

    @do
    def root():
        tasks = []
        for period in (3.0, 1.0, 2.0):
            tasks.append((yield Spawn(service(period))))
        yield Gather(*tasks)

    run(scheduled(sim_time_handler(clock=clock)(root()), implementation=implementation))
    assert seen == [1.0, 2.0, 3.0]
    assert clock.current_time == START + timedelta(seconds=3)
    # The caller may move the clock between runs; the next run starts there.
    clock.set_time(START + timedelta(hours=1))
    run(scheduled(sim_time_handler(clock=clock)(service(0.5)), implementation=implementation))
    assert clock.current_time == START + timedelta(hours=1, seconds=0.5)


def test_start_time_and_clock_are_exclusive() -> None:
    with pytest.raises(ValueError, match="either start_time or clock"):
        sim_time_handler(start_time=START, clock=SimClock(START))


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_sim_monotonic_follows_virtual_time(implementation: str) -> None:
    @do
    def root():
        first = yield GetMonotonic()
        yield Delay(12.5)
        second = yield GetMonotonic()
        return first, second

    first, second = _run(root(), implementation)
    assert first == START.timestamp()
    assert second - first == 12.5


def test_wall_clock_handlers_answer_injected_monotonic() -> None:
    readings = iter([10.0, 10.75])

    @do
    def root():
        return ((yield GetMonotonic()), (yield GetMonotonic()))

    assert run(scheduled(sync_time_handler(monotonic=lambda: next(readings))(root()))) == (10.0, 10.75)
    assert run(scheduled(async_time_handler(monotonic=lambda: 5.0)(root()))) == (5.0, 5.0)
