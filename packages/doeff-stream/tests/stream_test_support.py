"""Running a fold the way a host assembles it: the bus subscribed to the fold's inputs, timers inside it, on the
virtual clock under ``scheduled``."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TypeVar

from doeff_core_effects import CreatePromise, Spawn, Wait, scheduled
from doeff_events import EventBus, Publish, subscribed_event_handler, timer_handler
from doeff_stream import Inputs, Next, fold
from doeff_time import Delay, sim_time_handler

from doeff import Program, do, run

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

S = TypeVar("S")


@dataclass(frozen=True)
class Started:
    """A runner's announcement that its program began (published on the fold's bus)."""

    key: str


def hosted(
    step: Callable[[S, object], "Next[S] | Program[Next[S]]"],
    initial: S,
    inputs: Inputs,
    name: str = "host",
) -> Program[object]:
    """``fold`` under the handlers a host installs around it on a bus of its own."""
    return on_bus(EventBus(), name, inputs, fold(step, initial, inputs))


def on_bus(bus: EventBus, name: str, inputs: Inputs, body: Program[object]) -> Program[object]:
    """``body`` as the subscriber ``name`` of ``bus``, subscribed to ``inputs``, with the timer handler inside."""
    return subscribed_event_handler(bus, name, inputs.event_types)(timer_handler()(body))


def run_on_sim(program: Program[object]) -> object:
    """Run ``program`` on the virtual clock from ``T0`` and answer its value (raise its error)."""
    return run(scheduled(sim_time_handler(start_time=T0)(program)))


@do
def forever():
    """A program that never ends by itself: it waits on a promise nobody completes."""
    promise = yield CreatePromise()
    return (yield Wait(promise.future))


@dataclass(frozen=True)
class Kick:
    """The first input of a case: published once the fold waits."""


@do
def _kick_then(body: Program[object]):
    """Start ``body`` (a fold), publish ``Kick()`` once it waits, and answer the fold's value."""
    task = yield Spawn(body)
    yield Delay(1.0)
    yield Publish(Kick())
    return (yield Wait(task))


def kicked(
    step: Callable[[S, object], "Next[S] | Program[Next[S]]"], initial: S, inputs: Inputs
) -> object:
    """Run ``fold(step, initial, inputs)`` on a bus of its own, its first input ``Kick()``; answer the fold's value."""
    return run_on_sim(on_bus(EventBus(), "host", inputs, _kick_then(fold(step, initial, inputs))))
