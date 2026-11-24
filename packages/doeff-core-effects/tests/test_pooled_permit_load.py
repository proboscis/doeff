"""The connection-permit load of pooled-postgres-sql-handler (ReadSqlPermitLoad).

No PostgreSQL is needed: the permit bookkeeping is driven directly (counted-permitted on a
scheduler semaphore) and, for the handler, through statements to a refused address (each one
still takes and returns a permit and is answered with SqlUnreachable).
"""

import contextlib
import inspect
import threading
import time
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import pytest
from doeff_core_effects import (
    Cancel,
    CompletePromise,
    CreateExternalPromise,
    CreatePromise,
    CreateSemaphore,
    PostgresConnections,
    PostgresDatabase,
    ReadSqlPermitLoad,
    Spawn,
    SqlQuery,
    SqlUnreachable,
    TaskCancelledError,
    Wait,
    pooled_postgres_sql_handler,
    scheduled,
    state,
)
from doeff_core_effects._pooled_postgres_sql import (
    counted_permitted,
    permit_acquired,
    permit_asked,
    permit_load_of,
    permit_returned,
)

from doeff import do, run, with_handlers

REFUSED_DSN = "postgresql://probe@127.0.0.1:1/probe?connect_timeout=2"


def leases(size: int = 1) -> PostgresConnections:  # noqa: DOEFF022
    return PostgresConnections((PostgresDatabase(name="probe", dsn=REFUSED_DSN),), size=size)


@do
def pause():
    p = yield CreateExternalPromise()
    threading.Timer(0.01, lambda: p.complete(None)).start()
    yield Wait(p.future)


@do
def until(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, "synchronization deadline"
        yield pause()


@do
def held_until(gate):
    yield Wait(gate.future)
    return "held"


@do
def answered():
    return "answered"


@dataclass(frozen=True)
class ScriptedClock:
    read: Callable[[], float]
    calls: list[float]


def scripted_clock(ticks: tuple[float, ...]) -> ScriptedClock:  # noqa: DOEFF022
    readings = iter(ticks)
    calls: list[float] = []

    def read() -> float:  # noqa: DOEFF022
        value = next(readings)
        calls.append(value)
        return value

    return ScriptedClock(read=read, calls=calls)


def test_a_waiter_is_counted_while_waiting_and_its_wait_is_summed() -> None:
    connections = leases()
    seen = {}
    # Deterministic timeline with wakeup lag t_w1 - t_h1 = 0.005s exceeding
    # (t_w0 - t_h0) + (t_w2 - t_w1) = 0.001s + 0.001s = 0.002s:
    # holder ask/acquire: 10.000 -> 10.000 (waited 0.000)
    # waiter ask:         10.001
    # holder return:      10.011 (held 0.011)
    # waiter acquire:     10.016 (waited 0.015; wakeup lag 0.005 after holder return)
    # waiter return:      10.017 (held 0.001)
    clock = scripted_clock((10.000, 10.000, 10.001, 10.011, 10.016, 10.017))

    @do
    def program() -> Generator[Any, Any, tuple[str, str]]:
        permit = yield CreateSemaphore(1)
        gate = yield CreatePromise()
        holder = yield Spawn(
            counted_permitted(connections, "probe", permit, held_until(gate), clock=clock.read)
        )
        yield until(lambda: permit_load_of(connections, "probe").held == 1)
        waiter = yield Spawn(
            counted_permitted(connections, "probe", permit, answered(), clock=clock.read)
        )
        yield until(lambda: permit_load_of(connections, "probe").waiting == 1)
        seen["middle"] = permit_load_of(connections, "probe")
        yield CompletePromise(gate, None)
        return (yield Wait(holder)), (yield Wait(waiter))

    assert run(scheduled(with_handlers([state()], program()))) == ("held", "answered")
    assert len(clock.calls) == 6
    middle = seen["middle"]
    assert (middle.waiting, middle.held, middle.acquired, middle.released) == (1, 1, 1, 0)
    final = permit_load_of(connections, "probe")
    assert (final.waiting, final.held, final.acquired, final.released) == (0, 0, 2, 2)
    # Exact interval sums: held = (10.011 - 10.000) + (10.017 - 10.016) = 0.012,
    # waited = (10.000 - 10.000) + (10.016 - 10.001) = 0.015. Because the wakeup lag
    # (t_w1 - t_h1 = 0.005) exceeds (t_w0 - t_h0) + (t_w2 - t_w1) = 0.002, waited > held.
    assert final.waited_seconds == pytest.approx(0.015)
    assert final.held_seconds == pytest.approx(0.012)
    assert final.waited_seconds > final.held_seconds


def test_injected_clock_is_the_only_clock_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid_monotonic() -> float:  # noqa: DOEFF022
        raise AssertionError("real time.monotonic must not be called when clock is injected")

    monkeypatch.setattr(time, "monotonic", forbid_monotonic)

    connections = leases()
    clock = scripted_clock((5.0, 5.25, 6.0))

    @do
    def program() -> Generator[Any, Any, str]:
        permit = yield CreateSemaphore(1)
        return (yield counted_permitted(connections, "probe", permit, answered(), clock=clock.read))

    assert run(scheduled(with_handlers([state()], program()))) == "answered"
    assert clock.calls == [5.0, 5.25, 6.0]
    load = permit_load_of(connections, "probe")
    assert (load.waiting, load.held, load.acquired, load.released) == (0, 0, 1, 1)
    assert load.waited_seconds == pytest.approx(0.25)
    assert load.held_seconds == pytest.approx(0.75)

    direct_connections = leases()
    direct_clock = scripted_clock((1.0, 1.5, 2.75))
    asked = permit_asked(direct_connections, "probe", direct_clock.read)
    acquired = permit_acquired(direct_connections, "probe", asked, direct_clock.read)
    permit_returned(direct_connections, "probe", acquired, direct_clock.read)
    assert direct_clock.calls == [1.0, 1.5, 2.75]
    assert (asked, acquired) == (1.0, 1.5)
    direct_load = permit_load_of(direct_connections, "probe")
    assert (
        direct_load.waiting,
        direct_load.held,
        direct_load.acquired,
        direct_load.released,
    ) == (0, 0, 1, 1)
    assert direct_load.waited_seconds == pytest.approx(0.5)
    assert direct_load.held_seconds == pytest.approx(1.25)


def test_default_clock_is_time_monotonic() -> None:
    for fn in (counted_permitted, permit_asked, permit_acquired, permit_returned):
        assert inspect.signature(fn).parameters["clock"].default is time.monotonic
    assert "clock" not in inspect.signature(pooled_postgres_sql_handler).parameters

    connections = leases()
    before = time.monotonic()
    asked = permit_asked(connections, "probe")
    acquired = permit_acquired(connections, "probe", asked)
    permit_returned(connections, "probe", acquired)
    after = time.monotonic()
    assert before <= asked <= acquired <= after
    load = permit_load_of(connections, "probe")
    assert (load.waiting, load.held, load.acquired, load.released) == (0, 0, 1, 1)
    assert load.waited_seconds >= 0.0
    assert load.held_seconds >= 0.0


def test_a_waiter_cancelled_before_acquiring_leaves_no_waiting_count() -> None:
    # Counter-example: without the abandon branch, a cancelled waiter would stay counted as waiting forever.
    connections = leases()

    @do
    def program():
        permit = yield CreateSemaphore(1)
        gate = yield CreatePromise()
        holder = yield Spawn(counted_permitted(connections, "probe", permit, held_until(gate)))
        yield until(lambda: permit_load_of(connections, "probe").held == 1)
        waiter = yield Spawn(counted_permitted(connections, "probe", permit, answered()))
        yield until(lambda: permit_load_of(connections, "probe").waiting == 1)
        yield Cancel(waiter)
        with contextlib.suppress(TaskCancelledError):
            yield Wait(waiter)
        yield CompletePromise(gate, None)
        return (yield Wait(holder))

    assert run(scheduled(with_handlers([state()], program()))) == "held"
    final = permit_load_of(connections, "probe")
    assert (final.waiting, final.held, final.acquired, final.released) == (0, 0, 1, 1)


def test_the_handler_answers_the_load_of_its_statements() -> None:
    connections = leases(size=3)
    pool = ThreadPoolExecutor(max_workers=3)

    @do
    def program():
        before = yield ReadSqlPermitLoad("probe")
        first = yield SqlQuery("probe", "SELECT 1", ())
        second = yield SqlQuery("probe", "SELECT 1", ())
        after = yield ReadSqlPermitLoad("probe")
        return before, first, second, after

    try:
        handlers = [state(), pooled_postgres_sql_handler(connections, pool)]
        before, first, second, after = run(scheduled(with_handlers(handlers, program())))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        connections.close()
    assert isinstance(first, SqlUnreachable)
    assert isinstance(second, SqlUnreachable)
    assert (before.permits, before.acquired, before.released) == (3, 0, 0)
    assert (after.permits, after.waiting, after.held, after.acquired, after.released) == (3, 0, 0, 2, 2)
    assert after.waited_seconds >= 0.0
    assert after.held_seconds >= 0.0
