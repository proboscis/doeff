"""Publish/subscribe effects for event-driven doeff programs."""

from dataclasses import dataclass
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


def _normalize_event_types(event_types: tuple[type[Any], ...]) -> tuple[type[Any], ...]:
    if not event_types:
        raise ValueError("WaitForEvent requires at least one event type")

    normalized: list[type[Any]] = []
    for event_type in event_types:
        if not isinstance(event_type, type):
            raise TypeError(
                f"WaitForEvent event types must be type objects, got {type(event_type).__name__}"
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
        self.event_types = _normalize_event_types(event_types)

    def __repr__(self):
        names = ", ".join(t.__name__ for t in self.event_types)
        return f"WaitForEvent({names})"


def publish(event: Any) -> PublishEffect:
    return PublishEffect(event=event)


def wait_for_event(*event_types: type[Any]) -> WaitForEventEffect:
    return WaitForEventEffect(event_types=tuple(event_types))


# Capitalized aliases
Publish = publish
WaitForEvent = wait_for_event


__all__ = [
    "Publish",
    "PublishEffect",
    "SourceFailed",
    "StopArrived",
    "WaitForEvent",
    "WaitForEventEffect",
    "publish",
    "wait_for_event",
]
