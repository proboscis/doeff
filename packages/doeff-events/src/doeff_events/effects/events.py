"""Publish/subscribe effects for event-driven doeff programs."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from doeff import EffectBase


@dataclass(frozen=True)
class StopArrived:
    """The stop signal came: ``reason`` is the stop reason ``AwaitStop`` answered.

    A loop's one stop watcher (``doeff_events.event_loop.begin_watch``) publishes it on the loop's bus, so the loop
    waits for its events and for the stop with one ``WaitForEvent`` (agora-redesign #3080's follow-up). A bus is
    shared only inside the reach of one stop signal (one job), so every loop on it stops on the same signal.
    ``subscribed_event_handler`` always subscribes it.
    """

    reason: str


@dataclass(frozen=True)
class SourceFailed:
    """A task that publishes signals on this bus failed: ``source`` names it (the subscriber its signals feed) and
    ``error`` is the exception it ended with.

    The task publishes it before it ends with ``error``, and the body the source feeds waits for it together with its
    events in one ``WaitForEvent`` and raises ``error`` again (doeff-records' signal sources — the body never races the
    source task per wait — agora-redesign #3135). ``subscribed_event_handler`` always subscribes it.
    """

    source: str
    error: BaseException


@dataclass(frozen=True)
class SourceStalled:
    """A task that publishes signals on this bus lost its store: ``source`` names it (the subscriber its signals
    feed), ``detail`` is the store's own words for why it is unreachable, and ``since`` is when it first was.

    The task stays alive and waits for the store to come back (doeff-records' signal sources wait for it as an
    event, up to the patience its foundation declares — agora-redesign #3469). It publishes ``SourceResumed``
    when the store answers again, or ``SourceFailed`` when the patience runs out. Unlike ``SourceFailed`` it is
    not always subscribed: a body that shows the stall (a screen telling its users the records are unreachable)
    names it in its subscription; others never see it.
    """

    source: str
    detail: str
    since: datetime


@dataclass(frozen=True)
class SourceResumed:
    """The task named ``source`` that published ``SourceStalled`` reached its store again and goes on publishing
    signals. Not always subscribed, like ``SourceStalled``."""

    source: str


@dataclass(frozen=True)
class SourceMissed:
    """A sender on ``channel`` could not hand the broker an event while the task named ``source`` was subscribed
    and connected: what was published on that channel since may be missing. The body that names it catches up
    once from its records, like on ``SourceResumed`` (``notice_events_handler`` turns the sender's gap notice into
    this event; a sender also tells one when it starts, for what its previous process may have left missing). It
    may come more than once for one gap; catching up again changes nothing. Not always subscribed, like
    ``SourceResumed``."""

    source: str
    channel: str


@dataclass(frozen=True)
class SourceStarted:
    """The task named ``source`` reached its store for the first time and began its subscription.

    A source that feeds events from another process (``notice_events_handler``) publishes it once, before it lets
    the body run: the body that names it in its subscription catches up once from its records and then goes on
    with events alone. Not always subscribed, like ``SourceResumed``.
    """

    source: str


def _normalize_event_types(
    effect_name: str, event_types: tuple[type[Any], ...]
) -> tuple[type[Any], ...]:
    if not event_types:
        raise ValueError(f"{effect_name} requires at least one event type")

    normalized: list[type[Any]] = []
    for event_type in event_types:
        if not isinstance(event_type, type):
            raise TypeError(
                f"{effect_name} event types must be type objects, got {type(event_type).__name__}"
            )
        if event_type not in normalized:
            normalized.append(event_type)

    return tuple(normalized)


class PublishEffect(EffectBase):
    """Publish an event to all listeners waiting on compatible event types."""

    def __init__(self, event: Any):
        super().__init__()
        self.event = event

    def __repr__(self):
        return f"Publish({self.event!r})"


class WaitForEventEffect(EffectBase):
    """Wait for the next event matching any of the configured event types."""

    def __init__(self, event_types: tuple[type[Any], ...]):
        super().__init__()
        self.event_types = _normalize_event_types("WaitForEvent", event_types)

    def __repr__(self):
        names = ", ".join(t.__name__ for t in self.event_types)
        return f"WaitForEvent({names})"


class WaitForEventsEffect(EffectBase):
    """Wait until at least one event matching the configured event types arrives, then answer every matching event
    already delivered to the waiter, in arrival order, as a non-empty tuple.

    ``WaitForEvent`` answers one event per wait, so a receiver that gets several events at the same moment folds them
    one by one and shows each partial state. ``WaitForEvents`` takes them all in one wait. A handler without a queue
    (``event_handler``) answers the one event it delivered as ``(event,)``.
    """

    def __init__(self, event_types: tuple[type[Any], ...]):
        super().__init__()
        self.event_types = _normalize_event_types("WaitForEvents", event_types)

    def __repr__(self):
        names = ", ".join(t.__name__ for t in self.event_types)
        return f"WaitForEvents({names})"


def publish(event: Any) -> PublishEffect:
    return PublishEffect(event=event)


def wait_for_event(*event_types: type[Any]) -> WaitForEventEffect:
    return WaitForEventEffect(event_types=tuple(event_types))


def wait_for_events(*event_types: type[Any]) -> WaitForEventsEffect:
    return WaitForEventsEffect(event_types=tuple(event_types))


# Capitalized aliases
Publish = publish
WaitForEvent = wait_for_event
WaitForEvents = wait_for_events


__all__ = [
    "Publish",
    "PublishEffect",
    "SourceFailed",
    "SourceResumed",
    "SourceStalled",
    "SourceStarted",
    "StopArrived",
    "WaitForEvent",
    "WaitForEventEffect",
    "WaitForEvents",
    "WaitForEventsEffect",
    "publish",
    "wait_for_event",
    "wait_for_events",
]
