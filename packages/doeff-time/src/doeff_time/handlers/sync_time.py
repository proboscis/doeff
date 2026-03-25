"""Blocking wall-clock handler for doeff-time effects."""


import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from doeff import Pass, Resume, do
from doeff_core_effects.scheduler import CreateExternalPromise, Spawn, Wait as WaitTask
from doeff_time.effects import DelayEffect, GetTimeEffect, ScheduleAtEffect, WaitUntilEffect

ProtocolHandler = Callable[[Any, Any], Any]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SyncTimeRuntime:
    """Runtime container for sync wall-clock time effects."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime],
        sleep: Callable[[float], None],
    ) -> None:
        self._now = now
        self._sleep = sleep

    @do
    def _handle_delay(self, effect: DelayEffect, k: Any):
        self._sleep(max(0.0, effect.seconds))
        return (yield Resume(k, None))

    @do
    def _handle_wait_until(self, effect: WaitUntilEffect, k: Any):
        self._sleep(max(0.0, (effect.target - self._now()).total_seconds()))
        return (yield Resume(k, None))

    @do
    def _handle_get_time(self, _effect: GetTimeEffect, k: Any):
        return (yield Resume(k, self._now()))

    @do
    def _handle_schedule_at(self, effect: ScheduleAtEffect, k: Any):
        wait_seconds = max(0.0, (effect.time - self._now()).total_seconds())

        @do
        def deferred():
            ep = yield CreateExternalPromise()

            def _timer_done():
                ep.complete(None)

            timer = threading.Timer(wait_seconds, _timer_done)
            timer.daemon = True
            timer.start()
            yield WaitTask(ep.future)
            yield effect.program

        yield Spawn(deferred())
        return (yield Resume(k, None))

    @do
    def handle(self, effect: Any, k: Any):
        if isinstance(effect, DelayEffect):
            return (yield self._handle_delay(effect, k))
        if isinstance(effect, WaitUntilEffect):
            return (yield self._handle_wait_until(effect, k))
        if isinstance(effect, GetTimeEffect):
            return (yield self._handle_get_time(effect, k))
        if isinstance(effect, ScheduleAtEffect):
            return (yield self._handle_schedule_at(effect, k))
        yield Pass(effect, k)


def sync_time_handler(
    *,
    now: Callable[[], datetime] = _utc_now,
    sleep: Callable[[float], None] = time.sleep,
) -> ProtocolHandler:
    """Return a protocol handler for blocking wall-clock time semantics."""

    runtime = SyncTimeRuntime(now=now, sleep=sleep)

    @do
    def handler(effect: Any, k: Any):
        return (yield runtime.handle(effect, k))

    return handler


__all__ = [
    "ProtocolHandler",
    "sync_time_handler",
]
