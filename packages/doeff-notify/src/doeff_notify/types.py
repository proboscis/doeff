"""Shared notification types."""


from dataclasses import dataclass


class Urgency:
    """Notification urgency levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    ALL = (LOW, MEDIUM, HIGH, CRITICAL)

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls.ALL


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
