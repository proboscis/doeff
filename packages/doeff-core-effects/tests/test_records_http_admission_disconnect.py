"""Synthetic local HTTP/PG checks of client disconnect versus server work."""

import json
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen

import psycopg
import pytest
from doeff_core_effects.pooled_postgres_sql import pooled_postgres_sql_handler
from doeff_core_effects.postgres_sql import (
    PostgresConnections,
    PostgresDatabase,
    postgres_sql_handler,
)
from doeff_core_effects.scheduler import scheduled
from doeff_records.http_server import records_server_config, start_records_server
from doeff_records.laws import LAW_SCHEMA
from doeff_records.pg import pg_records_handler, prepare_records_store
from doeff_records.pg_sql import writer_lock_key
from doeff_time import SimClock, sim_time_handler
from test_pooled_writer_admission import DSN, AcquireObserver

from doeff import run, with_handlers

pytestmark = pytest.mark.skipif(not DSN, reason="explicit disposable PostgreSQL DSN required")


def eventually(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "HTTP/DB synchronization deadline"
        # predicate performs a real, blocking PostgreSQL round trip.


def admission_reached(observer, expected):
    with observer.changed:
        assert observer.changed.wait_for(
            lambda: sorted(observer.attempts.values()) == expected, timeout=5
        ), "admission attempt event deadline"


def put(url, key, timeout):
    body = json.dumps(
        {
            "table": "parts",
            "key": [key],
            "value": {"label": "synthetic"},
            "expect": {"kind": "absent"},
        }
    ).encode()
    request = Request(
        url + "/v1/records/put-row",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read()), time.monotonic() - started


def assert_completed_writes(first, surviving, observer_db, prefix, enabled):
    first_answer = first.result(timeout=10)
    surviving_answer = surviving.result(timeout=10)
    assert first_answer[0] == 200
    assert first_answer[1]["kind"] == "written"
    assert surviving_answer[0] == 200
    assert surviving_answer[1]["kind"] == "written"
    assert surviving_answer[2] >= 6.0
    # The abandoned client's synthetic row was committed after it disconnected.
    eventually(
        lambda: observer_db.execute(f"SELECT count(*) FROM {prefix}state_rows").fetchone()[0] == 3
    )
    print(
        json.dumps(
            {
                "admission": enabled,
                "client_timeout_seconds": 0.5,
                "connected_request_seconds": round(surviving_answer[2], 3),
                "rows_committed_including_disconnected_client": 3,
                "db_waiters_before_release": 1 if enabled else 3,
            }
        )
    )


def assert_http_callable_timed_out(abandoned):
    with pytest.raises((TimeoutError, socket.timeout)):
        abandoned.result(timeout=3)
    # Future.result's own timeout has the same exception class. The HTTP
    # callable must already have ended and stored its socket timeout.
    assert abandoned.done(), "only the test's Future wait timed out"
    http_error = abandoned.exception(timeout=0)
    assert isinstance(http_error, (TimeoutError, socket.timeout))
    assert http_error.args
    assert "timed out" in str(http_error).lower()


@pytest.mark.parametrize("enabled", [False, True], ids=["admission-off", "admission-on"])
def test_client_disconnect_does_not_cancel_waiting_write(enabled):
    prefix = "http_probe_" + uuid.uuid4().hex[:8] + "_"
    connections = PostgresConnections((PostgresDatabase(name="probe", dsn=DSN),), size=3)
    pool = ThreadPoolExecutor(max_workers=3)
    clients = ThreadPoolExecutor(max_workers=3)
    lock = psycopg.connect(DSN, autocommit=True)
    observer_db = psycopg.connect(DSN, autocommit=True)
    server = None
    held = False
    observer = AcquireObserver()
    key = run(writer_lock_key(prefix))
    try:
        store = run(
            scheduled(
                with_handlers(
                    [sim_time_handler(clock=SimClock()), postgres_sql_handler(connections)],
                    prepare_records_store("probe", "probe", LAW_SCHEMA, prefix),
                )
            )
        )
        config = run(
            records_server_config(
                LAW_SCHEMA,
                lambda writer: pg_records_handler(store, writer, "local-test"),
                request_handlers=(
                    observer.handler,
                    pooled_postgres_sql_handler(
                        connections, pool, serialized_lock_keys=(key,) if enabled else ()
                    ),
                ),
            )
        )
        server = start_records_server(config)
        lock.execute("SELECT pg_advisory_lock(hashtext(%s))", (key,))
        held = True
        oid = lock.execute("SELECT hashtext(%s)::bigint & 4294967295", (key,)).fetchone()[0]

        def db_waiters():
            return observer_db.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND objid=%s AND NOT granted",
                (oid,),
            ).fetchone()[0]

        first = clients.submit(put, server.url, "holder", 20)
        eventually(lambda: db_waiters() == 1)
        abandoned = clients.submit(put, server.url, "timed-out-client", 0.5)
        if enabled:
            admission_reached(observer, [1, 2])
        else:
            eventually(lambda: db_waiters() == 2)
        assert_http_callable_timed_out(abandoned)
        surviving = clients.submit(put, server.url, "connected-client", 20)
        if enabled:
            admission_reached(observer, [1, 3])
        else:
            eventually(lambda: db_waiters() == 3)
        # Observe beyond the readiness probe's 1s and a typical 5s client bound.
        # This bounds the empirical claim; it does not prove an infinite deadline.
        with pytest.raises(TimeoutError):
            surviving.result(timeout=6.0)
        assert not first.done()
        assert not surviving.done()
        assert db_waiters() == (1 if enabled else 3)
        lock.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))
        held = False
        assert_completed_writes(first, surviving, observer_db, prefix, enabled)
    finally:
        if held:
            lock.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))
        clients.shutdown(wait=True, cancel_futures=True)
        if server is not None:
            server.close()
        lock.close()
        observer_db.close()
        connections.close()
        pool.shutdown()
