"""Discard drops a task without unwinding it, like SIGKILL (agora-redesign #3055).

``Cancel(task)`` throws ``TaskCancelledError`` into the task so its
``except`` / ``finally`` run (tests/test_cancel_runs_finally.py). ``Discard``
is the hard kill: the task's continuation is dropped wherever it is
suspended, no effect of it is performed any more — not even one inside its
``finally`` — and its waiters see ``TaskCancelledError``. The sim host uses
it to kill a child process (a process killed with SIGKILL runs no cleanup),
so a killed child can write nothing more to the world. (CPython still runs
the plain statements of a dropped generator's ``finally`` up to its first
effect — see Discard's docstring — so the records here are effects.)

Each task records what it reached with ``Tell``; the run returns the writer
log, so a record that is missing is a line that never ran.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from doeff_core_effects import Tell, state, writer, writer_log
from doeff_core_effects.scheduler import (
    PRIORITY_IDLE,
    Cancel,
    CompletePromise,
    CreatePromise,
    Discard,
    Promise,
    SchedulerImplementation,
    Spawn,
    Task,
    TaskCancelledError,
    Wait,
    scheduled,
)

from doeff import do
from doeff import run as doeff_run
from doeff.program import Pure

if TYPE_CHECKING:
    from doeff import Program

RUN_TIMEOUT_SECONDS = 5.0

# The Rust scheduler joins in #3056 (both implementations keep the same meaning).
IMPLEMENTATIONS: tuple[SchedulerImplementation, ...] = ("python",)


@dataclass(frozen=True)
class _Recorded:
    """How the run ended and what its tasks recorded with Tell, in order."""

    outcome: object
    log: tuple[object, ...]


def _run(program: Program[object], implementation: SchedulerImplementation) -> _Recorded:
    """Run ``program`` under the scheduler; return its value and the writer log."""
    outcome: dict[str, _Recorded | BaseException] = {}

    @do
    def with_log():
        value = yield program
        return _Recorded(value, tuple((yield writer_log())))

    def _worker() -> None:
        try:
            outcome["result"] = doeff_run(
                state()(writer(scheduled(with_log(), implementation=implementation)))
            )
        except BaseException as exc:  # re-raised below on the test thread
            outcome["result"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=RUN_TIMEOUT_SECONDS)
    assert not thread.is_alive(), "scheduler run hung"
    result = outcome["result"]
    if isinstance(result, BaseException):
        raise result
    return result


@do
def _noop():
    return (yield Pure(None))


@do
def _outcome(task: Task[object]):
    """Wait on ``task`` and name how it ended."""
    try:
        value = yield Wait(task)
    except TaskCancelledError:
        return "cancelled"
    return ("completed", value)


@do
def _guarded_worker(gate: Promise[None]):
    """Park on ``gate``; its finally records and performs an effect."""
    try:
        yield Tell("start")
        yield Wait(gate.future)
        yield Tell("resumed")
    finally:
        yield Tell("finally")
        yield Wait((yield Spawn(_noop())))
        yield Tell("finally effect done")


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_discard_skips_finally_and_its_effects(implementation: SchedulerImplementation) -> None:
    @do
    def body():
        gate = yield CreatePromise()
        task = yield Spawn(_guarded_worker(gate))
        _ = yield Wait((yield Spawn(_noop())))  # let the worker park
        yield Discard(task)
        return (yield _outcome(task))

    assert _run(body(), implementation) == _Recorded("cancelled", ("start",))


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_cancel_still_runs_finally(implementation: SchedulerImplementation) -> None:
    """The contrast: Cancel keeps unwinding the task (its finally and effects run)."""

    @do
    def body():
        gate = yield CreatePromise()
        task = yield Spawn(_guarded_worker(gate))
        _ = yield Wait((yield Spawn(_noop())))
        yield Cancel(task)
        return (yield _outcome(task))

    assert _run(body(), implementation) == _Recorded(
        "cancelled", ("start", "finally", "finally effect done")
    )


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_discard_drops_a_queued_resume(implementation: SchedulerImplementation) -> None:
    """The task was woken (its resume is queued) but has not run yet."""

    @do
    def worker(gate: Promise[str]):
        try:
            value = yield Wait(gate.future, priority=PRIORITY_IDLE)
            yield Tell(f"got {value}")
        finally:
            yield Tell("finally")

    @do
    def completer(gate: Promise[str]):
        yield CompletePromise(gate, "value")  # queues the worker's resume

    @do
    def body():
        gate = yield CreatePromise()
        task = yield Spawn(worker(gate))
        completing = yield Spawn(completer(gate))
        yield Discard(task)  # the worker's resume is queued, not yet run
        outcome = yield _outcome(task)
        yield Wait(completing)
        return outcome

    assert _run(body(), implementation) == _Recorded("cancelled", ())


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_discard_stops_a_task_unwinding_from_cancel(
    implementation: SchedulerImplementation,
) -> None:
    """A Cancel queued the throw; a Discard before it is delivered drops the task."""

    @do
    def body():
        gate = yield CreatePromise()
        task = yield Spawn(_guarded_worker(gate))
        _ = yield Wait((yield Spawn(_noop())))
        yield Cancel(task)
        yield Discard(task)
        return (yield _outcome(task))

    assert _run(body(), implementation) == _Recorded("cancelled", ("start",))


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_self_discard_ends_the_task_where_it_stands(
    implementation: SchedulerImplementation,
) -> None:
    @do
    def worker(own: Promise[Task[object]]):
        try:
            me = yield Wait(own.future)
            yield Tell("before")
            yield Discard(me)
            yield Tell("after")
        finally:
            yield Tell("finally")

    @do
    def body():
        own = yield CreatePromise()
        task = yield Spawn(worker(own))
        yield CompletePromise(own, task)
        return (yield _outcome(task))

    assert _run(body(), implementation) == _Recorded("cancelled", ("before",))


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_discard_of_an_unstarted_task_runs_nothing(
    implementation: SchedulerImplementation,
) -> None:
    @do
    def worker():
        yield Tell("ran")
        return "value"

    @do
    def body():
        task = yield Spawn(worker(), priority=PRIORITY_IDLE)
        yield Discard(task)  # the task was only queued
        return (yield _outcome(task))

    assert _run(body(), implementation) == _Recorded("cancelled", ())


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_discard_of_a_finished_task_changes_nothing(
    implementation: SchedulerImplementation,
) -> None:
    @do
    def body():
        task = yield Spawn(_noop())
        before = yield _outcome(task)
        yield Discard(task)
        return (before, (yield _outcome(task)))

    finished = ("completed", None)
    assert _run(body(), implementation) == _Recorded((finished, finished), ())
