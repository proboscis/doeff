"""A job that keeps waiting on failing tasks keeps the same rows and tracebacks.

A failed task's row keeps its error for later waiters, and every throw into a
waiter prepends the waiter's frames to the error's traceback. Those finished
frames hold the waiter's ``Try``/``Wait`` effects and the ``Task`` handle they
name, in their locals and in the closures of their functions, so the row kept
its own handle alive and the #502 sweep never deleted it: after N failed and
waited tasks the run held N rows, N errors and their tracebacks and frames
(agora card ki-6ebcb9c44749). The sweep now gives such an error back the
traceback it had when it failed once its waiter frames are finished
(``_drop_waiter_frames``); the Rust version does so after its lock is released,
so its rows go at the next sweep.

Every test runs under both scheduler implementations.
"""

from __future__ import annotations

import gc
import sys
import traceback
from collections import Counter

import pytest
from doeff_core_effects import (
    CreatePromise,
    FailPromise,
    Raise,
    SchedulerIntrospection,
    Spawn,
    Wait,
    scheduled,
    try_handler,
)
from doeff_core_effects import _scheduler as scheduler_module

from doeff import Try, do, with_handlers
from doeff import run as doeff_run

pytestmark = [
    pytest.mark.usefixtures("scheduler_index_self_check"),
    # Before 3.13 frame.clear() closes a suspended generator instead of refusing,
    # so the sweep cannot tell a finished waiter frame and leaves them all.
    pytest.mark.skipif(sys.version_info < (3, 13), reason="needs frame.clear() to refuse suspended frames"),
]

FEW = 20
MANY = 400
# Rows a sweep at every id may leave: the Rust version lets go of the waiter
# frames after its lock is released, so the latest failures' rows wait for the
# next sweep.
SETTLING_ROWS = 4
WATCHED = ("traceback", "frame", "Task", "Future", "_Late")


@pytest.fixture(params=["python", "rust"], autouse=True)
def scheduler_implementation(request, monkeypatch):
    monkeypatch.setenv("DOEFF_SCHEDULER", request.param)
    return request.param


@pytest.fixture(autouse=True)
def sweep_at_every_id(monkeypatch):
    """So the counts do not depend on where HANDLE_SWEEP_INTERVAL falls."""
    monkeypatch.setattr(scheduler_module, "HANDLE_SWEEP_INTERVAL", 1)


class _Late:
    """A doeff Raise reason that is no exception."""


@do
def _python_failure(i: int):
    raise ValueError(f"timeout {i}")


@do
def _raise_failure(i: int):
    yield Raise(_Late())
    return i


@do
def _wait_failed_task(failure, i: int):
    task = yield Spawn(failure(i))
    return (yield Try(Wait(task)))


@do
def _wait_failed_promise(i: int):
    promise = yield CreatePromise()
    yield FailPromise(promise, ValueError(f"refused {i}"))
    return (yield Try(Wait(promise.future)))


def _counts() -> dict[str, int]:
    gc.collect()
    counts = Counter(type(obj).__name__ for obj in gc.get_objects())
    return {name: counts[name] for name in WATCHED}


def _counts_after(waits: int, wait_once) -> tuple[dict[str, int], int]:  # noqa: DOEFF006 — 検の補助の戻り
    @do
    def job():
        for i in range(waits):
            outcome = yield wait_once(i)
            assert outcome.is_err()
        del outcome
        rows = yield SchedulerIntrospection()
        return _counts(), rows["tasks"] + rows["promises"]

    return doeff_run(scheduled(with_handlers([try_handler], job())))


@pytest.mark.parametrize(
    "wait_once",
    [
        pytest.param(lambda i: _wait_failed_task(_python_failure, i), id="python-exception"),
        pytest.param(lambda i: _wait_failed_task(_raise_failure, i), id="doeff-raise"),
        pytest.param(_wait_failed_promise, id="failed-promise"),
    ],
)
def test_waiting_on_many_failures_keeps_the_same_rows(wait_once) -> None:
    # Failure case: before the fix every waited failure kept its row, so from
    # FEW to MANY waits the rows grew by MANY - FEW and the tracebacks and
    # frames by two or three times that.
    few_counts, few_rows = _counts_after(FEW, wait_once)
    many_counts, many_rows = _counts_after(MANY, wait_once)

    assert many_rows <= few_rows + SETTLING_ROWS, (few_rows, many_rows)
    grown = {name: many_counts[name] - few_counts[name] for name in WATCHED}
    assert all(growth <= 3 * SETTLING_ROWS for growth in grown.values()), (few_counts, many_counts)


def test_a_kept_handle_still_reads_the_error_with_its_failure_site() -> None:
    """A handle that outlives its waiters keeps the row: a later Wait gets the
    same error, and its traceback still names the failure site."""

    @do
    def job():
        task = yield Spawn(_python_failure(7))
        first = yield Try(Wait(task))
        for i in range(8):  # sweeps run while the first waiter's frames are finished
            yield _wait_failed_task(_python_failure, i)
        second = yield Try(Wait(task))
        return first.error, second.error

    first, second = doeff_run(scheduled(with_handlers([try_handler], job())))

    assert second is first
    names = [frame.name for frame in traceback.extract_tb(second.__traceback__)]
    assert "_python_failure" in names


def test_a_waiter_still_handling_the_error_keeps_its_traceback() -> None:
    """A waiter suspended in its except block is not done with the traceback:
    sweeps that run meanwhile leave the waiter's frame in it."""

    @do
    def job():
        task = yield Spawn(_python_failure(3))
        try:
            yield Wait(task)
        except ValueError as error:
            for i in range(8):  # sweeps run while this frame is suspended here
                yield _wait_failed_task(_python_failure, i)
            names = [frame.name for frame in traceback.extract_tb(error.__traceback__)]
            return names
        return []

    names = doeff_run(scheduled(with_handlers([try_handler], job())))

    assert "job" in names
    assert "_python_failure" in names
