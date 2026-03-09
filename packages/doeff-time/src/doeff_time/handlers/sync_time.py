"""Blocking wall-clock handler for doeff-time effects."""


import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import doeff_vm

from doeff import Effect, Pass, Resume, do, run
from doeff_time.effects import DelayEffect, GetTimeEffect, ScheduleAtEffect, WaitUntilEffect

ProtocolHandler = Callable[[Any, Any], Any]

_RUST_SENTINELS = (
    doeff_vm.state,
    doeff_vm.reader,
    doeff_vm.writer,
    doeff_vm.result_safe,
    doeff_vm.scheduler,
    doeff_vm.lazy_ask,
)


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
        self._pending_timers: set[threading.Timer] = set()

        @do
        def _protocol_handler(effect: Effect, k: Any):
            return (yield self.handle(effect, k))

        self._handler: ProtocolHandler = _protocol_handler

    def _rebuild_handler_stack(self, visible_handlers: list[Any]) -> list[Any]:
        sentinels = iter(_RUST_SENTINELS)
        rebuilt: list[Any] = []
        for handler in reversed(visible_handlers):
            if handler is None:
                rebuilt.append(next(sentinels))
            else:
                rebuilt.append(do(handler))
        return rebuilt

    def _run_scheduled(
        self,
        program: Any,
        timer: threading.Timer,
        visible_handlers: list[Any],
    ) -> None:
        try:
            run(
                program,
                handlers=self._rebuild_handler_stack(visible_handlers),
            )
        finally:
            self._pending_timers.discard(timer)

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
        visible_handlers = list((yield doeff_vm.GetHandlers()))
        timer_holder: dict[str, threading.Timer] = {}

        def _dispatch() -> None:
            self._run_scheduled(effect.program, timer_holder["timer"], visible_handlers)

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
    now: Callable[[], datetime] = _utc_now,
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
