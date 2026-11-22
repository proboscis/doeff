"""``fold_clocked``: the step is given every input with the instant the fold took it.

- Every input — an event, the end of a run, a ``TimerFired`` — reaches the step as ``Clocked(at, input)``, ``at`` being
  the clock right before the step (the virtual clock here: the timer's input comes at its deadline).
- ``at`` never goes back: a clock read earlier than the instant before gives that instant again.
- ``replay_clocked`` gives the step the recorded ``Clocked`` inputs as they are, so the same inputs answer the same
  trace; it refuses an input that is not a ``Clocked`` and an instant earlier than the one before.
- ``fold`` and ``replay`` still give the bare inputs.

Failure cases: a fold that stamped only the bus events would give the run end and the ``TimerFired`` bare
(``test_every_input_reaches_the_step_with_the_instant_the_fold_took_it`` fails); a fold that passed the clock as read
would give an earlier instant after a later one (``test_the_instant_never_goes_back`` fails).
"""

from datetime import datetime, timedelta

import pytest
from doeff_events import EventBus, Publish, TimerFired
from doeff_stream import (
    Answered,
    Arm,
    Clocked,
    Finish,
    Next,
    Run,
    fold,
    fold_clocked,
    merge,
    replay,
    replay_clocked,
    subscribe,
    timers,
)
from doeff_time import GetTimeEffect
from stream_test_support import T0, Kick, on_bus, run_on_sim

from doeff import Pass, Resume, do, handler, with_handlers

INPUTS = merge(subscribe(Kick), timers())


def _seen_step(state: tuple[Clocked, ...], given: Clocked) -> Next[tuple[Clocked, ...]]:
    """Record each input; at the Kick run one program inline and arm a deadline 30 seconds after the Kick's instant;
    finish at the deadline."""
    seen = (*state, given)
    match given.input:
        case Kick():
            return Next(seen, (Run("read", _answer(), inline=True), Arm("late", given.at + timedelta(seconds=30))))
        case TimerFired():
            return Next(seen, (Finish(seen),))
        case _:
            return Next(seen)


@do
def _answer():
    """A run that answers at once."""
    return "read"


@do
def _published_then(events, body):
    """Publish ``events``, then run ``body`` (the fold) — the fold's subscriber already holds them (as
    ``simulate_fold`` does)."""
    for event in events:
        yield Publish(event)
    return (yield body)


def _run_clocked(step, extra=()) -> tuple[Clocked, ...]:
    """``fold_clocked(step)`` on the virtual clock from ``T0``, after one ``Kick``, under the handlers ``extra`` — the
    inputs the step recorded (its ``Finish``)."""
    body = with_handlers(extra, _published_then((Kick(),), fold_clocked(step, (), INPUTS)))
    seen = run_on_sim(on_bus(EventBus(), "fold", INPUTS, body))
    assert isinstance(seen, tuple), seen
    return tuple(given for given in seen if isinstance(given, Clocked))


def test_every_input_reaches_the_step_with_the_instant_the_fold_took_it() -> None:
    seen = _run_clocked(_seen_step)
    assert tuple(type(given) for given in seen) == (Clocked, Clocked, Clocked), seen
    assert (Kick(), Answered("read", "read"), TimerFired("late")) == tuple(given.input for given in seen), seen
    assert seen[0].at == T0, seen
    assert seen[1].at == T0, seen  # the inline run ends at once: its end is taken at the same instant
    assert seen[2].at == T0 + timedelta(seconds=30), seen  # the deadline's input is taken at the deadline


def test_the_instant_never_goes_back() -> None:
    readings = iter((T0 + timedelta(seconds=10), T0 + timedelta(seconds=5), T0 + timedelta(seconds=50)))

    @handler
    def set_back(effect, k):  # a wall clock set back between the first and the second input
        if isinstance(effect, GetTimeEffect):
            return (yield Resume(k, next(readings)))
        yield Pass(effect, k)

    seen = _run_clocked(_seen_step, (set_back,))
    assert tuple(given.at for given in seen) == (
        T0 + timedelta(seconds=10),
        T0 + timedelta(seconds=10),
        T0 + timedelta(seconds=50),
    ), seen


def _count_step(state: int, given: Clocked) -> Next[int]:
    """Count the inputs and keep the instant of the last one in the effects (an Arm 1 second after it)."""
    return Next(state + 1, (Arm("next", given.at + timedelta(seconds=1)),))


def test_replay_clocked_answers_the_same_trace_for_the_same_inputs() -> None:
    inputs = (Clocked(T0, Kick()), Clocked(T0 + timedelta(seconds=2), TimerFired("next")))
    first = replay_clocked(_count_step, 0, inputs)
    again = replay_clocked(_count_step, 0, inputs)
    assert first == again
    assert first.final == 2
    assert first.effects == (Arm("next", T0 + timedelta(seconds=1)), Arm("next", T0 + timedelta(seconds=3)))
    # The trace reads the bare input: the TimerFired took back the first deadline.
    assert first.armed == frozenset({"next"})


def test_replay_clocked_refuses_what_fold_clocked_never_delivers() -> None:
    with pytest.raises(ValueError, match="never go back"):
        replay_clocked(_count_step, 0, (Clocked(T0 + timedelta(seconds=2), Kick()), Clocked(T0, Kick())))
    with pytest.raises(TypeError, match="Clocked inputs"):
        replay_clocked(_count_step, 0, (Kick(),))  # type: ignore[arg-type] — the refusal under test
    with pytest.raises(ValueError, match="timezone-aware"):
        Clocked(datetime(2026, 1, 1), Kick())  # noqa: DTZ001 — the naive instant is the case


def test_fold_and_replay_still_give_the_bare_inputs() -> None:
    def bare_step(state: tuple[object, ...], event: object) -> Next[tuple[object, ...]]:
        return Next((*state, event), (Finish((*state, event)),))

    traced = replay(bare_step, (), (Kick(),))
    assert traced.final == (Kick(),)
    body = _published_then((Kick(),), fold(bare_step, (), INPUTS))
    assert run_on_sim(on_bus(EventBus(), "fold", INPUTS, body)) == (Kick(),)
