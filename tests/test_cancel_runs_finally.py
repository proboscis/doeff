"""Cancel delivers TaskCancelledError into the cancelled task (#cancel-finally).

``Cancel(task)`` used to mark the task ``cancelled`` and drop its parked
continuation, so the task's ``except`` / ``finally`` never ran (a remote
worker lease stayed held until it expired). The contract fixed here:

- A task that already started receives ``TaskCancelledError`` at the point
  where it is suspended (Wait / Gather / Race / AcquireSemaphore / a queued
  resume / an Await), and its ``except`` and ``finally`` blocks run.
- Cleanup that waits (Wait / Gather / Race / AcquireSemaphore, an Await)
  runs inside ``shielded``; outside it, the cleanup stops at its first wait.
- ``Cancel`` does not wait for the task: the caller resumes immediately.
  Waiters of the task wake only after the task has finished unwinding.
- Outcome seen by waiters: ``TaskCancelledError`` when the task ended by
  propagating it OR by swallowing it and returning (the late value is
  discarded); a different exception raised during cleanup is reported
  instead (failed).
- A cancelled task stays cancelled: a task that swallows the cancellation
  (an ``except``, a ``Try``) gets it again at its next wait, outside
  ``shielded``. A Cancel that arrives inside ``shielded`` lands at the first
  wait after the region.
- A ``Try(Wait(other))`` of a task that is not itself cancelled answers
  ``Err(TaskCancelledError)`` for a cancelled ``other`` and keeps working.
- A task that never started is cancelled without running any of its body.

Every test runs on both scheduler implementations (python and rust).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import doeff_hy  # noqa: F401  # registers the Hy import hook for the defk module
import pytest
from doeff_core_effects import (
    AcquireSemaphore,
    Await,
    Cancel,
    CompletePromise,
    CreateExternalPromise,
    CreatePromise,
    CreateSemaphore,
    Gather,
    Race,
    ReleaseSemaphore,
    SchedulerImplementation,
    Spawn,
    TaskCancelledError,
    TaskPriority,
    Try,
    Wait,
    await_handler,
    scheduled,
    shielded,
    try_handler,
)

import tests.cancel_runs_finally_cases as hy_cases
from doeff import Err, do
from doeff import run as doeff_run
from doeff.program import Pure

RUN_TIMEOUT_SECONDS = 5.0


@pytest.fixture(autouse=True, params=["python", "rust"])
def implementation(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> SchedulerImplementation:
    """Every test of this module runs on both scheduler implementations."""
    chosen: SchedulerImplementation = request.param
    monkeypatch.setenv("DOEFF_SCHEDULER", chosen)  # read by scheduled() at every call
    return chosen


def _run(program: Any, *, with_await: bool = False) -> Any:
    """Run ``program`` under the scheduler (with ``Try`` answered) on a thread, failing on a hang."""
    result: dict[str, Any] = {}

    def _worker() -> None:
        try:
            body = await_handler(program) if with_await else program
            result["value"] = doeff_run(scheduled(try_handler(body)))
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
            value = yield Wait(gate.future, priority=TaskPriority.IDLE)
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


def test_shielded_cleanup_runs_to_the_end_and_waiters_see_the_end() -> None:
    """Cleanup that waits (Await, Spawn/Wait, CompletePromise) in ``shielded``
    runs to completion before anyone waiting on the task is woken."""
    events: list[str] = []

    @do
    def release_lease():
        yield Await(asyncio.sleep(0))
        events.append("lease released")
        return "released"

    @do
    def cleanup(done: Any):
        events.append("cleanup start")
        yield Await(asyncio.sleep(0))
        released = yield Wait((yield Spawn(release_lease())))
        yield CompletePromise(done, released)
        events.append("cleanup end")

    @do
    def worker(gate: Any, done: Any):
        try:
            yield Wait(gate.future)
        finally:
            yield shielded(cleanup(done))

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


def test_unshielded_cleanup_stops_at_its_first_wait() -> None:
    """Outside ``shielded``, a cancelled task's cleanup runs up to its first
    wait, which raises the cancellation again (the cost of the level form)."""
    events: list[str] = []

    @do
    def worker(gate: Any, second: Any):
        try:
            yield Wait(gate.future)
        finally:
            events.append("cleanup start")
            yield Wait(second.future)
            events.append("cleanup end")

    @do
    def body():
        gate = yield CreatePromise()
        second = yield CreatePromise()
        task = yield Spawn(worker(gate, second))
        _ = yield Wait((yield Spawn(_noop())))
        yield Cancel(task)
        return (yield _outcome(task))

    assert _run(body()) == "cancelled"
    assert events == ["cleanup start"]


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


def test_a_swallowed_cancel_is_raised_again_at_the_next_wait() -> None:
    """One Cancel: the task swallows it, and its next wait raises it again —
    the work after the swallow that waits does not run."""
    events: list[str] = []

    @do
    def stubborn(gate: Any, second: Any):
        try:
            yield Wait(gate.future)
        except TaskCancelledError:
            events.append("swallowed")
        events.append("ran up to the next wait")
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
    assert events == ["swallowed", "ran up to the next wait"]


def test_a_cancel_turned_into_err_by_try_is_raised_again_at_the_next_wait() -> None:
    """The judge flow of the card: ``Try`` turns the cancellation into Err (it
    turns every exception of its program into a Result), and the loop that
    reads the Err goes on — its next judgment's wait is cancelled again, at
    every kind of wait."""
    events: list[str] = []

    @do
    def judge(n: int, gate: Any):
        events.append(f"judge {n}")
        yield Wait(gate.future)
        events.append(f"judge {n} answered")
        return n

    @do
    def flow(gate: Any, sem: Any):
        for n in range(1, 4):
            _ = yield Try(judge(n, gate))
        for kind, program in (
            ("gather", Gather((yield Spawn(_noop())))),
            ("race", Race((yield Spawn(_noop())))),
            ("acquire", AcquireSemaphore(sem)),
        ):
            outcome = yield Try(program)
            events.append(f"{kind}: {type(outcome.error).__name__}")

    @do
    def body():
        gate = yield CreatePromise()
        sem = yield CreateSemaphore(1)
        task = yield Spawn(flow(gate, sem))
        _ = yield Wait((yield Spawn(_noop())))
        yield Cancel(task)
        return (yield _outcome(task))

    assert _run(body()) == "cancelled"
    assert events == [
        "judge 1",
        "judge 2",
        "judge 3",
        "gather: TaskCancelledError",
        "race: TaskCancelledError",
        "acquire: TaskCancelledError",
    ]


def test_try_wait_of_another_cancelled_task_answers_err_and_keeps_working() -> None:
    """Only the cancelled task is cancelled again: a task that waits on a
    cancelled task under ``Try`` gets ``Err(TaskCancelledError)`` and its next
    waits run as usual."""

    @do
    def parked(gate: Any):
        return (yield Wait(gate.future))

    @do
    def answer():
        return (yield Pure("still waiting"))

    @do
    def body():
        gate = yield CreatePromise()
        other = yield Spawn(parked(gate))
        _ = yield Wait((yield Spawn(_noop())))
        yield Cancel(other)
        seen = yield Try(Wait(other))
        after = yield Wait((yield Spawn(answer())))
        return seen, after

    seen, after = _run(body())
    assert isinstance(seen, Err)
    assert isinstance(seen.error, TaskCancelledError)
    assert after == "still waiting"


def test_a_cancel_inside_shielded_lands_at_the_first_wait_after_it() -> None:
    """Nothing is thrown into a shielded region: its waits run, and the
    Cancel that arrived meanwhile is raised at the first wait after it."""
    events: list[str] = []

    @do
    def region(inside: Any, release: Any):
        yield CompletePromise(inside, None)
        value = yield Wait(release.future)
        events.append(f"region got {value}")
        return value

    @do
    def worker(inside: Any, release: Any, after: Any):
        yield shielded(region(inside, release))
        events.append("after the region")
        yield Wait(after.future)
        events.append("waited after the region")

    @do
    def body():
        inside = yield CreatePromise()
        release = yield CreatePromise()
        after = yield CreatePromise()
        task = yield Spawn(worker(inside, release, after))
        yield Wait(inside.future)
        yield Cancel(task)
        yield CompletePromise(release, "released")
        yield CompletePromise(after, None)
        return (yield _outcome(task))

    assert _run(body()) == "cancelled"
    assert events == ["region got released", "after the region"]


def test_the_wait_a_cancelled_task_reaches_stops_its_external_work() -> None:
    """The external work a cancelled task was about to wait on is stopped, as
    Cancel stops the work a parked task waits on — so a judgment begun after
    the cancellation does not keep its I/O running."""
    stopped: list[str] = []

    @do
    def stubborn(gate: Any):
        try:
            yield Wait(gate.future)
        except TaskCancelledError:
            pass
        promise = yield CreateExternalPromise()
        promise.on_cancel(lambda: stopped.append("on_cancel"))
        yield Wait(promise.future)

    @do
    def body():
        gate = yield CreatePromise()
        task = yield Spawn(stubborn(gate))
        _ = yield Wait((yield Spawn(_noop())))
        yield Cancel(task)
        return (yield _outcome(task))

    assert _run(body()) == "cancelled"
    assert stopped == ["on_cancel"]


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
        task = yield Spawn(worker(), priority=TaskPriority.IDLE)
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
