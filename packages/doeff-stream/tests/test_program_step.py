"""A step may answer a Program of its ``Next`` (a defk composed of other defks) — and that Program performs no effect.

The fold and the replay evaluate the step's Program the same way: under the handler that refuses every effect, through
``step_answer``. So a Program step gives the same trace as the plain step it is written from, and a step that performs
an effect — whichever effect, ``Ask`` and the clock included — ends with ``StepYieldedEffectError``, which the step itself
cannot catch. A plain step's replay still runs without the VM.
"""

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from doeff_core_effects import Spawn, Wait
from doeff_events import WaitForEvent
from doeff_stream import (
    Answered,
    Cancelled,
    Finish,
    Next,
    Raised,
    Run,
    StepYieldedEffectError,
    merge,
    replay,
    subscribe,
)
from doeff_stream import _harness as harness
from doeff_time import Delay
from stream_test_support import Kick, forever, kicked

from doeff import Ask, Get, Program, Pure, do


@dataclass(frozen=True)
class Order:
    """An external event: an order for ``item``."""

    item: str


@dataclass(frozen=True)
class Shop:
    """The state of the steps below: what is being fetched and what came."""

    fetching: frozenset[str] = frozenset()
    stocked: tuple[str, ...] = ()


ShopAnswer = Next[Shop] | Program[Next[Shop]]
"""What the steps below answer: the value, or a Program of it."""


def shape(effects: tuple) -> tuple:
    """The effects with each ``Run`` as its key: a program is not compared by value (two ``Pure`` are not equal)."""
    return tuple(("run", effect.key) if isinstance(effect, Run) else effect for effect in effects)


def shop(state: Shop, event: object) -> Next[Shop]:
    """The plain step."""
    match event:
        case Order(item=item) if item in state.fetching:
            return Next(state)
        case Order(item=item):
            return Next(
                Shop(state.fetching | {item}, state.stocked), (Run(("fetch", item), Pure(item)),)
            )
        case Answered(key=("fetch", item), value=value):
            stocked = (*state.stocked, str(value))
            done = (Finish(stocked),) if len(stocked) == 2 else ()
            return Next(Shop(state.fetching - {item}, stocked), done)
        case Raised(key=("fetch", item)) | Cancelled(key=("fetch", item)):
            return Next(Shop(state.fetching - {item}, state.stocked))
        case _:
            return Next(state)


@do
def ordered(state: Shop, item: str):
    """The answer to an order, as a Program: the shape a defk reducer calls with ``<-``."""
    if item in state.fetching:
        return Next(state)
    return Next(Shop(state.fetching | {item}, state.stocked), (Run(("fetch", item), Pure(item)),))


@do
def stocked(state: Shop, item: str, value: object):
    """The answer to a fetched item, as a Program."""
    stocked = (*state.stocked, str(value))
    done = (Finish(stocked),) if len(stocked) == 2 else ()
    return Next(Shop(state.fetching - {item}, stocked), done)


@do
def shop_program(state: Shop, event: object):
    """``shop`` written as a Program that composes other Programs, and performs no effect."""
    match event:
        case Order(item=item):
            return (yield ordered(state, item))
        case Answered(key=("fetch", item), value=value):
            return (yield stocked(state, item, value))
        case Raised(key=("fetch", item)) | Cancelled(key=("fetch", item)):
            return Next(Shop(state.fetching - {item}, state.stocked))
        case _:
            return Next(state)


SHOP_INPUTS = (
    Order("tea"),
    Order("tea"),
    Order("rice"),
    Raised(("fetch", "rice"), RuntimeError("out of rice")),
    Order("rice"),
    Answered(("fetch", "rice"), "rice"),
    Answered(("fetch", "tea"), "tea"),
)


def test_a_program_step_replays_to_the_same_trace_as_the_plain_step() -> None:
    plain = replay(shop, Shop(), SHOP_INPUTS)
    program = replay(shop_program, Shop(), SHOP_INPUTS)
    assert program.states == plain.states
    assert shape(program.effects) == shape(plain.effects)
    assert program.finished == plain.finished == Finish(("rice", "tea"))


def test_a_program_step_folds_to_the_same_value_as_the_plain_step() -> None:
    def kicked_shop(
        step: Callable[[Shop, object], ShopAnswer],
    ) -> Callable[[Shop, object], ShopAnswer]:
        """``step`` with the fold's first input ``Kick()`` read as two orders."""

        def on_kick(state: Shop, event: object) -> ShopAnswer:
            if isinstance(event, Kick):
                first = shop(state, Order("tea"))
                second = shop(first.state, Order("rice"))
                return Next(second.state, (*first.effects, *second.effects))
            return step(state, event)

        return on_kick

    inputs = merge(subscribe(Kick))
    plain = kicked(kicked_shop(shop), Shop(), inputs)
    program = kicked(kicked_shop(shop_program), Shop(), inputs)
    assert plain == program == ("tea", "rice")


# One effect per kind a step might reach for: reader, state, task, event, clock.
EFFECTS = (
    pytest.param(lambda: Ask("budget"), "Ask", id="ask"),
    pytest.param(lambda: Get("seen"), "Get", id="get"),
    pytest.param(lambda: Spawn(Pure(1)), "Spawn", id="spawn"),
    pytest.param(lambda: WaitForEvent(Order), "WaitForEventEffect", id="wait-for-event"),
    pytest.param(lambda: Delay(1.0), "DelayEffect", id="delay"),
)


@pytest.mark.parametrize(("effect", "name"), EFFECTS)
def test_a_step_that_performs_an_effect_is_refused_by_name(
    effect: Callable[[], object], name: str
) -> None:
    @do
    def reads_the_world(state: Shop, event: object):
        yield effect()
        return Next(state)

    with pytest.raises(StepYieldedEffectError) as refused:
        replay(reads_the_world, Shop(), [Order("tea")])
    assert type(refused.value.effect).__name__ == name
    assert refused.value.step.endswith("reads_the_world")
    assert name in str(refused.value)


def test_a_step_that_waits_on_a_task_is_refused() -> None:
    @do
    def waits(state: Shop, event: object):
        task = yield Spawn(Pure(1))
        yield Wait(task)
        return Next(state)

    with pytest.raises(StepYieldedEffectError) as refused:
        replay(waits, Shop(), [Order("tea")])
    assert type(refused.value.effect).__name__ == "Spawn"


def test_a_step_cannot_catch_its_own_refusal() -> None:
    @do
    def swallows(state: Shop, event: object):
        try:
            yield Ask("budget")
        except Exception:  # what the refusal must not reach
            return Next(state)
        return Next(state)

    with pytest.raises(StepYieldedEffectError):
        replay(swallows, Shop(), [Order("tea")])


def test_an_effect_deep_in_the_composed_programs_is_refused() -> None:
    @do
    def budget_of(item: str):
        return (yield Ask(("budget", item)))

    @do
    def composed(state: Shop, event: object):
        yield budget_of("tea")
        return Next(state)

    with pytest.raises(StepYieldedEffectError) as refused:
        replay(composed, Shop(), [Order("tea")])
    assert type(refused.value.effect).__name__ == "Ask"
    assert refused.value.step.endswith("composed")


def test_a_program_step_answers_only_a_next() -> None:
    @do
    def answers_a_tuple(state: Shop, event: object):
        return (state, ())

    @do
    def answers_a_program(state: Shop, event: object):
        return Pure(Next(state))

    @do
    def answers_a_program_among_its_effects(state: Shop, event: object):
        return Next(state, (Pure(1),))  # type: ignore[arg-type]  # the check under test: not a StepEffect

    for step in (answers_a_tuple, answers_a_program, answers_a_program_among_its_effects):
        with pytest.raises(TypeError) as refused:
            replay(step, Shop(), [Order("tea")])  # type: ignore[arg-type]  # the check under test
        assert not isinstance(refused.value, StepYieldedEffectError)


def test_a_plain_step_replays_without_the_vm(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_vm(program: Program[object]) -> object:
        raise AssertionError("the replay of a plain step ran the VM")

    monkeypatch.setattr(harness, "run", no_vm)
    assert replay(shop, Shop(), SHOP_INPUTS).finished == Finish(("rice", "tea"))


def test_a_step_effect_in_the_fold_cancels_the_runs_and_raises() -> None:
    @do
    def asks_on_the_answer(state: tuple[str, ...], event: object):
        if isinstance(event, Kick):
            return Next(
                state, (Run(("wait", "forever"), forever()), Run(("fetch", "tea"), Pure("tea")))
            )
        if isinstance(event, Answered):
            budget = yield Ask("budget")
            return Next((*state, budget))
        return Next(state)

    with pytest.raises(StepYieldedEffectError) as refused:
        kicked(asks_on_the_answer, (), merge(subscribe(Kick)))
    assert type(refused.value.effect).__name__ == "Ask"
