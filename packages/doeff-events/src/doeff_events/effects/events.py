"""Publish/subscribe effects for event-driven doeff programs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doeff.effects.base import Effect, EffectBase, create_effect_with_trace


def _normalize_event_types(event_types: tuple[type[Any], ...]) -> tuple[type[Any], ...]:
    if not event_types:
        raise ValueError("WaitForEvent requires at least one event type")

    normalized: list[type[Any]] = []
    for event_type in event_types:
        if not isinstance(event_type, type):
            raise TypeError(
                "WaitForEvent event types must be type objects, "
                f"got {type(event_type).__name__}"
            )
        if event_type not in normalized:
            normalized.append(event_type)

    return tuple(normalized)


@dataclass(frozen=True)
class PublishEffect(EffectBase):
    """Publish an event to all listeners waiting on compatible event types."""

    event: Any


@dataclass(frozen=True)
class WaitForEventEffect(EffectBase):
    """Wait for the next event matching any of the configured event types."""

    event_types: tuple[type[Any], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_types", _normalize_event_types(self.event_types))


def publish(event: Any) -> PublishEffect:
    """Publish an event value."""

    return create_effect_with_trace(PublishEffect(event=event))


def wait_for_event(*event_types: type[Any]) -> WaitForEventEffect:
    """Wait for an event matching one of the given types."""

    return create_effect_with_trace(
        WaitForEventEffect(event_types=_normalize_event_types(tuple(event_types)))
    )


def Publish(event: Any) -> Effect:  # noqa: N802
    """Publish an event value (capitalized API)."""

    return create_effect_with_trace(PublishEffect(event=event), skip_frames=3)


def WaitForEvent(*event_types: type[Any]) -> Effect:  # noqa: N802
    """Wait for an event matching one of the given types (capitalized API)."""

    return create_effect_with_trace(
        WaitForEventEffect(event_types=_normalize_event_types(tuple(event_types))),
        skip_frames=3,
    )


__all__ = [
    "Publish",
    "PublishEffect",
    "WaitForEvent",
    "WaitForEventEffect",
    "publish",
    "wait_for_event",
]
