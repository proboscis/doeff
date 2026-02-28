"""In-memory notification handler for tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from itertools import count
from typing import Any, overload

from doeff import Effect, Pass, Resume, do
from doeff_notify.effects import Acknowledge, Notify, NotifyThread
from doeff_notify.types import Channel, NotificationResult

ProtocolHandler = Callable[[Any, Any], Any]


@dataclass
class NotificationCapture(Sequence[Notify]):
    """Captured notifications and related events from testing_handler."""

    notifications: list[Notify] = field(default_factory=list)
    thread_updates: list[NotifyThread] = field(default_factory=list)
    acknowledgements: list[Acknowledge] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.notifications)

    @overload
    def __getitem__(self, index: int) -> Notify: ...

    @overload
    def __getitem__(self, index: slice) -> list[Notify]: ...

    def __getitem__(self, index: int | slice) -> Notify | list[Notify]:
        return self.notifications[index]

    def __iter__(self) -> Iterator[Notify]:
        return iter(self.notifications)


def collected_notifications(capture: NotificationCapture) -> list[Notify]:
    """Return captured Notify effects as a plain list for assertions."""

    return list(capture.notifications)


def build_testing_handler(
    *,
    auto_acknowledge: bool = False,
    default_channel: str = Channel.TESTING,
) -> tuple[ProtocolHandler, NotificationCapture]:
    """Create a handler that captures notifications in memory."""

    capture = NotificationCapture()
    seen_ids: set[str] = set()
    next_id = count(1)

    @do
    def _handler(effect: Effect, k: Any):
        if isinstance(effect, Notify):
            capture.notifications.append(effect)
            notification_id = f"{default_channel}-{next(next_id)}"
            seen_ids.add(notification_id)
            return (
                yield Resume(
                    k,
                    NotificationResult(
                        notification_id=notification_id,
                        channel=effect.channel or default_channel,
                        thread_id=notification_id,
                    ),
                )
            )

        if isinstance(effect, NotifyThread):
            capture.thread_updates.append(effect)
            return (yield Resume(k, None))

        if isinstance(effect, Acknowledge):
            capture.acknowledgements.append(effect)
            acknowledged = auto_acknowledge and effect.notification_id in seen_ids
            return (yield Resume(k, acknowledged))

        yield Pass()

    return _handler, capture


testing_handler = build_testing_handler


__all__ = [
    "NotificationCapture",
    "ProtocolHandler",
    "build_testing_handler",
    "collected_notifications",
    "testing_handler",
]
