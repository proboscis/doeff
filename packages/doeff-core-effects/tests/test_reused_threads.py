"""ReusedThreads hands each call to an idle thread and starts a thread only when none is idle.

postgres-sql-handler used to start one thread per driver call (``ThreadPerCall``). Starting a thread
costs milliseconds on the caller's thread, and in the custody service that cost was a fifth of its
idle CPU (agora card ki-8fc4f9017572). ``ReusedThreads`` keeps a finished thread waiting for the next
call. It keeps ThreadPerCall's other promises: no upper bound on threads (a call that waits inside its
thread for a connection permit must not starve the permit holder's next call), daemon threads, and a
future cancelled before it starts never runs.
"""

from __future__ import annotations

import gc
import threading
import weakref
from concurrent.futures import Future

from doeff_core_effects._offloaded_call import ReusedThreads

# Upper bound on any wait in these tests, so a broken pool fails by name instead of hanging pytest.
SETTLE_SECONDS = 20.0


def test_calls_in_a_row_run_on_one_thread() -> None:
    pool = ReusedThreads()

    idents = {pool.submit(threading.get_ident).result(SETTLE_SECONDS) for _ in range(50)}

    assert pool.started == 1, f"{pool.started} threads started for 50 calls in a row"
    assert len(idents) == 1, idents


def test_calls_blocked_on_each_other_all_run_at_once() -> None:
    # Failure case for a bounded pool: every call waits until all of them run, so a pool with fewer
    # threads than calls never finishes (the shape of a statement waiting for a connection permit
    # while the permit holder's next statement waits for a thread).
    calls = 16
    barrier = threading.Barrier(calls, timeout=SETTLE_SECONDS)
    pool = ReusedThreads()

    futures = [pool.submit(barrier.wait) for _ in range(calls)]

    assert sorted(future.result(SETTLE_SECONDS) for future in futures) == list(range(calls))
    assert pool.started == calls


def test_threads_are_daemons() -> None:
    pool = ReusedThreads()

    assert pool.submit(lambda: threading.current_thread().daemon).result(SETTLE_SECONDS) is True


def test_a_thread_idle_past_its_limit_ends_and_a_later_call_starts_another() -> None:
    pool = ReusedThreads(idle_seconds=0.05)
    first = pool.submit(threading.current_thread).result(SETTLE_SECONDS)

    first.join(SETTLE_SECONDS)

    assert not first.is_alive()
    assert pool.idle == 0
    assert pool.submit(lambda: 1).result(SETTLE_SECONDS) == 1
    assert pool.started == 2


def test_a_call_handed_over_as_the_idle_wait_ends_still_runs() -> None:
    # The race the idle wait guards: a call is given to a thread just as its wait times out. With an
    # idle limit near zero most calls land on that edge; every one must still run.
    pool = ReusedThreads(idle_seconds=0.0001)

    answers = [pool.submit(lambda n=n: n).result(SETTLE_SECONDS) for n in range(500)]

    assert answers == list(range(500))


def test_a_future_cancelled_before_it_starts_never_runs() -> None:
    pool = ReusedThreads()
    for _ in range(200):
        ran = threading.Event()
        future = pool.submit(ran.set)
        removed = future.cancel()
        if not removed:
            future.result(SETTLE_SECONDS)
        assert ran.is_set() is not removed


class Answer:
    """An answer whose liveness a weakref can watch."""


def test_an_idle_thread_keeps_no_answer() -> None:
    pool = ReusedThreads()
    future: Future[Answer] = pool.submit(Answer)
    ref = weakref.ref(future.result(SETTLE_SECONDS))
    # Wait until the thread has gone back to waiting for the next call.
    pool.submit(lambda: None).result(SETTLE_SECONDS)

    del future
    gc.collect()

    assert ref() is None, "the idle thread still holds the last answer"
