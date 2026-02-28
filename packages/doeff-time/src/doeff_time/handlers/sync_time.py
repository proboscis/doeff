"""Blocking wall-clock handler for doeff-time effects."""


import threading
import time
from collections.abc import Callable
from typing import Any

from doeff import Effect, Pass, Resume, WithHandler, default_handlers, do, run
from doeff_time.effects import DelayEffect, GetTimeEffect, ScheduleAtEffect, WaitUntilEffect

ProtocolHandler = Callable[[Any, Any], Any]


class SyncTimeRuntime:
    """Runtime container for sync wall-clock time effects."""

    def __init__(
        self,
        *,
        now: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self._now = now
        self._sleep = sleep
        self._pending_timers: set[threading.Timer] = set()

        @do
        def _protocol_handler(effect: Effect, k: Any):
            return (yield self.handle(effect, k))

        self._handler: ProtocolHandler = _protocol_handler

    def _run_scheduled(self, program: Any, timer: threading.Timer) -> None:
        try:
            run(
                WithHandler(self._handler, program),
                handlers=default_handlers(),
            )
        finally:
            self._pending_timers.discard(timer)

    @do
    def _handle_delay(self, effect: DelayEffect, k: Any):
        self._sleep(max(0.0, effect.seconds))
        return (yield Resume(k, None))

    @do
    def _handle_wait_until(self, effect: WaitUntilEffect, k: Any):
        self._sleep(max(0.0, effect.target - self._now()))
        return (yield Resume(k, None))

    @do
    def _handle_get_time(self, _effect: GetTimeEffect, k: Any):
        return (yield Resume(k, self._now()))

    @do
    def _handle_schedule_at(self, effect: ScheduleAtEffect, k: Any):
        wait_seconds = max(0.0, effect.time - self._now())
        timer_holder: dict[str, threading.Timer] = {}

        def _dispatch() -> None:
            self._run_scheduled(effect.program, timer_holder["timer"])

        timer = threading.Timer(wait_seconds, _dispatch)
        timer.daemon = True
        timer_holder["timer"] = timer
        self._pending_timers.add(timer)
        timer.start()
        return (yield Resume(k, None))

    @do
    def handle(self, effect: Effect, k: Any):
        if isinstance(effect, DelayEffect):
            return (yield self._handle_delay(effect, k))
        if isinstance(effect, WaitUntilEffect):
            return (yield self._handle_wait_until(effect, k))
        if isinstance(effect, GetTimeEffect):
            return (yield self._handle_get_time(effect, k))
        if isinstance(effect, ScheduleAtEffect):
            return (yield self._handle_schedule_at(effect, k))
        yield Pass()


def sync_time_handler(
    *,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> ProtocolHandler:
    """Return a protocol handler for blocking wall-clock time semantics."""

    runtime = SyncTimeRuntime(now=now, sleep=sleep)

    @do
    def handler(effect: Effect, k: Any):
        return (yield runtime._handler(effect, k))

    return handler


__all__ = [
    "ProtocolHandler",
    "sync_time_handler",
]
