"""The test harness: a list of inputs in, the states and effects out — and the same refusals as the runtime.

``replay`` refuses what the runtime would refuse (a step answer that is not a valid ``Next``, a ``Run`` of a value that is
not a program, two ``Finish``) and what the runtime
never delivers (the end of a run that is not running, an input after ``Finish``), so a test cannot assert a trace the
fold could not produce.
"""

from dataclasses import dataclass

import pytest
from doeff_stream import (
    Answered,
    Cancelled,
    CancelRun,
    Finish,
    Next,
    Raised,
    Replay,
    Run,
    Stepped,
    replay,
)

from doeff import Pure


@dataclass(frozen=True)
class Order:
    """An external event: an order for ``item``."""

    item: str


@dataclass(frozen=True)
class Shop:
    """The state of the step below: what is being fetched and what came."""

    fetching: frozenset[str] = frozenset()
    stocked: tuple[str, ...] = ()


def fetch(item: str) -> Run:
    return Run(("fetch", item), Pure(item))


def shape(effects: tuple) -> tuple:
    """The effects with each ``Run`` as its key: a program is not compared by value (two ``Pure`` are not equal)."""
    return tuple(("run", effect.key) if isinstance(effect, Run) else effect for effect in effects)


def shop(state: Shop, event: object) -> Next[Shop]:
    match event:
        case Order(item=item) if item in state.fetching:
            return Next(state)
        case Order(item=item):
            return Next(Shop(state.fetching | {item}, state.stocked), (fetch(item),))
        case Answered(key=("fetch", item), value=value):
            stocked = (*state.stocked, value)
            done = (Finish(stocked),) if len(stocked) == 2 else ()
            return Next(Shop(state.fetching - {item}, stocked), done)
        case Raised(key=("fetch", item)) | Cancelled(key=("fetch", item)):
            return Next(Shop(state.fetching - {item}, state.stocked))
        case _:
            return Next(state)


def test_replay_answers_the_state_and_effects_of_each_input() -> None:
    trace = replay(
        shop,
        Shop(),
        [
            Order("tea"),
            Order("tea"),
            Order("rice"),
            Answered(("fetch", "rice"), "rice"),
            Answered(("fetch", "tea"), "tea"),
        ],
    )
    assert shape(trace.effects) == (
        ("run", ("fetch", "tea")),
        ("run", ("fetch", "rice")),
        Finish(("rice", "tea")),
    )
    assert trace.states[1] == Shop(frozenset({"tea"}))
    assert trace.final == Shop(frozenset(), ("rice", "tea"))
    assert trace.finished == Finish(("rice", "tea"))
    assert trace.running == frozenset()
    first = trace.steps[0]
    assert isinstance(first, Stepped)
    assert (first.input, first.state, shape(first.effects)) == (
        Order("tea"),
        Shop(frozenset({"tea"})),
        (("run", ("fetch", "tea")),),
    )


def test_replay_of_no_input_is_the_initial_state() -> None:
    trace = replay(shop, Shop(), [])
    assert trace == Replay(Shop(), ())
    assert trace.final == Shop()
    assert trace.finished is None


def test_replay_keeps_the_runs_still_running() -> None:
    trace = replay(
        shop, Shop(), [Order("tea"), Order("rice"), Raised(("fetch", "tea"), OSError("closed"))]
    )
    assert trace.running == frozenset({("fetch", "rice")})


def test_the_end_of_a_run_that_is_not_running_is_refused() -> None:
    with pytest.raises(ValueError, match="not running"):
        replay(shop, Shop(), [Answered(("fetch", "tea"), "tea")])
    with pytest.raises(ValueError, match="not running"):
        replay(
            shop, Shop(), [Order("tea"), Cancelled(("fetch", "tea")), Cancelled(("fetch", "tea"))]
        )


def test_finish_anywhere_in_an_answer_is_carried_out_last() -> None:
    # The effects of several reducers joined in any order: the Finish of one does not cut off the others.
    trace = replay(
        lambda s, _: Next(s, (Finish("end"), CancelRun("a"), CancelRun("b"))), 0, ["once"]
    )
    assert trace.effects == (CancelRun("a"), CancelRun("b"), Finish("end"))
    assert trace.finished == Finish("end")


def test_an_input_after_finish_is_refused() -> None:
    def finishes(state: int, event: object) -> Next[int]:
        return Next(state, (Finish(state),))

    with pytest.raises(ValueError, match="after Finish"):
        replay(finishes, 0, ["first", "second"])


def test_a_run_of_a_running_key_is_refused() -> None:
    def runs_twice(state: int, event: object) -> Next[int]:
        return Next(state, (Run("k", Pure(1)),))

    with pytest.raises(ValueError, match="has not come back"):
        replay(runs_twice, 0, ["first", "second"])
    with pytest.raises(ValueError, match="has not come back"):
        replay(lambda s, _: Next(s, (Run("k", Pure(1)), Run("k", Pure(2)))), 0, ["once"])


def test_a_key_may_run_again_once_its_end_came() -> None:
    def runs(state: int, event: object) -> Next[int]:
        return Next(state + 1, (Run("k", Pure(state)),)) if event == "go" else Next(state)

    trace = replay(runs, 0, ["go", Answered("k", 0), "go"])
    assert trace.running == frozenset({"k"})


def test_a_cancel_of_a_key_that_is_not_running_is_no_error() -> None:
    trace = replay(lambda s, _: Next(s, (CancelRun("gone"),)), 0, ["once"])
    assert trace.effects == (CancelRun("gone"),)


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        ((0, ()), TypeError),
        (Next(0, ("not an effect",)), TypeError),  # type: ignore[arg-type]  # the check under test: a wrong effect
        (Next(0, (Finish(0), CancelRun("k"), Finish(1))), ValueError),
        (Next(0, (Run("k", 7),)), TypeError),  # type: ignore[arg-type]  # the check under test: not a program
    ],
    ids=["not-next", "not-an-effect", "two-finishes", "run-of-a-value"],
)
def test_a_wrong_answer_is_refused(answer: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        replay(lambda _s, _e: answer, 0, ["once"])
