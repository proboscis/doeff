"""Log-backed notification handler that emits Tell effects."""


from itertools import count
from typing import Any

from doeff import Effect, Pass, Resume, Tell, do
from doeff_notify.effects import Acknowledge, Notify, NotifyThread
from doeff_notify.types import Channel, NotificationResult

_LOG_IDS = count(1)


def _notify_payload(effect: Notify, notification_id: str) -> dict[str, Any]:
    return {
        "event": "notify",
        "notification_id": notification_id,
        "title": effect.title,
        "message": effect.message,
        "urgency": effect.urgency,
        "channel_hint": effect.channel,
        "metadata": dict(effect.metadata),
        "tags": list(effect.tags),
        "link": effect.link,
    }


@do
def log_handler(effect: Effect, k: Any):
    """Emit notification events as Tell effects for logging handlers."""

    if isinstance(effect, Notify):
        notification_id = f"log-{next(_LOG_IDS)}"
        yield Tell(_notify_payload(effect, notification_id))
        return (
            yield Resume(
                k,
                NotificationResult(
                    notification_id=notification_id,
                    channel=Channel.LOG,
                    thread_id=notification_id,
                ),
            )
        )

    if isinstance(effect, NotifyThread):
        yield Tell(
            {
                "event": "notify_thread",
                "thread_id": effect.thread_id,
                "message": effect.message,
                "metadata": dict(effect.metadata),
            }
        )
        return (yield Resume(k, None))

    if isinstance(effect, Acknowledge):
        yield Tell(
            {
                "event": "acknowledge",
                "notification_id": effect.notification_id,
                "timeout": effect.timeout,
            }
        )
        return (yield Resume(k, False))

    yield Pass()


__all__ = ["log_handler"]
