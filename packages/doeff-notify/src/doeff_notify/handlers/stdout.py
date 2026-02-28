"""Stdout notification handler."""

from __future__ import annotations

from itertools import count
from typing import Any

from doeff import Effect, Pass, Resume, do
from doeff_notify.effects import Acknowledge, Notify, NotifyThread
from doeff_notify.types import Channel, NotificationResult, Urgency

_URGENCY_PREFIX = {
    Urgency.LOW: "INFO",
    Urgency.MEDIUM: "WARN",
    Urgency.HIGH: "ALERT",
    Urgency.CRITICAL: "CRITICAL",
}
_CONSOLE_IDS = count(1)


def _next_id() -> str:
    return f"console-{next(_CONSOLE_IDS)}"


@do
def console_handler(effect: Effect, k: Any):
    """Print notifications to stdout and return NotificationResult payloads."""

    if isinstance(effect, Notify):
        notification_id = _next_id()
        prefix = _URGENCY_PREFIX.get(effect.urgency, "INFO")
        title = f"{effect.title}: " if effect.title else ""
        print(f"[{prefix}] {title}{effect.message}")
        return (
            yield Resume(
                k,
                NotificationResult(
                    notification_id=notification_id,
                    channel=Channel.CONSOLE,
                    thread_id=notification_id,
                ),
            )
        )

    if isinstance(effect, NotifyThread):
        print(f"[THREAD:{effect.thread_id}] {effect.message}")
        return (yield Resume(k, None))

    if isinstance(effect, Acknowledge):
        return (yield Resume(k, False))

    yield Pass()


__all__ = ["console_handler"]
