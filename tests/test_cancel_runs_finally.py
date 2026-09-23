"""Cancel delivers TaskCancelledError into the cancelled task (#cancel-finally).

``Cancel(task)`` used to mark the task ``cancelled`` and drop its parked
continuation, so the task's ``except`` / ``finally`` never ran (a remote
worker lease stayed held until it expired). The contract fixed here:

- A task that already started receives ``TaskCancelledError`` at the point
  where it is suspended (Wait / Gather / Race / AcquireSemaphore / a queued
  resume / an Await), and its ``except`` and ``finally`` blocks run.
- Cleanup may perform effects (scheduler effects, Await I/O, user effects).
- ``Cancel`` only requests: the caller resumes immediately. Waiters of the
  task wake only after the task has finished unwinding.
- Outcome seen by waiters: ``TaskCancelledError`` when the task ended by
  propagating it OR by swallowing it and returning (the late value is
  discarded); a different exception raised during cleanup is reported
  instead (failed).
- Cancellation is delivered once per ``Cancel``: a task that swallows it
  keeps running; another ``Cancel`` delivers it again at the next point.
- A task that never started is cancelled without running any of its body.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import doeff_hy  # noqa: F401  # registers the Hy import hook for the defk module
from doeff_core_effects import Await, await_handler
from doeff_core_effects.scheduler import (
    PRIORITY_IDLE,
    AcquireSemaphore,
    Cancel,
    CompletePromise,
    CreatePromise,
    CreateSemaphore,
    Gather,
    Race,
    ReleaseSemaphore,
    Spawn,
    TaskCancelledError,
    Wait,
    scheduled,
)

import tests.cancel_runs_finally_cases as hy_cases
from doeff import do
from doeff import run as doeff_run
from doeff.program import Pure

RUN_TIMEOUT_SECONDS = 5.0


def _run(program: Any, *, with_await: bool = False) -> Any:
    """Run ``program`` under the scheduler on a thread, failing on a hang."""
    result: dict[str, Any] = {}

    def _worker() -> None:
        try:
            body = await_handler()(program) if with_await else program
            result["value"] = doeff_run(scheduled(body))
        except BaseException as exc:  # re-raised below on the test thread
            result["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=RUN_TIMEOUT_SECONDS)
    assert not thread.is_alive(), "scheduler run hung"
    if "error" in result:
        raise result["error"]
    return result["value"]


@do
def _noop():
    return (yield Pure(None))


@do
def _outcome(task: Any):
    """Wait on ``task`` and name how it ended."""
    try:
        value = yield Wait(task)
    except TaskCancelledError:
        return "cancelled"
    except Exception as error:  # the test inspects the reported error
        return ("failed", error)
    return ("completed", value)


# ---------------------------------------------------------------------------
# finally / except run at every suspension point
# ---------------------------------------------------------------------------


def test_finally_runs_when_cancelled_while_waiting_on_promise() -> None:
    events: list[str] = []

    @do
    def worker(gate: Any):
        try:
            events.append("start")
            yield Wait(gate.future)
            events.append("resumed")
        except TaskCancelledError:
            events.append("except")
            raise
        finally:
            events.append("finally")

    @do
    def body():
        gate = yield CreatePromise()
        task = yield Spawn(worker(gate))
        _ = yield Wait((yield Spawn(_noop())))  # let the worker park
        yield Cancel(task)
        return (yield _outcome(task))

    assert _run(body()) == "cancelled"
    assert events == ["start", "except", "finally"]


def test_finally_runs_when_cancelled_by_another_task() -> None:
    events: list[str] = []

    @do
    def worker(gate: Any):
        try:
            yield Wait(gate.future)
        finally:
            events.append("finally")

    @do
    def canceller(task: Any):
        yield Cancel(task)
        events.append("cancel returned")

    @do
    def body():
        gate = yield CreatePromise()
        task = yield Spawn(worker(gate))
        _ = yield Wait((yield Spawn(_noop())))
        c = yield Spawn(canceller(task))
        outcome = yield _outcome(task)
        yield Wait(c)
        return outcome

    assert _run(body()) == "cancelled"
    # Cancel only requests: the canceller resumes before the victim unwinds.
    assert events == ["cancel returned", "finally"]


def test_finally_runs_when_cancelled_with_a_queued_resume() -> None:
    """The task was woken (resume queued) but has not run yet."""
    events: list[str] = []

    @do
    def worker(gate: Any):
        try:
            # Wakes at IDLE, so the root (NORMAL) runs before it resumes.
            value = yield Wait(gate.future, priority=PRIORITY_IDLE)
            events.append(f"got {value}")
        finally:
            events.append("finally")

    @do
    def completer(gate: Any):
        yield CompletePromise(gate, "value")  # queues the worker's resume

    @do
    def body():
        gate = yield CreatePromise()
        task = yield Spawn(worker(gate))
        c = yield Spawn(completer(gate))
        yield Cancel(task)  # the worker's resume is queued, not yet run
        outcome = yield _outcome(task)
        yield Wait(c)
        return outcome

    assert _run(body()) == "cancelled"
    assert events == ["finally"]


def test_finally_runs_when_cancelled_in_gather_and_race() -> None:
    events: list[str] = []

    @do
    def gatherer(gate: Any):
        try:
            yield Gather(gate.future)
        except TaskCancelledError:
            events.append("gather except")
            raise
        finally:
            events.append("gather finally")

    @do
    def racer(gate: Any):
        try:
            yield Race(gate.future)
        except TaskCancelledError:
            events.append("race except")
            raise
        finally:
            events.append("race finally")

    @do
    def body():
        gate = yield CreatePromise()
        g = yield Spawn(gatherer(gate))
        r = yield Spawn(racer(gate))
        _ = yield Wait((yield Spawn(_noop())))
        yield Cancel(g)
        yield Cancel(r)
        outcomes = (yield _outcome(g)), (yield _outcome(r))
        # The promise is still usable by others: nothing else was resolved.
        yield CompletePromise(gate, "later")
        return outcomes, (yield Wait(gate.future))

    assert _run(body()) == (("cancelled", "cancelled"), "later")
    # "except" proves the exception was delivered (a garbage-collected
    # generator would run only its finally, via GeneratorExit).
    assert sorted(events) == [
        "gather except", "gather finally", "race except", "race finally",
    ]


def test_finally_runs_when_cancelled_while_acquiring_semaphore() -> None:
    events: list[str] = []

    @do
    def worker(sem: Any):
        try:
            yield AcquireSemaphore(sem)
            events.append("acquired")
        except TaskCancelledError:
            events.append("except")
            raise
        finally:
            events.append("finally")

    @do
    def after(sem: Any):
        yield AcquireSemaphore(sem)
        yield ReleaseSemaphore(sem)
        return "next waiter got the permit"

    @do
    def body():
        sem = yield CreateSemaphore(1)
        yield AcquireSemaphore(sem)
        task = yield Spawn(worker(sem))
        _ = yield Wait((yield Spawn(_noop())))
        yield Cancel(task)
        outcome = yield _outcome(task)
        yield ReleaseSemaphore(sem)
        return outcome, (yield Wait((yield Spawn(after(sem)))))

    assert _run(body()) == ("cancelled", "next waiter got the permit")
    assert events == ["except", "finally"]


def test_finally_runs_for_task_parked_on_await() -> None:
    """The actual failure: a task waiting on remote/async work must run its
    cleanup when cancelled (the bridged coroutine is cancelled too, #498)."""
    events: list[str] = []
    started = threading.Event()
    coroutine_finalized = threading.Event()

    async def remote_call() -> str:
        started.set()
        try:
            await asyncio.Event().wait()
            return "finished"
        finally:
            coroutine_finalized.set()

    async def until_started() -> None:
        assert await asyncio.to_thread(started.wait, RUN_TIMEOUT_SECONDS)

    @do
    def worker():
        try:
            return (yield Await(remote_call()))
        finally:
            events.append("task finally")

    @do
    def body():
        task = yield Spawn(worker())
        yield Await(until_started())
        yield Cancel(task)
        return (yield _outcome(task))

    assert _run(body(), with_await=True) == "cancelled"
    assert events == ["task finally"]
    assert coroutine_finalized.wait(RUN_TIMEOUT_SECONDS)


# ---------------------------------------------------------------------------
# Cleanup that performs effects
# ---------------------------------------------------------------------------


def test_finally_may_perform_effects_and_waiters_see_the_end() -> None:
    """Cleanup I/O (Await, Spawn/Wait, CompletePromise) runs to completion
    before anyone waiting on the task is woken."""
    events: list[str] = []

    @do
    def release_lease():
        yield Await(asyncio.sleep(0))
        events.append("lease released")
        return "released"

    @do
    def worker(gate: Any, done: Any):
        try:
            yield Wait(gate.future)
        finally:
            events.append("cleanup start")
            yield Await(asyncio.sleep(0))
            released = yield Wait((yield Spawn(release_lease())))
            yield CompletePromise(done, released)
            events.append("cleanup end")

    @do
    def body():
        gate = yield CreatePromise()
        done = yield CreatePromise()
        task = yield Spawn(worker(gate, done))
        _ = yield Wait((yield Spawn(_noop())))
        yield Cancel(task)
        outcome = yield _outcome(task)
        events.append("waiter woke")
        return outcome, (yield Wait(done.future))

    assert _run(body(), with_await=True) == ("cancelled", "released")
    assert events == ["cleanup start", "lease released", "cleanup end", "waiter woke"]


def test_error_raised_during_cleanup_is_reported_to_waiters() -> None:
    boom = RuntimeError("cleanup failed")

    @do
    def worker(gate: Any):
        try:
            yield Wait(gate.future)
        finally:
            raise boom

    @do
    def body():
        gate = yield CreatePromise()
        task = yield Spawn(worker(gate))
        _ = yield Wait((yield Spawn(_noop())))
        yield Cancel(task)
        return (yield _outcome(task))

    outcome = _run(body())
    assert outcome == ("failed", boom)
    assert isinstance(boom.__context__, TaskCancelledError)


# ---------------------------------------------------------------------------
# Swallowed cancellation, repeated Cancel, unstarted and finished tasks
# ---------------------------------------------------------------------------


def test_swallowed_cancel_keeps_running_and_waiters_still_see_cancelled() -> None:
    events: list[str] = []

    @do
    def stubborn(gate: Any, second: Any):
        try:
            yield Wait(gate.future)
        except TaskCancelledError:
            events.append("swallowed")
        value = yield Wait(second.future)
        events.append(f"continued with {value}")
        return "late value"

    @do
    def body():
        gate = yield CreatePromise()
        second = yield CreatePromise()
        task = yield Spawn(stubborn(gate, second))
        _ = yield Wait((yield Spawn(_noop())))
        yield Cancel(task)
        _ = yield Wait((yield Spawn(_noop())))
        yield CompletePromise(second, "second")
        return (yield _outcome(task))

    assert _run(body()) == "cancelled"
    assert events == ["swallowed", "continued with second"]


def test_second_cancel_is_delivered_again() -> None:
    events: list[str] = []

    @do
    def stubborn(gate: Any, second: Any):
        try:
            yield Wait(gate.future)
        except TaskCancelledError:
            events.append("swallowed first")
        try:
            yield Wait(second.future)
        finally:
            events.append("second finally")

    @do
    def body():
        gate = yield CreatePromise()
        second = yield CreatePromise()
        task = yield Spawn(stubborn(gate, second))
        _ = yield Wait((yield Spawn(_noop())))
        yield Cancel(task)
        _ = yield Wait((yield Spawn(_noop())))
        yield Cancel(task)
        return (yield _outcome(task))

    assert _run(body()) == "cancelled"
    assert events == ["swallowed first", "second finally"]


def test_unstarted_task_is_cancelled_without_running() -> None:
    events: list[str] = []

    @do
    def worker():
        try:
            events.append("ran")
            yield _noop()
        finally:
            events.append("finally")

    @do
    def body():
        task = yield Spawn(worker(), priority=PRIORITY_IDLE)
        yield Cancel(task)  # the task was only queued
        return (yield _outcome(task))

    assert _run(body()) == "cancelled"
    assert events == []


def test_cancel_of_finished_task_is_a_no_op() -> None:
    @do
    def worker():
        return (yield Pure("done"))

    @do
    def body():
        task = yield Spawn(worker())
        value = yield Wait(task)
        yield Cancel(task)
        return value, (yield _outcome(task))

    assert _run(body()) == ("done", ("completed", "done"))


def test_self_cancel_raises_at_the_cancel_point() -> None:
    events: list[str] = []

    @do
    def worker(handle: Any):
        me = yield Wait(handle.future)
        try:
            yield Cancel(me)
            events.append("continued after self cancel")
        finally:
            events.append("finally")

    @do
    def body():
        handle = yield CreatePromise()
        task = yield Spawn(worker(handle))
        yield CompletePromise(handle, task)
        return (yield _outcome(task))

    assert _run(body()) == "cancelled"
    assert events == ["finally"]


# ---------------------------------------------------------------------------
# Hy defk
# ---------------------------------------------------------------------------


def test_hy_defk_finally_runs_on_cancel() -> None:
    events: list[str] = []
    assert _run(hy_cases.cancel_parked_worker(events)) == "cancelled"
    assert events == ["start", "except", "finally"]


def test_hy_defk_cleanup_performs_effects() -> None:
    events: list[str] = []
    assert _run(hy_cases.cancel_worker_with_effectful_cleanup(events)) == [
        "cancelled",
        "released",
    ]
    assert events == ["cleanup start", "cleanup end"]
