"""The parts of the cases that assemble a body other than one fold (``simulate_fold`` runs one fold): the bus
subscribed to the fold's inputs, timers inside it, on the virtual clock under ``scheduled``; and the cases' short
forms of ``Fold`` under ``fold_handler`` (``folded``) and of ``ReplayFold`` under ``replay_handler`` (``replay``)."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from doeff_core_effects import CreatePromise, Wait, scheduled
from doeff_events import EventBus, subscribed_event_handler, timer_handler
from doeff_stream import Fold, Inputs, Next, Replay, ReplayFold, fold_handler, replay_handler
from doeff_stream._sim import simulate_fold as simulate_fold
from doeff_time import sim_time_handler

from doeff import EffectBase, Program, do, run

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

Step = Callable[[object, object], "Next[object] | Program[Next[object]]"]


@dataclass(frozen=True)
class Started:
    """A runner's announcement that its program began (published on the fold's bus)."""

    key: str


def on_bus(bus: EventBus, name: str, inputs: Inputs, body: Program[object]) -> Program[object]:
    """``body`` as the subscriber ``name`` of ``bus``, subscribed to ``inputs``, with the timer handler inside."""
    return subscribed_event_handler(bus, name, inputs.event_types)(timer_handler()(body))


def run_on_sim(program: Program[object]) -> object:
    """Run ``program`` on the virtual clock from ``T0`` and answer its value (raise its error)."""
    return run(scheduled(sim_time_handler(start_time=T0)(program)))


@do
def performed(effect: EffectBase):
    """The program that performs ``effect`` and answers its answer."""
    return (yield effect)


def folded(
    step: Step, initial: object, inputs: Inputs, *, clocked: bool = False
) -> Program[object]:
    """``Fold(step, initial, inputs)`` under ``fold_handler``: the fold as a host performs it, innermost."""
    return fold_handler(performed(Fold(step, initial, inputs, clocked)))


def replay(step: Step, initial: object, inputs: Iterable[object]) -> Replay[object]:
    """The trace ``replay_handler`` answers for ``ReplayFold(step, initial, inputs)``."""
    return run(replay_handler(performed(ReplayFold(step, initial, tuple(inputs)))))


def replay_clocked(step: Step, initial: object, inputs: Iterable[object]) -> Replay[object]:
    """``replay`` of a step of ``Fold(…, clocked=True)``: the inputs are ``Clocked``."""
    return run(replay_handler(performed(ReplayFold(step, initial, tuple(inputs), clocked=True))))


@do
def forever():
    """A program that never ends by itself: it waits on a promise nobody completes."""
    promise = yield CreatePromise()
    return (yield Wait(promise.future))


@dataclass(frozen=True)
class Kick:
    """The first input of a case (``simulate_fold(..., events=(Kick(),))``)."""
