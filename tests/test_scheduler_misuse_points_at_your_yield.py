"""Gather / Wait / Race given something other than a task, and a wait nothing can ever end, fail at
the user's own yield with a message that names the fix.

Before (doeff 0.5.0 and main 4685dc801d62, the default Python scheduler):

- ``Gather(worker("a"), worker("b"))`` without Spawn printed an empty "doeff Traceback" heading,
  then ``TypeError: expected Task or Future, got Call`` (the VM's node name), and the last line was
  ``RuntimeError: generator ignored GeneratorExit`` from the scheduler's ``root_close_out``: the
  scheduler raised the TypeError while dispatching, so it escaped the run past the root body, and
  the dropped body ignored its close.
- Waiting on a promise nobody completes printed the same empty heading above the
  ``SchedulerDeadlockError``, and the tasks the run left behind were never unwound.

(first-look error table 2026-10-17, row 14 and the appendix; card ki-8152466e96ad)
"""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import textwrap
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from doeff_core_effects.scheduler import (
    Cancel,
    CreatePromise,
    Future,
    Gather,
    Race,
    SchedulerDeadlockError,
    SchedulerImplementation,
    Spawn,
    Wait,
    scheduled,
)

from doeff import EffectBase, do, run

pytestmark = pytest.mark.usefixtures("scheduler_index_self_check")

IMPLEMENTATIONS: list[SchedulerImplementation] = ["python", "rust"]

GATHER_OF_PROGRAMS = (
    "Gather takes the tasks that Spawn returns, got a program. Spawn each program first and "
    "pass the tasks: t = yield Spawn(prog); yield Gather(t, ...)"
)


@do
def worker(label: str):
    return label
    yield  # a generator: @do takes a generator function


def _run_script(tmp_path: Path, source: str, implementation: SchedulerImplementation) -> str:
    """Run ``source`` as a first-time user would — a new process — and return its stderr."""
    script = tmp_path / "main.py"
    script.write_text(textwrap.dedent(source))
    env = {**os.environ, "DOEFF_SCHEDULER": implementation, "PYTHONDONTWRITEBYTECODE": "1"}
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 1, completed.stderr
    return completed.stderr


def _last_line(text: str) -> str:
    return [line for line in text.splitlines() if line.strip()][-1]


def _doeff_traceback(stderr: str) -> str:
    """The "doeff Traceback" block the run prints, up to Python's own traceback."""
    _, _, rest = stderr.partition("doeff Traceback (most recent call last):")
    assert rest, f"no doeff Traceback in:\n{stderr}"
    return rest.partition("Traceback (most recent call last):")[0]


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_gather_of_programs_ends_with_spawn_first_at_the_users_yield(
    tmp_path: Path, implementation: SchedulerImplementation
) -> None:
    stderr = _run_script(
        tmp_path,
        """\
        from doeff import do, run
        from doeff_core_effects.scheduler import scheduled, Gather

        @do
        def worker(label):
            return label

        @do
        def main():
            return (yield Gather(worker("a"), worker("b")))

        print(run(scheduled(main())))
        """,
        implementation,
    )
    assert _last_line(stderr) == f"TypeError: {GATHER_OF_PROGRAMS}"
    assert "GeneratorExit" not in stderr
    assert "Exception ignored" not in stderr
    assert "got Call" not in stderr
    shown = _doeff_traceback(stderr)
    assert "main()" in shown
    assert 'return (yield Gather(worker("a"), worker("b")))' in shown


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_a_wait_nothing_completes_points_at_the_users_wait(
    tmp_path: Path, implementation: SchedulerImplementation
) -> None:
    stderr = _run_script(
        tmp_path,
        """\
        from doeff import do, run
        from doeff_core_effects.scheduler import scheduled, CreatePromise, Wait

        @do
        def main():
            p = yield CreatePromise()
            return (yield Wait(p.future))

        print(run(scheduled(main())))
        """,
        implementation,
    )
    assert _last_line(stderr) == (
        "doeff_core_effects.scheduler.SchedulerDeadlockError: scheduler deadlock: parked waiters "
        "that no external completion can wake: root (wait) on promise 0"
    )
    assert "Exception ignored" not in stderr
    shown = _doeff_traceback(stderr)
    assert "main()" in shown
    assert "return (yield Wait(p.future))" in shown


@pytest.mark.parametrize(
    "implementation",
    [
        "python",
        pytest.param(
            "rust",
            marks=pytest.mark.xfail(
                strict=True,
                reason=(
                    "the Rust scheduler throws the deadlock into the continuation it was "
                    "dispatching but keeps that continuation's parked registration, so the "
                    "close-out reports the root itself as abandoned work"
                ),
            ),
        ),
    ],
)
def test_the_root_given_a_deadlock_is_not_reported_as_abandoned_work(
    tmp_path: Path, implementation: SchedulerImplementation
) -> None:
    stderr = _run_script(
        tmp_path,
        """\
        from doeff import do, run
        from doeff_core_effects.scheduler import scheduled, CreatePromise, Wait

        @do
        def main():
            p = yield CreatePromise()
            return (yield Wait(p.future))

        print(run(scheduled(main())))
        """,
        implementation,
    )
    assert "abandoning in-flight work" not in stderr


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
@pytest.mark.parametrize(
    ("make_effect", "message"),
    [
        (
            lambda: Wait(worker("a")),
            "Wait takes the task that Spawn returns, got a program. Spawn the program first and "
            "wait on its task: t = yield Spawn(prog); yield Wait(t)",
        ),
        (lambda: Gather(worker("a"), worker("b")), GATHER_OF_PROGRAMS),
        (
            lambda: Race(worker("a"), worker("b")),
            "Race takes the tasks that Spawn returns, got a program. Spawn each program first and "
            "pass the tasks: t = yield Spawn(prog); yield Race(t, ...)",
        ),
        (
            lambda: Gather(42),
            "Gather takes a Task (t = yield Spawn(prog)) or a Future (p.future), got 42 (int)",
        ),
    ],
    ids=["wait-program", "gather-programs", "race-programs", "gather-plain-value"],
)
def test_the_type_error_names_the_effect_and_the_fix(
    implementation: SchedulerImplementation, make_effect: Callable[[], EffectBase], message: str
) -> None:
    @do
    def body():
        return (yield make_effect())

    with pytest.raises(TypeError) as raised:
        run(scheduled(body(), implementation=implementation))
    assert str(raised.value) == message


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_waiting_on_the_promise_itself_says_to_wait_on_its_future(
    implementation: SchedulerImplementation,
) -> None:
    @do
    def body():
        p = yield CreatePromise()
        return (yield Wait(p))

    with pytest.raises(TypeError) as raised:
        run(scheduled(body(), implementation=implementation))
    assert (
        str(raised.value)
        == "Wait takes the promise's future, not the promise: yield Wait(p.future)"
    )


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_the_body_catches_the_type_error_at_its_own_yield(
    implementation: SchedulerImplementation,
) -> None:
    @do
    def body():
        try:
            yield Gather(worker("a"))
        except TypeError as error:
            return f"caught: {error}"
        return "not raised"

    answer = run(scheduled(body(), implementation=implementation))
    assert answer == f"caught: {GATHER_OF_PROGRAMS}"


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_race_checks_every_waitable_before_it_reads_one(
    implementation: SchedulerImplementation,
) -> None:
    @do
    def body():
        done = yield Spawn(worker("done"))
        yield Wait(done)
        return (yield Race(done, worker("b")))

    with pytest.raises(TypeError, match=r"^Race takes the tasks that Spawn returns"):
        run(scheduled(body(), implementation=implementation))


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_a_deadlock_reaches_the_root_body_at_its_wait(
    implementation: SchedulerImplementation,
) -> None:
    @do
    def body():
        p = yield CreatePromise()
        try:
            yield Wait(p.future)
        except SchedulerDeadlockError as error:
            return f"caught: {error.parked_waiters}"
        return "not raised"

    assert run(scheduled(body(), implementation=implementation)) == (
        "caught: ['root (wait) on promise 0']"
    )


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_a_deadlocked_run_unwinds_the_tasks_it_leaves_behind(
    implementation: SchedulerImplementation,
) -> None:
    unwound = threading.Event()

    @do
    def stuck(future: Future[None]):
        try:
            yield Wait(future)
        finally:
            unwound.set()

    @do
    def body():
        never = yield CreatePromise()
        yield Spawn(stuck(never.future))
        return (yield Wait(never.future))

    with pytest.raises(SchedulerDeadlockError):
        run(scheduled(body(), implementation=implementation))
    assert unwound.is_set()


@pytest.mark.filterwarnings("error::pytest.PytestUnraisableExceptionWarning")
@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_an_error_that_escapes_the_scheduler_leaves_no_ignored_generator_exit(
    implementation: SchedulerImplementation,
) -> None:
    # Cancel of something that is not a task fails inside the Python scheduler's dispatch, so the
    # error leaves the run past the root body; the dropped body is closed when it is collected,
    # and an exception it ignores there turns into the warning this test makes an error.
    @do
    def body():
        yield Cancel(42)

    with pytest.raises(AttributeError) as raised:
        run(scheduled(body(), implementation=implementation))
    del raised
    gc.collect()
