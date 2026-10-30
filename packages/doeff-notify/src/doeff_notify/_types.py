"""Shared notification types."""


from dataclasses import dataclass
from enum import StrEnum


class Urgency(StrEnum):
    """Notification urgency levels (the type of ``Notify.urgency`` — each level is its lower-case string)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._value2member_map_


class Channel:
    """Common channel identifiers used by handlers."""

    CONSOLE = "console"
    LOG = "log"
    TESTING = "testing"
    SLACK = "slack"
    EMAIL = "email"
    DISCORD = "discord"
    PAGERDUTY = "pagerduty"


@dataclass(frozen=True, kw_only=True)
class NotificationResult:
    """Result payload returned by notification handlers."""

    notification_id: str
    channel: str
    thread_id: str | None = None
    acknowledged: bool = False


__all__ = [
    "Channel",
    "NotificationResult",
    "Urgency",
]
