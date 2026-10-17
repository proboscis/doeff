"""A spawned task's exception that nothing retrieved is not lost silently.

asyncio warns "Task exception was never retrieved" when a failed task is
dropped unread. The scheduler does the same at the end of the run: one
RuntimeWarning names every spawned task that ended with an exception while
nothing claimed its outcome — no Wait, Gather or Race was given the task, and
no Cancel or Discard either (card ki-84667a89b47a; before, the run returned
and the exception went nowhere — cases/e18_spawned_error_never_waited).

Every test runs under both scheduler implementations.
"""

import warnings
from typing import Any

import pytest
from doeff_core_effects import scheduler as scheduler_module
from doeff_core_effects.handlers import try_handler
from doeff_core_effects.scheduler import (
    PRIORITY_IDLE,
    Cancel,
    CompletePromise,
    CreatePromise,
    Discard,
    Gather,
    Race,
    SchedulerIntrospection,
    Spawn,
    Wait,
    scheduled,
)

from doeff import Try, do
from doeff import run as doeff_run

pytestmark = pytest.mark.usefixtures("scheduler_index_self_check")

NEVER_RETRIEVED = "never retrieved"


@pytest.fixture(params=["python", "rust"], autouse=True)
def scheduler_implementation(request, monkeypatch):
    monkeypatch.setenv("DOEFF_SCHEDULER", request.param)
    return request.param


def _run_recording(program: Any) -> tuple[Any, list[warnings.WarningMessage]]:  # noqa: DOEFF006 — 検の補助の戻り
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = doeff_run(scheduled(program))
    return result, caught


def _never_retrieved(caught: list[warnings.WarningMessage]) -> list[str]:
    return [
        str(w.message)
        for w in caught
        if issubclass(w.category, RuntimeWarning) and NEVER_RETRIEVED in str(w.message)
    ]


def _run_without_warnings(program: Any) -> Any:
    """Run with every warning turned into an error: the run must not warn at all."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return doeff_run(scheduled(program))


@do
def boom(message: str = "failed in task"):
    raise ValueError(message)


@do
def fail_after(promise: Any, message: str):
    yield Wait(promise.future)
    raise ValueError(message)


@do
def answer(value: Any):
    return value


@do
def complete(promise: Any):
    yield CompletePromise(promise, None)


@do
def let_queued_tasks_run():
    """Spawn a no-op task and wait for it: every entry queued before it runs first."""
    t = yield Spawn(answer(None))
    return (yield Wait(t))


class TestWarns:
    def test_failure_nothing_waited_on_warns_once_at_run_end(self):
        """The e18 shape: the run still returns, and the exception is named."""

        @do
        def main():
            yield Spawn(boom())
            return "done"

        result, caught = _run_recording(main())

        assert result == "done"
        assert len(caught) == 1, [str(w.message) for w in caught]
        [text] = _never_retrieved(caught)
        assert "ValueError: failed in task" in text
        # The task's program, where it was spawned, and where it raised.
        assert "running boom" in text
        assert "(spawned at TestWarns." in text
        assert f".<locals>.main  {__file__}:" in text
        assert f"raised at {__file__}:" in text
        assert "in boom" in text

    def test_daemon_task_failure_warns_too(self):
        """daemon=True says the run does not wait for the task, not that its
        exception may vanish."""

        @do
        def main():
            yield Spawn(boom("daemon failed"), daemon=True)
            return "done"

        result, caught = _run_recording(main())

        assert result == "done"
        [text] = _never_retrieved(caught)
        assert "ValueError: daemon failed" in text

    def test_one_warning_names_every_unretrieved_exception(self):
        @do
        def main():
            for name in ("first", "second", "third"):
                yield Spawn(boom(name))
            waited = yield Spawn(answer(1))
            return (yield Wait(waited))

        result, caught = _run_recording(main())

        assert result == 1
        [text] = _never_retrieved(caught)
        assert text.startswith("3 spawned task exceptions were never retrieved")
        for name in ("first", "second", "third"):
            assert f"ValueError: {name}" in text

    def test_named_exceptions_are_capped_and_the_rest_counted(self, monkeypatch):
        monkeypatch.setattr(scheduler_module, "UNRETRIEVED_EXCEPTIONS_NAMED", 2)

        @do
        def main():
            for i in range(5):
                yield Spawn(boom(f"failure {i}"))
            return "done"

        _result, caught = _run_recording(main())

        [text] = _never_retrieved(caught)
        assert text.startswith("5 spawned task exceptions were never retrieved")
        assert "failure 0" in text
        assert "failure 1" in text
        assert "failure 2" not in text
        assert "and 3 more" in text

    def test_exception_of_a_swept_task_is_still_reported(self, monkeypatch):
        """The #502 sweep deletes a terminal task whose handles are all dead —
        a failed task nobody kept a handle to is exactly that. Its exception
        must still reach the run-end warning."""
        monkeypatch.setattr(scheduler_module, "HANDLE_SWEEP_INTERVAL", 1)  # sweep at every id

        @do
        def main():
            yield Spawn(boom("swept"))  # fails at once; its handle is dropped
            t = yield Spawn(answer(0))  # this id's sweep deletes the failed row
            yield Wait(t)
            counts = yield SchedulerIntrospection()
            return counts["tasks"]

        rows, caught = _run_recording(main())

        assert rows == 1, "only the waited task's row is left: the failed one was swept"
        [text] = _never_retrieved(caught)
        assert "ValueError: swept" in text

    def test_root_failure_still_reports_the_task_exception(self):
        @do
        def main():
            yield Spawn(boom("task failed"))
            raise RuntimeError("root failed")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(RuntimeError, match="root failed"):
                doeff_run(scheduled(main()))

        [text] = _never_retrieved(caught)
        assert "ValueError: task failed" in text


class TestDoesNotWarn:
    def test_task_waited_after_it_failed(self):
        @do
        def main():
            t = yield Spawn(boom())  # runs (and fails) before main resumes
            try:
                yield Wait(t)
            except ValueError as error:
                return str(error)
            return "unreachable"

        assert _run_without_warnings(main()) == "failed in task"

    def test_task_waited_before_it_failed(self):
        @do
        def main():
            p = yield CreatePromise()
            t = yield Spawn(fail_after(p, "late failure"))  # parks on p
            completer = yield Spawn(complete(p), priority=PRIORITY_IDLE)
            try:
                yield Wait(t)  # parked before the failure
                return "unreachable"
            except ValueError as error:
                yield Wait(completer)
                return str(error)

        assert _run_without_warnings(main()) == "late failure"

    def test_gather_sibling_failing_after_the_fail_fast(self):
        """Gather raises the first failure; a sibling that fails later was still
        given to the Gather (asyncio.gather retrieves it too)."""

        @do
        def main():
            p = yield CreatePromise()
            first = yield Spawn(boom("first"))
            later = yield Spawn(fail_after(p, "later"))
            try:
                yield Gather(first, later)
            except ValueError as error:
                caught = str(error)
            yield CompletePromise(p, None)  # `later` fails before main resumes
            return caught

        assert _run_without_warnings(main()) == "first"

    def test_race_loser_failing_after_the_race(self):
        @do
        def main():
            p = yield CreatePromise()
            winner = yield Spawn(answer("won"))
            loser = yield Spawn(fail_after(p, "lost"))
            value = yield Race(winner, loser)
            yield CompletePromise(p, None)  # the loser fails before main resumes
            return value

        assert _run_without_warnings(main()) == "won"

    def test_cancelled_task(self):
        @do
        def main():
            p = yield CreatePromise()
            t = yield Spawn(fail_after(p, "never raised"))
            yield Cancel(t)
            # Let the cancelled task unwind before the root returns.
            yield let_queued_tasks_run()
            return "done"

        assert _run_without_warnings(main()) == "done"

    def test_cancelled_task_whose_cleanup_raised(self):
        """The Cancel claimed the task's outcome, so the exception its cleanup
        raised is not reported as never retrieved."""

        @do
        def cleanup_raises(p: Any):
            try:
                yield Wait(p.future)
            finally:
                raise ValueError("cleanup failed")

        @do
        def main():
            p = yield CreatePromise()
            t = yield Spawn(cleanup_raises(p))
            yield Cancel(t)
            yield let_queued_tasks_run()
            return "done"

        assert _run_without_warnings(main()) == "done"

    def test_task_cancelled_or_discarded_after_it_failed(self):
        @do
        def main():
            cancelled = yield Spawn(boom("cancelled later"))
            discarded = yield Spawn(boom("discarded later"))
            yield Cancel(cancelled)
            yield Discard(discarded)
            return "done"

        assert _run_without_warnings(main()) == "done"

    def test_task_ending_with_task_cancelled_error(self):
        """A task that let the TaskCancelledError of a task it waited on
        propagate ended by cancellation, as asyncio treats it — not by a
        failure to report."""

        @do
        def waits_on(t: Any):
            return (yield Wait(t))

        @do
        def main():
            p = yield CreatePromise()
            target = yield Spawn(fail_after(p, "never raised"))
            yield Spawn(waits_on(target))
            yield Cancel(target)
            yield let_queued_tasks_run()  # target unwinds; the waiter is woken
            yield let_queued_tasks_run()  # the waiter ends with TaskCancelledError
            counts = yield SchedulerIntrospection()
            return counts["ready"]

        assert _run_without_warnings(main()) == 0

    def test_successful_task_nothing_waited_on(self):
        @do
        def main():
            yield Spawn(answer(1))
            return "done"

        assert _run_without_warnings(main()) == "done"

    def test_failure_caught_by_try_inside_the_task(self):
        @do
        def main():
            yield Spawn(Try(boom()))
            return "done"

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert doeff_run(scheduled(try_handler(main()))) == "done"
