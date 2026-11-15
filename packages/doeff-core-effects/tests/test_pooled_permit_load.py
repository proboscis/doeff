"""The connection-permit load of pooled-postgres-sql-handler (ReadSqlPermitLoad).

No PostgreSQL is needed: the permit bookkeeping is driven directly (counted-permitted on a
scheduler semaphore) and, for the handler, through statements to a refused address (each one
still takes and returns a permit and is answered with SqlUnreachable).
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

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
from doeff_core_effects._pooled_postgres_sql import counted_permitted, permit_load_of

from doeff import do, run, with_handlers

REFUSED_DSN = "postgresql://probe@127.0.0.1:1/probe?connect_timeout=2"


def leases(size=1):
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


def test_a_waiter_is_counted_while_waiting_and_its_wait_is_summed():
    connections = leases()
    seen = {}

    @do
    def program():
        permit = yield CreateSemaphore(1)
        gate = yield CreatePromise()
        holder = yield Spawn(counted_permitted(connections, "probe", permit, held_until(gate)))
        yield until(lambda: permit_load_of(connections, "probe").held == 1)
        waiter = yield Spawn(counted_permitted(connections, "probe", permit, answered()))
        yield until(lambda: permit_load_of(connections, "probe").waiting == 1)
        seen["middle"] = permit_load_of(connections, "probe")
        yield pause()
        yield CompletePromise(gate, None)
        return (yield Wait(holder)), (yield Wait(waiter))

    assert run(scheduled(with_handlers([state()], program()))) == ("held", "answered")
    middle = seen["middle"]
    assert (middle.waiting, middle.held, middle.acquired, middle.released) == (1, 1, 1, 0)
    final = permit_load_of(connections, "probe")
    assert (final.waiting, final.held, final.acquired, final.released) == (0, 0, 2, 2)
    # The waiter waited at least the pause before the holder let go; the holder held as long.
    assert final.waited_seconds >= 0.005
    assert final.held_seconds >= final.waited_seconds


def test_a_waiter_cancelled_before_acquiring_leaves_no_waiting_count():
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
        try:
            yield Wait(waiter)
        except TaskCancelledError:
            pass
        yield CompletePromise(gate, None)
        return (yield Wait(holder))

    assert run(scheduled(with_handlers([state()], program()))) == "held"
    final = permit_load_of(connections, "probe")
    assert (final.waiting, final.held, final.acquired, final.released) == (0, 0, 1, 1)


def test_the_handler_answers_the_load_of_its_statements():
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
    assert isinstance(first, SqlUnreachable) and isinstance(second, SqlUnreachable)
    assert (before.permits, before.acquired, before.released) == (3, 0, 0)
    assert (after.permits, after.waiting, after.held, after.acquired, after.released) == (3, 0, 0, 2, 2)
