"""The simulated host: ``fold`` assembled the way a host assembles it, on a virtual clock and a memory bus of its own.

A host installs, around ``fold(step, initial, inputs)``, the bus handler subscribed to ``inputs.event_types`` and the
timer handler inside it, under ``scheduled``. ``simulate_fold`` installs the same, with the two parts that touch the
outside world swapped for simulated ones: the clock is ``sim_time_handler`` (time moves only when every task waits)
and the bus is a fresh in-memory ``EventBus``. The caller's handlers (the simulated answerers of the effects its runs
perform) go inside both, so a run's effects reach them first — as in a host whose handlers sit around the fold.
"""

from collections.abc import Callable, Generator, Iterable
from datetime import datetime
from typing import Any, TypeVar

from doeff_core_effects import scheduled
from doeff_events import EventBus, Publish, subscribed_event_handler, timer_handler
from doeff_time import sim_time_handler

from doeff import Program, do, run, with_handlers
from doeff_stream._fold import fold
from doeff_stream._source import Inputs
from doeff_stream._step import Next

S = TypeVar("S")


@do
def _published_then(
    events: tuple[object, ...], body: Program[object]
) -> Generator[Any, Any, object]:
    """Publish ``events`` in order, then run ``body`` (the fold) and answer its value.

    The fold's subscriber was subscribed when its bus handler was built, so the events wait in its queue and are the
    first inputs of the step, in this order.
    """
    for event in events:
        yield Publish(event)
    return (yield body)


def simulate_fold(
    step: Callable[[S, object], "Next[S] | Program[Next[S]]"],
    initial: S,
    inputs: Inputs,
    *,
    events: Iterable[object] = (),
    handlers: Iterable[Callable[..., object]] = (),
    start_time: datetime | None = None,
) -> object:
    """Run ``fold(step, initial, inputs)`` on the virtual clock and a bus of its own; answer the fold's value.

    - ``events``: published on the bus before the fold's first wait, so they are the step's first inputs in this
      order (each must be of a type ``inputs`` subscribes). Later inputs come from the runs' ends, the timers the step
      arms and what the runs publish.
    - ``handlers``: the caller's handlers, first outermost (as ``with_handlers``), installed inside the bus and timer
      handlers — they may ``Publish``, arm timers and wait on the virtual clock.
    - ``start_time``: where the virtual clock starts (``sim_time_handler``'s default when ``None``).

    The fold's error (the step's, or ``StepYieldedEffectError``) is raised here.
    """
    body = with_handlers(handlers, _published_then(tuple(events), fold(step, initial, inputs)))
    hosted = subscribed_event_handler(EventBus(), "fold", inputs.event_types)(timer_handler()(body))
    return run(scheduled(sim_time_handler(start_time=start_time)(hosted)))
