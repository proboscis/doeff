"""``Run(…, inline=True)``: the fold runs the program in its own task and gives its end to the step as the next input.

Counterexamples these checks name: a runtime that spawns inline runs like the others, or puts the inline end on the
bus (the input the run published reaches the step before the run's end); a runtime that lets an inline run's
exception end the fold (no ``Raised`` input); a runtime that runs the inline runs of one answer out of order.
"""

import pytest
from doeff_core_effects import Spawn
from doeff_events import Publish
from doeff_stream import Answered, Finish, Next, Raised, Run, merge, simulate_fold, subscribe
from doeff_stream._step import checked_next
from stream_test_support import T0, Kick, Started

from doeff import do


class BoomError(Exception):
    """The exception an inline run raises in these checks."""


@do
def publishes_then_answers(key: str, value: object):
    # The input this publishes is queued on the bus while the run is running.
    yield Publish(Started(key))
    return value


@do
def raises():
    raise BoomError("boom")


@do
def spawns():
    # An inline run may still start tasks of its own: it runs under the fold's handlers.
    return (yield Spawn(publishes_then_answers("child", 0)))


def recording(first_effects: tuple, ends_expected: int):
    """A step that keeps every input it is given, starts ``first_effects`` on the first and finishes once
    ``ends_expected`` run ends came."""

    def step(seen: tuple, event: object) -> Next[tuple]:
        seen = (*seen, event)
        if len(seen) == 1:
            return Next(seen, first_effects)
        ends = [e for e in seen if isinstance(e, Answered | Raised)]
        if len(ends) == ends_expected:
            return Next(seen, (Finish(seen),))
        return Next(seen)

    return step


def run_case(step) -> tuple[object, ...]:
    return simulate_fold(step, (), merge(subscribe(Kick, Started)), events=(Kick(),), start_time=T0)


def test_an_inline_end_is_the_next_input_before_what_the_run_published() -> None:
    seen = run_case(recording((Run("r", publishes_then_answers("r", 7), inline=True),), 1))
    assert seen == (Kick(), Answered("r", 7))


def test_a_spawned_run_s_end_comes_after_what_it_published() -> None:
    # The same program spawned: its end goes over the bus behind the input it published.
    def step(seen: tuple, event: object) -> Next[tuple]:
        seen = (*seen, event)
        if len(seen) == 1:
            return Next(seen, (Run("r", publishes_then_answers("r", 7)),))
        if isinstance(event, Answered):
            return Next(seen, (Finish(seen),))
        return Next(seen)

    assert run_case(step) == (Kick(), Started("r"), Answered("r", 7))


def test_an_inline_run_that_raises_gives_one_raised_input() -> None:
    seen = run_case(recording((Run("r", raises(), inline=True),), 1))
    (end,) = seen[1:]
    assert isinstance(end, Raised)
    assert end.key == "r"
    assert isinstance(end.error, BoomError)


def test_inline_runs_of_one_answer_end_in_order() -> None:
    effects = (
        Run("a", publishes_then_answers("a", 1), inline=True),
        Run("b", publishes_then_answers("b", 2), inline=True),
    )
    seen = run_case(recording(effects, 2))
    assert seen == (Kick(), Answered("a", 1), Answered("b", 2))


def test_an_inline_run_may_spawn_tasks_of_its_own() -> None:
    seen = run_case(recording((Run("r", spawns(), inline=True),), 1))
    assert len(seen) == 2
    assert isinstance(seen[1], Answered)
    assert seen[1].key == "r"


def test_a_key_whose_inline_end_has_not_come_back_is_refused() -> None:
    with pytest.raises(ValueError, match="has not come back"):
        checked_next(Next((), (Run("r", raises(), inline=True),)), frozenset({"r"}))

