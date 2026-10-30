"""Real scheduler/PG tests. Disposable DSN only; DB gates establish ordering."""

import selectors
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
from disposable_postgres import session_dsn
from doeff_core_effects import (
    AcquireSemaphore,
    Cancel,
    CreateExternalPromise,
    PostgresConnections,
    PostgresDatabase,
    ReleaseSemaphore,
    SchedulerIntrospection,
    Spawn,
    SqlQuery,
    SqlTransaction,
    TaskCancelledError,
    Wait,
    pooled_postgres_sql_handler,
    scheduled,
    state,
)

from doeff import do, run, with_handlers

DSN = session_dsn("DOEFF_SQL_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="explicit disposable PostgreSQL DSN required")


@pytest.fixture(params=[False, True], ids=["ordinary", "batched"])
def batched(request):
    return request.param


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
def query(sql, database="probe"):
    return (yield SqlQuery(database, sql, ()))


def transaction(key, program, batched):
    return SqlTransaction(database="probe", program=program, lock_key=key, batched=batched)


class Gate:
    """A held PostgreSQL lock, observed through pg_locks; timer is cleanup only."""

    def __init__(self):
        self.db = psycopg.connect(DSN, autocommit=True)
        self.observer = psycopg.connect(DSN, autocommit=True)
        self.key = 812345
        self.db.execute("SELECT pg_advisory_lock(%s)", (self.key,))
        self.released = threading.Event()
        self.mutex = threading.Lock()
        self.mut_timer = threading.Timer(30, self.release)
        self.mut_timer.start()

    def waiting(self, count=1):
        return (
            self.observer.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND classid=0 AND objid=%s AND NOT granted",
                (self.key,),
            ).fetchone()[0]
            >= count
        )

    def arm_timeout(self, seconds):
        self.mut_timer.cancel()
        self.mut_timer.join(timeout=2)
        self.mut_timer = threading.Timer(seconds, self.release)
        self.mut_timer.start()

    def release(self):
        with self.mutex:
            if not self.released.is_set():
                self.released.set()
                self.db.execute("SELECT pg_advisory_unlock(%s)", (self.key,))

    def close(self):
        self.mut_timer.cancel()
        self.release()
        self.mut_timer.join(timeout=2)
        self.db.close()
        self.observer.close()

    def program(self):
        return query(f"SELECT pg_advisory_xact_lock({self.key})")


@pytest.fixture
def gate():
    value = Gate()
    try:
        yield value
    finally:
        value.close()


DECLARED = (
    "A",
    "B",
    "cancel",
    "atomic",
    "different-A",
    "different-B",
    "observed",
    "process-key",
    "pool",
    "handoff",
)


def execute(program, size=2, declared=DECLARED, observer=None, dsn=DSN):
    connections = PostgresConnections((PostgresDatabase(name="probe", dsn=dsn),), size=size)
    pool = ThreadPoolExecutor(max_workers=size)
    try:
        handlers = [state()]
        if observer is not None:
            handlers.append(observer)
        handlers.append(
            pooled_postgres_sql_handler(connections, pool, serialized_lock_keys=declared)
        )
        answer = run(scheduled(with_handlers(handlers, program)))
        # Acquire EVERY declared slot simultaneously, bounded even if one leaked.
        permits = connections.permits["probe"]
        acquired = 0
        try:
            for _ in range(size):
                assert permits.acquire(timeout=2), "connection permit leaked"
                acquired += 1
        finally:
            for _ in range(acquired):
                permits.release()
        leased = []
        try:
            for _ in range(size):
                c = connections.acquire("probe")
                leased.append(c)
                assert c.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
                assert c.pgconn.pipeline_status == psycopg.pq.PipelineStatus.OFF
                assert c.execute("SELECT 1").fetchone() == (1,)
        finally:
            for c in leased:
                connections.release("probe", c)
        return answer
    finally:
        connections.close()
        pool.shutdown()


@do
def reader_beside_writers(prime, batched, gate, count=2, observer=None):
    if prime:
        yield transaction("A", query("SELECT 1"), batched)
    tasks = [(yield Spawn(transaction("B", gate.program(), batched)))]
    yield until(gate.waiting)
    for _ in range(count - 1):
        tasks.append((yield Spawn(transaction("B", query("SELECT 1"), batched))))
    if observer is not None:
        admission = yield admission_handle("B")
        yield until(lambda: observer.attempts.get(admission.sem_id, 0) == count)
    else:
        yield until(
            lambda: (
                gate.observer.execute(
                    "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND objid=(hashtext('B')::bigint & 4294967295) AND NOT granted"
                ).fetchone()[0]
                >= 1
            )
        )
    gate.arm_timeout(3)
    yield query("SELECT 1")
    completed_before_release = not gate.released.is_set()
    gate.release()
    for task in tasks:
        yield Wait(task)
    return completed_before_release


@pytest.mark.parametrize("prime", [False, True])
def test_same_lock_waiters_do_not_starve_reads(prime, batched, gate):
    observer = AcquireObserver()
    assert execute(
        reader_beside_writers(prime, batched, gate, observer=observer), observer=observer.handler
    ), "read waited until writer gate released"


def test_cold_writer_burst_keeps_a_read_slot(batched, gate):
    observer = AcquireObserver()
    assert execute(
        reader_beside_writers(False, batched, gate, count=6, observer=observer),
        observer=observer.handler,
    )


@do
def cancel_waiter(batched, gate):
    first = yield Spawn(transaction("cancel", gate.program(), batched))
    yield until(gate.waiting)
    waiting = yield Spawn(transaction("cancel", query("SELECT 1"), batched))
    yield pause()
    yield Cancel(waiting)
    # Cancellation can wait for in-flight driver SQL on baseline; release on a
    # separate thread so Cancel/Wait cannot deadlock cleanup.
    gate.release()
    cancelled = False
    try:
        yield Wait(waiting)
    except TaskCancelledError:
        cancelled = True
    yield Wait(first)
    yield transaction("cancel", query("SELECT 1"), batched)
    return cancelled


def test_cancel_admission_waiter_releases_resources(batched, gate):
    assert execute(cancel_waiter(batched, gate))


@do
def different_keys(batched, gate, first_key):
    first = yield Spawn(transaction(first_key, gate.program(), batched))
    yield until(gate.waiting)
    yield transaction(None if first_key is None else "different-B", query("SELECT 1"), batched)
    before_release = not gate.released.is_set()
    gate.release()
    yield Wait(first)
    return before_release


def test_different_keys_are_not_serialized(batched, gate):
    assert execute(different_keys(batched, gate, "different-A"))


def test_keyless_transactions_remain_parallel(batched, gate):
    assert execute(different_keys(batched, gate, None))


@do
def insert_then_gate(gate):
    yield query("INSERT INTO admission_atomic VALUES (1)")
    yield gate.program()


@do
def atomic_cancel(batched, gate):
    yield query("DROP TABLE IF EXISTS admission_atomic")
    yield query("CREATE TABLE admission_atomic (id int PRIMARY KEY)")
    task = yield Spawn(transaction("atomic", insert_then_gate(gate), batched))
    # Gate query is only issued AFTER INSERT completed in the same transaction.
    yield until(gate.waiting)
    visible = yield query("SELECT count(*) FROM admission_atomic")
    yield Cancel(task)
    gate.release()
    cancelled = False
    try:
        yield Wait(task)
    except TaskCancelledError:
        cancelled = True
    after = yield query("SELECT count(*) FROM admission_atomic")
    yield transaction("atomic", query("SELECT 1"), batched)
    return cancelled, visible.rows, after.rows


def test_atomicity_cancel_rolls_back_and_reuses_lock_and_pool(batched, gate):
    assert execute(atomic_cancel(batched, gate)) == (True, ((0,),), ((0,),))


@do
def increment_counter(gated):
    before = yield query("SELECT value FROM admission_process_counter")
    if gated:
        yield query("SELECT pg_advisory_xact_lock(812345)")
    yield query(f"UPDATE admission_process_counter SET value={before.rows[0][0] + 1}")


def child_process(batched, gated, dsn):
    print("READY", flush=True)
    assert sys.stdin.readline().strip() == "START"
    execute(transaction("process-key", increment_counter(gated), batched), dsn=dsn)


def test_same_lock_preserves_exclusion_across_processes(batched, gate):
    db = gate.observer
    db.execute("DROP TABLE IF EXISTS admission_process_counter")
    db.execute("CREATE TABLE admission_process_counter (value int)")
    db.execute("INSERT INTO admission_process_counter VALUES (0)")
    children = []

    def wait_for(predicate):
        deadline = time.monotonic() + 4
        while not predicate():
            assert time.monotonic() < deadline, "process lock synchronization deadline"
            # predicate performs a real, blocking PostgreSQL round trip.

    try:
        for number in range(2):
            code = (
                "import runpy, sys; "
                f"sys.path.insert(0, {str(Path(__file__).resolve().parent / 'postgres_support')!r}); "
                f"m=runpy.run_path({str(Path(__file__).resolve())!r}); "
                f"m['child_process']({batched!r}, {number == 0!r}, {DSN!r})"
            )
            children.append(
                subprocess.Popen(
                    [sys.executable, "-c", code],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        # Both children are initialized, but neither transaction has started.
        for child in children:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                # A cold Hy import in a fresh Python-version environment can take several seconds.
                assert selector.select(timeout=15), "child ready deadline"
                ready = child.stdout.readline().strip()
                if ready != "READY":
                    out, err = child.communicate(timeout=5)
                    pytest.fail(
                        f"child failed before READY: {child.returncode}, {ready}, {out}, {err}"
                    )
        children[0].stdin.write("START\n")
        children[0].stdin.flush()
        wait_for(gate.waiting)  # first holds process-key and reached inner query
        lock_oid = db.execute("SELECT hashtext('process-key')::bigint & 4294967295").fetchone()[0]
        assert (
            db.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND objid=%s AND granted",
                (lock_oid,),
            ).fetchone()[0]
            == 1
        )
        children[1].stdin.write("START\n")
        children[1].stdin.flush()
        wait_for(
            lambda: (
                db.execute(
                    "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND objid=%s AND NOT granted",
                    (lock_oid,),
                ).fetchone()[0]
                == 1
            )
        )
        assert not gate.released.is_set()
        gate.release()
        for child in children:
            out, err = child.communicate(timeout=15)
            assert child.returncode == 0, (out, err)
        assert db.execute("SELECT value FROM admission_process_counter").fetchone() == (2,)
    finally:
        gate.release()
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)


@do
def bounded_semaphores(batched):
    yield transaction("B", query("SELECT 1"), batched)
    before = yield SchedulerIntrospection()
    for number in range(30):
        yield transaction("B", query("SELECT 1"), batched)
        yield transaction(f"undeclared-{number}", query("SELECT 1"), batched)
    after = yield SchedulerIntrospection()
    assert before["semaphores"] == 2, before
    assert after["semaphores"] == before["semaphores"], (before, after)
    return after


def test_declared_and_undeclared_key_churn_keeps_semaphores_bounded(batched):
    execute(bounded_semaphores(batched))


@pytest.mark.parametrize("declared", [(), ("other",)], ids=["disabled", "undeclared"])
def test_admission_requires_explicit_key_opt_in(batched, declared, gate):
    assert not execute(reader_beside_writers(False, batched, gate), declared=declared)


class AcquireObserver:
    """Observe attempts; forward every effect to the real scheduler unchanged."""

    def __init__(self):
        self.changed = threading.Condition()
        self.attempts = {}
        self.releases = {}
        from doeff.program import Pass, handler

        @handler
        def observe(effect, continuation):
            if isinstance(effect, AcquireSemaphore):
                sid = effect.semaphore.sem_id
                with self.changed:
                    self.attempts[sid] = self.attempts.get(sid, 0) + 1
                    self.changed.notify_all()
            if isinstance(effect, ReleaseSemaphore):
                sid = effect.semaphore.sem_id
                self.releases[sid] = self.releases.get(sid, 0) + 1
            yield Pass(effect, continuation)

        self.handler = handler(observe)


@do
def admission_handle(key):
    from doeff_core_effects import Get
    from doeff_hy._session import session_key

    stored = yield Get(
        session_key(
            "doeff_core_effects._pooled_postgres_sql",
            "pooled-postgres-sql-handler",
            "transaction-permits",
        )
    )
    return stored.value[("probe", key)]


@do
def confirmed_admission_cancel(batched, gate, observer):
    first = yield Spawn(transaction("cancel", gate.program(), batched))
    yield until(gate.waiting)
    admission = yield admission_handle("cancel")
    second = yield Spawn(transaction("cancel", query("SELECT 1"), batched))
    yield until(lambda: observer.attempts.get(admission.sem_id, 0) == 2)
    assert not gate.released.is_set()
    yield Cancel(second)
    cancelled = False
    try:
        yield Wait(second)
    except TaskCancelledError:
        cancelled = True
    assert cancelled
    assert not gate.released.is_set(), "cancellation waited for holder"
    gate.release()
    yield Wait(first)
    yield transaction("cancel", query("SELECT 1"), batched)


def test_cancel_is_confirmed_while_waiting_for_admission(batched, gate):
    observer = AcquireObserver()
    execute(confirmed_admission_cancel(batched, gate, observer), observer=observer.handler)


@do
def confirmed_pool_cancel(batched, gate, observer):
    from doeff_core_effects import Get
    from doeff_hy._session import session_key

    first = yield Spawn(transaction(None, gate.program(), batched))
    yield until(gate.waiting)
    stored = yield Get(
        session_key(
            "doeff_core_effects._pooled_postgres_sql", "pooled-postgres-sql-handler", "permits"
        )
    )
    pool_id = stored.value["probe"].sem_id
    second = yield Spawn(transaction("pool", query("SELECT 1"), batched))
    yield until(lambda: observer.attempts.get(pool_id, 0) == 2)
    yield Cancel(second)
    cancelled = False
    try:
        yield Wait(second)
    except TaskCancelledError:
        cancelled = True
    assert cancelled
    assert not gate.released.is_set()
    gate.release()
    yield Wait(first)
    yield transaction("pool", query("SELECT 1"), batched)


def test_cancel_after_admission_while_waiting_for_pool(batched, gate):
    observer = AcquireObserver()
    execute(confirmed_pool_cancel(batched, gate, observer), size=1, observer=observer.handler)


@do
def cancel_during_handoff(batched, observer):
    # Initialize the real handler's admission, then hold that exact semaphore.
    yield transaction("handoff", query("SELECT 1"), batched)
    admission = yield admission_handle("handoff")
    yield AcquireSemaphore(admission)
    target = yield Spawn(transaction("handoff", query("SELECT 1"), batched))
    yield until(lambda: observer.attempts.get(admission.sem_id, 0) == 3)
    yield ReleaseSemaphore(admission)
    yield Cancel(target)
    cancelled = False
    try:
        yield Wait(target)
    except TaskCancelledError:
        cancelled = True
    assert cancelled
    yield transaction("handoff", query("SELECT 1"), batched)


def test_cancel_admission_receiver_during_handoff(batched):
    observer = AcquireObserver()
    execute(cancel_during_handoff(batched, observer), observer=observer.handler)
