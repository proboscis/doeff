"""Sources of a fold's inputs and their merge into the one set of event types the fold waits for.

Four kinds of source feed a fold. Two are named by the caller: events other tasks publish on the bus
(``subscribe``) and deadlines (``timers`` — ``TimerFired`` of the timers the step arms). Two are the runtime's own and
are in every merge, so no fold can leave them out: the answer of an effect the step ran and the end of its task
(``Answered`` / ``Raised`` / ``Cancelled``). Every merge also carries the bus's own two, which
``subscribed_event_handler`` subscribes for every waiting subscriber: the stop signal (``StopArrived`` — the step
decides the order of the stop and answers ``Finish``) and a failed signal source (``SourceFailed``). ``merge`` joins the sources; ``Inputs.event_types`` is what the caller
subscribes the fold's bus handler to (``subscribed_event_handler(bus, name, inputs.event_types)``).
"""

from collections.abc import Hashable
from dataclasses import dataclass

from doeff_events import SourceFailed, StopArrived, TimerFired

from doeff_stream._step import Answered, Cancelled, Raised, RunEnd


@dataclass(frozen=True)
class RunEnded:
    """The bus event a run publishes when it ended (its runner, or the watcher of a cancelled run): the fold
    ``origin`` that started it, the ``mark`` of that one run, its ``key`` and ``end``. The fold takes the events of its
    own origin and gives the step ``end`` once per mark."""

    origin: object
    mark: object
    key: Hashable
    end: RunEnd


_RUN_ENDS: tuple[type, ...] = (Answered, Raised, Cancelled, RunEnded)


@dataclass(frozen=True)
class Source:
    """One source of a fold's inputs: the event types it brings to the step."""

    event_types: tuple[type, ...]


@dataclass(frozen=True)
class Inputs:
    """The merged sources of one fold: the event types the fold waits for, the runtime's own included."""

    event_types: tuple[type, ...]


def subscribe(*event_types: type) -> Source:
    """The events of ``event_types`` that tasks publish on the fold's bus.

    The runtime's own inputs are not subscribed this way (a published ``Answered`` would read as a run's end):
    naming them is refused.
    """
    if not event_types:
        raise ValueError("subscribe needs at least one event type")
    for event_type in event_types:
        if not isinstance(event_type, type):
            raise TypeError(f"subscribe takes event types, got {event_type!r}")
        if issubclass(event_type, _RUN_ENDS):
            raise ValueError(
                f"{event_type.__name__} is the runtime's own input: every fold has it, no source brings it"
            )
    return Source(tuple(event_types))


def timers() -> Source:
    """The deadlines the step sets: ``TimerFired(tag)``.

    A deadline set with ``Arm(tag, at)`` comes to the fold that set it only (the fold tells its own apart). One set by
    a run (``Run(key, ArmTimer(tag, at))``) reaches every subscriber of the bus, not only the fold that armed it:
    folds sharing a bus keep those tags apart (a tag that names the fold, as a key names its part).
    """
    return Source((TimerFired,))


def merge(*sources: Source) -> Inputs:
    """One input stream of ``sources`` and the runtime's own (the stop signal, a failed source, effect answers and
    task ends), in that order.

    A type two sources name is waited for once.
    """
    for source in sources:
        if not isinstance(source, Source):
            raise TypeError(f"merge takes sources, got {type(source).__name__}")
    named = (event_type for source in sources for event_type in source.event_types)
    # dict.fromkeys keeps the first place of each type and drops the repeats.
    return Inputs(tuple(dict.fromkeys((*named, StopArrived, SourceFailed, RunEnded))))
