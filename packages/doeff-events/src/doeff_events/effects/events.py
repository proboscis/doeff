"""Publish/subscribe effects for event-driven doeff programs."""

from typing import Any

from doeff import EffectBase


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
    "WaitForEvent",
    "WaitForEventEffect",
    "publish",
    "wait_for_event",
]
