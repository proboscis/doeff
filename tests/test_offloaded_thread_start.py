"""submit hands a new thread to the starter thread and never waits for it to start.

``Thread.start`` returns only after the new thread has run its first lines, and the caller of an
Executor's ``submit`` is the scheduler's thread (``offloaded``). A host process with 140 threads
whose file reads, child processes and transcript reads each start a thread (``ThreadPerCall``) had
its main loop report ready 55 to 83 seconds apart while that thread used 2 to 3% of a CPU, and the
stack dumps found it in ``Thread.start``'s wait under ``ThreadPerCall.submit``. Both Executors now
put the thread on ``ThreadStarter``'s queue, so a slow start delays only the call that needs it.

These tests make the start of every call thread slow (``Thread.start`` waits on a gate). Before the
change ``submit`` itself waited on the gate.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Executor

import pytest
from doeff_core_effects import Spawn, Wait, scheduled
from doeff_core_effects._offloaded_call import ReusedThreads, ThreadPerCall, offloaded

from doeff import do, run

# How long a gated start waits for the test to open the gate. A pool that still starts its thread on
# the caller's thread passes the gate after this and the test fails by its assertion, not by a hang.
GATE_SECONDS = 5.0
# Upper bound on any other wait in these tests.
SETTLE_SECONDS = 20.0
# What a submit that does not wait for the start may take.
SUBMIT_SECONDS = 1.0

POOLS: dict[str, Callable[[], Executor]] = {"ThreadPerCall": ThreadPerCall, "ReusedThreads": ReusedThreads}


class SlowStart:
    """Makes ``Thread.start`` of call threads wait until the gate opens (other threads start as usual)."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.requested = threading.Event()
        self.callers: list[threading.Thread] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = threading.Thread.start
        slow = self

        def start(thread: threading.Thread) -> None:
            if thread.name == "doeff-offloaded-call":
                slow.callers.append(threading.current_thread())
                slow.requested.set()
                slow.gate.wait(GATE_SECONDS)
            original(thread)

        monkeypatch.setattr(threading.Thread, "start", start)


@pytest.fixture
def slow_start(monkeypatch: pytest.MonkeyPatch) -> Iterator[SlowStart]:
    slow = SlowStart()
    slow.install(monkeypatch)
    yield slow
    slow.gate.set()


@pytest.mark.parametrize("name", POOLS)
def test_submit_returns_before_the_thread_starts(name: str, slow_start: SlowStart) -> None:
    pool = POOLS[name]()

    began = time.monotonic()
    future = pool.submit(lambda: 7)
    took = time.monotonic() - began

    assert took < SUBMIT_SECONDS, f"{name}.submit waited {took:.1f} s for its thread to start"
    assert slow_start.requested.wait(SETTLE_SECONDS)
    assert threading.current_thread() not in slow_start.callers
    assert not future.done()
    slow_start.gate.set()
    assert future.result(SETTLE_SECONDS) == 7


@pytest.mark.parametrize("name", POOLS)
def test_a_thread_that_cannot_start_fails_its_call(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    original = threading.Thread.start

    def refuse(thread: threading.Thread) -> None:
        if thread.name == "doeff-offloaded-call":
            raise RuntimeError("can't start new thread")
        original(thread)

    monkeypatch.setattr(threading.Thread, "start", refuse)
    pool = POOLS[name]()

    future = pool.submit(lambda: 7)

    assert isinstance(future.exception(SETTLE_SECONDS), RuntimeError)
    monkeypatch.setattr(threading.Thread, "start", original)
    # The starter outlives the refused start and starts the next thread.
    assert pool.submit(lambda: 8).result(SETTLE_SECONDS) == 8


@pytest.mark.parametrize("name", POOLS)
def test_a_call_cancelled_while_its_thread_waits_to_start_never_runs(name: str, slow_start: SlowStart) -> None:
    pool = POOLS[name]()
    ran = threading.Event()

    future = pool.submit(ran.set)
    assert future.cancel()
    slow_start.gate.set()
    # A later call on the same starter runs after the cancelled one's thread has started.
    assert pool.submit(lambda: 1).result(SETTLE_SECONDS) == 1

    assert not ran.is_set()


def _keep_nothing(_value: object) -> None:
    return None


@pytest.mark.parametrize("implementation", ["rust", "python"])
def test_other_tasks_run_while_an_offloaded_call_waits_for_its_thread(
    implementation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One task's call needs a new thread whose start is slow. Another task's call goes to a thread
    # that is already idle, so it needs no start. Before the change the scheduler's thread waited in
    # the first task's submit, so the second task got its answer only after the slow thread started.
    warm = ReusedThreads()
    warm.submit(lambda: None).result(SETTLE_SECONDS)
    slow = SlowStart()
    slow.install(monkeypatch)
    started = threading.Event()

    @do
    def needs_a_new_thread():
        return (yield offloaded(ThreadPerCall(), lambda: started.set() or "slow", _keep_nothing))

    @do
    def program():
        task = yield Spawn(needs_a_new_thread())
        quick = yield offloaded(warm, lambda: "quick", _keep_nothing)
        started_before = started.is_set()
        slow.gate.set()
        late = yield Wait(task)
        return quick, started_before, late

    try:
        got = run(scheduled(program(), implementation=implementation))
    finally:
        slow.gate.set()

    assert got == ("quick", False, "slow")
