"""Effect definitions for provider-agnostic notifications."""


from dataclasses import dataclass, field
from typing import Any

from doeff import EffectBase
from doeff_notify._types import NotificationResult, Urgency


@dataclass(frozen=True, kw_only=True)
class NotificationEffectBase(EffectBase):
    """Base class for notification effects."""


@dataclass(frozen=True, kw_only=True)
class Notify(NotificationEffectBase, EffectBase[NotificationResult]):
    """Send a notification to one or more channels."""

    message: str
    title: str | None = None
    urgency: Urgency = Urgency.LOW
    channel: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    link: str | None = None


@dataclass(frozen=True, kw_only=True)
class NotifyThread(NotificationEffectBase):
    """Reply to an existing notification thread."""

    thread_id: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class Acknowledge(NotificationEffectBase):
    """Wait for or check acknowledgment state for a notification."""

    notification_id: str
    timeout: float | None = None


__all__ = [
    "Acknowledge",
    "NotificationEffectBase",
    "Notify",
    "NotifyThread",
]
