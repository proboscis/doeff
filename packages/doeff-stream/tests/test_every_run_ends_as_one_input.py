"""The runtime's guarantee: however a run's task ends — value, exception or cancel — the step gets exactly one input
for it, and the fold never waits for a run's answer outside the step.

Each case starts runs from the first input and records every input the step is given; the step finishes once the
ends it expects have come, and one more quiet input (a timer) shows no second end came for any run.

Counterexamples these checks name: a runtime whose runner publishes its own end and starts no watcher when the step
cancels it (a runner cancelled before it started publishes nothing — the cancelled-before-start checks find no end); a
runtime that takes every end it is published (a runner that published ends cancelled, and its watcher publishes a
second end — the cancel checks find two, or a key used again takes the old run's second end); a runtime that only
reports values (the exception case never finishes); a runtime that waits for the answer inside the fold (the second run's end
comes after the first's though it ended first).
"""

from datetime import timedelta
from functools import partial

import pytest
from doeff_core_effects import Cancel, Spawn, TaskCancelledError, TaskPriority, Wait
from doeff_events import ArmTimer, EventBus, Publish, TimerFired, WaitForEvents
from doeff_stream import (
    Answered,
    Cancelled,
    CancelRun,
    Finish,
    Next,
    Raised,
    Run,
    merge,
    simulate_fold,
    subscribe,
    timers,
)
from doeff_stream._fold import _watch_cancelled
from doeff_stream._source import RunEnded
from stream_test_support import T0, Kick, Started, forever, on_bus, run_on_sim

from doeff import do

QUIET = "quiet"


class BoomError(Exception):
    """The exception a run raises in these checks."""


@do
def answers(value: object):
    return value


@do
def raises():
    raise BoomError("boom")


@do
def starts_then_waits(key: str):
    yield Publish(Started(key))
    return (yield forever())


@do
def raises_a_cancel_of_its_own():
    # Not cancelled by the step: it waits on a task it cancelled itself, and the wait raises TaskCancelledError.
    inner = yield Spawn(forever())
    yield Cancel(inner)
    return (yield Wait(inner))


def recording(first_effects: tuple, on_started: dict | None = None, expected_ends: int = 1):
    """A step that keeps every input it is given, starts ``first_effects`` on the first one, cancels a run when it
    announces it started (``on_started``), arms a quiet timer once ``expected_ends`` ends came and finishes on it."""

    def step(seen: tuple, event: object) -> Next[tuple]:
        seen = (*seen, event)
        if len(seen) == 1:
            return Next(seen, first_effects)
        if isinstance(event, Started) and on_started is not None:
            return Next(seen, on_started[event.key])
        if isinstance(event, TimerFired) and event.tag == QUIET:
            return Next(seen, (Finish(seen),))
        ends = [e for e in seen if isinstance(e, Answered | Raised | Cancelled)]
        if isinstance(event, Answered | Raised | Cancelled) and len(ends) == expected_ends:
            return Next(seen, (Run(QUIET, ArmTimer(QUIET, T0 + timedelta(hours=1))),))
        return Next(seen)

    return step


def run_case(step) -> tuple:
    return simulate_fold(
        step, (), merge(subscribe(Kick, Started), timers()), events=(Kick(),), start_time=T0
    )


def ends_of(seen: tuple) -> list:
    return [e for e in seen if isinstance(e, Answered | Raised | Cancelled) and e.key != QUIET]


def test_a_run_that_answers_gives_one_answered_input() -> None:
    seen = run_case(recording((Run("r", answers(7)),)))
    assert ends_of(seen) == [Answered("r", 7)]


def test_a_run_that_raises_gives_one_raised_input() -> None:
    seen = run_case(recording((Run("r", raises()),)))
    (end,) = ends_of(seen)
    assert isinstance(end, Raised)
    assert end.key == "r"
    assert isinstance(end.error, BoomError)


def test_a_run_cancelled_after_it_started_gives_one_cancelled_input() -> None:
    seen = run_case(
        recording((Run("r", starts_then_waits("r")),), on_started={"r": (CancelRun("r"),)})
    )
    assert Started("r") in seen
    assert ends_of(seen) == [Cancelled("r")]


def test_a_run_cancelled_in_the_answer_that_started_it_gives_one_cancelled_input() -> None:
    seen = run_case(recording((Run("r", starts_then_waits("r")), CancelRun("r"))))
    assert ends_of(seen) == [Cancelled("r")]


def test_a_runner_cancelled_before_it_started_still_gives_its_end() -> None:
    # The watcher itself on a runner that never ran: its program publishes nothing, the watcher one end.
    origin = object()
    mark = object()

    @do
    def program():
        runner = yield Spawn(starts_then_waits("r"), priority=TaskPriority.IDLE)
        yield Cancel(runner)
        yield _watch_cancelled(origin, mark, "r", runner)
        return (yield WaitForEvents(RunEnded, Started))

    (ended,) = run_on_sim(on_bus(EventBus(), "host", merge(subscribe(Started)), program()))
    assert ended.origin is origin
    assert ended.mark is mark
    assert isinstance(ended.end, Raised)
    assert isinstance(ended.end.error, TaskCancelledError)


def test_an_idle_run_cancelled_in_the_answer_that_started_it_gives_one_cancelled_input() -> None:
    # Below the fold's priority the runner has not started when the cancel reaches it: only its watcher publishes.
    seen = run_case(
        recording((Run("r", starts_then_waits("r"), TaskPriority.IDLE), CancelRun("r")))
    )
    assert Started("r") not in seen
    assert ends_of(seen) == [Cancelled("r")]


def test_a_key_used_again_after_a_cancel_takes_only_the_new_run_s_end() -> None:
    # The cancelled runner publishes its end and ends cancelled, so its watcher publishes a second one; the step
    # starts "r" again on the first. The second end of the old run must not be taken as the new run's end.
    def step(seen: tuple, event: object) -> Next[tuple]:
        seen = (*seen, event)
        match event:
            case Kick():
                return Next(seen, (Run("r", starts_then_waits("r")),))
            case Started():
                return Next(seen, (CancelRun("r"),))
            case Cancelled(key="r"):
                return Next(seen, (Run("r", answers(9)),))
            case Answered(key="r"):
                return Next(seen, (Run(QUIET, ArmTimer(QUIET, T0 + timedelta(hours=1))),))
            case TimerFired(tag=tag) if tag == QUIET:
                return Next(seen, (Finish(seen),))
            case _:
                return Next(seen)

    seen = run_case(step)
    assert ends_of(seen) == [Cancelled("r"), Answered("r", 9)]


def test_a_cancel_the_run_raised_on_its_own_is_raised_not_cancelled() -> None:
    seen = run_case(recording((Run("r", raises_a_cancel_of_its_own()),)))
    (end,) = ends_of(seen)
    assert isinstance(end, Raised)
    assert isinstance(end.error, TaskCancelledError)


def test_a_cancel_of_a_run_that_already_answered_gives_its_answer_only() -> None:
    # "fast" answers before the step cancels it on the announcement of "slow": the cancel finds a finished task.
    seen = run_case(
        recording(
            (Run("fast", answers(1)), Run("slow", starts_then_waits("slow"))),
            on_started={"slow": (CancelRun("fast"), CancelRun("slow"))},
            expected_ends=2,
        )
    )
    assert sorted(ends_of(seen), key=repr) == sorted(
        [Answered("fast", 1), Cancelled("slow")], key=repr
    )


@pytest.mark.parametrize(
    "program_of",
    [partial(answers, 1), raises, partial(starts_then_waits, "r"), raises_a_cancel_of_its_own],
    ids=["value", "exception", "cancel", "own-cancel"],
)
def test_every_way_a_run_ends_gives_exactly_one_input(program_of) -> None:
    # The run is cancelled on its announcement when it makes one (the cancel case); the others end by themselves.
    seen = run_case(recording((Run("r", program_of()),), on_started={"r": (CancelRun("r"),)}))
    assert [type(end) for end in ends_of(seen)] in ([Answered], [Raised], [Cancelled])
    assert len(ends_of(seen)) == 1


def test_the_fold_takes_the_next_input_while_a_run_is_still_running() -> None:
    # The fold does not wait for "slow": the end of "fast", started after it, reaches the step first.
    seen = run_case(
        recording(
            (Run("slow", starts_then_waits("slow")), Run("fast", answers(2))),
            on_started={"slow": ()},
            expected_ends=1,
        )
    )
    assert ends_of(seen) == [Answered("fast", 2)]
